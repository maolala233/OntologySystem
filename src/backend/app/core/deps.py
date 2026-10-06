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
    """模块码守卫：模块码见 docs/design/README §4.1（含 R11 新增 report/ppt）。

    admin 直通；否则 grants 显式拒绝 > 显式允许 > is_default_on 兜底。
    未授权抛 ForbiddenError(code="MODULE_NOT_GRANTED")。
    """

    def _dependency(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        ensure_module(user, db, module_code)
        return user

    return _dependency


def require_any_module(*module_codes: str):
    """任一模块开通即通过（admin 直通）——工具类共用端点（报告/PPT）按 kind 前置放行用。"""

    def _dependency(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        if user.role == "admin":
            return user
        grants, default_on, all_codes = _user_module_context(db, user)
        allowed = resolve_user_modules("user", grants, default_on, all_codes)
        if not any(c in allowed for c in module_codes):
            raise ForbiddenError(f"未开通模块: {'/'.join(module_codes)}", code="MODULE_NOT_GRANTED")
        return user

    return _dependency


def ensure_module(user: User, db: Session, module_code: str) -> None:
    """命令式模块码检查（端点体内按运行时参数判定时用，如 report/ppt 按 kind 分流）。"""
    if user.role == "admin":
        return
    grants, default_on, all_codes = _user_module_context(db, user)
    allowed = resolve_user_modules("user", grants, default_on, all_codes)
    if module_code not in allowed:
        raise ForbiddenError(f"未开通模块: {module_code}", code="MODULE_NOT_GRANTED")


def is_super_admin(user: User) -> bool:
    """超级管理员（平台主账号 username='admin'）：可跨租户查看/编辑所有用户的项目；
    其余 role=admin 为普通管理员，项目访问与普通用户一致（仅 owner/成员），但模块与管理后台直通。"""
    return user.role == "admin" and user.username == "admin"


def require_project_role(
    project_id: int, min_role: str = "viewer", user: User = None, db: Session = None,
    allow_published: bool = False,
) -> User:
    """项目成员守卫：owner > editor > viewer；非成员/等级不足抛 ProjectForbiddenError。

    判定顺序：超级管理员直通 → project_members 显式行 → projects.owner_id 隐含 owner
    （R11：普通管理员不再直通项目，仅超管 username='admin' 跨租户全量可访问）。
    在端点体内直接调用（project_id 来自路径参数）：
        current_user = require_project_role(project_id, "editor", user, db)
    发布态只读拦截（03 §12）：project.status=='published' 且请求的是写角色
    （editor/owner）→ 403 PUBLISHED_READONLY；publish/unpublish 等治理端点传
    allow_published=True 豁免。viewer 只读不受影响。
    """
    if min_role not in PROJECT_ROLES:
        raise ValueError(f"未知项目角色: {min_role}")
    if user is not None and is_super_admin(user):
        return user
    # 已发布项目对所有人开放只读（03 §12 公共区语义；写角色仍被下方 PUBLISHED_READONLY 拦截）
    if min_role == "viewer":
        from app.infrastructure.database import Project

        row = (db.query(Project.is_published, Project.status)
               .filter(Project.id == project_id).first())
        if row and (row[0] or row[1] == "published"):
            return user
    if min_role in ("editor", "owner") and not allow_published:
        from app.infrastructure.database import Project

        proj_status = (db.query(Project.status)
                       .filter(Project.id == project_id).first())
        if proj_status is not None and proj_status[0] == "published":
            from app.core.exceptions import APIError

            raise APIError("项目已发布，处于只读状态；请先取消发布或另起新版本",
                           code="PUBLISHED_READONLY", http_status=403)
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
