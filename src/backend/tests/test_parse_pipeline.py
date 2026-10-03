# tests/test_parse_pipeline.py - M3-2 解析切片管道契约（03 §7 / 04 §2）
# Celery eager 模式（任务同进程执行，进度写真实 Redis）；MinIO/MySQL 真连（沿用 M3-1 套路）。
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import DocumentChunk, Project, UploadedDocument, User
from main import app

client = TestClient(app)
TAG = f"m3p{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    import bcrypt

    from app.infrastructure.database import UserModuleGrant

    admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    user = User(username=f"{TAG}_user", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add_all([admin, user])
    db.commit()
    db.refresh(admin)
    db.refresh(user)
    db.add(UserModuleGrant(user_id=user.id, module_code="documents", allowed=1,
                           granted_by=admin.id))
    db.commit()
    proj = Project(name=f"{TAG}_proj", description="M3-2 管道测试", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    # Celery eager：delay() 同进程直跑（真实 Redis 进度键仍生效）
    from app.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False  # 失败走任务终态而非抛测试端
    yield {"admin": admin, "user": user, "proj": proj}
    celery_app.conf.task_always_eager = False
    # teardown：chunks + docs（含 MinIO 对象）+ 授权 + 项目 + 用户
    docs = db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id).all()
    for d in docs:
        if d.storage_key:
            from app.core.config import settings
            from app.infrastructure.minio_client import get_minio_client

            mc = get_minio_client()
            mc.remove_object(settings.MINIO_BUCKET_UPLOADS, d.storage_key)
            if d.text_key:
                mc.remove_object(settings.MINIO_BUCKET_PARSED, d.text_key)
    (db.query(UploadedDocument)
     .filter(UploadedDocument.project_id == proj.id).delete(synchronize_session=False))
    p = db.query(Project).filter(Project.id == proj.id).first()
    if p:
        db.delete(p)
    db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).delete()
    for u in (admin, user):
        row = db.query(User).filter(User.id == u.id).first()
        if row:
            db.delete(row)
    db.commit()


def _login(username, password="Passw0rd!123"):
    resp = client.post("/api/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _upload(token, proj_id, files, auto_parse=False):
    data = [("files", (name, io.BytesIO(content), "application/octet-stream"))
            for name, content in files]
    resp = client.post(f"/api/projects/{proj_id}/documents/upload?auto_parse={auto_parse}",
                       headers={"Authorization": f"Bearer {token}"}, files=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _wait_parsed(db, doc_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.commit()
        row = db.query(UploadedDocument).filter(UploadedDocument.id == doc_id).first()
        if row is not None and row.parse_status in ("parsed", "failed"):
            return row
        time.sleep(0.5)
    raise AssertionError(f"文档 {doc_id} 解析超时")


def test_upload_auto_parse_pipeline(env, db):
    """上传 auto_parse → parse 队列（eager）→ parsed + chunks + text_key + MinIO 全文对象。"""
    token = _login(env["user"].username)
    proj = env["proj"].id
    body = ("星辰科技是一家从事人工智能的中国公司。它成立于2020年。"
            "主营产品包括知识图谱平台与本体编辑器。" * 100)
    out = _upload(token, proj, [("报告.txt", body.encode()),
                                ("notes.md", ("# 标题\n- 要点一\n- 要点二\n" * 20).encode())],
                  auto_parse=True)
    saved = out["saved"]
    assert len(saved) == 2
    assert all(d["task_id"] for d in saved), "auto_parse 响应必须带 task_id"

    d0 = saved[0]
    row = _wait_parsed(db, d0["id"])
    assert row.parse_status == "parsed", row.parse_error
    assert row.parse_backend in ("native", "docling", "docling_ocr")
    assert row.language in ("zh", "mixed")
    assert row.text_key == f"{proj}/{row.id}.md"
    # 兼容期双写：text_content 同步回填（旧 extract-* 链路依赖）
    assert row.text_content and len(row.text_content) > 100
    # chunks 落库
    db.commit()
    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id == row.id)
              .order_by(DocumentChunk.chunk_index).all())
    assert len(chunks) >= 2
    assert chunks[0].chunk_index == 0 and chunks[0].token_count > 0
    # 偏移可回溯原文
    src = row.text_content
    assert src[chunks[0].char_start:chunks[0].char_start + len(chunks[0].text)] == chunks[0].text
    # MinIO parsed 对象存在且内容一致
    from app.core.config import settings
    from app.infrastructure.minio_client import get_minio_client

    st = get_minio_client().stat_object(settings.MINIO_BUCKET_PARSED, row.text_key)
    assert st is not None and st["size"] == len(row.text_content.encode("utf-8"))
    # 列表端点带 chunk_count
    rl = client.get(f"/api/projects/{proj}/documents", headers={"Authorization": f"Bearer {token}"})
    listing = {d["id"]: d for d in rl.json()["documents"]}
    assert listing[row.id]["chunk_count"] == len(chunks)
    assert listing[row.id]["parse_status"] == "parsed"


def test_sse_parse_events_stream(env, db):
    """parse-events SSE：ticket 鉴权 + 终态事件转发（03 §7）。"""
    token = _login(env["user"].username)
    proj = env["proj"].id
    out = _upload(token, proj, [("sse_doc.txt", ("SSE 进度流测试文档。" * 200).encode())], auto_parse=False)
    doc_id = out["saved"][0]["id"]
    # 手动派发（eager 内联执行，进度写 Redis）
    rp = client.post(f"/api/projects/{proj}/documents/{doc_id}/parse",
                     headers={"Authorization": f"Bearer {token}"}, json={})
    assert rp.status_code == 200, rp.text
    task_id = rp.json()["task_id"]
    _wait_parsed(db, doc_id)

    # ticket 鉴权：无 ticket → 401
    r = client.get(f"/api/projects/{proj}/documents/parse-events?task_id={task_id}&ticket=bad")
    assert r.status_code == 401
    # 有效 ticket → 收到终态事件
    tk = client.get(f"/api/auth/sse-ticket?task_id={task_id}",
                    headers={"Authorization": f"Bearer {token}"})
    assert tk.status_code == 200, tk.text
    ticket = tk.json()["ticket"]
    r = client.get(f"/api/projects/{proj}/documents/parse-events?task_id={task_id}&ticket={ticket}")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert '"status": "completed"' in r.text or '"status":"completed"' in r.text
    assert "chunks" in r.text
    # ticket 一次性：第二个连接用同一 ticket → 401
    tk2 = client.get(f"/api/auth/sse-ticket?task_id={task_id}",
                     headers={"Authorization": f"Bearer {token}"}).json()["ticket"]
    client.get(f"/api/projects/{proj}/documents/parse-events?task_id={task_id}&ticket={tk2}")
    r = client.get(f"/api/projects/{proj}/documents/parse-events?task_id={task_id}&ticket={tk2}")
    assert r.status_code == 401


def test_reparse_validation_and_backend_param(env, db):
    token = _login(env["user"].username)
    proj = env["proj"].id
    out = _upload(token, proj, [("reparse.txt", ("重解析参数校验文档。" * 100).encode())], auto_parse=False)
    doc_id = out["saved"][0]["id"]
    # 非法 backend → 400
    r = client.post(f"/api/projects/{proj}/documents/{doc_id}/parse",
                    headers={"Authorization": f"Bearer {token}"}, json={"backend": "bogus"})
    assert r.status_code == 400
    # 指定 native backend 重解析 → 成功
    r = client.post(f"/api/projects/{proj}/documents/{doc_id}/parse",
                    headers={"Authorization": f"Bearer {token}"}, json={"backend": "native"})
    assert r.status_code == 200, r.text
    row = _wait_parsed(db, doc_id)
    assert row.parse_status == "parsed" and row.parse_backend == "native"
    # 不存在的文档 → 404
    r = client.post(f"/api/projects/{proj}/documents/999999/parse",
                    headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code == 404


def test_legacy_parse_files_shim(env, db):
    """旧 parse-files shim：同步解析 + 旧响应契约（text_content/saved_documents）+ 失败隔离。"""
    token = _login(env["user"].username)
    proj = env["proj"].id
    data = [("files", ("旧链路.txt", io.BytesIO(("旧前端同步解析链路测试。" * 100).encode()), "text/plain")),
            ("files", ("旧链路2.md", io.BytesIO(("# 次文档\n内容行\n" * 50).encode()), "text/markdown"))]
    r = client.post(f"/api/projects/{proj}/parse-files?save_documents=true",
                    headers={"Authorization": f"Bearer {token}"}, files=data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["text_content"]) > 200
    assert len(body["saved_documents"]) == 2
    ids = [d["id"] for d in body["saved_documents"]]
    db.commit()
    rows = db.query(UploadedDocument).filter(UploadedDocument.id.in_(ids)).all()
    assert all(row.parse_status == "parsed" for row in rows)
    # shim 走了新管道：chunks 已落库
    counts = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id.in_(ids)).count())
    assert counts >= 2
    # 秒传复用：同内容再传不新增行
    r2 = client.post(f"/api/projects/{proj}/parse-files",
                     headers={"Authorization": f"Bearer {token}"},
                     files=[("files", ("旧链路.txt", io.BytesIO(("旧前端同步解析链路测试。" * 100).encode()), "text/plain"))])
    assert r2.status_code == 200
    assert ids[0] in [d["id"] for d in r2.json()["saved_documents"]]


def test_chunks_cursor_pagination(env, db):
    """chunks 游标分页在真实分块数据上工作（M3-1 空占位的端到端闭环）。"""
    token = _login(env["user"].username)
    proj = env["proj"].id
    out = _upload(token, proj, [("pager.txt", ("游标分页分块测试文档。" * 500).encode())], auto_parse=True)
    doc_id = out["saved"][0]["id"]
    _wait_parsed(db, doc_id)
    db.commit()
    total = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
    assert total >= 1
    r = client.get(f"/api/projects/{proj}/documents/{doc_id}/chunks?limit=1",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    page1 = r.json()
    assert len(page1["chunks"]) == 1
    if total > 1:
        assert page1["next_cursor"] is not None
        r2 = client.get(
            f"/api/projects/{proj}/documents/{doc_id}/chunks?limit=200&cursor={page1['next_cursor']}",
            headers={"Authorization": f"Bearer {token}"})
        rest = r2.json()
        assert len(rest["chunks"]) == total - 1
        assert rest["next_cursor"] is None
