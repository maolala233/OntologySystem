# tests/test_env_configs.py - 环境配置契约（admin 中间件连接参数 UI 可改）
# 覆盖：权限（非 admin 403）/ 读取掩码与来源 / 更新落库（DB 覆盖 + secret AES 加密）
#      / 掩码回传跳过 / 测试端点参数校验 / platform_config 既有键不被破坏。
# 真实连通性测试（neo4j/redis/milvus/minio）依赖真实中间件，标注 integration。
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import SystemConfig, User
from main import app

client = TestClient(app)
TAG = f"env{uuid.uuid4().hex[:6]}"
PLATFORM_KEY = "platform_config"


def _login(username, password="Passw0rd!123"):
    resp = client.post("/api/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    import bcrypt

    admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    user = User(username=f"{TAG}_user", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add_all([admin, user])
    db.commit()
    db.refresh(admin)
    db.refresh(user)

    # 保存既有 platform_config，测完还原（不污染 vector_client 读取的 milvus 键）
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    saved = dict(row.value) if row and row.value else None
    yield {"admin": admin, "user": user, "saved_platform": saved}
    db.rollback()
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    if saved is None:
        if row:
            db.delete(row)
    else:
        row.value = saved
    for u in (admin, user):
        r = db.query(User).filter(User.id == u.id).first()
        if r:
            db.delete(r)
    db.commit()
    # PUT 端点 live-apply 会用测试写入的临时配置重建全局客户端（假密码驱动 /
    # secure=True 缓存）；platform_config 已还原，这里同步重建，避免毒化同进程
    # 后续模块（test_graph_view 等真实连 Neo4j 的用例）。
    from app.infrastructure.minio_client import get_minio_client
    from app.infrastructure.neo4j_client import neo4j_client

    try:
        neo4j_client.reconfigure()
    except Exception:
        pass
    get_minio_client.cache_clear()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(env):
    return _auth(_login(f"{TAG}_admin")["access_token"])


def _user_headers(env):
    return _auth(_login(f"{TAG}_user")["access_token"])


# ── 权限 ──

def test_non_admin_forbidden(env):
    for method, path in (("get", "/api/admin/env-configs"),):
        resp = getattr(client, method)(path, headers=_user_headers(env))
        assert resp.status_code == 403
    resp = client.put("/api/admin/env-configs", json={"updates": {}}, headers=_user_headers(env))
    assert resp.status_code == 403
    resp = client.post("/api/admin/env-configs/test", json={"service": "redis"},
                       headers=_user_headers(env))
    assert resp.status_code == 403


def test_unauthenticated_401():
    assert client.get("/api/admin/env-configs").status_code == 401


# ── 读取 ──

def test_get_describe_services(env):
    resp = client.get("/api/admin/env-configs", headers=_admin_headers(env))
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["services"].keys()) == {"milvus", "neo4j", "redis", "minio"}
    assert len(data["notes"]) >= 2
    flat = {f["key"]: f for fs in data["services"].values() for f in fs}
    # 字段全集（12 个：milvus4 + neo4j3 + redis1 + minio4）
    assert len(flat) == 12
    # secret 字段恒掩码且 kind=password
    for key in ("neo4j_password", "minio_secret_key"):
        assert flat[key]["secret"] is True
        assert flat[key]["kind"] == "password"
        assert flat[key]["value"] == "••••••••" if flat[key]["has_value"] else flat[key]["value"] == ""
    # 非 secret 字段回显真实值且 source ∈ {db, env}
    assert flat["redis_url"]["secret"] is False
    assert flat["redis_url"]["source"] in ("db", "env")
    assert flat["redis_url"]["value"]
    # switch 字段
    assert flat["milvus_enabled"]["kind"] == "switch"


# ── 更新 ──

def test_put_updates_db_override_and_keeps_existing_keys(env, db):
    # 记录既有键（vector_client 依赖的 milvus_* 不应被 PUT 破坏）
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    db.rollback()
    existing_milvus = (row.value or {}).get("milvus_host") if row and row.value else None

    new_url = "redis://127.0.0.1:6399/0"
    resp = client.put("/api/admin/env-configs", headers=_admin_headers(env),
                      json={"updates": {"redis_url": new_url, "unknown_key": "x"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] == 1  # unknown_key 跳过
    assert body["skipped"] == 1
    assert body["applied"]["neo4j"] in (True, False)  # 重连尝试不阻断

    # GET：source=db 且值生效
    data = client.get("/api/admin/env-configs", headers=_admin_headers(env)).json()
    flat = {f["key"]: f for fs in data["services"].values() for f in fs}
    assert flat["redis_url"]["source"] == "db"
    assert flat["redis_url"]["value"] == new_url

    # 库内 platform_config：redis_url 写入且既有 milvus 键保留
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    db.rollback()
    assert row is not None and row.value["redis_url"] == new_url
    if existing_milvus is not None:
        assert row.value.get("milvus_host") == existing_milvus


def test_put_secret_encrypted_and_mask_skip(env, db):
    resp = client.put("/api/admin/env-configs", headers=_admin_headers(env),
                      json={"updates": {"neo4j_password": "Sup3rSecret!"}})
    assert resp.status_code == 200
    assert resp.json()["changed"] == 1

    # 库内为 AES 密文（enc: 前缀），GET 回显仍是掩码
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    db.rollback()
    stored = row.value["neo4j_password"]
    assert isinstance(stored, str) and stored.startswith("enc:")

    data = client.get("/api/admin/env-configs", headers=_admin_headers(env)).json()
    flat = {f["key"]: f for fs in data["services"].values() for f in fs}
    assert flat["neo4j_password"]["value"] == "••••••••"
    assert flat["neo4j_password"]["has_value"] is True

    # 回传掩码/空值 → 跳过不覆盖（不会把掩码存成真值）
    resp = client.put("/api/admin/env-configs", headers=_admin_headers(env),
                      json={"updates": {"neo4j_password": "••••••••", "minio_secret_key": ""}})
    assert resp.status_code == 200
    assert resp.json()["changed"] == 0
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    db.rollback()
    assert row.value["neo4j_password"] == stored  # 未变


def test_put_switch_field(env, db):
    resp = client.put("/api/admin/env-configs", headers=_admin_headers(env),
                      json={"updates": {"minio_secure": True}})
    assert resp.status_code == 200
    row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_KEY).first()
    db.rollback()
    assert row.value["minio_secure"] is True


# ── 测试连通端点 ──

def test_test_endpoint_invalid_service(env):
    resp = client.post("/api/admin/env-configs/test", json={"service": "mysql"},
                       headers=_admin_headers(env))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SERVICE_NOT_TESTABLE"


@pytest.mark.integration
@pytest.mark.parametrize("service", ["neo4j", "milvus", "redis", "minio"])
def test_test_endpoint_real_services(env, service):
    """真实中间件连通：dev 环境五件套 healthy 时应全 ok。"""
    resp = client.post("/api/admin/env-configs/test", json={"service": service},
                       headers=_admin_headers(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body.get("error")
    assert body["latency_ms"] >= 0
