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

_PROGRESS_KEY = "progress:{task_id}"
_PROGRESS_TTL = 24 * 3600


def _redis():
    # 进程级共享客户端（连接池复用）：此前每次进度读写都新建 Redis 连接，
    # 500 切片 + SSE 每秒轮询会制造数千次连接建立。见 env_config_service.get_redis_client。
    from app.services.env_config_service import get_redis_client
    return get_redis_client(decode_responses=True)


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
    merged_stats = stats if stats is not None else current.get("stats")
    percent = 100 if status == "completed" else current.get("percent", 0)
    set_task_progress(
        task_id,
        stage=current.get("stage", "done"),
        percent=percent,
        message=message,
        stats=merged_stats,
        queue=current.get("queue", ""),
        status=status,
    )
    # 一键构建落终态时留一份 MySQL 快照（8 阶段时间线 + 各阶段耗时）：
    # Redis 进度键 24h 过期后 /pipeline/status 仍能回放上一次构建，
    # 不再显示成「没有进行中的构建」。文档解析任务的 stats 没有 pipeline 标记，不会误写。
    if status in ("completed", "failed", "cancelled"):
        _persist_pipeline_snapshot(task_id, status, message, merged_stats,
                                   stage=current.get("stage", "") or "",
                                   percent=percent)


def _persist_pipeline_snapshot(task_id: str, status: str, message: str,
                               stats: Any, *, stage: str, percent: int) -> None:
    """写构建快照（非管线任务/写失败都不影响主流程）。"""
    # 先做一次廉价过滤：文档解析等非管线任务不导入服务、不开数据库会话
    if not isinstance(stats, dict) or not stats.get("pipeline") or not stats.get("stages"):
        return
    try:
        from app.services.pipeline_snapshot import save_pipeline_snapshot

        save_pipeline_snapshot(task_id=task_id, status=status, message=message,
                               stats=stats, stage=stage, percent=percent)
    except Exception:  # noqa: BLE001 —— 留痕失败不能反过来打断任务收尾
        pass


def get_task_progress(task_id: str) -> Optional[dict[str, Any]]:
    """SSE 端点轮询读取（M3 的 extraction/documents events 端点数据源）。"""
    raw = _redis().get(_PROGRESS_KEY.format(task_id=task_id))
    return json.loads(raw) if raw else None
