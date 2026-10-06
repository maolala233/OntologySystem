# app/services/audit_service.py - 审计日志（docs/design/02 §3.8 / 03 §10，M3-6）
# 治理动作全量留痕：review.decide / publish / unpublish / rollback / version.create。
# API 事务内写入（同事务保证一致），失败不阻断主流程。
from __future__ import annotations

from sqlalchemy.orm import Session


def log_action(db: Session, user_id: int | None, action: str,
               resource_type: str | None = None, resource_id: str | int | None = None,
               detail: dict | None = None, ip: str | None = None) -> None:
    """写 audit_logs（同事务；异常吞掉只记日志——审计失败不阻断业务）。"""
    try:
        from app.infrastructure.database import AuditLog

        db.add(AuditLog(user_id=user_id, action=action,
                        resource_type=resource_type,
                        resource_id=str(resource_id) if resource_id is not None else None,
                        detail=detail, ip=ip))
    except Exception:  # noqa: BLE001
        from app.core.logging import logger

        logger.warning(f"[audit] 写入失败 action={action} resource={resource_type}:{resource_id}")


def put_outbox(db: Session, aggregate_type: str, aggregate_id: int, event_type: str,
               payload: dict, trace_id: str | None = None) -> None:
    """事务性 Outbox 写入（02 §5）：与业务同事务；消费链 M4（worker-graph/rdf）接线。"""
    try:
        from app.infrastructure.database import OutboxEvent

        db.add(OutboxEvent(aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                           event_type=event_type, payload=payload, trace_id=trace_id))
    except Exception:  # noqa: BLE001
        from app.core.logging import logger

        logger.warning(f"[outbox] 写入失败 {event_type} {aggregate_type}:{aggregate_id}")


__all__ = ["log_action", "put_outbox"]
