# app/core/deps.py - FastAPI 依赖注入与权限守卫（docs/design/03 §1，M1 实现）
# 权限判定优先级（02 §3.1）：role=admin 全通过 → user_module_grants.allowed=0 显式拒绝
#           → grants.allowed=1 通过 → modules.is_default_on 兜底

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AuthError,
    ForbiddenError,
    ProjectForbiddenError,
    TokenExpiredError,
)
from app.core.permissions import has_project_role, resolve_user_modules
from app.infrastructure.database import (
    Module,
    ProjectMember,
    User,
    UserModuleGrant,
)
from app.infrastructure.database import (
    get_db as _db_get_db,
)

# 平台角色 / 项目角色（README §4.1）
PLATFORM_ROLES = ("admin", "user")
PROJECT_ROLES = ("owner", "editor", "viewer")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_db() -> Generator[Session, None, None]:
    """请求级数据库会话（沿用 infrastructure.database.get_db）。"""
    yield from _db_get_db()


def _load_user_or_raise(token: str, db: Session) -> User:
    """JWT 解析 + 用户校验（与 auth.verify_token 同语义，供 SSE 复用）。"""
    from jose import JWTError, jwt

    from app.core.config import settings

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        msg = str(exc) or exc.__class__.__name__
        if "expired" in msg.lower():
            raise TokenExpiredError("登录已过期，请重新登录")
        raise AuthError("无法校验凭证")
    username: str | None = payload.get("sub")
    if not username or payload.get("type") == "refresh":
        # type=refresh 的令牌不得用于 API 访问；旧令牌无 type 视为 access（兼容）
        raise AuthError("无法校验凭证")
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise AuthError("无法校验凭证")
    if not user.is_active:
        raise ForbiddenError("账号已停用", code="PROJECT_FORBIDDEN")
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """解析 JWT → User 实体；无效/过期抛 AuthError/TokenExpiredError。"""
    return _load_user_or_raise(token, db)


def _user_module_context(db: Session, user: User) -> tuple[dict[str, bool], set[str], list[str]]:
    """加载用户的授权行与模块字典。"""
    grants_rows = (
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).all()
    )
    grants = {g.module_code: bool(g.allowed) for g in grants_rows}
    modules = db.query(Module).order_by(Module.sort_order).all()
    all_codes = [m.code for m in modules]
    default_on = {m.code for m in modules if m.is_default_on}
    return grants, default_on, all_codes


def require_role(platform_role: str):
    """平台角色守卫：require_role("admin") 用于 /admin/* 端点。"""
    if platform_role not in PLATFORM_ROLES:
        raise ValueError(f"未知平台角色: {platform_role}")

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != platform_role:
            raise ForbiddenError("需要管理员权限", code="ADMIN_REQUIRED")
        return user

    return _dependency


def require_module(module_code: str):
    """模块码守卫：14 个模块码见 docs/design/README §4.1。

    admin 直通；否则 grants 显式拒绝 > 显式允许 > is_default_on 兜底。
    未授权抛 ForbiddenError(code="MODULE_NOT_GRANTED")。
    """

    def _dependency(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        if user.role == "admin":
            return user
        grants, default_on, all_codes = _user_module_context(db, user)
        allowed = resolve_user_modules("user", grants, default_on, all_codes)
        if module_code not in allowed:
            raise ForbiddenError(f"未开通模块: {module_code}", code="MODULE_NOT_GRANTED")
        return user

    return _dependency


def require_project_role(
    project_id: int, min_role: str = "viewer", user: User = None, db: Session = None
) -> User:
    """项目成员守卫：owner > editor > viewer；非成员/等级不足抛 ProjectForbiddenError。

    判定顺序：admin 直通 → project_members 显式行 → projects.owner_id 隐含 owner
    （02 §3.3：owner 与 members 冗余同步；members 行写入方落地前先查 owner_id 兜底）。
    在端点体内直接调用（project_id 来自路径参数）：
        current_user = require_project_role(project_id, "editor", user, db)
    发布态只读拦截（PublishedReadOnlyError）由 M4 项目状态机接入。
    """
    if min_role not in PROJECT_ROLES:
        raise ValueError(f"未知项目角色: {min_role}")
    if user is not None and getattr(user, "role", None) == "admin":
        return user
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        .first()
    )
    if not has_project_role(member.role if member else None, min_role):
        # members 行缺失时的 owner 兜底（建项目服务层尚未写 members 行，M3-7 收口）
        from app.infrastructure.database import Project

        is_owner = (
            db.query(Project.id)
            .filter(Project.id == project_id, Project.owner_id == user.id)
            .first()
            is not None
        )
        if not is_owner:
            raise ProjectForbiddenError("无该项目访问权限")
    return user


def get_rate_limiter():
    """LLM 类端点限流依赖（Redis 计数）。TODO(M2)。"""
    raise NotImplementedError("M2 实现（docs/design/03 §1）")
