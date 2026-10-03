# tests/test_documents.py - M3-1 文档存储面契约（docs/design/03 §7 / 08 §2 M3-1 验收）
# 覆盖：多文件上传落 MinIO + 元数据入库 / sha256 同项目 409 秒传 / 列表 / 软删 / 预签名 / 项目角色隔离
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import Project, UploadedDocument, User
from main import app

client = TestClient(app)
TAG = f"m3t{uuid.uuid4().hex[:6]}"


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

    from app.infrastructure.database import UserModuleGrant

    admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    user = User(username=f"{TAG}_user", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add_all([admin, user])
    db.commit()
    db.refresh(admin)
    db.refresh(user)
    # documents 模块非 default_on（README §4.1），普通用户需显式授权——这正是 M1 矩阵语义
    db.add(UserModuleGrant(user_id=user.id, module_code="documents", allowed=1,
                           granted_by=admin.id))
    db.commit()
    proj = Project(name=f"{TAG}_proj", description="M3-1 文档测试项目", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield {"admin": admin, "user": user, "proj": proj}
    # teardown：清文档（硬删，含 MinIO 对象跳过——tag 化 key 不碍事）+ 授权 + 项目 + 用户
    (db.query(UploadedDocument)
     .filter(UploadedDocument.project_id == proj.id)
     .delete(synchronize_session=False))
    p = db.query(Project).filter(Project.id == proj.id).first()
    if p:
        db.delete(p)
    db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).delete()
    for u in (admin, user):
        row = db.query(User).filter(User.id == u.id).first()
        if row:
            db.delete(row)
    db.commit()


def _upload(token, proj_id, files, expect=200):
    data = [("files", (name, io.BytesIO(content), "application/octet-stream"))
            for name, content in files]
    resp = client.post(f"/api/projects/{proj_id}/documents/upload",
                       headers={"Authorization": f"Bearer {token}"},
                       files=data)
    assert resp.status_code == expect, resp.text
    return resp


def test_upload_and_metadata(env, db):
    token = _login(env["user"].username)["access_token"]
    proj = env["proj"].id
    body = "星辰科技成立于2020年，主营人工智能产品。" * 10
    resp = _upload(token, proj, [("report.txt", body.encode()), ("notes.md", b"# title\nhello m3")])
    out = resp.json()
    assert len(out["saved"]) == 2
    doc = out["saved"][0]
    assert doc["parse_status"] == "uploaded"
    assert doc["storage_key"] and doc["storage_key"].startswith(f"{proj}/")
    assert doc["sha256"] and len(doc["sha256"]) == 64
    # 元数据确实入库（先结束本会话 REPEATABLE_READ 快照，才能看到 API 会话提交的行）
    db.commit()
    row = db.query(UploadedDocument).filter(UploadedDocument.id == doc["id"]).first()
    assert row is not None and row.storage_key == doc["storage_key"]
    # MinIO 对象真实存在
    from app.core.config import settings
    from app.infrastructure.minio_client import get_minio_client
    stat = get_minio_client().stat_object(settings.MINIO_BUCKET_UPLOADS, row.storage_key)
    assert stat is not None and stat["size"] == row.file_size


def test_duplicate_upload_409(env):
    token = _login(env["user"].username)["access_token"]
    proj = env["proj"].id
    content = b"same content for dedup test"
    _upload(token, proj, [("first.txt", content)])
    resp = _upload(token, proj, [("second.txt", content)], expect=409)
    err = resp.json()["error"]
    assert err["code"] == "DUPLICATE_UPLOAD"
    assert err["detail"]["duplicates"], "409 响应必须带既有 doc"
    # 不同项目同内容不拦截（秒传是项目内判重）
    other = Project(name=f"{TAG}_proj2", owner_id=env["user"].id)
    db2 = None
    from app.infrastructure.database import SessionLocal
    db2 = SessionLocal()
    db2.add(other)
    db2.commit()
    db2.refresh(other)
    try:
        _upload(token, other.id, [("third.txt", content)])
    finally:
        (db2.query(UploadedDocument).filter(UploadedDocument.project_id == other.id)
         .delete(synchronize_session=False))
        db2.query(Project).filter(Project.id == other.id).delete()
        db2.commit()
        db2.close()


def test_list_and_soft_delete(env, db):
    token = _login(env["user"].username)["access_token"]
    proj = env["proj"].id
    resp = client.get(f"/api/projects/{proj}/documents",
                      headers={"Authorization": f"Bearer {token}"})
    docs = resp.json()["documents"]
    assert len(docs) >= 2
    doc_id = docs[0]["id"]
    # 软删
    resp = client.delete(f"/api/projects/{proj}/documents/{doc_id}",
                         headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    db.commit()  # 刷新快照
    row = db.query(UploadedDocument).filter(UploadedDocument.id == doc_id).first()
    assert row.deleted_at is not None
    # 列表默认不含软删
    resp = client.get(f"/api/projects/{proj}/documents",
                      headers={"Authorization": f"Bearer {token}"})
    assert doc_id not in [d["id"] for d in resp.json()["documents"]]
    # 删除后同内容可再传（判重只看未删）
    _upload(token, proj, [("re-upload.txt", docs[0]["filename"].encode() or b"x")])


def test_download_presigned(env):
    token = _login(env["user"].username)["access_token"]
    proj = env["proj"].id
    resp = client.get(f"/api/projects/{proj}/documents",
                      headers={"Authorization": f"Bearer {token}"})
    doc_id = resp.json()["documents"][0]["id"]
    resp = client.get(f"/api/projects/{proj}/documents/{doc_id}/download",
                      headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
    assert resp.status_code == 302
    assert "ontology-uploads" in resp.headers["location"]
    assert "X-Amz-Signature" in resp.headers["location"]


def test_project_role_isolation(env):
    """非成员连 viewer 都不是 → 403 PROJECT_FORBIDDEN；admin 直通。"""
    user_token = _login(env["user"].username)["access_token"]
    resp = client.get(f"/api/projects/{env['proj'].id}/documents",
                      headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200  # owner（members 行缺失时按 owner_id 兜底）
    # 另一个非成员用户 → 403
    other_token = _login(env["admin"].username)["access_token"]
    # admin 直通项目角色 → 200
    resp = client.get(f"/api/projects/{env['proj'].id}/documents",
                      headers={"Authorization": f"Bearer {other_token}"})
    assert resp.status_code == 200
    # 真正的非成员：user 调一个 admin 拥有且无成员的项目
    from app.infrastructure.database import Project, SessionLocal
    db2 = SessionLocal()
    proj2 = Project(name=f"{TAG}_proj3", owner_id=env["admin"].id)
    db2.add(proj2)
    db2.commit()
    db2.refresh(proj2)
    try:
        resp = client.get(f"/api/projects/{proj2.id}/documents",
                          headers={"Authorization": f"Bearer {user_token}"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PROJECT_FORBIDDEN"
    finally:
        db2.query(Project).filter(Project.id == proj2.id).delete()
        db2.commit()
        db2.close()


def test_chunks_empty_before_parse(env):
    """未解析文档（auto_parse=false 上传）chunks 为空——M3-1 占位测试收编为端点契约。"""
    token = _login(env["user"].username)["access_token"]
    proj = env["proj"].id
    resp = client.post(f"/api/projects/{proj}/documents/upload?auto_parse=false",
                       headers={"Authorization": f"Bearer {token}"},
                       files=[("files", ("pre.txt", io.BytesIO("尚未解析的文档内容".encode()), "text/plain"))])
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["saved"][0]["id"]
    resp = client.get(f"/api/projects/{proj}/documents/{doc_id}/chunks",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["chunks"] == []
    assert resp.json()["next_cursor"] is None
