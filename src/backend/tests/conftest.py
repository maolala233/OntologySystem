"""管线锁相关单测的轻量桩。

单测只验证锁生命周期，不需要真实 broker、Redis 或 worker：
- 本地环境没装 celery 时，用最小模块顶替 `app.tasks.celery_app`
- 无论真/假 celery，`fake_celery` fixture 都把 `celery_app` 换成可控假对象
- Redis 与进度读写通过 fixture 注入，不触碰真实中间件
"""
from __future__ import annotations

import fnmatch
import sys
import types

import pytest


class FakeInspector:
    """模拟 celery.control.inspect() 的三个查询。"""

    def __init__(self) -> None:
        self.active_data: dict = {}
        self.reserved_data: dict = {}
        self.scheduled_data: dict = {}

    def active(self):
        return self.active_data

    def reserved(self):
        return self.reserved_data

    def scheduled(self):
        return self.scheduled_data


class FakeCeleryApp:
    def __init__(self) -> None:
        self.inspector = FakeInspector()

    @property
    def control(self):
        return types.SimpleNamespace(
            inspect=lambda timeout=2.0: self.inspector)

    def task(self, *args, **kwargs):
        def _deco(fn):
            return fn
        return _deco

    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeRedis:
    """只实现锁生命周期用到的命令（get/set nx/delete/scan_iter）。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def scan_iter(self, match="*", count=None):
        for key in list(self.store):
            if fnmatch.fnmatch(key, match):
                yield key


_FAKE_CELERY = FakeCeleryApp()

# 本地未安装 celery 时，让 app.tasks.celery_app 可导入；装了则用真实模块，
# fake_celery fixture 会替换其中的 celery_app 对象。
try:  # noqa: SIM105
    import celery  # noqa: F401
except ImportError:
    if "app.tasks.celery_app" not in sys.modules:
        _celery_mod = types.ModuleType("app.tasks.celery_app")
        _celery_mod.celery_app = _FAKE_CELERY
        sys.modules["app.tasks.celery_app"] = _celery_mod


@pytest.fixture
def fake_celery(monkeypatch):
    import app.tasks.celery_app as celery_mod

    _FAKE_CELERY.inspector = FakeInspector()
    monkeypatch.setattr(celery_mod, "celery_app", _FAKE_CELERY)
    return _FAKE_CELERY


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def pipeline_module(monkeypatch, fake_redis):
    import app.tasks.pipeline_tasks as pt

    monkeypatch.setattr(pt, "_redis", lambda: fake_redis)
    return pt


@pytest.fixture
def progress_state(monkeypatch, pipeline_module):
    """可控的进度读写：测试往 state 里放 {tid: progress}。"""
    state: dict[str, dict] = {}

    def _get(tid):
        return state.get(tid)

    def _finish(tid, status, message="", stats=None):
        state[tid] = {"status": status, "message": message,
                      "updated_at": __import__("time").time()}

    monkeypatch.setattr(pipeline_module, "get_task_progress", _get)
    monkeypatch.setattr(pipeline_module, "finish_task_progress", _finish)
    return state
