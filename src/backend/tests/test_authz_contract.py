# tests/test_authz_contract.py - M1 权限契约矩阵（08 §2 M1 验收）
# 覆盖：admin / 已授权普通用户 / 未授权普通用户 / 未登录 四类身份 ×
#       （用户管理、模块授权、system config、domains、auth/me）+ require_module 集成。
# 说明：跑在真实开发库上（CI 用 MySQL service 容器）；测试用户/数据用 fixture 创建并清理。
import uuid

import bcrypt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.deps import require_module
from app.infrastructure.database import User
from main import app

client = TestClient(app)

M1_TAG = f"m1t{uuid.uuid4().hex[:6]}"  # 本轮测试用户/数据前缀，保证幂等


def _mk_user(db, username, role="user"):
    from app.infrastructure.database import User

    pw = bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode()
    user = User(username=username, hashed_password=pw, role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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
def users(db):
    """创建 admin/授权用户/未授权用户 + user_a 的授权行（显式 allow qa、显式 deny projects）。"""
    from app.infrastructure.database import UserModuleGrant

    admin = _mk_user(db, f"{M1_TAG}_admin", role="admin")
    user_a = _mk_user(db, f"{M1_TAG}_a")
    user_b = _mk_user(db, f"{M1_TAG}_b")
    user_c = _mk_user(db, f"{M1_TAG}_c")  # 专供 reset_password 测试，避免污染 user_a 登录态
    admin_by_name = db.query(User).filter(User.username == "admin").first()
    grantor = admin_by_name or admin
    db.add(UserModuleGrant(user_id=user_a.id, module_code="qa", allowed=True, granted_by=grantor.id))
    db.add(UserModuleGrant(user_id=user_a.id, module_code="projects", allowed=False, granted_by=grantor.id))
    db.commit()
    yield {"admin": admin, "user_a": user_a, "user_b": user_b, "user_c": user_c,
           "admin_password": "Passw0rd!123"}
    # 清理
    for u in (admin, user_a, user_b, user_c):
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == u.id).delete()
        u2 = db.query(User).filter(User.id == u.id).first()
        if u2:
            db.delete(u2)
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_user_a_modules_in_me(users):
    """授权用户 /auth/me：默认 3 模块 + 显式 allow(qa) - 显式 deny(projects)。"""
    tokens = _login(f"{M1_TAG}_a")
    resp = client.get("/api/auth/me", headers=_auth(tokens["access_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["modules"]) == ["asset_center", "dashboard", "qa"]
    assert body["role"] == "user"


def test_user_b_defaults_only(users):
    tokens = _login(f"{M1_TAG}_b")
    body = client.get("/api/auth/me", headers=_auth(tokens["access_token"])).json()
    assert sorted(body["modules"]) == ["asset_center", "dashboard", "projects"]


def test_admin_me_all_modules(users):
    tokens = _login(f"{M1_TAG}_admin")
    body = client.get("/api/auth/me", headers=_auth(tokens["access_token"])).json()
    assert len(body["modules"]) == 14
    assert body["role"] == "admin"


def test_admin_endpoints_require_admin(users):
    """未授权普通用户调 admin API → 403 ADMIN_REQUIRED；未登录 → 401。"""
    t_a = _login(f"{M1_TAG}_a")["access_token"]
    resp = client.get("/api/admin/users", headers=_auth(t_a))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"

    resp = client.get("/api/admin/modules")
    assert resp.status_code == 401

    # admin 畅通
    t_adm = _login(f"{M1_TAG}_admin")["access_token"]
    assert client.get("/api/admin/users", headers=_auth(t_adm)).status_code == 200
    mods = client.get("/api/admin/modules", headers=_auth(t_adm)).json()["items"]
    assert len(mods) == 14
    assert {"dashboard", "qa", "export"} <= {m["code"] for m in mods}


def test_grants_matrix_roundtrip(users):
    """授权矩阵整表保存 → /auth/me 即时反映（验收门槛③）。"""
    t_adm = _login(f"{M1_TAG}_admin")["access_token"]
    uid = users["user_b"].id
    resp = client.put("/api/admin/modules/grants", headers=_auth(t_adm), json={
        "user_id": uid,
        "grants": [{"module_code": "qa", "allowed": True},
                   {"module_code": "documents", "allowed": True}],
    })
    assert resp.status_code == 200, resp.text
    t_b = _login(f"{M1_TAG}_b")["access_token"]
    body = client.get("/api/auth/me", headers=_auth(t_b)).json()
    assert sorted(body["modules"]) == ["asset_center", "dashboard", "documents", "projects", "qa"]

    # 审计路径可追溯：GET grants 返回显式行
    rows = client.get(f"/api/admin/modules/grants?user_id={uid}", headers=_auth(t_adm)).json()
    assert {g["module_code"] for g in rows["grants"]} == {"qa", "documents"}

    # 非法模块码 → 400 VALIDATION_ERROR（03 §2.3 错误码表）
    resp = client.put("/api/admin/modules/grants", headers=_auth(t_adm), json={
        "user_id": uid, "grants": [{"module_code": "nope", "allowed": True}]})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # admin 用户不允许被配置矩阵
    admin_id = users["admin"].id
    resp = client.put("/api/admin/modules/grants", headers=_auth(t_adm), json={
        "user_id": admin_id, "grants": []})
    assert resp.status_code == 400


def test_system_config_endpoints_removed(users):
    """M2：/api/system/config/* 已删除（模型配置迁至 /api/model-configs，见 test_model_configs.py）。"""
    t_adm = _login(f"{M1_TAG}_admin")["access_token"]
    resp = client.put("/api/system/config/whatever", headers=_auth(t_adm), json={"value": {}})
    assert resp.status_code == 404
    resp = client.get("/api/system/config/whatever", headers=_auth(t_adm))
    assert resp.status_code == 404


def test_domains_write_admin_only(users):
    """知识域写端点补挂 admin 守卫（03 §6 契约）。"""
    t_adm = _login(f"{M1_TAG}_admin")["access_token"]
    t_a = _login(f"{M1_TAG}_a")["access_token"]
    name = f"{M1_TAG}域"
    resp = client.post("/api/domains", headers=_auth(t_a), json={"name": name})
    assert resp.status_code == 403
    resp = client.post("/api/domains", headers=_auth(t_adm), json={"name": name})
    assert resp.status_code == 201, resp.text
    domain_id = resp.json()["id"]
    resp = client.delete(f"/api/domains/{domain_id}", headers=_auth(t_adm))
    assert resp.status_code == 200


def test_user_patch_and_reset_password(users):
    """PATCH 用户：重置密码一次性返回；禁止自我角色变更。"""
    t_adm = _login(f"{M1_TAG}_admin")["access_token"]
    uid = users["user_c"].id
    resp = client.patch(f"/api/admin/users/{uid}", headers=_auth(t_adm),
                        json={"reset_password": True})
    assert resp.status_code == 200
    new_password = resp.json()["password"]
    assert len(new_password) >= 12
    # 新密码可登录
    assert _login(f"{M1_TAG}_c", password=new_password)["access_token"]

    # admin 修改自己的角色 → 400
    admin_id = users["admin"].id
    resp = client.patch(f"/api/admin/users/{admin_id}", headers=_auth(t_adm),
                        json={"role": "user"})
    assert resp.status_code in (400, 422)


def test_refresh_flow_and_refresh_token_not_accepted_as_access(users):
    t = _login(f"{M1_TAG}_b")
    resp = client.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    # refresh token 冒充 access → 401
    resp = client.get("/api/auth/me", headers=_auth(t["refresh_token"]))
    assert resp.status_code == 401


def test_sse_ticket_issued(users):
    t = _login(f"{M1_TAG}_b")["access_token"]
    resp = client.get("/api/auth/sse-ticket", params={"task_id": "t1"}, headers=_auth(t))
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_in"] == 60 and len(body["ticket"]) >= 16


def test_require_module_guard_integration(users):
    """require_module 集成验证（真实依赖 + mini app）：MODULE_NOT_GRANTED 行为。"""
    from fastapi.responses import JSONResponse

    from app.core.exceptions import APIError

    mini = FastAPI()

    @mini.exception_handler(APIError)
    async def _api_error_handler(request, exc: APIError):
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @mini.get("/guarded/qa")
    def guarded_qa(_u: None = Depends(require_module("qa"))):
        return {"ok": True}

    @mini.get("/guarded/documents")
    def guarded_documents(_u: None = Depends(require_module("documents"))):
        return {"ok": True}

    tc = TestClient(mini)
    t_a = _login(f"{M1_TAG}_a")["access_token"]

    resp = tc.get("/guarded/qa", headers=_auth(t_a))
    assert resp.status_code == 200  # user_a 显式 allow qa

    resp = tc.get("/guarded/documents", headers=_auth(t_a))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "MODULE_NOT_GRANTED"  # 验收门槛②
