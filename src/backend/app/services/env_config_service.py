# app/services/env_config_service.py - 环境配置（中间件连接参数）服务
# 需求：向量库(Milvus)/知识图谱(Neo4j)/Redis/MinIO 地址与凭据可在管理后台修改。
#
# 存储模型：沿用 M2 既有模式——SystemConfig 表 key='platform_config'（JSON）里
# 存 DB 覆盖值，读取方以「DB 覆盖 → .env 默认」顺序取值（vector_client 已是此模式）。
# 敏感字段（密码/secret key）AES-256-GCM 加密，密文带 "enc:" 前缀存 JSON 字符串。
#
# 范围：MySQL（应用自身元数据库）不开放 UI 修改——改错会导致平台自身失联。
# Celery broker URL 仍在 .env：worker 进程不热更，改 Redis 地址后需重启 worker。

import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.exceptions import APIError
from app.core.security import decrypt_str, encrypt_str
from app.infrastructure.database import SystemConfig

logger = logging.getLogger("ontology_system")

PLATFORM_CONFIG_KEY = "platform_config"
_ENC_PREFIX = "enc:"
_SECRET_MASK = "••••••••"


class EnvField:
    def __init__(self, key: str, label: str, service: str, secret: bool = False,
                 kind: str = "text", placeholder: str = "", help: str = ""):
        self.key = key
        self.label = label
        self.service = service  # milvus | neo4j | redis | minio
        self.secret = secret
        self.kind = kind  # text | password | switch
        self.placeholder = placeholder
        self.help = help


# 字段注册表（key 与 config.py Settings / platform_config 既有键一致）
ENV_FIELDS: List[EnvField] = [
    EnvField("milvus_host", "Milvus 地址", "milvus", placeholder="192.168.x.x"),
    EnvField("milvus_port", "Milvus 端口", "milvus", placeholder="19530"),
    EnvField("milvus_collection", "Milvus Collection", "milvus", placeholder="knowledge_graph_rag"),
    EnvField("milvus_enabled", "启用向量检索", "milvus", kind="switch",
             help="关闭后问答/检索不再访问 Milvus"),
    EnvField("neo4j_uri", "Neo4j URI", "neo4j", placeholder="bolt://192.168.x.x:7687"),
    EnvField("neo4j_username", "Neo4j 用户名", "neo4j", placeholder="neo4j"),
    EnvField("neo4j_password", "Neo4j 密码", "neo4j", secret=True, kind="password"),
    EnvField("redis_url", "Redis URL", "redis", placeholder="redis://192.168.x.x:6379/0",
             help="Celery broker 仍读 .env，修改后需重启 worker 才对任务队列生效"),
    EnvField("minio_endpoint", "MinIO Endpoint", "minio", placeholder="192.168.x.x:9010"),
    EnvField("minio_access_key", "MinIO Access Key", "minio", placeholder="minioadmin"),
    EnvField("minio_secret_key", "MinIO Secret Key", "minio", secret=True, kind="password"),
    EnvField("minio_secure", "MinIO HTTPS", "minio", kind="switch"),
]

_FIELD_MAP = {f.key: f for f in ENV_FIELDS}

# settings 属性名（env 默认值来源）
_SETTINGS_KEY = {
    "milvus_host": "MILVUS_HOST",
    "milvus_port": "MILVUS_PORT",
    "milvus_collection": "MILVUS_COLLECTION_NAME",
    "milvus_enabled": "MILVUS_ENABLED",
    "neo4j_uri": "NEO4J_URI",
    "neo4j_username": "NEO4J_USERNAME",
    "neo4j_password": "NEO4J_PASSWORD",
    "redis_url": "REDIS_URL",
    "minio_endpoint": "MINIO_ENDPOINT",
    "minio_access_key": "MINIO_ACCESS_KEY",
    "minio_secret_key": "MINIO_SECRET_KEY",
    "minio_secure": "MINIO_SECURE",
}


def _load_platform_cfg(db) -> Dict[str, Any]:
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_CONFIG_KEY).first()
    if row and row.value:
        return row.value if isinstance(row.value, dict) else {}
    return {}


def _decrypt(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_ENC_PREFIX):
        try:
            return decrypt_str(value[len(_ENC_PREFIX):].encode("utf-8"))
        except Exception:
            logger.warning("[env-config] 密文解密失败，回退空值")
            return ""
    return value


def _encrypt(value: str) -> str:
    return _ENC_PREFIX + encrypt_str(value).decode("utf-8")


def _env_default(key: str) -> Any:
    val = getattr(settings, _SETTINGS_KEY[key], None)
    if key == "milvus_enabled" and val is None:
        val = True
    return val


def get_effective_raw(db) -> Dict[str, Any]:
    """读取方用：DB 覆盖（解密后）→ .env 默认。"""
    overrides = _load_platform_cfg(db)
    out: Dict[str, Any] = {}
    for f in ENV_FIELDS:
        if f.key in overrides and overrides[f.key] is not None:
            out[f.key] = _decrypt(overrides[f.key])
        else:
            out[f.key] = _env_default(f.key)
    return out


def redis_url(db=None) -> str:
    """便捷读取：Redis URL（DB 覆盖优先，DB 不可达回退 .env）。"""
    try:
        if db is None:
            from app.infrastructure.database import SessionLocal
            db = SessionLocal()
            try:
                return get_effective_raw(db)["redis_url"]
            finally:
                db.close()
        return get_effective_raw(db)["redis_url"]
    except Exception as e:
        logger.debug(f"[env-config] redis_url 读库失败，回退 .env：{e}")
        return settings.REDIS_URL


def describe(db) -> Dict[str, Any]:
    """管理页用：字段元数据 + 掩码值 + 来源（db|env）。"""
    overrides = _load_platform_cfg(db)
    services: Dict[str, List[Dict[str, Any]]] = {}
    for f in ENV_FIELDS:
        from_db = f.key in overrides and overrides[f.key] is not None
        raw = _decrypt(overrides[f.key]) if from_db else _env_default(f.key)
        services.setdefault(f.service, []).append({
            "key": f.key,
            "label": f.label,
            "kind": f.kind,
            "secret": f.secret,
            "help": f.help,
            "value": _SECRET_MASK if (f.secret and raw) else raw,
            "has_value": bool(raw),
            "source": "db" if from_db else "env",
        })
    return {
        "services": services,
        "notes": [
            "MySQL 为平台自身元数据库，不在此修改（改错会导致平台失联）",
            "保存后 API 进程即时生效；Celery worker 为独立进程，Redis/broker 变更需重启 worker",
        ],
    }


def update(db, updates: Dict[str, Any], operator_id: int) -> Dict[str, int]:
    """合并写入 platform_config。secret 字段传掩码/空/缺省 = 不变。"""
    overrides = _load_platform_cfg(db)
    changed, skipped = 0, 0
    for key, value in (updates or {}).items():
        f = _FIELD_MAP.get(key)
        if f is None:
            skipped += 1
            continue
        if f.secret and (value is None or value == "" or value == _SECRET_MASK):
            skipped += 1
            continue
        if f.kind == "switch":
            overrides[key] = bool(value)
        else:
            text = str(value).strip()
            overrides[key] = _encrypt(text) if f.secret else text
        changed += 1

    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_CONFIG_KEY).first()
    if row is None:
        row = SystemConfig(key=PLATFORM_CONFIG_KEY, value=overrides)
        db.add(row)
    else:
        row.value = overrides
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    db.commit()
    logger.info(f"[env-config] 用户 {operator_id} 更新环境配置：changed={changed} skipped={skipped}")
    return {"changed": changed, "skipped": skipped}


def test_service(db, service: str) -> Dict[str, Any]:
    """用当前生效配置（含未保存前的 DB 覆盖）实测连通性，2s 超时。"""
    import time

    cfg = get_effective_raw(db)
    t0 = time.perf_counter()

    def _err(exc: Exception) -> Dict[str, Any]:
        return {"service": service, "status": "down",
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "error": str(exc)[:200]}

    try:
        if service == "neo4j":
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                cfg["neo4j_uri"], auth=(cfg["neo4j_username"], cfg["neo4j_password"]),
                connection_timeout=2,
            )
            try:
                driver.verify_connectivity()
            finally:
                driver.close()
        elif service == "milvus":
            from pymilvus import connections, utility
            alias = f"env_test_{int(time.time() * 1000)}"
            connections.connect(alias=alias, host=cfg["milvus_host"],
                                port=cfg["milvus_port"], timeout=2)
            try:
                utility.get_server_version(using=alias)
            finally:
                connections.disconnect(alias)
        elif service == "redis":
            import redis as _redis
            client = _redis.Redis.from_url(cfg["redis_url"], socket_connect_timeout=2)
            client.ping()
        elif service == "minio":
            from minio import Minio
            client = Minio(cfg["minio_endpoint"], access_key=cfg["minio_access_key"],
                           secret_key=cfg["minio_secret_key"], secure=bool(cfg["minio_secure"]))
            client.list_buckets()
        else:
            raise APIError(f"不支持的服务：{service}", code="SERVICE_NOT_TESTABLE", http_status=400)
        return {"service": service, "status": "ok",
                "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except APIError:
        raise
    except Exception as exc:
        return _err(exc)
