# app/api/model_configs.py - 模型配置 API（M2，docs/design/03 §5）
# 权限：global 配置 admin；project 配置 [PR:owner]。api_key 落库密文、响应掩码。

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.provider import (
    PROVIDER_META,
    ModelPurpose,
    mark_test_result,
    resolve_provider,
    test_connectivity,
)
from app.core.deps import get_current_user, get_db, require_project_role, require_role
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.security import decrypt_str, encrypt_str, mask_secret
from app.infrastructure.database import ModelConfig, User
from app.core.logging import logger

router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])


class ModelConfigIn(BaseModel):
    scope: str = "global"
    project_id: Optional[int] = None
    purpose: ModelPurpose
    name: str = Field(min_length=1, max_length=64)
    provider: str
    base_url: str
    api_key: Optional[str] = None       # 明文仅出现在请求中；落库前加密
    model_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    dims: Optional[int] = None          # purpose=embedding 必填
    is_default: bool = False


class ModelConfigPatch(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None       # 提供才更新；None 保持不变
    model_name: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    dims: Optional[int] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None


class TestIn(BaseModel):
    purpose: ModelPurpose
    provider: str
    base_url: str
    api_key: Optional[str] = None       # 未提供且带 config_id 时取库内密文
    model_name: str
    dims: Optional[int] = None
    config_id: Optional[int] = None     # 保存后测试：回填 last_test_at/ok


def _scope_guard(data_scope: str, project_id: Optional[int], user: User, db: Session):
    """global → admin；project → [PR:owner]。授权失败统一 403。"""
    if data_scope == "global":
        if user.role != "admin":
            raise ForbiddenError("全局模型配置需要管理员权限", code="ADMIN_REQUIRED")
        return
    if project_id is None:
        raise ValidationError("scope=project 必须提供 project_id")
    require_project_role(project_id, "owner", user, db)


def _out(row: ModelConfig) -> dict:
    masked = ""
    if row.api_key_encrypted:
        try:
            masked = mask_secret(decrypt_str(bytes(row.api_key_encrypted)))
        except Exception:  # noqa: BLE001 —— 密钥主密钥更换等场景，掩码降级
            masked = "****"
    return {
        "id": row.id, "scope": row.scope, "project_id": row.project_id,
        "purpose": row.purpose, "name": row.name, "provider": row.provider,
        "base_url": row.base_url, "model_name": row.model_name,
        "api_key_masked": masked, "api_key_set": bool(row.api_key_encrypted),
        "params": row.params or {}, "dims": row.dims,
        "is_default": row.is_default, "enabled": row.enabled,
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
        "last_test_ok": row.last_test_ok,
    }


def _unset_other_defaults(db: Session, scope: str, project_id: Optional[int], purpose: str,
                          keep_id: Optional[int] = None):
    q = (db.query(ModelConfig)
         .filter(ModelConfig.scope == scope, ModelConfig.purpose == purpose,
                 ModelConfig.is_default.is_(True)))
    q = q.filter(ModelConfig.project_id == project_id) if scope == "project" else \
        q.filter(ModelConfig.scope == "global")
    for row in q.all():
        if keep_id is None or row.id != keep_id:
            row.is_default = False


@router.get("/providers")
def list_providers(_user: User = Depends(get_current_user)):
    """provider 元数据（表单动态渲染，03 §5）。"""
    return {"items": [{"id": k, **v} for k, v in PROVIDER_META.items()]}


@router.get("")
def list_configs(purpose: Optional[str] = None, scope: Optional[str] = None,
                 project_id: Optional[int] = None,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """列表（api_key 只回掩码）。项目级配置要求项目成员。"""
    if project_id is not None:
        require_project_role(project_id, "viewer", user, db)
    q = db.query(ModelConfig)
    if purpose:
        q = q.filter(ModelConfig.purpose == purpose)
    if scope:
        q = q.filter(ModelConfig.scope == scope)
    if project_id is not None:
        q = q.filter(ModelConfig.project_id == project_id)
    rows = q.order_by(ModelConfig.purpose, ModelConfig.scope, ModelConfig.id).all()
    # 普通用户不可见 global 行的完整信息？——可见（只读用于选择），密钥已掩码
    return {"items": [_out(r) for r in rows]}


@router.post("", status_code=201)
def create_config(data: ModelConfigIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _scope_guard(data.scope, data.project_id, user, db)
    if data.purpose == ModelPurpose.EMBEDDING and not data.dims:
        raise ValidationError("嵌入模型必须声明 dims（与 Milvus collection 一致）")
    if data.provider not in PROVIDER_META:
        raise ValidationError(f"未知 provider: {data.provider}，可选 {sorted(PROVIDER_META)}")
    row = ModelConfig(
        scope=data.scope, project_id=data.project_id if data.scope == "project" else None,
        purpose=data.purpose.value, name=data.name, provider=data.provider,
        base_url=data.base_url,
        api_key_encrypted=encrypt_str(data.api_key) if data.api_key else None,
        model_name=data.model_name, params=data.params or {},
        dims=data.dims, is_default=data.is_default, enabled=True,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    if data.is_default:
        _unset_other_defaults(db, row.scope, row.project_id, row.purpose, keep_id=row.id)
    db.commit()
    db.refresh(row)
    logger.info(f"[audit] action=model.create operator={user.username} target={row.name} "
                f"purpose={row.purpose} scope={row.scope}")
    return _out(row)


@router.patch("/{config_id}")
def patch_config(config_id: int, data: ModelConfigPatch,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if not row:
        raise NotFoundError("配置不存在", code="MODEL_CONFIG_NOT_FOUND")
    _scope_guard(row.scope, row.project_id, user, db)

    if data.name is not None:
        row.name = data.name
    if data.provider is not None:
        if data.provider not in PROVIDER_META:
            raise ValidationError(f"未知 provider: {data.provider}")
        row.provider = data.provider
    if data.base_url is not None:
        row.base_url = data.base_url
    if data.api_key:  # 提供非空才轮换密钥；空串=清除
        row.api_key_encrypted = encrypt_str(data.api_key)
    elif data.api_key == "":
        row.api_key_encrypted = None
    if data.model_name is not None:
        row.model_name = data.model_name
    if data.params is not None:
        row.params = data.params
    if data.dims is not None:
        row.dims = data.dims
    if data.enabled is not None:
        row.enabled = data.enabled
    if data.is_default is not None:
        row.is_default = data.is_default
        if data.is_default:
            _unset_other_defaults(db, row.scope, row.project_id, row.purpose, keep_id=row.id)
    row.updated_at = datetime.utcnow()
    db.commit()
    logger.info(f"[audit] action=model.update operator={user.username} target={row.name}")
    return _out(row)


@router.delete("/{config_id}")
def delete_config(config_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    row = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if not row:
        raise NotFoundError("配置不存在", code="MODEL_CONFIG_NOT_FOUND")
    _scope_guard(row.scope, row.project_id, user, db)
    if row.is_default:
        raise ValidationError("默认配置不可删除：请先将默认标记移到其他配置")
    db.delete(row)
    db.commit()
    logger.info(f"[audit] action=model.delete operator={user.username} target={row.name}")
    return {"message": "ok"}


@router.put("/{config_id}/default")
def set_default(config_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    row = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if not row:
        raise NotFoundError("配置不存在", code="MODEL_CONFIG_NOT_FOUND")
    _scope_guard(row.scope, row.project_id, user, db)
    row.is_default = True
    row.enabled = True
    _unset_other_defaults(db, row.scope, row.project_id, row.purpose, keep_id=row.id)
    db.commit()
    logger.info(f"[audit] action=model.default operator={user.username} target={row.name}")
    return _out(row)


@router.post("/test")
def test_config(data: TestIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """连通性测试：支持未保存的表单值，也支持已保存配置（config_id → 更新测试状态）。"""
    api_key = data.api_key
    if not api_key and data.config_id:
        row = db.query(ModelConfig).filter(ModelConfig.id == data.config_id).first()
        if row and row.api_key_encrypted:
            api_key = decrypt_str(bytes(row.api_key_encrypted))
    result = test_connectivity(data.purpose, data.provider, data.base_url,
                               api_key or "", data.model_name, data.dims)
    if data.config_id:
        mark_test_result(db, data.config_id, result.get("ok", False))
    return result


@router.get("/resolve")
def resolve_current(purpose: str = "extract", project_id: Optional[int] = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """调试用：按当前解析优先级返回生效配置（密钥掩码）。"""
    try:
        resolved = resolve_provider(ModelPurpose(purpose), project_id, db=db)
        return {
            "base_url": resolved.base_url, "model_name": resolved.model_name,
            "source": resolved.source, "params": resolved.params,
            "api_key_masked": mask_secret(resolved.api_key) if resolved.api_key else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
