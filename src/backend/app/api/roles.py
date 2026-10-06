# app/api/roles.py - 角色配置（R8）：角色 = 模块授权预设（admin）
# 应用角色 = 该用户 grants 全量重写（允许列表 allowed=1，其余 allowed=0，角色即真相）；
# 内置角色不可删除、不可改名；audit 走结构化日志（与其他 admin api 一致）。
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.exceptions import NotFoundError, ValidationError
from app.infrastructure.database import Module, Role, User, UserModuleGrant
from app.core.logging import logger

router = APIRouter(prefix="/api/admin/roles", tags=["admin-roles"])


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=255)
    module_codes: List[str] = Field(default_factory=list)


class ApplyIn(BaseModel):
    user_ids: List[int] = Field(min_length=1)


def _role_out(r: Role) -> dict:
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "module_codes": r.module_codes or [], "is_builtin": bool(r.is_builtin),
    }


def _validate_codes(db: Session, codes: List[str]) -> None:
    valid = {m.code for m in db.query(Module).all()}
    unknown = [c for c in codes if c not in valid]
    if unknown:
        raise ValidationError(f"未知模块码: {', '.join(unknown)}")


@router.get("")
def list_roles(_admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    rows = db.query(Role).order_by(Role.is_builtin.desc(), Role.id).all()
    return {"items": [_role_out(r) for r in rows]}


@router.post("", status_code=201)
def create_role(data: RoleIn, admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    _validate_codes(db, data.module_codes)
    if db.query(Role).filter(Role.name == data.name.strip()).first():
        raise ValidationError("角色名已存在", code="ROLE_NAME_TAKEN")
    role = Role(name=data.name.strip(), description=data.description,
                module_codes=data.module_codes, is_builtin=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    logger.info(f"[audit] action=role.create operator={admin.username} role={role.name} "
                f"codes={role.module_codes}")
    return _role_out(role)


@router.patch("/{role_id}")
def update_role(role_id: int, data: RoleIn,
                admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise NotFoundError("角色不存在", code="ROLE_NOT_FOUND")
    _validate_codes(db, data.module_codes)
    new_name = data.name.strip()
    if role.is_builtin and new_name != role.name:
        raise ValidationError("内置角色不可改名")
    if new_name != role.name and db.query(Role).filter(Role.name == new_name).first():
        raise ValidationError("角色名已存在", code="ROLE_NAME_TAKEN")
    role.name = new_name
    role.description = data.description
    role.module_codes = data.module_codes
    db.commit()
    logger.info(f"[audit] action=role.update operator={admin.username} role={role.name} "
                f"codes={role.module_codes}")
    return _role_out(role)


@router.delete("/{role_id}")
def delete_role(role_id: int, admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise NotFoundError("角色不存在", code="ROLE_NOT_FOUND")
    if role.is_builtin:
        raise ValidationError("内置角色不可删除")
    db.query(User).filter(User.app_role_id == role_id).update(
        {User.app_role_id: None}, synchronize_session=False)
    db.delete(role)
    db.commit()
    logger.info(f"[audit] action=role.delete operator={admin.username} role={role.name}")
    return {"deleted": True, "id": role_id}


@router.post("/{role_id}/apply")
def apply_role(role_id: int, data: ApplyIn, admin: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    """把角色预设应用到所选用户（全量覆盖 grants）。admin 用户直通全模块，自动跳过。"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise NotFoundError("角色不存在", code="ROLE_NOT_FOUND")
    allow = set(role.module_codes or [])
    all_codes = [m.code for m in db.query(Module).order_by(Module.sort_order).all()]

    applied, skipped = [], []
    for uid in data.user_ids:
        user = db.query(User).filter(User.id == uid).first()
        if user is None:
            skipped.append({"user_id": uid, "reason": "用户不存在"})
            continue
        if user.role == "admin":
            skipped.append({"user_id": uid, "reason": "管理员默认全模块"})
            continue
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == uid).delete()
        for code in all_codes:
            db.add(UserModuleGrant(
                user_id=uid, module_code=code, allowed=code in allow, granted_by=admin.id,
            ))
        user.app_role_id = role.id  # R12：记录应用的角色预设（用户管理列表展示）
        applied.append(user.username)
    db.commit()
    logger.info(f"[audit] action=role.apply operator={admin.username} role={role.name} "
                f"applied={applied} skipped={[s['user_id'] for s in skipped]}")
    return {"applied": applied, "skipped": skipped, "role": role.name}
