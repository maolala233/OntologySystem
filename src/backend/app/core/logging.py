# app/core/logging.py - 统一日志配置
# 终端打印 + 按天滚动文件（保留 7 天，超期自动清理）双通道：
# handler 挂 root logger，业务模块 logging.getLogger(__name__) 与共享 logger 实例
# 全部走同一配置；uvicorn 启动消息收编进 root，访问日志由 main.py 中间件负责
# （uvicorn.access 关闭传播防双写）。TimedRotatingFileHandler 的 backupCount
# 只在轮转（写入跨天）时清理，长期不写会堆积旧文件——启动时按日期后缀主动清扫。
# 已知边界：多 worker 部署共享同一日志文件，午夜轮转存在极小概率的并发改名竞争，
# 最坏丢一次轮转、文件继续写入，不丢业务日志。

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.environ.get("ONTOLOGY_LOG_DIR", "logs")
LOG_FILE = "ontology_system.log"
RETENTION_DAYS = 7  # 按天保存，保留最近 7 天
_STALE_SUFFIX = re.compile(r"\.(\d{8})$")


def _sweep_stale_files(log_dir: str = LOG_DIR, retention_days: int = RETENTION_DAYS) -> int:
    """清扫超过保留期的按天轮转旧日志（文件名以 .YYYYMMDD 结尾），返回删除数。
    保留近 retention_days 天：日期 >= (今天 - retention_days) 的不动。"""
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y%m%d")
    removed = 0
    if not os.path.isdir(log_dir):
        return 0
    for name in os.listdir(log_dir):
        m = _STALE_SUFFIX.search(name)
        if not m or m.group(1) >= cutoff:
            continue
        try:
            os.remove(os.path.join(log_dir, name))
            removed += 1
        except OSError:
            pass  # Windows 下可能被其他进程占用，留待下次启动再清
    return removed


def setup_logging() -> logging.Logger:
    """终端 + 按天文件（保留 7 天）双通道日志，幂等可重复调用。"""
    root = logging.getLogger()
    if getattr(root, "_ontology_logging_ready", False):
        return logging.getLogger("ontology_system")
    root.setLevel(logging.INFO)

    os.makedirs(LOG_DIR, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        os.path.join(LOG_DIR, LOG_FILE),
        when="midnight",      # 每天午夜轮转，历史按天存为 ontology_system.log.YYYYMMDD
        interval=1,
        backupCount=RETENTION_DAYS,  # 轮转时自动清理，仅保留最近 7 天
        encoding="utf-8",
    )
    file_handler.suffix = "%Y%m%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # uvicorn 启动/错误消息收编进 root 双通道；访问日志由 main.py 中间件统一记录，
    # uvicorn.access 自带 handler 且默认不传播，关掉防止中间件之外再双写一遍
    logging.getLogger("uvicorn").handlers.clear()
    logging.getLogger("uvicorn").propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.addHandler(logging.NullHandler())
    access_logger.propagate = False

    removed = _sweep_stale_files()
    app_logger = logging.getLogger("ontology_system")
    app_logger.info(
        f"[logging] 日志初始化完成：终端 + 按天文件 {LOG_DIR}/{LOG_FILE}"
        f"（保留 {RETENTION_DAYS} 天），清扫超期旧日志 {removed} 个")

    root._ontology_logging_ready = True
    return app_logger


# 全局日志实例（兼容既有 from app.core.logging import logger 的模块）
logger = setup_logging()
