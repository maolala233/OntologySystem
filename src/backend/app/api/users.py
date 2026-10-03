# app/api/users.py - 用户管理（admin，docs/design/03 §3）
# 审计说明：audit_logs 表 M4 才建（02 §6 R6），本期以结构化日志替代。

from __future__ import annotations

import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.exceptions import DuplicateUploadError, NotFoundError, ValidationError
from app.infrastructure.database import User
from app.core.logging import logger

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    locale: str
    is_active: bool

    class Config:
        from_attributes = True


class UserCreateIn(BaseModel):
    username: str = Query(min_length=2, max_length=64)
    password: str = Query(min_length=6)
    role: str = "user"
    email: Optional[EmailStr] = None
    display_name: Optional[str] = None


class UserPatchIn(BaseModel):
    role: Optional[str] = None                 # admin | user
    is_active: Optional[bool] = None
    display_name: Optional[str] = None
    reset_password: bool = False               # true → 生成随机密码，响应一次性返回


@router.get("")
def list_users(
    keyword: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[int] = None,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(User).order_by(User.id)
    if cursor:
        q = q.filter(User.id > cursor)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(User.username.like(like) | User.email.like(like))
    rows = q.limit(limit).all()
    items = [UserOut.model_validate(u).model_dump() for u in rows]
    next_cursor = rows[-1].id if len(rows) == limit else None
    return {"items": items, "next_cursor": next_cursor, "has_more": next_cursor is not None}


@router.post("", status_code=201)
def create_user(data: UserCreateIn,
                admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    if data.role not in ("admin", "user"):
        raise ValidationError("role 仅支持 admin/user")
    if db.query(User).filter(User.username == data.username).first():
        raise DuplicateUploadError("用户名已存在", code="USERNAME_TAKEN")
    from app.api.auth import hash_password

    user = User(
        username=data.username,
        hashed_password=hash_password(data.password),
        role=data.role,
        email=str(data.email) if data.email else None,
        display_name=data.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"[audit] action=user.create operator={admin.username} target={user.username} role={user.role}")
    return UserOut.model_validate(user)


@router.patch("/{user_id}")
def patch_user(user_id: int, data: UserPatchIn,
               admin: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundError("用户不存在", code="USER_NOT_FOUND")
    if user.id == admin.id and (data.role is not None or data.is_active is False):
        # 防锁死：不允许自己改自己的角色或停用自己
        raise ValidationError("不能修改自己的角色或停用自己")

    reset_password: Optional[str] = None
    if data.role is not None:
        if data.role not in ("admin", "user"):
            raise ValidationError("role 仅支持 admin/user")
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.display_name is not None:
        user.display_name = data.display_name.strip() or None
    if data.reset_password:
        reset_password = secrets.token_urlsafe(9)  # 12 字符级，仅本次响应返回
        from app.api.auth import hash_password
        user.hashed_password = hash_password(reset_password)

    db.commit()
    logger.info(
        f"[audit] action=user.patch operator={admin.username} target={user.username} "
        f"role={data.role} is_active={data.is_active} reset_password={data.reset_password}"
    )
    body = UserOut.model_validate(user).model_dump()
    if reset_password:
        body["password"] = reset_password  # 仅此一次可见
    return body
