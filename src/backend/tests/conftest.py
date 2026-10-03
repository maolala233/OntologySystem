# tests/conftest.py - pytest 全局配置
# 运行位置约定：必须在 src/backend 下执行 pytest（.env 相对读取，见 config.py）。
# CI 中由 service container 提供 MySQL（.github/workflows/ci.yml）。


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs real middleware (run with -m integration)"
    )
