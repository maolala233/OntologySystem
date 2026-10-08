# app/api/mcp_tokens.py - MCP 令牌管理（M5，docs/design/03 §16 / 07 §3.2）
# admin 端：GET/POST/DELETE /api/admin/mcp-tokens（任意用户均可为他人签发）；
# 自助端：GET/POST/DELETE /api/mcp-tokens（登录用户管理自己的令牌，签发前校验项目成员身份）；
# 明文 sk-mcp-* 只在签发响应出现一次，库内仅存 SHA-256；持有人须持有 [M:mcp] 模块码（07 §3.4）。
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_role
from app.core.exceptions import APIError
from app.infrastructure.database import McpToken, Module, Project, ProjectMember, User, UserModuleGrant
from app.services.audit_service import log_action

from app.api.mcp_gateway import _hash_token

router = APIRouter(prefix="/api/admin/mcp-tokens", tags=["admin-mcp-tokens"])
router_self = APIRouter(prefix="/api/mcp-tokens", tags=["mcp-tokens"])

TOKEN_PREFIX = "sk-mcp-"
TOKEN_BODY_BYTES = 20  # 40 hex 字符（07 §3.2）


class CreateTokenRequest(BaseModel):
    user_id: int
    project_id: int
    name: str = Field(min_length=1, max_length=64)
    can_write: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=365)


class CreateSelfTokenRequest(BaseModel):
    project_id: int
    name: str = Field(min_length=1, max_length=64)
    can_write: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=365)


def _require_mcp_module(db: Session, user_id: int) -> None:
    """持有人须开通 mcp 模块（admin 直通）。"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise APIError("目标用户不存在", code="USER_NOT_FOUND", http_status=404)
    if user.role == "admin":
        return
    module = db.query(Module).filter(Module.code == "mcp").first()
    grant = None
    if module is not None:
        grant = (
            db.query(UserModuleGrant)
            .filter(UserModuleGrant.user_id == user_id, UserModuleGrant.module_code == "mcp")
            .first()
        )
    allowed = (module.is_default_on if module and grant is None else
               grant.allowed if grant is not None else False)
    if not allowed:
        raise APIError("目标用户未开通 MCP 模块，无法持有令牌", code="MODULE_NOT_GRANTED", http_status=400)


@router.get("")
def list_tokens(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    rows = db.query(McpToken).order_by(McpToken.id.desc()).limit(200).all()
    return {"items": [_token_out(r) for r in rows]}


def _token_out(r: McpToken) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "name": r.name,
        "token_hint": f"{TOKEN_PREFIX}***{r.token_hash[-4:]}",
        "project_id": r.project_id,
        "can_write": bool(r.can_write),
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _issue_token(db: Session, operator: User, user_id: int, body: CreateTokenRequest) -> tuple[McpToken, str]:
    """共用签发逻辑：模块校验 + 项目校验 + 落库 + 审计。明文由调用方返回。"""
    _require_mcp_module(db, user_id)
    project = db.query(Project).filter(Project.id == body.project_id).first()
    if project is None:
        raise APIError("绑定项目不存在", code="PROJECT_NOT_FOUND", http_status=404)

    plaintext = f"{TOKEN_PREFIX}{secrets.token_hex(TOKEN_BODY_BYTES)}"
    expires_at = None
    if body.expires_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=body.expires_days)
    tok = McpToken(
        user_id=user_id,
        name=body.name.strip(),
        token_hash=_hash_token(plaintext),
        project_id=body.project_id,
        can_write=body.can_write,
        expires_at=expires_at,
    )
    db.add(tok)
    db.commit()
    db.refresh(tok)
    log_action(db, operator.id, "mcp_token.create", resource_type="mcp_token",
               resource_id=str(tok.id),
               detail={"target_user": user_id, "project": body.project_id,
                       "can_write": body.can_write})
    db.commit()  # 审计同事务落盘（log_action 只 add，会话 close 不 commit）
    return tok, plaintext


@router.post("", status_code=201)
def create_token(
    body: CreateTokenRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    """签发令牌（07 §3.2）：明文只回一次。"""
    tok, plaintext = _issue_token(db, admin, body.user_id, body)
    return {"token": plaintext, **_token_out(tok)}  # ★ 明文仅此一次


@router.delete("/{token_id}")
def revoke_token(
    token_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    """撤销令牌（07 §6：泄露可撤销）——软撤销保留审计线索。"""
    tok = db.query(McpToken).filter(McpToken.id == token_id).first()
    if tok is None:
        raise APIError("令牌不存在", code="MCP_TOKEN_NOT_FOUND", http_status=404)
    if tok.revoked_at is None:
        tok.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        log_action(db, admin.id, "mcp_token.revoke", resource_type="mcp_token",
                   resource_id=str(tok.id), detail={"name": tok.name})
        db.commit()  # 审计同事务落盘
    return {"revoked": True, "id": tok.id}


# ───────────────────────── 自助端（登录用户管理自己的令牌；/api/mcp-tokens）

@router_self.get("")
def list_my_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """仅返回当前登录用户自己的令牌（admin 亦走此口径，管理他人用 /api/admin/mcp-tokens）。"""
    rows = (db.query(McpToken)
            .filter(McpToken.user_id == user.id)
            .order_by(McpToken.id.desc()).limit(200).all())
    return {"items": [_token_out(r) for r in rows]}


def _require_project_member(db: Session, user: User, project_id: int) -> None:
    """自助签发必须持有目标项目成员身份（owner/成员），防止给自己签任意项目令牌绕过隔离。"""
    is_owner = db.query(Project.id).filter(
        Project.id == project_id, Project.owner_id == user.id).first() is not None
    if is_owner:
        return
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == user.id).first()
    if member is None:
        raise APIError("仅项目成员可为该项目签发令牌", code="PROJECT_NOT_MEMBER", http_status=403)


@router_self.post("", status_code=201)
def create_my_token(
    body: CreateSelfTokenRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """自助签发：持有人固定为当前用户，且须为绑定项目成员。"""
    _require_project_member(db, user, body.project_id)
    full = CreateTokenRequest(user_id=user.id, **body.model_dump())
    tok, plaintext = _issue_token(db, user, user.id, full)
    return {"token": plaintext, **_token_out(tok)}  # ★ 明文仅此一次


@router_self.delete("/{token_id}")
def revoke_my_token(
    token_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销自己的令牌（软撤销保留审计线索）；他人令牌按不存在处理不泄露存在性。"""
    tok = db.query(McpToken).filter(McpToken.id == token_id, McpToken.user_id == user.id).first()
    if tok is None:
        raise APIError("令牌不存在", code="MCP_TOKEN_NOT_FOUND", http_status=404)
    if tok.revoked_at is None:
        tok.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        log_action(db, user.id, "mcp_token.revoke", resource_type="mcp_token",
                   resource_id=str(tok.id), detail={"name": tok.name, "self": True})
        db.commit()  # 审计同事务落盘
    return {"revoked": True, "id": tok.id}
