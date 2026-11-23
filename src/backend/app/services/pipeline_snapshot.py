# app/services/pipeline_snapshot.py - 最近一次构建的阶段时间线快照（projects.build_progress）
#
# 背景：构建进度明细只存在 Redis（progress:{task_id}，TTL 24h）。过期后
# GET /pipeline/status 只能回 idle，详情页就显示成「没有进行中的构建」——
# 项目明明刚构建过，用户看到的却是「从没构建过」。
#
# 方案（最小留痕）：每次构建落终态时，把「8 个阶段的状态 / 起止时间 / 耗时」
# 压成一份约 1KB 的 JSON 写进 projects.build_progress（该列随迁移 b2c3d4e5f6a7
# 已存在，全仓此前无消费者），status 在 Redis 冷路径上回退读取。
# 只保留**最后一次**构建；不存子步骤、不存 summary/build_report 等大对象。
#
# 成本：一次构建 1 次 UPDATE、冷路径 1 次按列查询（不加载 graph_data 等大列）；
# 不改 Redis 热路径、不新增表、不新增迁移。
from __future__ import annotations

import json
import time
from typing import Any

SNAPSHOT_VERSION = 1
MAX_MESSAGE = 1000

_TERMINAL = ("completed", "failed", "cancelled")
# 每阶段只留时间线需要的字段（前端按 key/label/status 渲染、按耗时汇总）
_STAGE_FIELDS = ("key", "label", "status", "started_at", "finished_at",
                 "duration_seconds")


def _clip(value: Any, limit: int = MAX_MESSAGE) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_snapshot_payload(*, task_id: str, status: str, message: str = "",
                           stats: Any = None, stage: str = "",
                           percent: int | None = None) -> dict | None:
    """从终态进度 stats 提取 8 阶段时间线；非管线进度/非终态返回 None。"""
    if status not in _TERMINAL or not isinstance(stats, dict):
        return None
    if not stats.get("pipeline"):  # 只留一键构建，文档解析任务不写
        return None
    try:
        project_id = int(stats.get("project_id") or 0)
    except (TypeError, ValueError):
        return None
    stages = stats.get("stages")
    if not project_id or not isinstance(stages, list) or not stages:
        return None

    stages_out: list[dict] = []
    for st in stages:
        if not isinstance(st, dict):
            continue
        stages_out.append({k: st.get(k) for k in _STAGE_FIELDS})
    if not stages_out:
        return None

    return {
        "v": SNAPSHOT_VERSION,
        "project_id": project_id,
        "task_id": task_id,
        "status": status,
        "stage": stage or "",
        "percent": int(percent if percent is not None
                       else (100 if status == "completed" else 0)),
        "message": _clip(message),
        "stages": stages_out,
        "depth": stats.get("depth"),
        "need_human": stats.get("need_human"),
        "saved_at": int(time.time()),
    }


def save_pipeline_snapshot(*, task_id: str, status: str, message: str = "",
                           stats: Any = None, stage: str = "",
                           percent: int | None = None) -> None:
    """终态构建写一份快照；任何失败只告警（展示数据不能影响构建）。"""
    payload = build_snapshot_payload(task_id=task_id, status=status, message=message,
                                     stats=stats, stage=stage, percent=percent)
    if payload is None:
        return
    project_id = payload["project_id"]
    try:
        from sqlalchemy import text, update

        from app.infrastructure.database import Project, SessionLocal

        with SessionLocal() as db:
            # 独立短会话写；万一管线的长事务还持着 projects 行锁，
            # 最多等 5s 就放弃这次留痕，绝不卡住构建收尾。
            try:
                if db.get_bind() is not None and db.get_bind().dialect.name == "mysql":
                    db.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            except Exception:  # noqa: BLE001 —— 方言不支持时按默认行为
                pass
            db.execute(update(Project).where(Project.id == project_id)
                       .values(build_progress=payload))
            db.commit()
    except Exception as e:  # noqa: BLE001 —— 快照写失败不能反过来打断构建收尾
        try:
            from app.core.logging import logger

            logger.warning(f"[pipeline] 构建快照写入失败 project={project_id}: {e}")
        except Exception:  # noqa: BLE001
            pass


def load_pipeline_snapshot(project_id: int) -> dict | None:
    """读最近一次构建快照（Redis 冷路径用）；不存在/损坏返回 None。"""
    try:
        from app.infrastructure.database import Project, SessionLocal

        with SessionLocal() as db:
            row = (db.query(Project.build_progress)
                   .filter(Project.id == project_id).first())
        value = row[0] if row else None
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict) and value.get("stages"):
            return value
    except Exception:  # noqa: BLE001 —— 读取失败按「没有快照」处理，状态回落 idle
        return None
    return None


__all__ = ["build_snapshot_payload", "save_pipeline_snapshot",
           "load_pipeline_snapshot", "MAX_MESSAGE"]
