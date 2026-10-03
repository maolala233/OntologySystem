# app/tasks/extract_tasks.py - extract 队列任务（docs/design/03 §7/§8，M3-4）
# Schema 抽取：读 document_chunks → extraction.extract_schema（并行+缓存）→
# 产物写 graph_data（nodes/edges 画布兼容 + "schema" 键权威 TBox）→ commit 触发
# M3-3 双写监听器自动同步行表。进度键 progress:{task_id}，取消键 cancel:extract:{task_id}。

from __future__ import annotations

import time
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import finish_task_progress, set_task_progress

_CANCEL_KEY = "cancel:extract:{task_id}"


def request_cancel(task_id: str) -> bool:
    """API 侧取消：写取消标记（worker 在 chunk 间隙检查）+ 软 revoke。"""
    import redis

    from app.core.config import settings

    r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    r.set(_CANCEL_KEY.format(task_id=task_id), "1", ex=24 * 3600)
    return True


def is_cancelled(task_id: str) -> bool:
    import redis

    from app.core.config import settings

    r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    return bool(r.get(_CANCEL_KEY.format(task_id=task_id)))


def _grid_position(i: int) -> dict:
    """确定性网格布局（04 §3.1 画布兼容；6 列）。"""
    return {"x": 80 + (i % 6) * 220, "y": 80 + (i // 6) * 150}


@celery_app.task(name="app.tasks.extract_tasks.run_schema_extraction",
                 bind=True, max_retries=0, acks_late=True)
def run_schema_extraction(self, project_id: int,
                          document_ids: Optional[list[int]] = None,
                          parallelism: int = 4,
                          chunk_limit: int = 200) -> dict:
    """Schema 阶段抽取（TBox）。失败置 failed 并上抛（需人工诊断模型配置后重跑）。"""
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "任务已入队", {"project_id": project_id},
                      queue="extract")

    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import DocumentChunk, Project, SessionLocal, UploadedDocument

    def _cancel_check():
        if is_cancelled(task_id):
            raise _TaskCancelled()

    try:
        db = SessionLocal()
        try:
            # 1) 读切片（项目未删文档；document_ids 可选过滤）
            q = (db.query(DocumentChunk)
                 .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
                 .filter(UploadedDocument.project_id == project_id,
                         UploadedDocument.deleted_at.is_(None))
                 .order_by(UploadedDocument.id, DocumentChunk.chunk_index))
            if document_ids:
                q = q.filter(UploadedDocument.id.in_(document_ids))
            rows = q.limit(chunk_limit).all()
            if not rows:
                finish_task_progress(task_id, "failed", "项目没有可用的已解析切片",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_chunks"}
            chunks = [{"index": i, "text": r.text} for i, r in enumerate(rows)]
            set_task_progress(task_id, "extracting", 5,
                              f"读取到 {len(chunks)} 个切片，开始抽取",
                              {"project_id": project_id, "chunks_total": len(chunks)},
                              queue="extract")

            # 2) LLM 并行抽取（缺陷 #5/#2/#9 在 adapters/extraction 内）
            from app.adapters.extraction import extract_schema

            def _progress(done: int, total: int, cache_hits: int) -> None:
                _cancel_check()
                set_task_progress(
                    task_id, "extracting", 5 + int(85 * done / max(total, 1)),
                    f"骨架抽取中 {done}/{total}",
                    {"project_id": project_id, "chunks_done": done,
                     "chunks_total": total, "cache_hits": cache_hits},
                    queue="extract")

            result = extract_schema(chunks, base_uri=f"urn:onto:{project_id}",
                                    parallelism=parallelism,
                                    progress_cb=_progress, cancel_cb=_cancel_check)

            # 3) 产物写 graph_data（画布兼容节点/边 + "schema" 权威 TBox 键）
            set_task_progress(task_id, "writing", 92, "写入画布与行表",
                              {"project_id": project_id}, queue="extract")
            nodes, id_by_class = _build_nodes(result.classes)
            edges = _build_edges(result.object_properties, id_by_class)
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is None:
                raise RuntimeError(f"项目不存在: {project_id}")
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            gd["nodes"] = nodes
            gd["edges"] = edges
            gd["schema"] = {
                "classes": result.classes,
                "object_properties": result.object_properties,
                "datatype_properties": result.datatype_properties,
                "task_id": task_id,
                "extracted_at": int(time.time()),
            }
            proj.graph_data = gd
            flag_modified(proj, "graph_data")  # 就地改 JSON 列必须显式标记
            db.commit()  # 触发 graph-rows 监听器 → entities/relations 双写
            stats = {"project_id": project_id,
                     "classes": len(result.classes),
                     "object_properties": len(result.object_properties),
                     "datatype_properties": len(result.datatype_properties),
                     "chunks_processed": result.chunks_processed,
                     "cache_hits": result.cache_hits,
                     "warnings": result.warnings[:10]}
            finish_task_progress(task_id, "completed",
                                 f"骨架抽取完成：{len(result.classes)} 类 / "
                                 f"{len(result.object_properties)} 关系", stats=stats)
            return {"status": "completed", **stats}
        finally:
            db.close()
    except _TaskCancelled:
        finish_task_progress(task_id, "cancelled", "用户取消", stats={"project_id": project_id})
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300], stats={"project_id": project_id})
        raise


class _TaskCancelled(Exception):
    """用户取消（cancel:extract 键触发）。"""


def _build_nodes(classes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """类 → 画布节点（data.type='Class'，与 graph_rows.CLASS_TYPES 判据对齐）。"""
    nodes = []
    id_by_class: dict[str, str] = {}
    for i, c in enumerate(classes):
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        node_id = f"cls_{i}_{abs(hash(label)) % 100000}"
        id_by_class[label] = node_id
        props = {p.get("name"): p.get("data_type") for p in (c.get("properties") or [])
                 if p.get("name")}
        data: dict[str, Any] = {"label": label, "type": "Class", "class_label": label,
                                "properties": props, "aliases": c.get("aliases") or []}
        if c.get("definition"):
            data["definition"] = c["definition"]
        if c.get("pending"):
            data["pending"] = True
        nodes.append({"id": node_id, "type": "default",
                      "position": _grid_position(i), "data": data,
                      "style": {"background": "#fff", "border": "2px solid #3b82f6",
                                "borderRadius": "8px", "padding": "10px"}})
    return nodes, id_by_class


def _build_edges(object_properties: list[dict], id_by_class: dict[str, str]) -> list[dict]:
    """对象属性 → 画布边（data.relation 供 graph_rows._edge_relation 读取）。"""
    edges = []
    for i, op in enumerate(object_properties or []):
        s = id_by_class.get(str(op.get("domain") or ""))
        t = id_by_class.get(str(op.get("range") or ""))
        if not s or not t:
            continue  # domain/range 类抽取失败时跳过（schema 键仍保留原文）
        edges.append({"id": f"edge_{i}", "source": s, "target": t,
                      "type": "smoothstep", "label": op.get("label"),
                      "data": {"relation": op.get("label"), "type": "ObjectProperty",
                               "domain": op.get("domain"), "range": op.get("range")}})
    return edges
