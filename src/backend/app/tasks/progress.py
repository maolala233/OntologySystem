# app/tasks/progress.py - Redis 进度键 helper（docs/design/03 §7 / §8）
# 替代进程内 TaskManager（task_manager.py 将在 M3 删除）：
# SSE 数据源改为 Redis 键，API/worker 分离后任务进度不再丢失。
#
# 键规范：progress:{task_id}，值 JSON：
#   {queue, stage, percent, message, stats, status, updated_at}
# TTL 24h；status ∈ running|completed|failed|cancelled

from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.core.config import settings

_PROGRESS_KEY = "progress:{task_id}"
_PROGRESS_TTL = 24 * 3600


def _redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def set_task_progress(task_id: str, stage: str, percent: int,
                      message: str = "", stats: Optional[dict[str, Any]] = None,
                      queue: str = "", status: str = "running") -> None:
    """任务进行中更新进度（worker 侧调用）。percent 取 0-100。"""
    payload = {
        "queue": queue,
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "stats": stats or {},
        "status": status,
        "updated_at": int(time.time()),
    }
    _redis().set(_PROGRESS_KEY.format(task_id=task_id),
                 json.dumps(payload, ensure_ascii=False), ex=_PROGRESS_TTL)


def finish_task_progress(task_id: str, status: str,
                         message: str = "", stats: Optional[dict[str, Any]] = None) -> None:
    """任务终态：completed | failed | cancelled。"""
    current = get_task_progress(task_id) or {}
    set_task_progress(
        task_id,
        stage=current.get("stage", "done"),
        percent=100 if status == "completed" else current.get("percent", 0),
        message=message,
        stats=stats if stats is not None else current.get("stats"),
        queue=current.get("queue", ""),
        status=status,
    )


def get_task_progress(task_id: str) -> Optional[dict[str, Any]]:
    """SSE 端点轮询读取（M3 的 extraction/documents events 端点数据源）。"""
    raw = _redis().get(_PROGRESS_KEY.format(task_id=task_id))
    return json.loads(raw) if raw else None
