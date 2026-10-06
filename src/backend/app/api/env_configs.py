# app/api/env_configs.py - 环境配置管理（admin）
# 需求：中间件连接参数（Milvus/Neo4j/Redis/MinIO）可在管理后台查看、修改、测试连通。
# 存储与生效机制见 app/services/env_config_service.py。

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.infrastructure.database import User
from app.services import env_config_service as ecs
from app.services.audit_service import log_action
from app.infrastructure.neo4j_client import neo4j_client

router = APIRouter(prefix="/api/admin/env-configs", tags=["admin-env"])


class EnvConfigUpdate(BaseModel):
    updates: dict


class EnvTestRequest(BaseModel):
    service: str


@router.get("")
def get_env_config(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    return ecs.describe(db)


@router.put("")
def update_env_config(
    body: EnvConfigUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    result = ecs.update(db, body.updates, admin.id)
    log_action(db, admin.id, "admin", resource_type="env_config",
               resource_id=None, detail={"changed": result["changed"],
                                         "keys": sorted(body.updates.keys())[:20]})
    db.commit()

    # 即时生效：重建 API 进程内的 Neo4j / MinIO 单例（Milvus 由 VectorStoreManager
    # 每次实例化时读 platform_config，天然生效；Celery worker 需重启）
    applied = {"neo4j": False, "minio": False}
    try:
        applied["neo4j"] = neo4j_client.reconfigure()
    except Exception as exc:
        import logging
        logging.getLogger("ontology_system").warning(f"[env-config] Neo4j 重连失败：{exc}")
    try:
        from app.infrastructure.minio_client import get_minio_client
        get_minio_client.cache_clear()
        get_minio_client()
        applied["minio"] = True
    except Exception as exc:
        import logging
        logging.getLogger("ontology_system").warning(f"[env-config] MinIO 重连失败：{exc}")

    return {"message": "环境配置已保存", "changed": result["changed"],
            "skipped": result["skipped"], "applied": applied}


@router.post("/test")
def test_env_config(
    body: EnvTestRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    return ecs.test_service(db, body.service)
