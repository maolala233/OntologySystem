from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List
from app.infrastructure.database import get_db, SystemConfig, User
from app.schemas.ontology import SystemConfigUpdate, SystemConfigResponse
from app.api.auth import get_current_user
from app.core.deps import require_role
from app.infrastructure.neo4j_client import neo4j_client
import requests
import logging

router = APIRouter(prefix="/api/system", tags=["system"])


# ==============================================================================
# M0 新增：统一健康检查（docs/design/03 §17）
# 五灯 = mysql/neo4j/milvus/redis/minio（各 2s 超时，互不阻塞）；
# oxigraph 为嵌入式（M4 接入），本阶段固定 pending。
# ==============================================================================
def _check_mysql() -> dict:
    import time
    import pymysql
    from app.core.config import settings

    t0 = time.perf_counter()
    conn = pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE, connect_timeout=2,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    finally:
        conn.close()


def _check_neo4j() -> dict:
    import time
    from neo4j import GraphDatabase
    from app.core.config import settings

    t0 = time.perf_counter()
    driver = GraphDatabase.driver(
        settings.NEO4J_URI, auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        connection_timeout=2,
    )
    try:
        driver.verify_connectivity()
        return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    finally:
        driver.close()


def _check_milvus() -> dict:
    import time
    from pymilvus import connections, utility
    from app.core.config import settings

    t0 = time.perf_counter()
    alias = "healthcheck"
    connections.connect(alias=alias, host=settings.MILVUS_HOST,
                        port=settings.MILVUS_PORT, timeout=2)
    try:
        utility.get_server_version(using=alias)
        return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    finally:
        connections.disconnect(alias)


def _check_redis() -> dict:
    import time
    import redis
    from app.core.config import settings

    t0 = time.perf_counter()
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    client.ping()
    return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}


def _check_minio() -> dict:
    import time
    from minio import Minio
    from app.core.config import settings

    t0 = time.perf_counter()
    client = Minio(settings.MINIO_ENDPOINT, access_key=settings.MINIO_ACCESS_KEY,
                   secret_key=settings.MINIO_SECRET_KEY, secure=settings.MINIO_SECURE)
    list(client.list_buckets())
    return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}


@router.get("/health")
def system_health():
    """平台健康检查（公开）：五灯 + oxigraph pending（M4 接入后转 ok）。"""
    from concurrent.futures import ThreadPoolExecutor

    checks = {
        "mysql": _check_mysql,
        "neo4j": _check_neo4j,
        "milvus": _check_milvus,
        "redis": _check_redis,
        "minio": _check_minio,
    }
    results: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in checks.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result(timeout=5)
            except Exception as exc:  # 探测失败不是 5xx：健康检查如实报告灯色
                results[name] = {"status": "down", "error": str(exc)[:200]}
    results["oxigraph"] = {"status": "pending"}  # M4：worker-rdf 接入嵌入式 Oxigraph
    overall = "ok" if all(v.get("status") == "ok" for k, v in results.items() if k != "oxigraph") else "degraded"
    return {"status": overall, "checks": results}




@router.get("/middleware")
def middleware_view(_admin: User = Depends(require_role("admin"))):
    """中间件配置只读视图（03 §17；敏感字段掩码）。模型/密钥配置已迁至 /api/model-configs。"""
    import json

    from app.core.config import settings

    def mask(v: str) -> str:
        return (v[:4] + "***") if v and len(v) > 8 else "***"

    return {
        "mysql": {"host": settings.MYSQL_HOST, "port": settings.MYSQL_PORT, "database": settings.MYSQL_DATABASE,
                  "password": mask(settings.MYSQL_PASSWORD)},
        "neo4j": {"uri": settings.NEO4J_URI, "password": mask(settings.NEO4J_PASSWORD)},
        "redis": {"url": settings.REDIS_URL.split("@")[-1] if "@" in settings.REDIS_URL else settings.REDIS_URL},
        "milvus": {"host": settings.MILVUS_HOST, "port": settings.MILVUS_PORT},
        "minio": {"endpoint": settings.MINIO_ENDPOINT, "secret": mask(settings.MINIO_SECRET_KEY)},
        "oxigraph": {"path": settings.OXIGRAPH_PATH, "note": "M4 接入（嵌入式）"},
    }
