"""
日志模块
提供统一的日志记录功能，支持控制台和文件双输出
"""
import logging
import sys
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from app.core.config import settings


def setup_logger(name: str = "graph_injector", log_dir: str = "data/logs") -> logging.Logger:
    """
    配置并返回日志记录器

    Args:
        name: 日志记录器名称
        log_dir: 日志文件存储目录

    Returns:
        配置好的Logger实例
    """
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 创建logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.service.log_level.upper(), logging.INFO))

    # 如果已有处理器则不再添加
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 通用日志文件处理器（按大小轮转，最大50MB，保留10个备份）
    info_log_path = log_path / "app.log"
    info_handler = RotatingFileHandler(
        filename=info_log_path,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=10,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.DEBUG)
    info_handler.setFormatter(formatter)
    logger.addHandler(info_handler)

    # 错误日志文件处理器（只记录ERROR及以上级别）
    error_log_path = log_path / "error.log"
    error_handler = RotatingFileHandler(
        filename=error_log_path,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    return logger


# 创建全局logger实例
logger = setup_logger()


class LoggerMixin:
    """
    日志混入类
    继承此类的类将自动获得logger属性，日志格式中包含类名
    """

    @property
    def logger(self) -> logging.Logger:
        if not hasattr(self, "_logger"):
            class_name = self.__class__.__name__
            self._logger = setup_logger(f"graph_injector.{class_name}")
        return self._logger
