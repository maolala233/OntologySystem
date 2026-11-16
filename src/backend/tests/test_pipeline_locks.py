"""管线锁生命周期单测（2026-11-16 重启后误显示「构建中」的修复）。

覆盖：
- _progress_alive：终态 / 新鲜 / 超时 / 缺失
- reconcile_pipeline_locks：任务在跑保留、任务消失取消、worker 未上线时
  只清理超时锁、终态进度直接释放
- pipeline_build_active：详情/文档守卫的判定
- pipeline_active_project_ids：列表状态过滤死锁
"""
from __future__ import annotations

import time


def _prog(status="running", idle_seconds=0):
    return {"status": status, "updated_at": int(time.time()) - idle_seconds,
            "message": ""}


# ---------------------------------------------------------------- _progress_alive

def test_progress_alive_missing_and_terminal(pipeline_module):
    alive = pipeline_module._progress_alive
    assert alive(None) is True          # 刚入队/未写进度：保守按存活
    assert alive(_prog("completed")) is False
    assert alive(_prog("failed")) is False
    assert alive(_prog("cancelled")) is False


def test_progress_alive_fresh_and_stale(pipeline_module):
    alive = pipeline_module._progress_alive
    assert alive(_prog("running", idle_seconds=10)) is True
    assert alive(_prog("queued", idle_seconds=10)) is True
    assert alive(_prog("running", idle_seconds=181)) is False


# ---------------------------------------------------------------- reconcile

def test_reconcile_keeps_live_task(fake_redis, progress_state, fake_celery,
                                   pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    progress_state["task-a"] = _prog()
    fake_celery.inspector.active_data = {"extract@host": [{"id": "task-a"}]}

    res = pipeline_module.reconcile_pipeline_locks()

    assert res["kept"] == [5] and res["released"] == [] and res["workers"] == 1
    assert fake_redis.get("pipeline:running:5") == "task-a"
    assert progress_state["task-a"]["status"] == "running"


def test_reconcile_keeps_reserved_and_scheduled_tasks(fake_redis, progress_state,
                                                      fake_celery, pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    fake_redis.store["pipeline:running:6"] = "task-b"
    progress_state["task-a"] = _prog()
    progress_state["task-b"] = _prog()
    fake_celery.inspector.reserved_data = {"extract@host": [{"id": "task-a"}]}
    fake_celery.inspector.scheduled_data = {
        "extract@host": [{"request": {"id": "task-b"}}]}

    res = pipeline_module.reconcile_pipeline_locks()

    assert sorted(res["kept"]) == [5, 6] and res["released"] == []


def test_reconcile_cancels_missing_task_when_worker_online(
        fake_redis, progress_state, fake_celery, pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    progress_state["task-a"] = _prog()
    # worker 在线，但任务不在 active/reserved/scheduled → 已死
    fake_celery.inspector.active_data = {"extract@host": []}

    res = pipeline_module.reconcile_pipeline_locks()

    assert res["released"] == [5] and res["kept"] == []
    assert fake_redis.get("pipeline:running:5") is None
    assert progress_state["task-a"]["status"] == "cancelled"


def test_reconcile_offline_worker_keeps_fresh_lock(
        fake_redis, progress_state, fake_celery, pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    progress_state["task-a"] = _prog(idle_seconds=10)
    # 所有 inspector 返回空：worker 还没起来，无法判断是否会被重新投递
    res = pipeline_module.reconcile_pipeline_locks()

    assert res["workers"] == 0 and res["unknown"] == [5]
    assert fake_redis.get("pipeline:running:5") == "task-a"
    assert progress_state["task-a"]["status"] == "running"


def test_reconcile_offline_worker_cancels_stale_lock(
        fake_redis, progress_state, fake_celery, pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    progress_state["task-a"] = _prog(idle_seconds=1000)

    res = pipeline_module.reconcile_pipeline_locks()

    assert res["released"] == [5]
    assert fake_redis.get("pipeline:running:5") is None


def test_reconcile_releases_terminal_progress(fake_redis, progress_state,
                                             fake_celery, pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-a"
    progress_state["task-a"] = _prog("completed", idle_seconds=1000)

    res = pipeline_module.reconcile_pipeline_locks()

    assert res["released"] == [5]
    assert fake_redis.get("pipeline:running:5") is None


def test_reconcile_guard_prevents_duplicate_run(fake_redis, fake_celery,
                                               pipeline_module):
    fake_redis.store["pipeline:reconcile:guard"] = "1"

    res = pipeline_module.reconcile_pipeline_locks()

    assert res == {"skipped": "another_instance"}


# ---------------------------------------------------------------- build_active

def test_pipeline_build_active(monkeypatch, pipeline_module, progress_state):
    monkeypatch.setattr(pipeline_module, "pipeline_running_task_id",
                        lambda pid: "task-a" if pid == 5 else None)
    progress_state["task-a"] = _prog(idle_seconds=10)
    assert pipeline_module.pipeline_build_active(5) is True
    assert pipeline_module.pipeline_build_active(6) is False

    progress_state["task-a"] = _prog(idle_seconds=1000)
    assert pipeline_module.pipeline_build_active(5) is False

    progress_state["task-a"] = _prog("completed")
    assert pipeline_module.pipeline_build_active(5) is False


# ---------------------------------------------------------------- list status

def test_active_project_ids_filters_dead_locks(fake_redis, progress_state,
                                              pipeline_module):
    fake_redis.store["pipeline:running:5"] = "task-live"
    fake_redis.store["pipeline:running:6"] = "task-dead"
    fake_redis.store["pipeline:schedule:7"] = "{}"
    fake_redis.store["pipeline:schedule:7:task"] = "legacy"
    progress_state["task-live"] = _prog(idle_seconds=10)
    progress_state["task-dead"] = _prog(idle_seconds=1000)

    running, scheduled = pipeline_module.pipeline_active_project_ids()

    assert running == {5}
    assert scheduled == {7}
