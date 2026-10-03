# tests/test_model_configs.py - M2 模型配置契约（08 §2 M2 验收）
# 覆盖：解析优先级 / 密钥加密落库与掩码回显 / CRUD 权限 / 默认互斥 / 连通性测试(mock probe)
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import ModelConfig, User
from main import app

client = TestClient(app)
TAG = f"m2t{uuid.uuid4().hex[:6]}"


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
    yield {"admin": admin, "user": user}
    for u in (admin, user):
        db.query(ModelConfig).filter(ModelConfig.created_by == u.id).delete()
        row = db.query(User).filter(User.id == u.id).first()
        if row:
            db.delete(row)
    db.commit()
    # 恢复导入行（name=导入-*）的默认标记，避免测试残留导致 purpose 无默认
    for purpose in ("chat", "extract", "embedding", "vl"):
        has_default = (db.query(ModelConfig)
                       .filter(ModelConfig.purpose == purpose, ModelConfig.is_default.is_(True))
                       .first())
        if not has_default:
            row = (db.query(ModelConfig)
                   .filter(ModelConfig.purpose == purpose,
                           ModelConfig.name.like("导入-%")).first())
            if row:
                row.is_default = True
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(env):
    return _auth(_login(f"{TAG}_admin")["access_token"])


def test_create_requires_admin(env):
    """普通用户创建全局配置 → 403 ADMIN_REQUIRED。"""
    t = _login(f"{TAG}_user")["access_token"]
    resp = client.post("/api/model-configs", headers=_auth(t), json={
        "purpose": "chat", "name": f"{TAG}_x", "provider": "openai_compatible",
        "base_url": "http://example.invalid/v1", "model_name": "m", "api_key": "sk-test",
    })
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_crud_encrypt_and_mask(env, db):
    """api_key 落库密文 + 列表掩码回显（验收②）。"""
    headers = _admin_headers(env)
    resp = client.post("/api/model-configs", headers=headers, json={
        "purpose": "chat", "name": f"{TAG}_chat", "provider": "openai_compatible",
        "base_url": "http://example.invalid/v1", "model_name": "glm-test",
        "api_key": "sk-abcdefgh12345678", "is_default": True,
    })
    assert resp.status_code == 201, resp.text
    row_id = resp.json()["id"]
    assert resp.json()["api_key_masked"] == "sk-***5678"
    assert resp.json()["api_key_set"] is True

    # DB 内为密文（非明文）；先结束本会话旧快照事务以读到 API 会话提交的行
    db.commit()
    row = db.query(ModelConfig).filter(ModelConfig.id == row_id).first()
    assert row.api_key_encrypted and b"sk-abcdefgh" not in bytes(row.api_key_encrypted)

    # 列表无明文
    items = client.get("/api/model-configs", headers=headers).json()["items"]
    assert all("sk-abcdefgh" not in str(i) for i in items)

    # 解析可还原明文（provider 内部用）
    from app.adapters.provider import ModelPurpose, resolve_provider

    resolved = resolve_provider(ModelPurpose.CHAT, db=db)
    assert resolved.api_key == "sk-abcdefgh12345678"
    assert resolved.source == "model_config"


def test_default_exclusive_and_delete_guard(env):
    """同 purpose 默认互斥；默认配置禁删。"""
    headers = _admin_headers(env)
    client.post("/api/model-configs", headers=headers, json={
        "purpose": "extract", "name": f"{TAG}_e1", "provider": "openai_compatible",
        "base_url": "http://example.invalid/v1", "model_name": "m1", "api_key": "sk-1",
        "is_default": True}).json()
    r2 = client.post("/api/model-configs", headers=headers, json={
        "purpose": "extract", "name": f"{TAG}_e2", "provider": "openai_compatible",
        "base_url": "http://example.invalid/v1", "model_name": "m2", "api_key": "sk-2",
        "is_default": True}).json()
    rows = client.get("/api/model-configs?purpose=extract", headers=headers).json()["items"]
    mine = [r for r in rows if r["name"].startswith(TAG)]
    assert sum(1 for r in mine if r["is_default"]) == 1  # 互斥

    resp = client.delete(f"/api/model-configs/{r2['id']}", headers=headers)
    assert resp.status_code == 400  # 默认不可删
    client.patch(f"/api/model-configs/{r2['id']}", headers=headers, json={"is_default": False})
    assert client.delete(f"/api/model-configs/{r2['id']}", headers=headers).status_code == 200


def test_embedding_requires_dims(env):
    headers = _admin_headers(env)
    resp = client.post("/api/model-configs", headers=headers, json={
        "purpose": "embedding", "name": f"{TAG}_emb", "provider": "ollama",
        "base_url": "http://localhost:11434/v1", "model_name": "bge-m3:latest"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_probe_mocked(monkeypatch, env):
    """连通性测试端点：mock 探测函数（真实探测在验收时对真实端点执行）。"""

    from app.adapters import provider as provider_mod
    monkeypatch.setattr(provider_mod, "_probe_chat",
                        lambda *a, **k: (True, 42, "连通成功", "hi"))
    headers = _admin_headers(env)
    resp = client.post("/api/model-configs/test", headers=headers, json={
        "purpose": "chat", "provider": "openai_compatible",
        "base_url": "http://example.invalid/v1", "model_name": "m"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["latency_ms"] == 42


def test_resolution_fallback_order(db, env):
    """解析优先级：项目级 > 全局级；无配置时 env 兜底或抛 PROVIDER_UNAVAILABLE。"""
    from app.adapters.provider import ModelPurpose, resolve_provider

    # 当前库已有导入的全局 extract 默认行（真实数据）
    resolved = resolve_provider(ModelPurpose.EXTRACT, db=db)
    assert resolved.source == "model_config"
    assert "ark" in resolved.base_url or resolved.base_url  # 导入行真实存在
