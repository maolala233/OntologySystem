# app/tasks/parse_tasks.py - parse 队列任务（docs/design/03 §7，M3-2）
# 解析/切片全流程在 services/document_pipeline.run_parse；本模块只做 Celery 包装与进度。
# 进度键 progress:{task_id}（tasks/progress.py），SSE 端点 documents.parse_events 订阅转发。
#
# dispatch_parse：派发入口（API 侧上传/手动重解析共用）。优先投递 Celery parse 队列；
# 探测不到消费 parse 队列的 worker（或 broker 不可达）时，降级为当前进程内后台线程直接
# 执行 run_parse——保证 uvicorn 单进程部署（不另起 worker）上传文档后解析也能执行，
# 避免任务投进队列后无人消费、文档永远停在"待解析"。

from __future__ import annotations

import threading
import uuid
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


def _parse_worker_alive(timeout: float = 2.0) -> bool:
    """探测是否有 Celery worker 在线且消费 parse 队列。

    返回 False 表示：无任何 worker / broker 不可达 / 探测异常——
    调用方应降级为进程内执行，避免任务投进队列后无人消费。
    """
    from app.core.logging import logger
    try:
        pong = celery_app.control.ping(timeout=timeout)  # {worker: {'ok': 'pong'}}
        if not pong:
            return False
    except Exception as e:  # noqa: BLE001 —— broker 不可达
        logger.warning(f"[dispatch_parse] worker 探测失败（broker 不可达？）: {e}")
        return False
    try:
        queues = celery_app.control.inspect().active_queues() or {}
    except Exception:  # noqa: BLE001 —— 探测异常按无 worker 处理
        queues = {}
    for worker_queues in queues.values():
        if any(str(q.get("name")) == "parse" for q in worker_queues):
            return True
    return False


def _run_inline(document_id: int, backend: str = "auto",
                chunk_params: Optional[dict[str, Any]] = None) -> dict:
    """进程内后台线程直接执行 run_parse（自带 Session，线程内可调），进度照常写 Redis。"""
    from app.core.logging import logger
    from app.services.document_pipeline import run_parse

    task_id = f"inline-{uuid.uuid4().hex[:12]}"

    def _worker() -> None:
        try:
            run_parse(document_id, backend=backend, chunk_params=chunk_params,
                      task_id=task_id)
        except Exception as e:  # noqa: BLE001 —— run_parse 已置 failed，兜底记录
            logger.error(f"[dispatch_parse] 进程内解析文档 {document_id} 失败: {e}")

    threading.Thread(target=_worker, name=f"parse-inline-{document_id}",
                     daemon=True).start()
    return {"mode": "inline", "task_id": task_id}


def dispatch_parse(document_id: int, backend: str = "auto",
                   chunk_params: Optional[dict[str, Any]] = None,
                   force_inline: bool = False) -> dict:
    """派发解析任务（M3-2 派发入口，上传/手动重解析共用）。

    优先投递 Celery parse 队列；探测不到消费该队列的 worker（或 broker 不可达）
    时，降级为当前进程内后台线程执行。
    返回 {"mode": "celery"|"inline", "task_id": str}。
    """
    from app.core.logging import logger

    if not force_inline and _parse_worker_alive():
        try:
            task = parse_document.delay(document_id, backend=backend,
                                        chunk_params=chunk_params)
            return {"mode": "celery", "task_id": task.id}
        except Exception as e:  # noqa: BLE001 —— 派发失败降级进程内
            logger.warning(f"[dispatch_parse] Celery 派发失败，降级进程内执行: {e}")
    logger.info(f"[dispatch_parse] 未检测到 parse worker，文档 {document_id} 进程内解析")
    return _run_inline(document_id, backend=backend, chunk_params=chunk_params)
