# tests/test_qa_upgrade.py - M5 本体问答升级契约（docs/design/07 §4 / 03 §15，08 §2 M5）
# 覆盖：[M:qa] 模块守卫、[Pub+] 已发布项目开放语义、_milvus_expr 强制 project_id 与域值消毒、
#       非流式聚合响应 + 历史/溯源端点、SSE 事件流（token→sources→done）。
# HTTP 层用 monkeypatch 替换检索/LLM（离线确定性）；_milvus_expr 为纯单测。
import json
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.adapters.retrieval import QAResult, RetrievalQuery, RetrievedSource, _milvus_expr
from main import app

client = TestClient(app)

M5Q_TAG = f"m5q{uuid.uuid4().hex[:6]}"


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


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _stub_result(**overrides) -> QAResult:
    base = dict(
        answer="根据引用，该产品风险等级为 R2 [1]。",
        sources=[RetrievedSource(doc_file="产品说明书.pdf", quote="本产品风险等级为 R2。",
                                 score=0.87, source_document_id=None)],
        reasoning_path=["向量召回 + 图扩展融合得 1 条引用", "LLM 依据引用生成接地答案"],
        confidence=0.83, model="stub-model", latency_ms=5,
    )
    base.update(overrides)
    return QAResult(**base)


@pytest.fixture()
def mock_llm(monkeypatch):
    """替换 QA 端点的检索与 LLM 构建（离线确定性；真实链路由 integration 用例覆盖）。"""
    monkeypatch.setattr("app.api.qa._build_llm", lambda db, pid: None)
    monkeypatch.setattr("app.api.qa.query_with_reasoning", lambda query, db=None, llm=None: _stub_result())

    def _fake_iter(query, db=None, llm=None):
        yield ("meta", {"source_count": 1})
        yield ("token", "根据引用，")
        yield ("token", "该产品风险等级为 R2 [1]。")
        yield ("done", _stub_result())

    monkeypatch.setattr("app.api.qa.iter_answer", _fake_iter)


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    """qa 模块用户 / 无 qa 模块用户 + 草稿项目（他人所有）/ 已发布项目。"""
    from app.infrastructure.database import Project, QaHistory, UserModuleGrant

    admin = _mk_user(db, f"{M5Q_TAG}_admin", role="admin")
    qa_user = _mk_user(db, f"{M5Q_TAG}_qa")
    noqa_user = _mk_user(db, f"{M5Q_TAG}_noqa")
    db.add(UserModuleGrant(user_id=qa_user.id, module_code="qa", allowed=True, granted_by=admin.id))
    db.commit()

    draft = Project(name=f"{M5Q_TAG}_草稿", owner_id=admin.id, status="draft")
    published = Project(name=f"{M5Q_TAG}_已发布", owner_id=admin.id, status="published")
    db.add_all([draft, published])
    db.commit()
    db.refresh(draft)
    db.refresh(published)

    admin_tok = _login(f"{M5Q_TAG}_admin")
    qa_tok = _login(f"{M5Q_TAG}_qa")
    noqa_tok = _login(f"{M5Q_TAG}_noqa")

    yield {"db": db, "admin": admin, "qa_user": qa_user, "noqa_user": noqa_user,
           "draft": draft, "published": published,
           "admin_tok": admin_tok, "qa_tok": qa_tok, "noqa_tok": noqa_tok}

    db.query(QaHistory).filter(QaHistory.project_id.in_([draft.id, published.id])).delete(synchronize_session=False)
    for p in (draft, published):
        p2 = db.query(Project).filter(Project.id == p.id).first()
        if p2:
            db.delete(p2)
    from app.infrastructure.database import User

    for u in (admin, qa_user, noqa_user):
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == u.id).delete()
        u2 = db.query(User).filter(User.id == u.id).first()
        if u2:
            db.delete(u2)
    db.commit()


# ---------------------------------------------------------------- _milvus_expr 纯单测

def test_milvus_expr_forces_project_id():
    """向量召回表达式强制 project_id 限定（07 §4 权限过滤第一道闸）。"""
    expr = _milvus_expr(RetrievalQuery(project_id=42, question="风险等级"))
    assert "project_id == 42" in expr
    # 不可能查到别的项目
    assert "project_id == 41" not in expr and "project_id != " not in expr


def test_milvus_expr_domain_value_sanitized():
    """域过滤值去引号，阻断表达式注入。"""
    q = RetrievalQuery(project_id=1, question="q", knowledge_domain='金融" || project_id != 1 || "')
    expr = _milvus_expr(q)
    # 值内引号被剥掉，表达式只剩包裹域值的两个双引号（注入无法逃出字符串字面量）
    assert expr.count('"') == 2
    assert "project_id == 1" in expr


def test_milvus_expr_no_domain():
    expr = _milvus_expr(RetrievalQuery(project_id=3, question="q"))
    assert expr == "project_id == 3"


# ---------------------------------------------------------------- [M:qa] / [Pub+] 守卫

def test_qa_requires_qa_module(env, mock_llm):
    """未开通 qa 模块 → 403 MODULE_NOT_GRANTED。"""
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["noqa_tok"]),
                       json={"question": "风险等级", "stream": False})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "MODULE_NOT_GRANTED"


def test_qa_history_requires_qa_module(env):
    resp = client.get(f"/api/projects/{env['draft'].id}/qa/history", headers=_auth(env["noqa_tok"]))
    assert resp.status_code == 403


def test_qa_unpublished_non_member_forbidden(env, mock_llm):
    """未发布项目：非成员即使有 qa 模块也 → 403。"""
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["qa_tok"]),
                       json={"question": "风险等级", "stream": False})
    assert resp.status_code == 403


def test_qa_published_open_to_module_users(env, mock_llm):
    """已发布项目对所有 qa 模块用户开放（[Pub+] 语义）。"""
    resp = client.post(f"/api/projects/{env['published'].id}/qa", headers=_auth(env["qa_tok"]),
                       json={"question": "该产品的风险等级是什么？", "stream": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("answer", "sources", "confidence", "reasoning_path", "model", "latency_ms", "history_id"):
        assert key in body
    assert body["history_id"]


# ---------------------------------------------------------------- 非流式 + 历史 + 溯源

def test_qa_nonstream_saves_history(env, mock_llm):
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["admin_tok"]),
                       json={"question": "产品风险等级？", "stream": False})
    assert resp.status_code == 200
    hid = resp.json()["history_id"]
    hist_list = client.get(f"/api/projects/{env['draft'].id}/qa/history?limit=5", headers=_auth(env["admin_tok"]))
    ids = [i["id"] for i in hist_list.json()["items"]]
    assert hid in ids
    item = next(i for i in hist_list.json()["items"] if i["id"] == hid)
    assert item["question"] == "产品风险等级？"
    assert item["sources"]
    # 溯源详情端点
    detail = client.get(f"/api/projects/{env['draft'].id}/qa/sources/{hid}", headers=_auth(env["admin_tok"]))
    assert detail.status_code == 200
    assert detail.json()["sources"][0]["quote"] == "本产品风险等级为 R2。"
    # 其他项目的 ref_id → 404（跨项目隔离）
    wrong = client.get(f"/api/projects/{env['published'].id}/qa/sources/{hid}", headers=_auth(env["admin_tok"]))
    assert wrong.status_code == 404


def test_qa_empty_question_rejected(env):
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["admin_tok"]),
                       json={"question": "   ", "stream": False})
    assert resp.status_code in (400, 422)


def test_qa_project_not_found(env):
    resp = client.post("/api/projects/99999999/qa", headers=_auth(env["admin_tok"]),
                       json={"question": "q", "stream": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------- SSE 流式

def _parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((event, data))
    return events


def test_qa_sse_event_stream(env, mock_llm):
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["admin_tok"]),
                       json={"question": "流式风险等级？", "stream": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    # 事件序：token（增量）→ sources → done
    assert "token" in kinds
    assert kinds[-1] == "done"
    assert kinds.index("sources") < kinds.index("done")
    deltas = "".join(d["delta"] for e, d in events if e == "token")
    assert "风险等级" in deltas
    done = dict(events)["done"]
    assert done["history_id"]
    assert done["confidence"] == pytest.approx(0.83)
    sources_ev = dict(events)["sources"]
    assert sources_ev["sources"][0]["doc_file"] == "产品说明书.pdf"
    # 流式也落历史
    hist = client.get(f"/api/projects/{env['draft'].id}/qa/history", headers=_auth(env["admin_tok"]))
    assert done["history_id"] in [i["id"] for i in hist.json()["items"]]


def test_qa_sse_error_event_on_failure(env, monkeypatch):
    """生成器抛异常 → SSE error 事件收尾（HTTP 200 已发出）。"""
    def _boom_iter(query, db=None, llm=None):
        yield ("meta", {"source_count": 0})
        raise RuntimeError("检索后端炸了")

    monkeypatch.setattr("app.api.qa._build_llm", lambda db, pid: None)
    monkeypatch.setattr("app.api.qa.iter_answer", _boom_iter)
    resp = client.post(f"/api/projects/{env['draft'].id}/qa", headers=_auth(env["admin_tok"]),
                       json={"question": "异常问题", "stream": True})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert "检索后端炸了" in events[-1][1]["message"]
