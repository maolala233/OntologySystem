#!/usr/bin/env python3
"""
Graph Injector Service 启动脚本
"""
import uvicorn
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载.env配置
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

if __name__ == "__main__":
    host = os.getenv("SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("SERVICE_PORT", "8000"))
    workers = int(os.getenv("SERVICE_WORKERS", "1"))
    log_level = os.getenv("LOG_LEVEL", "info")

    print(f"启动 Graph Injector Service...")
    print(f"  地址: http://{host}:{port}")
    print(f"  API文档: http://{host}:{port}/docs")
    print(f"  工作进程: {workers}")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers,
        log_level=log_level.lower(),
        reload=False,
    )
