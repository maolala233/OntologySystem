# tests/test_logging_setup.py - 统一日志配置（终端 + 按天文件保留 7 天）
# 覆盖：root 双 handler 挂载与幂等、记录落入按天文件、7 天清扫（超期删/保留期内留）、
#       uvicorn 日志收编（启动消息传播到 root、access 关闭传播防双写）。
import logging
import os
import time
from datetime import datetime, timedelta

import pytest

import app.core.logging as log_mod
from app.core.logging import _sweep_stale_files, setup_logging


@pytest.fixture()
def tmp_log_dir(tmp_path):
    """把 root 的按天文件 handler 临时换成指向 tmp 的同款 handler（import 期已绑定真实目录）。"""
    from logging.handlers import TimedRotatingFileHandler

    root = logging.getLogger()
    old_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    for h in old_handlers:
        root.removeHandler(h)
        h.close()
    new_fh = TimedRotatingFileHandler(
        os.path.join(str(tmp_path), "ontology_system.log"),
        when="midnight", backupCount=7, encoding="utf-8")
    new_fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(new_fh)
    yield str(tmp_path)
    root.removeHandler(new_fh)
    new_fh.close()
    for h in old_handlers:
        root.addHandler(h)


def _make_stale_file(log_dir, name):
    path = os.path.join(log_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("stale")
    # 目录时间戳不影响判定：清扫按文件名日期后缀，无需改 mtime
    return path


def test_setup_logging_dual_handlers_and_idempotent():
    """root 挂终端+按天文件双 handler；重复调用不叠加。"""
    setup_logging()
    root = logging.getLogger()
    from logging.handlers import TimedRotatingFileHandler
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    console_handlers = [h for h in root.handlers
                        if isinstance(h, logging.StreamHandler)
                        and not isinstance(h, TimedRotatingFileHandler)]
    assert file_handlers, "root 应有按天轮转文件 handler"
    assert console_handlers, "root 应有终端 handler"
    fh = file_handlers[0]
    assert fh.when.lower().startswith("m"), "应按天（midnight）轮转"
    assert fh.backupCount == 7, "保留 7 天"
    n_before = len(root.handlers)
    setup_logging()
    assert len(root.handlers) == n_before, "重复 setup 不得叠加 handler"


def test_log_record_reaches_daily_file(tmp_log_dir):
    """日志记录写入按天文件（终端由 StreamHandler 保证）。"""
    logger = setup_logging()
    marker = f"logtest-{time.time()}"
    logger.info(marker)
    for h in logging.getLogger().handlers:
        h.flush()
    path = os.path.join(tmp_log_dir, "ontology_system.log")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert marker in content
    assert "ontology_system" in content  # logger 名在格式化行中


def test_sweep_removes_stale_keeps_recent(tmp_log_dir):
    """清扫：超 7 天的 .YYYYMMDD 旧日志删除，保留期内与无日期后缀的文件不动。"""
    old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
    stale = _make_stale_file(tmp_log_dir, f"ontology_system.log.{old_date}")
    recent = _make_stale_file(tmp_log_dir, f"ontology_system.log.{recent_date}")
    _make_stale_file(tmp_log_dir, "ontology_system.log")  # 活动文件不动

    removed = _sweep_stale_files(log_dir=tmp_log_dir, retention_days=7)

    assert removed == 1
    assert not os.path.exists(stale), "超期日志应被删除"
    assert os.path.exists(recent), "保留期内的日志不动"
    assert os.path.exists(os.path.join(tmp_log_dir, "ontology_system.log"))


def test_sweep_exact_boundary(tmp_log_dir):
    """边界：恰好第 8 天的删除，第 7 天内的保留（retention_days+1 为删除线）。"""
    d8 = (datetime.now() - timedelta(days=8)).strftime("%Y%m%d")
    d7 = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    f8 = _make_stale_file(tmp_log_dir, f"a.log.{d8}")
    f7 = _make_stale_file(tmp_log_dir, f"a.log.{d7}")
    removed = _sweep_stale_files(log_dir=tmp_log_dir, retention_days=7)
    assert removed == 1 and not os.path.exists(f8) and os.path.exists(f7)


def test_uvicorn_loggers_integrated():
    """uvicorn 启动消息传播到 root（进终端+文件）；access 关闭传播防双写。"""
    setup_logging()
    uv = logging.getLogger("uvicorn")
    assert uv.propagate is True
    assert not uv.handlers, "uvicorn 自带 handler 应清空（防终端重复打印）"
    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False, "access 由 main.py 中间件记录，须关闭传播防双写"


def test_business_module_logger_captured(tmp_log_dir):
    """业务模块 logging.getLogger(__name__)（root 体系）的记录同样进双通道。"""
    mod_logger = logging.getLogger("app.adapters.retrieval")
    marker = f"modlog-{time.time()}"
    mod_logger.info(marker)
    for h in logging.getLogger().handlers:
        h.flush()
    with open(os.path.join(tmp_log_dir, "ontology_system.log"), encoding="utf-8") as f:
        assert marker in f.read()
