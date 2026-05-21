"""
应用主入口
FastAPI应用实例创建和配置
"""
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    启动时执行初始化，关闭时执行清理
    """
    logger.info("=" * 60)
    logger.info("Graph Injector Service 启动中...")
    logger.info("=" * 60)

    # 确保必要的目录存在
    for dir_path in [
        settings.temp_upload_dir,
        settings.schema_output_dir,
        settings.output_dir,
        "data/logs",
    ]:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        logger.info(f"目录已确认: {dir_path}")

    # 打印配置信息
    logger.info(f"LLM配置: base_url={settings.llm.base_url}, model={settings.llm.model}")
    logger.info(f"Embedding配置: base_url={settings.embed.base_url}, model={settings.embed.model}")
    logger.info(f"ES配置: host={settings.es.host}, port={settings.es.port}")
    logger.info(f"服务配置: host={settings.service.host}, port={settings.service.port}")

    logger.info("Graph Injector Service 启动完成!")

    yield

    # 关闭时清理
    logger.info("Graph Injector Service 正在关闭...")
    from app.infrastructure.es_client import es_client
    es_client.close()
    logger.info("Graph Injector Service 已关闭")


def create_app() -> FastAPI:
    """
    创建并配置FastAPI应用实例

    Returns:
        FastAPI应用实例
    """
    app = FastAPI(
        title="Graph Injector Service",
        description="知识图谱注入服务 - 从文档自动构建知识图谱并注入到Elasticsearch",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载API路由
    app.include_router(api_router, prefix="/api/v1")

    # 根路径
    @app.get("/")
    async def root():
        return {
            "service": "Graph Injector Service",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/api/v1/system/health",
        }

    return app


# 创建应用实例
app = create_app()
