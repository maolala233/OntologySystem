# app/services/trajectory.py - 进化素材轨迹聚合（S21-1，docs/15 S21 / EvoOntology 轨迹形状）
# 事实已在 qa_history / copilot_messages 落库；本模块只做聚合视图，不新建事实表。
# 合同（对齐 EvoOntology trajectory）：只暴露可观察行为（工具输入/结果 + 最终答案），
# 不记思维链；结果字段截断防膨胀；status 归一为 completed|failed|interrupted。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure.database import CopilotMessage, QaHistory

RESULT_MAX_CHARS = 2000

_STATUS_MAP_QA = {"ok": "completed", "repaired": "completed", "failed": "failed"}
_STATUS_MAP_COPILOT = {"ok": "completed", "failed": "failed", "stopped": "interrupted"}


def _trunc(value) -> str | None:
    if value is None:
        return None
    return str(value)[:RESULT_MAX_CHARS]


def list_trajectories(db: Session, project_id: int, limit: int = 50,
                      since_time=None) -> list[dict]:
    """按时间倒序聚合问答 + copilot 执行轨迹（各取 limit 条后按时间归并）。"""
    limit = max(1, min(int(limit), 200))

    q = (db.query(QaHistory).filter(QaHistory.project_id == project_id))
    if since_time is not None:
        q = q.filter(QaHistory.created_at > since_time)
    qas = q.order_by(QaHistory.created_at.desc()).limit(limit).all()

    m = (db.query(CopilotMessage).filter(CopilotMessage.project_id == project_id,
                                         CopilotMessage.role == "assistant"))
    if since_time is not None:
        m = m.filter(CopilotMessage.created_at > since_time)
    msgs = m.order_by(CopilotMessage.created_at.desc()).limit(limit).all()

    # copilot 消息的「提问」= 同会话中该条 assistant 消息之前最近一条 user 消息
    session_ids = {msg.session_id for msg in msgs}
    user_msg_by_session: dict[int, dict] = {}
    if session_ids:
        users = (db.query(CopilotMessage)
                 .filter(CopilotMessage.session_id.in_(session_ids),
                         CopilotMessage.role == "user")
                 .order_by(CopilotMessage.created_at.asc()).all())
        for u in users:
            user_msg_by_session.setdefault(u.session_id,
                                           {"content": u.content, "id": u.id})

    out: list[dict] = []
    for qh in qas:
        out.append({
            "task_id": f"qa-{qh.id}",
            "kind": "qa",
            "question": qh.question,
            "final_answer": _trunc(qh.answer),
            "task_status": _STATUS_MAP_QA.get(qh.status, "completed"),
            "errors": [qh.error] if qh.error else [],
            # 溯源引用数即语义服务的可观察调用结果（不记思维链）
            "source_count": len(qh.sources or []),
            "conversation_id": qh.conversation_id,
            "recorded_at": qh.created_at.isoformat() if qh.created_at else None,
        })
    for msg in msgs:
        ask = user_msg_by_session.get(msg.session_id, {})
        out.append({
            "task_id": f"copilot-{msg.id}",
            "kind": "copilot",
            "question": _trunc(ask.get("content")),
            "final_answer": _trunc(msg.content),
            "task_status": _STATUS_MAP_COPILOT.get(msg.status, "completed"),
            "errors": [msg.status] if msg.status != "ok" else [],
            # 工具调用只保留可观察的 name/输入/结果摘要（截断；绝不持久化思维链）
            "tool_calls": [
                {"tool": (tc or {}).get("tool") or (tc or {}).get("name"),
                 "input_summary": _trunc((tc or {}).get("input") or (tc or {}).get("args")),
                 "result_summary": _trunc((tc or {}).get("result"))}
                for tc in (msg.tool_calls or []) if isinstance(tc, dict)
            ][:20],
            "kind_flag": msg.kind,
            "session_id": msg.session_id,
            "recorded_at": msg.created_at.isoformat() if msg.created_at else None,
        })
    out.sort(key=lambda r: r["recorded_at"] or "", reverse=True)
    return out[:limit]
