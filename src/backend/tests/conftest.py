# tests/conftest.py - pytest 全局配置
# 运行位置约定：必须在 src/backend 下执行 pytest（.env 相对读取，见 config.py）。
# CI 中由 service container 提供 MySQL（.github/workflows/ci.yml）。

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs real middleware (run with -m integration)"
    )


def pytest_collection_modifyitems(config, items):
    # integration 用例默认跳过（需真实中间件/LLM）；显式 `-m integration` 才纳入
    if (config.getoption("-m") or "") == "integration":
        return
    skip = pytest.mark.skip(reason="integration 用例需真实中间件/LLM：以 -m integration 运行")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
