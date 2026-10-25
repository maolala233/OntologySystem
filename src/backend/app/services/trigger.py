# app/services/trigger.py - 进化触发器（S21-2，docs/15 / EvoOntology trigger 语义）
# 只决定「是否该提醒进化」，绝不自动启动进化；checkpoint 只在发布成功后推进
# （publish() → advance_checkpoint），被拒绝的进化轮不推进——同一批轨迹可重试。
# 窗口游标 = 轨迹自增 ID（checkpoint_qa_id / checkpoint_copilot_id）：DATETIME 秒级
# 精度下"同秒创建"会漏计（实测踩坑），ID 游标天然单调；checkpoint_time 仅用于天数阈值。
# 时钟同源：checkpoint_time 与天数判断一律用数据库服务器时间（NOW()）——代码库
# ORM utcnow（UTC）与 server_default CURRENT_TIMESTAMP（服务器本地 UTC+8）混用，
# 用 Python 时钟会让 checkpoint 比数据"晚 8 小时"，阈值全错（实测踩坑）。
from __future__ import annotations

import datetime

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.infrastructure.database import (
    CopilotMessage, EvolutionTriggerState, QaHistory,
)

DEFAULT_MIN_TRAJECTORIES = 30
DEFAULT_MIN_DAYS = 7


def _db_now(db: Session) -> datetime.datetime:
    return db.execute(text("SELECT NOW()")).scalar()


def _max_ids(db: Session, project_id: int) -> tuple[int, int]:
    qa_max = (db.query(func.max(QaHistory.id))
              .filter(QaHistory.project_id == project_id).scalar()) or 0
    cp_max = (db.query(func.max(CopilotMessage.id))
              .filter(CopilotMessage.project_id == project_id,
                      CopilotMessage.role == "assistant").scalar()) or 0
    return int(qa_max), int(cp_max)


def initialize(db: Session, project_id: int) -> EvolutionTriggerState:
    """幂等初始化：无状态行则建（checkpoint=当前游标+服务器时间）；已有行原样返回。"""
    row = db.query(EvolutionTriggerState).filter(
        EvolutionTriggerState.project_id == project_id).first()
    if row is None:
        qa_id, cp_id = _max_ids(db, project_id)
        row = EvolutionTriggerState(project_id=project_id,
                                    checkpoint_time=_db_now(db),
                                    checkpoint_qa_id=qa_id,
                                    checkpoint_copilot_id=cp_id)
        db.add(row)
        db.flush()
    return row


def check(db: Session, project_id: int, now: datetime.datetime | None = None) -> dict:
    """触发器检查：checkpoint 后新轨迹数 / 距上次进化天数 任一超阈值 → due。

    返回 {evolution_due, new_trajectories, days_since_checkpoint, reason, thresholds}；
    due 翻转持久化（幂等：重复检查结果一致）。now 参数仅供测试注入（服务器时钟）。
    """
    row = initialize(db, project_id)
    now = now or _db_now(db)
    n_qa = (db.query(QaHistory)
            .filter(QaHistory.project_id == project_id,
                    QaHistory.id > row.checkpoint_qa_id).count())
    n_cp = (db.query(CopilotMessage)
            .filter(CopilotMessage.project_id == project_id,
                    CopilotMessage.role == "assistant",
                    CopilotMessage.id > row.checkpoint_copilot_id).count())
    new_count = n_qa + n_cp
    days = (now - row.checkpoint_time).total_seconds() / 86400.0

    due_by_count = new_count >= row.min_new_trajectories
    due_by_days = days >= row.min_days
    if due_by_count:
        reason = f"checkpoint 后新增 {new_count} 条轨迹（阈值 {row.min_new_trajectories}）"
    elif due_by_days:
        reason = f"距上次进化 {days:.1f} 天（阈值 {row.min_days} 天）"
    else:
        reason = None
    due = bool(due_by_count or due_by_days)
    if row.evolution_due != due:
        row.evolution_due = due
        db.flush()
    return {"evolution_due": due, "new_trajectories": new_count,
            "days_since_checkpoint": round(days, 2), "reason": reason,
            "thresholds": {"min_new_trajectories": row.min_new_trajectories,
                           "min_days": row.min_days},
            "checkpoint_time": row.checkpoint_time.isoformat()}


def advance_checkpoint(db: Session, project_id: int,
                       now: datetime.datetime | None = None) -> dict:
    """推进 checkpoint（发布成功后由 publish() 调用）：游标推到最新、due 复位、窗口重开。"""
    row = initialize(db, project_id)
    qa_id, cp_id = _max_ids(db, project_id)
    row.checkpoint_qa_id = qa_id
    row.checkpoint_copilot_id = cp_id
    row.checkpoint_time = now or _db_now(db)
    row.evolution_due = False
    db.flush()
    return {"checkpoint_time": row.checkpoint_time.isoformat(),
            "checkpoint_qa_id": qa_id, "checkpoint_copilot_id": cp_id,
            "evolution_due": False}
