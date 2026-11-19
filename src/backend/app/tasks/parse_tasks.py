# app/tasks/parse_tasks.py - parse 队列任务（docs/design/03 §7，M3-2）
# 解析/切片全流程在 services/document_pipeline.run_parse；本模块只做 Celery 包装与进度。
# 进度键 progress:{task_id}（tasks/progress.py），SSE 端点 documents.parse_events 订阅转发。
#
# dispatch_parse：派发入口（API 侧上传/手动重解析共用）。优先投递 Celery parse 队列；
# 探测不到消费 parse 队列的 worker（或 broker 不可达）时，降级为当前进程内后台线程直接
# 执行 run_parse——保证 uvicorn 单进程部署（不另起 worker）上传文档后解析也能执行，
# 避免任务投进队列后无人消费、文档永远停在"待解析"。

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import set_task_progress

# worker 存活探测缓存（2026-11-19）：与 extract 队列同口径，避免批量上传时
# 每个文档都同步做一次 broker 往返。
_alive_cache: tuple[float, bool] | None = None
# 无 worker 时的进程内解析并发上限：docling/OCR 是 CPU/内存重活，不能一个文档
# 一个线程无限并发（旧实现批量上传会同时起 N 个解析线程）。
_PARSE_INLINE_CONCURRENCY = max(1, int(os.environ.get("PARSE_INLINE_CONCURRENCY", "2")))
_inline_sem = threading.BoundedSemaphore(_PARSE_INLINE_CONCURRENCY)


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


def _parse_worker_alive(timeout: float = 1.0) -> bool:
    """探测是否有 Celery worker 在线且消费 parse 队列（带短缓存）。

    旧实现每次 ping(timeout=2)+active_queues 且无缓存：上传 N 个文档就同步等
    N×(2s+active_queues) 才返回。这里只查 active_queues 并缓存，与
    extract_tasks._extract_worker_alive 同口径。
    """
    global _alive_cache
    now = time.monotonic()
    if _alive_cache is not None:
        cached_at, cached_ok = _alive_cache
        ttl = 15.0 if cached_ok else 2.0
        if now - cached_at < ttl:
            return cached_ok
    from app.core.logging import logger
    try:
        queues = celery_app.control.inspect(timeout=timeout).active_queues() or {}
        alive = any(
            any(str(q.get("name")) == "parse" for q in worker_queues)
            for worker_queues in queues.values()
        )
    except Exception as e:  # noqa: BLE001 —— broker 不可达
        logger.warning(f"[dispatch_parse] worker 探测失败（broker 不可达？）: {e}")
        alive = False
    _alive_cache = (now, alive)
    return alive


def _run_inline(document_id: int, backend: str = "auto",
                chunk_params: Optional[dict[str, Any]] = None) -> dict:
    """进程内后台线程直接执行 run_parse（自带 Session，线程内可调），进度照常写 Redis。"""
    from app.core.logging import logger
    from app.services.document_pipeline import run_parse

    task_id = f"inline-{uuid.uuid4().hex[:12]}"

    def _worker() -> None:
        with _inline_sem:
            try:
                run_parse(document_id, backend=backend, chunk_params=chunk_params,
                          task_id=task_id)
            except Exception as e:  # noqa: BLE001 —— run_parse 已置 failed，兜底记录
                logger.error(f"[dispatch_parse] 进程内解析文档 {document_id} 失败: {e}")

    threading.Thread(target=_worker, name=f"parse-inline-{document_id}",
                     daemon=True).start()
    return {"mode": "inline", "task_id": task_id}


def dispatch_parse_many(document_ids: list[int], backend: str = "auto",
                        chunk_params: Optional[dict[str, Any]] = None,
                        force_inline: bool = False) -> list[dict]:
    """批量派发解析：只做一次 worker 探测，避免上传 N 个文档等 N 次 broker 往返。

    返回 [{document_id, mode, task_id}]，顺序与 document_ids 一致。
    """
    from app.core.logging import logger

    ids = [int(i) for i in (document_ids or [])]
    if not ids:
        return []
    if not force_inline and _parse_worker_alive():
        out: list[dict] = []
        for document_id in ids:
            try:
                task = parse_document.delay(document_id, backend=backend,
                                            chunk_params=chunk_params)
                out.append({"document_id": document_id, "mode": "celery",
                            "task_id": task.id})
            except Exception as e:  # noqa: BLE001 —— 单个失败降级进程内，不拖累整批
                logger.warning(f"[dispatch_parse] 文档 {document_id} Celery 派发失败，"
                               f"降级进程内执行: {e}")
                res = _run_inline(document_id, backend=backend,
                                  chunk_params=chunk_params)
                out.append({"document_id": document_id, **res})
        return out
    logger.info(f"[dispatch_parse] 未检测到 parse worker，{len(ids)} 个文档进程内解析")
    return [{"document_id": document_id,
             **_run_inline(document_id, backend=backend, chunk_params=chunk_params)}
            for document_id in ids]


def dispatch_parse(document_id: int, backend: str = "auto",
                   chunk_params: Optional[dict[str, Any]] = None,
                   force_inline: bool = False) -> dict:
    """派发单个解析任务（M3-2 派发入口，手动重解析等单文档场景）。"""
    res = dispatch_parse_many([document_id], backend=backend,
                              chunk_params=chunk_params, force_inline=force_inline)
    return {"mode": res[0]["mode"], "task_id": res[0]["task_id"]}
