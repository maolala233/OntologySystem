# app/core/logging.py - 日志配置文件
# 功能：设置统一的日志配置，提供全局日志实例

import logging
import sys
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler  # 使用时间轮转处理器


def setup_logging():
    """设置统一的日志配置"""
    # 创建 logger
    logger = logging.getLogger("ontology_system")
    logger.setLevel(logging.INFO)
    
    # 避免重复添加处理器
    if logger.handlers:
        return logger
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # 创建目录
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # 使用时间轮转文件处理器，每天创建一个新的日志文件
    log_filename = os.path.join(log_dir, "ontology_system.log")
    file_handler = TimedRotatingFileHandler(
        log_filename, 
        when="midnight",      # 在午夜进行轮转
        interval=1,           # 每1天轮转一次
        backupCount=30,       # 保留最近30个备份
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    # 设置轮转文件的命名格式
    file_handler.suffix = "%Y%m%d"
    
    # 创建格式器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    # 添加处理器到 logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# 全局日志实例
logger = setup_logging()