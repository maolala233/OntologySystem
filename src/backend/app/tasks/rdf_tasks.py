# app/tasks/rdf_tasks.py - rdf 队列任务（docs/design/02 §5，M4）：worker-rdf 消费 outbox
# → Oxigraph 四命名图整体替换（concurrency=1，嵌入式 pyoxigraph RocksDB 独占锁）。
# 幂等：命名图重建而非增量三元组追加；失败置 failed+last_error 可重放。

from __future__ import annotations

import datetime

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.rdf_tasks.drain_rdf_outbox",
                 bind=True, max_retries=0, acks_late=True)
def drain_rdf_outbox(self, limit: int = 20) -> dict:
    """消费 publication.created / ttl.rebuild → Oxigraph 命名图（02 §5 worker-rdf）。"""
    from app.infrastructure.database import OutboxEvent, Project, SessionLocal
    from app.services.oxigraph_sink import delete_project_graphs, write_project_graphs

    db = SessionLocal()
    processed = failed = 0
    try:
        events = (db.query(OutboxEvent)
                  .filter(OutboxEvent.status == "pending",
                          OutboxEvent.aggregate_type.in_(("publication", "project")))
                  .order_by(OutboxEvent.id.asc()).limit(limit).all())
        for ev in events:
            try:
                if ev.event_type == "project.deleted":
                    delete_project_graphs(ev.aggregate_id)
                elif ev.event_type in ("publication.created", "ttl.rebuild",
                                       "project.graph_rebuilt"):
                    proj = db.query(Project).filter(Project.id == ev.aggregate_id).first()
                    if proj is None:
                        pass  # 项目已删除：跳过
                    else:
                        res = write_project_graphs(db, ev.aggregate_id)
                        if not res.get("ok"):
                            raise RuntimeError(res.get("error", "oxigraph write failed"))
                ev.status = "processed"
                ev.processed_at = datetime.datetime.utcnow()
                db.commit()
                processed += 1
            except Exception as e:  # noqa: BLE001 —— 单事件失败不阻断批
                db.rollback()
                ev2 = db.query(OutboxEvent).filter(OutboxEvent.id == ev.id).first()
                if ev2 is not None:
                    ev2.status = "failed"
                    ev2.attempts = (ev2.attempts or 0) + 1
                    ev2.last_error = str(e)[:500]
                db.commit()
                failed += 1
        return {"status": "completed", "processed": processed, "failed": failed}
    finally:
        db.close()
