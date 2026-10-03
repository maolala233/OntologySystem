# app/tasks/graph_tasks.py - graph 队列任务（docs/design/02 §4 / 01 §4.4，M3-3）
# S1 存量回填：把全部项目 graph_data 拆写为行表并对账（一次性任务，可重跑——幂等全量重建）。

from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.graph_tasks.backfill_graph_rows",
                 bind=True, max_retries=0)
def backfill_graph_rows(self) -> dict:
    """存量项目行表回填 + 逐项目对账（02 §4 S1：blob 为准，行表对账）。"""
    from app.infrastructure.database import SessionLocal
    from app.services.graph_rows import backfill_all_projects

    db = SessionLocal()
    try:
        results = backfill_all_projects(db)
        ok = sum(1 for r in results if r.get("reconcile_ok") and "error" not in r)
        return {"status": "completed", "projects": len(results),
                "reconciled": ok, "details": results}
    finally:
        db.close()
