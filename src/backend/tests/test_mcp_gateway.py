# tests/test_mcp_gateway.py - M5 MCP 网关契约（docs/design/07 §3 / 03 §16，08 §2 M5）
# 覆盖：GET 405、JSON-RPC 骨架错误码、令牌鉴权（缺失/格式/过期/撤销）、
#       tools/list 权限矩阵（viewer 8 读 / editor 10 工具）、写工具拒写（viewer/已发布）、
#       query_graph 只读白名单（CREATE/CALL/注释注入被拒）、限流、审计留痕、令牌管理 API。
# 跑在真实开发库上（同 test_authz_contract 约定）；测试用户/项目/令牌 fixture 创建并清理。
import json
import re
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

M5_TAG = f"m5t{uuid.uuid4().hex[:6]}"
ERR_UNAUTHORIZED = -32001
ERR_FORBIDDEN = -32002
ERR_RATE_LIMITED = -32029
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
READ_TOOLS = 8
ALL_TOOLS = 10


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
    return resp.json()["access_token"]


def _rpc(method, params=None, token=None, rpc_id=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def _call(tool, args, token):
    return _rpc("tools/call", {"name": tool, "arguments": args}, token=token)


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    """admin + viewer/editor 令牌持有人（mcp 模块显式授权）+ 测试项目（TBox 含「理财产品」类）。"""
    from app.infrastructure.database import (
        Entity,
        McpToken,
        Project,
        UserModuleGrant,
    )

    admin = _mk_user(db, f"{M5_TAG}_admin", role="admin")
    viewer_u = _mk_user(db, f"{M5_TAG}_viewer")
    editor_u = _mk_user(db, f"{M5_TAG}_editor")
    nomod_u = _mk_user(db, f"{M5_TAG}_nomod")
    for u in (viewer_u, editor_u):
        db.add(UserModuleGrant(user_id=u.id, module_code="mcp", allowed=True, granted_by=admin.id))
    db.commit()

    project = Project(
        name=f"{M5_TAG}_项目",
        owner_id=admin.id,
        status="draft",
        graph_data={
            "nodes": [
                {
                    "id": "cls_prod", "type": "custom", "position": {"x": 0, "y": 0},
                    "data": {"label": "理财产品", "type": "owl:Class", "properties": []},
                },
                {
                    # 行表种子须有对应 blob 节点（M3-3 监听器按 blob 重建行表）
                    "id": "e_gold", "type": "custom", "position": {"x": 100, "y": 100},
                    "data": {"label": "金豆一号", "type": "owl:NamedIndividual",
                             "class_label": "理财产品", "properties": {}},
                },
            ],
            "edges": [
                {"id": "edge_seed", "source": "e_gold", "target": "cls_prod",
                 "data": {"label": "rdf:type", "relation": "instance_of"}},
            ],
        },
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    admin_tok = _login(f"{M5_TAG}_admin")
    headers = {"Authorization": f"Bearer {admin_tok}"}
    rv = client.post("/api/admin/mcp-tokens", headers=headers, json={
        "user_id": viewer_u.id, "project_id": project.id, "name": "viewer令牌",
        "can_write": False, "expires_days": 7,
    })
    assert rv.status_code == 201, rv.text
    viewer_token = rv.json()["token"]
    re_ = client.post("/api/admin/mcp-tokens", headers=headers, json={
        "user_id": editor_u.id, "project_id": project.id, "name": "editor令牌", "can_write": True,
    })
    assert re_.status_code == 201, re_.text
    editor_token = re_.json()["token"]

    yield {
        "db": db, "admin": admin, "viewer_u": viewer_u, "editor_u": editor_u,
        "nomod_u": nomod_u, "project": project,
        "admin_tok": admin_tok, "viewer_token": viewer_token, "editor_token": editor_token,
    }

    # 清理：令牌 → 实体 → 项目 → 授权 → 用户
    for u in (admin, viewer_u, editor_u, nomod_u):
        db.query(McpToken).filter(McpToken.user_id == u.id).delete()
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == u.id).delete()
    from app.infrastructure.database import Relation

    db.query(Relation).filter(Relation.project_id == project.id).delete()
    db.query(Entity).filter(Entity.project_id == project.id).delete()
    p2 = db.query(Project).filter(Project.id == project.id).first()
    if p2:
        db.delete(p2)
    for u in (admin, viewer_u, editor_u, nomod_u):
        from app.infrastructure.database import User

        u2 = db.query(User).filter(User.id == u.id).first()
        if u2:
            db.delete(u2)
    db.commit()


# ---------------------------------------------------------------- 传输与协议骨架

def test_get_mcp_returns_405():
    resp = client.get("/mcp")
    assert resp.status_code == 405


def test_bad_json_returns_parse_error():
    resp = client.post("/mcp", content=b"not-json{", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700


def test_missing_jsonrpc_returns_invalid_request():
    resp = client.post("/mcp", json={"id": 1, "method": "ping"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32600


def test_initialize_without_token_succeeds(env):
    resp = _rpc("initialize", {"protocolVersion": "2025-03-26"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["serverInfo"]["name"] == "ontology-platform"
    assert "tools" in body["result"]["capabilities"]


def test_ping_without_token_unauthorized(env):
    resp = _rpc("ping")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == ERR_UNAUTHORIZED


def test_bad_token_format_unauthorized(env):
    resp = _rpc("ping", token="abc123-not-mcp")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == ERR_UNAUTHORIZED


def test_unknown_token_unauthorized(env):
    resp = _rpc("ping", token="sk-mcp-" + "0" * 40)
    assert resp.json()["error"]["code"] == ERR_UNAUTHORIZED


def test_expired_token_unauthorized(env):
    """过期令牌：直接落库一条 expires_at 已过的令牌。"""
    from datetime import datetime, timedelta

    from app.api.mcp_gateway import _hash_token
    from app.infrastructure.database import McpToken

    db = env["db"]
    plaintext = "sk-mcp-" + uuid.uuid4().hex + uuid.uuid4().hex[:32]
    db.add(McpToken(user_id=env["admin"].id, name="expired", token_hash=_hash_token(plaintext),
                    project_id=env["project"].id,
                    expires_at=datetime.utcnow() - timedelta(days=1)))
    db.commit()
    resp = _rpc("ping", token=plaintext)
    assert resp.json()["error"]["code"] == ERR_UNAUTHORIZED


def test_revoked_token_unauthorized(env):
    """撤销令牌（07 §6：泄露可撤销）——revoke 后即失效。"""
    from app.infrastructure.database import McpToken

    db = env["db"]
    headers = {"Authorization": f"Bearer {env['admin_tok']}"}
    rv = client.post("/api/admin/mcp-tokens", headers=headers, json={
        "user_id": env["editor_u"].id, "project_id": env["project"].id,
        "name": "待撤销", "can_write": True,
    })
    tok = rv.json()
    client.delete(f"/api/admin/mcp-tokens/{tok['id']}", headers=headers)
    resp = _rpc("ping", token=tok["token"])
    assert resp.json()["error"]["code"] == ERR_UNAUTHORIZED
    db.rollback()  # 结束模块级会话快照，读到 API 会话提交的行
    assert db.query(McpToken).filter(McpToken.id == tok["id"]).first().revoked_at is not None


def test_unknown_method(env):
    resp = _rpc("tools/unknown", token=env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_METHOD_NOT_FOUND


# ---------------------------------------------------------------- tools/list 权限矩阵

def test_viewer_tools_list_readonly(env):
    resp = _rpc("tools/list", token=env["viewer_token"])
    tools = [t["name"] for t in resp.json()["result"]["tools"]]
    assert len(tools) == READ_TOOLS
    assert "search_entities" in tools and "query_graph" in tools
    assert not {"add_entity", "add_relationship"} & set(tools)


def test_editor_tools_list_has_write(env):
    resp = _rpc("tools/list", token=env["editor_token"])
    tools = [t["name"] for t in resp.json()["result"]["tools"]]
    assert len(tools) == ALL_TOOLS
    assert {"add_entity", "add_relationship"} <= set(tools)


# ---------------------------------------------------------------- 读工具

def test_search_entities_empty_query_invalid_params(env):
    resp = _call("search_entities", {"query": ""}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_INVALID_PARAMS


def test_search_entities_hits_seeded_entity(env):
    resp = _call("search_entities", {"query": "金豆"}, env["viewer_token"])
    result = resp.json()["result"]
    payload = result["content"][0]["text"]
    assert result["isError"] is False
    data = __import__("json").loads(payload)
    uris = [e["uri"] for e in data["entities"]]
    assert f"urn:onto:{env['project'].id}:entity/e_gold" in uris


def test_search_entities_class_filter(env):
    resp = _call("search_entities", {"query": "金豆", "class_label": "不存在类"}, env["viewer_token"])
    data = __import__("json").loads(resp.json()["result"]["content"][0]["text"])
    assert data["count"] == 0


def test_get_entity_with_provenance_shape(env):
    resp = _call("get_entity", {"uri": f"urn:onto:{env['project'].id}:entity/e_gold"}, env["viewer_token"])
    data = __import__("json").loads(resp.json()["result"]["content"][0]["text"])
    assert data["label"] == "金豆一号"
    assert isinstance(data["provenance"], list)


def test_query_graph_create_injection_forbidden(env):
    """只读白名单：CREATE 注入被拒（07 §3.3），并留 blocked 审计。"""
    resp = _call("query_graph", {"cypher": "MATCH (n) CREATE (m:Bad) RETURN m"}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_FORBIDDEN
    _assert_blocked_audit(env, "query_graph")


def test_query_graph_call_injection_forbidden(env):
    resp = _call("query_graph", {"cypher": "CALL db.labels()"}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_FORBIDDEN


def test_query_graph_comment_injection_forbidden(env):
    resp = _call("query_graph", {"cypher": "; MATCH (n) DETACH DELETE n"}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_FORBIDDEN


def test_query_graph_empty_cypher(env):
    resp = _call("query_graph", {"cypher": ""}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_INVALID_PARAMS


def test_unknown_tool(env):
    resp = _call("no_such_tool", {}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_METHOD_NOT_FOUND


# ---------------------------------------------------------------- 写工具权限矩阵

def _assert_blocked_audit(env, tool, user=None):
    from app.infrastructure.database import AuditLog

    db = env["db"]
    db.rollback()  # 结束快照，读 API 会话提交的审计行
    row = (
        db.query(AuditLog)
        .filter(AuditLog.resource_type == "mcp_tool", AuditLog.resource_id == tool,
                AuditLog.user_id == (user or env["viewer_u"]).id)
        .order_by(AuditLog.id.desc()).first()
    )
    assert row is not None, f"工具 {tool} 缺少审计行"
    assert row.detail.get("blocked") is True
    assert row.detail.get("args_digest"), "审计须含 args_digest"


def test_viewer_write_forbidden(env):
    resp = _call("add_entity", {"label": "越权实体", "class_label": "理财产品"}, env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_FORBIDDEN
    _assert_blocked_audit(env, "add_entity")


def test_editor_write_on_published_forbidden(env):
    """已发布项目 MCP 写只读拦截（PUBLISHED_READONLY）。"""
    db = env["db"]
    project = env["project"]
    try:
        project.status = "published"
        db.commit()
        resp = _call("add_entity", {"label": "发布后写入", "class_label": "理财产品"}, env["editor_token"])
        assert resp.json()["error"]["code"] == ERR_FORBIDDEN
        _assert_blocked_audit(env, "add_entity", user=env["editor_u"])
    finally:
        project.status = "draft"
        db.commit()


def test_editor_add_entity_syncs_blob_and_rows(env):
    """写工具走 graph_data blob（S1 权威）→ commit 触发行表双写。"""
    import json as _json

    from app.infrastructure.database import Entity

    db = env["db"]
    project = env["project"]
    label = f"mcp实体{uuid.uuid4().hex[:5]}"
    resp = _call("add_entity", {"label": label, "class_label": "理财产品",
                                "properties": {"风险等级": "R2"}}, env["editor_token"])
    assert resp.json().get("error") is None, resp.text
    out = _json.loads(resp.json()["result"]["content"][0]["text"])
    assert out["created"] is True
    assert out["uri"].startswith(f"urn:onto:{project.id}:entity/mcp_")
    # blob 落盘
    db.rollback()  # 结束快照，读 API 会话提交的 blob 与行表
    db.expire(project)
    gd = db.query(type(project)).filter(type(project).id == project.id).first().graph_data
    node = next(n for n in gd["nodes"] if (n.get("data") or {}).get("label") == label)
    assert node["data"]["type"] == "owl:NamedIndividual"
    assert node["data"]["_source"] == "mcp"
    # 行表双写（M3-3 监听器）
    row = db.query(Entity).filter(Entity.project_id == project.id, Entity.label == label).first()
    assert row is not None, "graph_data blob 提交后行表未同步（M3-3 监听器）"
    assert row.class_label == "理财产品"
    # 同名同类判重
    resp2 = _call("add_entity", {"label": label, "class_label": "理财产品"}, env["editor_token"])
    out2 = _json.loads(resp2.json()["result"]["content"][0]["text"])
    assert out2["created"] is False


def test_editor_add_relationship_and_dedup(env):
    import json as _json

    from app.infrastructure.database import Entity, Relation

    db = env["db"]
    db.rollback()  # 结束快照：上一用例 add_entity 经监听器双写的行表可见
    pid = env["project"].id
    subj = db.query(Entity).filter(Entity.project_id == pid, Entity.label == "金豆一号").first()
    assert subj is not None
    obj = db.query(Entity).filter(Entity.project_id == pid, Entity.class_label == "理财产品",
                                  Entity.label != "金豆一号").first()
    assert obj is not None, "需要第二个实体（add_entity 用例产生）"
    subj_uri, obj_uri = subj.uri, obj.uri  # 行表重建会删除旧 ORM 行，提前取字符串
    pred = f"关联于{uuid.uuid4().hex[:4]}"
    resp = _call("add_relationship", {"subject_uri": subj_uri, "predicate": pred,
                                      "object_uri": obj_uri}, env["editor_token"])
    out = _json.loads(resp.json()["result"]["content"][0]["text"])
    assert out["created"] is True
    db.rollback()  # API 会话在测试快照之后提交，需新开事务才能看到
    rel = db.query(Relation).filter(Relation.project_id == pid, Relation.predicate == pred).first()
    assert rel is not None, "关系行表未同步"
    # 同三元组判重
    resp2 = _call("add_relationship", {"subject_uri": subj_uri, "predicate": pred,
                                       "object_uri": obj_uri}, env["editor_token"])
    out2 = _json.loads(resp2.json()["result"]["content"][0]["text"])
    assert out2["created"] is False


def test_add_relationship_missing_entity_invalid_params(env):
    resp = _call("add_relationship", {"subject_uri": "urn:onto:999999:entity/nope",
                                      "predicate": "p", "object_uri": "urn:onto:999999:entity/n2"},
                 env["editor_token"])
    assert resp.json()["error"]["code"] == ERR_INVALID_PARAMS


# ---------------------------------------------------------------- Resources

def test_resources_list(env):
    resp = _rpc("resources/list", token=env["viewer_token"])
    uris = [r["uri"] for r in resp.json()["result"]["resources"]]
    assert uris == ["ontology://graph/summary", "ontology://schema/info"]


def test_read_resource_graph_summary(env):
    resp = _rpc("resources/read", {"uri": "ontology://graph/summary"}, token=env["viewer_token"])
    text = resp.json()["result"]["contents"][0]["text"]
    data = __import__("json").loads(text)
    assert data["project"]["id"] == env["project"].id
    assert "nodes" in data and "classes" in data


def test_read_resource_unknown_uri(env):
    resp = _rpc("resources/read", {"uri": "ontology://nope"}, token=env["viewer_token"])
    assert resp.json()["error"]["code"] == ERR_INVALID_PARAMS


# ---------------------------------------------------------------- 审计

def test_read_tool_audit_written(env):
    from app.infrastructure.database import AuditLog

    db = env["db"]
    db.rollback()  # 结束快照
    _call("search_entities", {"query": "审计探针"}, env["viewer_token"])
    db.rollback()
    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "mcp.read", AuditLog.user_id == env["viewer_u"].id)
        .order_by(AuditLog.id.desc()).first()
    )
    assert row is not None
    assert row.detail.get("project_id") == env["project"].id
    assert row.detail.get("token_id")


# ---------------------------------------------------------------- 限流

def test_rate_limit(env, monkeypatch):
    """Redis 计数限流（读 60/分→压到 3 验证 -32029）；Redis 不可用则跳过。"""
    import redis as redis_lib

    from app.core.config import settings

    try:
        probe = redis_lib.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        probe.ping()
    except Exception:
        pytest.skip("Redis 不可用，限流链路跳过")
    import app.api.mcp_gateway as gw

    monkeypatch.setattr(gw, "RATE_LIMIT_READ", 3)
    codes = []
    for _ in range(4):
        r = _call("search_entities", {"query": "限流"}, env["editor_token"])
        body = r.json()
        codes.append(body.get("error", {}).get("code") if "error" in body else None)
    assert codes[:3] == [None, None, None]
    assert codes[3] == ERR_RATE_LIMITED


# ---------------------------------------------------------------- 令牌管理 API（03 §16）

def test_token_api_requires_admin(env):
    tok = _login(f"{M5_TAG}_nomod")
    resp = client.get("/api/admin/mcp-tokens", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 403


def test_token_create_requires_mcp_module(env):
    nomod = db_user(env, "nomod")
    resp = client.post("/api/admin/mcp-tokens",
                       headers={"Authorization": f"Bearer {env['admin_tok']}"},
                       json={"user_id": nomod.id, "project_id": env["project"].id, "name": "无模块"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MODULE_NOT_GRANTED"


def db_user(env, key):
    return env[f"{key}_u"]


def test_token_plaintext_only_once_and_hint_masked(env):
    db = env["db"]
    resp = client.post("/api/admin/mcp-tokens",
                       headers={"Authorization": f"Bearer {env['admin_tok']}"},
                       json={"user_id": env["editor_u"].id, "project_id": env["project"].id,
                             "name": "明文一次性", "expires_days": 30})
    body = resp.json()
    assert re.fullmatch(r"sk-mcp-[0-9a-f]{40}", body["token"])
    # token_hint 由 SHA-256 派生（库内不存明文，提示位也不回明文尾）
    assert re.fullmatch(r"sk-mcp-\*\*\*[0-9a-f]{4}", body["token_hint"])
    # 库里不存明文
    from app.api.mcp_gateway import _hash_token
    from app.infrastructure.database import McpToken

    db.rollback()  # 结束快照，读 API 会话提交的行
    row = db.query(McpToken).filter(McpToken.id == body["id"]).first()
    assert row is not None and row.token_hash == _hash_token(body["token"])
    assert row.token_hash != body["token"]


# ---------------------------------------------------------------- R8：query_graph 推理三元组附带

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _FakeTx:
    def __init__(self, rows):
        self._rows = rows

    def run(self, cypher, params):
        return _FakeResult(self._rows)

    def commit(self):
        return None


class _FakeSession:
    def __init__(self, rows):
        self._tx = _FakeTx(rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def begin_transaction(self, timeout=None):
        return self._tx


class _FakeDriver:
    def __init__(self, rows):
        self._rows = rows

    def session(self):
        return _FakeSession(self._rows)


def test_query_graph_inferred_schema_exposed(env):
    """tools/list 中 query_graph 声明 include_inferred 参数。"""
    resp = _rpc("tools/list", {}, token=env["viewer_token"])
    tools = resp.json()["result"]["tools"]
    spec = next(t for t in tools if t["name"] == "query_graph")
    assert "include_inferred" in spec["inputSchema"]["properties"]


def test_query_graph_include_inferred(env, monkeypatch):
    """include_inferred=true：rows（Cypher 结果）与 inferred（推理产物）分字段返回，
    origin 标注推导方式，note 声明非原始事实。"""
    from app.infrastructure.database import ReasoningResult
    import app.api.mcp_gateway as gw

    db = env["db"]
    pid = env["project"].id
    rows = [
        ReasoningResult(project_id=pid, batch_id=f"{M5_TAG}-r8b", source="owlrl",
                        rule_name=None, subject_uri="ex:a", subject_label="个人投资者",
                        predicate_uri="rdf:type", predicate_label="类型",
                        object_uri="ex:c", object_label="投资者"),
        ReasoningResult(project_id=pid, batch_id=f"{M5_TAG}-r8b", source="rule",
                        rule_name="持有即投资", subject_uri="ex:a", subject_label="个人投资者",
                        predicate_uri="ex:p", predicate_label="投资",
                        object_uri="ex:b", object_label="理财产品"),
    ]
    db.add_all(rows)
    db.commit()
    monkeypatch.setattr(gw.neo4j_client, "driver",
                        _FakeDriver([{"n": {"label": "金豆一号"}}]))
    try:
        resp = _call("query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 1",
                                     "include_inferred": True}, env["viewer_token"])
        assert resp.status_code == 200, resp.text
        payload = json.loads(resp.json()["result"]["content"][0]["text"])
        assert payload["rows"] == [{"n": {"label": "金豆一号"}}]
        assert payload["inferred_count"] == 2
        origins = {(i["subject"], i["object"], i["origin"]) for i in payload["inferred"]}
        assert ("个人投资者", "投资者", "蕴含推理（owlrl）") in origins
        assert ("个人投资者", "理财产品", "规则「持有即投资」") in origins
        assert "非原始事实记载" in payload["inference_note"]

        # 默认关闭：不带 include_inferred 时无 inferred 字段
        resp2 = _call("query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 1"},
                      env["viewer_token"])
        payload2 = json.loads(resp2.json()["result"]["content"][0]["text"])
        assert "inferred" not in payload2 and "inference_note" not in payload2
    finally:
        db.query(ReasoningResult).filter(ReasoningResult.project_id == pid).delete(
            synchronize_session=False)
        db.commit()
