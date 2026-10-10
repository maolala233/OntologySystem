"""Alembic 迁移环境。

数据库连接优先级：环境变量 ALEMBIC_DATABASE_URL > settings.DATABASE_URL（src/backend/.env）。
R1 baseline 之后的表结构演进一律新增 revision（docs/design/02 §6 的 R2-R7 分批）。
"""
import logging
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.infrastructure.database import Base

config = context.config

# fileConfig 会按 ini 的 [logger_*] 全量重建日志并清掉已挂 handler（disable_existing_loggers
# 默认 True）——应用进程内 init_db() 跑迁移时会因此杀掉 app/core/logging.py 的终端+按天
# 文件双通道，启动后业务日志全部静默。应用进程内 root 已有统一配置，跳过；
# 仅独立 CLI（alembic revision/upgrade）时使用 ini 日志配置。
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    override = os.getenv("ALEMBIC_DATABASE_URL")
    if override:
        return override
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
