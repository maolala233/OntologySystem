# app/tasks/graph_tasks.py - graph 队列任务（docs/design/02 §4/§5，M3-3 / M4）
# S1 存量回填 + M4 outbox 消费：worker-graph 消费 outbox_events → Neo4j 投影（02 §5）。
# 幂等：Neo4j sync_graph 自带 DETACH+MERGE 全量重建；失败置 failed+last_error 可重放。

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


def _handle_graph_event(db, ev) -> str:
    """单条 outbox 事件 → Neo4j 投影（幂等）。返回处理说明。"""
    from app.infrastructure.database import Project
    from app.infrastructure.neo4j_client import neo4j_client

    et = ev.event_type
    if et == "project.deleted":
        ok = neo4j_client.delete_project_data(ev.aggregate_id)
        if not ok:
            raise RuntimeError("Neo4j delete_project_data 返回失败")
        return "neo4j deleted"
    if et in ("project.graph_rebuilt", "publication.created",
              "entity.upsert", "relation.upsert"):
        # 幂等全量重建（MERGE 语义）；entity/relation 级事件收敛为项目级重建
        proj = db.query(Project).filter(Project.id == ev.aggregate_id).first()
        if proj is None:
            return "skipped: project gone"
        if not proj.graph_data:
            return "skipped: empty graph"
        ok = neo4j_client.sync_graph(ev.aggregate_id, proj.graph_data)
        if not ok:
            raise RuntimeError("Neo4j sync_graph 返回失败")
        return "neo4j synced"
    if et == "chunk.vectorize":
        # Milvus 向量化（chunks 嵌入）归 M5 问答链路接入时发事件；此处占位
        return "skipped: vectorize deferred"
    return f"skipped: unknown {et}"


@celery_app.task(name="app.tasks.graph_tasks.drain_outbox",
                 bind=True, max_retries=0, acks_late=True)
def drain_outbox(self, limit: int = 50) -> dict:
    """消费 outbox_events（02 §5）：worker-graph → Neo4j。beat 每 30s + 事件后即时派发。"""
    from app.infrastructure.database import OutboxEvent, SessionLocal

    db = SessionLocal()
    processed = failed = 0
    try:
        events = (db.query(OutboxEvent)
                  .filter(OutboxEvent.status == "pending",
                          OutboxEvent.aggregate_type.in_(("project", "publication", "entity", "relation")))
                  .order_by(OutboxEvent.id.asc()).limit(limit).all())
        # 注：outbox status ENUM 无 processing 态；单消费者（worker-graph concurrency=1）
        # 顺序消费 + Neo4j MERGE 幂等，失败置 failed 留 last_error 可重放
        for ev in events:
            try:
                _handle_graph_event(db, ev)
                ev.status = "processed"
                ev.processed_at = __import__("datetime").datetime.utcnow()
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


@celery_app.task(name="app.tasks.graph_tasks.run_graph_layout",
                 bind=True, max_retries=0, acks_late=True)
def run_graph_layout(self, project_id: int) -> dict:
    """服务端预布局（06 §10 >25k）：networkx spring → graph_layouts + MinIO。"""
    from app.api.graph_view import compute_and_store_layout
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        res = compute_and_store_layout(db, project_id)
        return {"status": "completed", **res}
    finally:
        db.close()
