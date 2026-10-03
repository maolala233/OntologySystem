# app/api/modules.py - 模块授权矩阵（admin，docs/design/03 §4）
# 审计说明：audit_logs 表 M4 才建（02 §6 R6），本期以结构化日志替代。

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.exceptions import NotFoundError, ValidationError
from app.infrastructure.database import Module, User, UserModuleGrant
from app.core.logging import logger

router = APIRouter(prefix="/api/admin/modules", tags=["admin-modules"])


class GrantItem(BaseModel):
    module_code: str
    allowed: bool


class GrantsIn(BaseModel):
    user_id: int
    grants: List[GrantItem]


@router.get("")
def list_modules(_admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    """14 个模块码字典 + 每模块开通人数（显式 allow + default_on 的去重用户数）。"""
    modules = db.query(Module).order_by(Module.sort_order).all()
    grants = db.query(UserModuleGrant).all()
    default_on = {m.code for m in modules if m.is_default_on}
    user_allow: dict[int, set[str]] = {}
    user_deny: dict[int, set[str]] = {}
    for g in grants:
        (user_allow if g.allowed else user_deny).setdefault(g.user_id, set()).add(g.module_code)
    uids = {r[0] for r in db.query(User.id).all()}
    items = []
    for m in modules:
        # 开通人数 = 显式 allow 的用户 + 默认开通且未显式 deny 的用户
        enabled_users = 0
        for uid in uids:
            if m.code in user_deny.get(uid, set()):
                continue
            if m.code in user_allow.get(uid, set()) or m.code in default_on:
                enabled_users += 1
        items.append({
            "code": m.code, "name": m.name, "description": m.description,
            "is_default_on": m.is_default_on, "sort_order": m.sort_order,
            "enabled_users": enabled_users,
        })
    return {"items": items}


@router.get("/grants")
def get_grants(user_id: int = Query(...), _admin: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    """单用户授权矩阵：返回显式授权行；未列出的模块按 is_default_on 兜底。"""
    if not db.query(User).filter(User.id == user_id).first():
        raise NotFoundError("用户不存在", code="USER_NOT_FOUND")
    rows = db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user_id).all()
    return {
        "user_id": user_id,
        "grants": [{"module_code": g.module_code, "allowed": bool(g.allowed)} for g in rows],
    }


@router.put("/grants")
def put_grants(data: GrantsIn, admin: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    """整表保存单用户授权矩阵：先删后插（事务），模块码必须存在于字典。"""
    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        raise NotFoundError("用户不存在", code="USER_NOT_FOUND")
    if user.role == "admin":
        raise ValidationError("管理员默认全模块，无需授权矩阵")
    valid_codes = {m.code for m in db.query(Module).all()}
    for g in data.grants:
        if g.module_code not in valid_codes:
            raise ValidationError(f"未知模块码: {g.module_code}")

    db.query(UserModuleGrant).filter(UserModuleGrant.user_id == data.user_id).delete()
    for g in data.grants:
        db.add(UserModuleGrant(
            user_id=data.user_id, module_code=g.module_code,
            allowed=g.allowed, granted_by=admin.id,
        ))
    db.commit()
    logger.info(f"[audit] action=user.grant operator={admin.username} "
                f"target={user.username} grants={[(g.module_code, g.allowed) for g in data.grants]}")
    return {"message": "ok", "user_id": data.user_id, "count": len(data.grants)}
