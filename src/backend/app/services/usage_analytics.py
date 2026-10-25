# app/services/usage_analytics.py - 用量分析聚合（S25-1，docs/15 Learn 闭环）
# 数据源 = qa_history（coverage=no 的诚实拒答记 failed，天然成为挖掘输入）。
# 挖掘落库复用 B16 analyze_qa_failures（失败聚类→审核项），本模块只做只读聚合。
from __future__ import annotations

import datetime
from collections import Counter

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.infrastructure.database import (
    EvolutionReport,
    QaHistory,
    ReviewItem,
)


def _db_now(db: Session) -> datetime.datetime:
    """服务器时钟（与 created_at 的 CURRENT_TIMESTAMP 同源；时钟混用坑见 S21）。"""
    return db.execute(text("SELECT NOW()")).scalar()


def usage_summary(db: Session, project_id: int, days: int = 7) -> dict:
    days = max(1, min(int(days), 90))
    now = _db_now(db)
    since = now - datetime.timedelta(days=days)

    rows = (db.query(QaHistory)
            .filter(QaHistory.project_id == project_id,
                    QaHistory.created_at >= since)
            .order_by(QaHistory.created_at.asc()).all())

    total = len(rows)
    ok = sum(1 for r in rows if r.status == "ok")
    repaired = sum(1 for r in rows if r.status == "repaired")
    failed = sum(1 for r in rows if r.status == "failed")

    # 日趋势（按服务器本地日）
    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        key = r.created_at.strftime("%Y-%m-%d") if r.created_at else "未知"
        d = by_day.setdefault(key, {"total": 0, "failed": 0})
        d["total"] += 1
        if r.status == "failed":
            d["failed"] += 1
    trend = []
    for i in range(days - 1, -1, -1):
        day = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        d = by_day.get(day, {"total": 0, "failed": 0})
        trend.append({"day": day, **d})

    # 失败问题 TOP（原文去重计数——进化挖掘的输入面）
    failed_qs = [r.question.strip() for r in rows if r.status == "failed" and r.question]
    top_failed = [{"question": q, "count": c}
                  for q, c in Counter(failed_qs).most_common(5)]

    # 挖掘/建议现状
    pending_suggestions = (db.query(func.count(ReviewItem.id))
                           .filter(ReviewItem.project_id == project_id,
                                   ReviewItem.item_type.in_(
                                       ("cq_suggestion", "new_class", "new_predicate")),
                                   ReviewItem.status.in_(("pending", "claimed")))
                           .scalar() or 0)
    last_report = (db.query(EvolutionReport)
                   .filter(EvolutionReport.project_id == project_id)
                   .order_by(EvolutionReport.id.desc()).first())

    return {
        "days": days,
        "total": total,
        "ok": ok,
        "repaired": repaired,
        "failed": failed,
        "answer_rate": round((ok + repaired) / total, 3) if total else None,
        "trend": trend,
        "top_failed": top_failed,
        "pending_suggestions": int(pending_suggestions),
        "last_analysis_at": (last_report.created_at.isoformat()
                             if last_report and last_report.created_at else None),
    }
