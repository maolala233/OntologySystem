# app/services/data_auth.py - 数据级授权（S23，docs/15 / COA 数据级授权语义）
# 合同：无 grant 行 = 不受限（grandfather，管理员/老用户不受影响）；
# 有 grant 行 = 受限用户：知识域白名单（QA/MCP 域门控，fail-closed）+
# 类黑名单（检索/解析裁剪）+ 属性黑名单（详情脱敏）。裁剪处留审计。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import APIError, ForbiddenError
from app.core.logging import logger
from app.infrastructure.database import DataGrant, Project, User


def get_grant(db: Session, user_id: int) -> DataGrant | None:
    return db.query(DataGrant).filter(DataGrant.user_id == user_id).first()


def _project_domains(db: Session, proj: Project) -> set[str]:
    """项目知识域集合：domain 关系名 + domains 冗余串（逗号/中文顿号分隔）。"""
    names: set[str] = set()
    if proj.domain_id is not None:
        from app.infrastructure.database import KnowledgeDomain

        d = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == proj.domain_id).first()
        if d:
            names.add(d.name.strip())
    raw = (proj.domains or "")
    for sep in (",", "，", "、", ";"):
        raw = raw.replace(sep, "\n")
    names.update(x.strip() for x in raw.splitlines() if x.strip())
    return names


def ensure_qa_domain_access(db: Session, proj: Project, user: User) -> None:
    """QA/公共区数据门控（S23 执行点 1）：受限用户的知识域白名单。

    fail-closed：受限用户且项目域不在白名单 → 403（明确"无权限"，不是编造不是空结果）。
    超管/项目 owner 不受限（治理语义，非数据语义）。
    """
    from app.core.deps import is_super_admin

    if is_super_admin(user) or proj.owner_id == user.id:
        return
    grant = get_grant(db, user.id)
    if grant is None:
        return  # 未配置 = 不受限（grandfather）
    allow: list[str] = [str(x).strip() for x in (grant.domain_allowlist or []) if str(x).strip()]
    domains = _project_domains(db, proj)
    if domains and allow and not (domains & set(allow)):
        logger.warning(f"[data-auth] 用户 {user.id} 被拒访问知识域 {domains}"
                       f"（白名单 {allow}）——fail closed")
        raise ForbiddenError(
            f"您没有「{'、'.join(sorted(domains))}」知识域的访问权限；如需访问请联系管理员开通。",
            code="QA_DOMAIN_FORBIDDEN")


class DataScope:
    """类/属性级裁剪上下文（S23 执行点 2）：受限用户的检索与详情视图统一过滤。"""

    def __init__(self, grant: DataGrant | None):
        self.active = grant is not None
        self.class_denylist = {str(x).strip() for x in ((grant.class_denylist or []) if grant else []) if str(x).strip()}
        self.property_denylist = {str(x).strip() for x in ((grant.property_denylist or []) if grant else []) if str(x).strip()}

    def class_allowed(self, class_label) -> bool:
        return (not self.active) or (str(class_label or "").strip() not in self.class_denylist)

    def filter_props(self, props: dict | None) -> dict:
        if not self.active or not props:
            return props or {}
        return {k: v for k, v in props.items() if str(k).strip() not in self.property_denylist}


def scope_for(db: Session, user: User) -> DataScope:
    """取调用者的数据视图（MCP/详情统一入口）。"""
    from app.core.deps import is_super_admin

    if is_super_admin(user):
        return DataScope(None)
    return DataScope(get_grant(db, user.id))


def audit_trim(db: Session, user_id: int, where: str, detail: dict) -> None:
    """裁剪留痕（不入业务事务也可见：独立短会话由调用方保证）。"""
    from app.services.audit_service import log_action

    try:
        log_action(db, user_id, "data_auth.trim", resource_type=where,
                   resource_id=None, detail=detail)
    except Exception as e:  # noqa: BLE001 —— 审计失败不阻断主流程
        logger.warning(f"[data-auth] 裁剪审计失败：{e}")
