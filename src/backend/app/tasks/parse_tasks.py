# app/tasks/parse_tasks.py - parse 队列任务（docs/design/03 §7，M3-2）
# 解析/切片全流程在 services/document_pipeline.run_parse；本模块只做 Celery 包装与进度。
# 进度键 progress:{task_id}（tasks/progress.py），SSE 端点 documents.parse_events 订阅转发。

from __future__ import annotations

from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import set_task_progress


@celery_app.task(name="app.tasks.parse_tasks.parse_document",
                 bind=True, max_retries=0, acks_late=True)
def parse_document(self, document_id: int, backend: str = "auto",
                   chunk_params: Optional[dict[str, Any]] = None) -> dict:
    """解析单文档：失败置 failed 并向上抛（解析失败需人工诊断后重解析，不做自动重试）。"""
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "任务已入队", {"document_id": document_id},
                      queue="parse")
    from app.services.document_pipeline import run_parse

    result = run_parse(document_id, backend=backend, chunk_params=chunk_params,
                       task_id=task_id)
    return {"status": "completed", "document_id": document_id, **result}
