# M3-1 真实 API 验证（对照 08 §2 验收门槛）
# 1) 多格式上传落 MinIO 且元数据入库  2) 秒传 409 整批拒绝  3) 302 预签名可真实取回字节
# 4) 软删后可重传  5) clear-all 收编  6) admin 直通 + 非成员 403
import io
import sys
import uuid

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")

import bcrypt
import requests

BASE = "http://127.0.0.1:3001"
TAG = f"m3r{uuid.uuid4().hex[:6]}"

from app.infrastructure.database import (  # noqa: E402
    Project, SessionLocal, UploadedDocument, User, UserModuleGrant,
)
from app.infrastructure.minio_client import get_minio_client  # noqa: E402
from app.core.config import settings  # noqa: E402

db = SessionLocal()
admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
             hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
user = User(username=f"{TAG}_user", role="user", is_active=True,
            hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
db.add_all([admin, user]); db.commit(); db.refresh(admin); db.refresh(user)
db.add(UserModuleGrant(user_id=user.id, module_code="documents", allowed=1, granted_by=admin.id))
db.commit()
proj = Project(name=f"{TAG}_proj", owner_id=user.id)
db.add(proj); db.commit(); db.refresh(proj)
proj2 = Project(name=f"{TAG}_other", owner_id=admin.id)  # admin 的项目：验证非成员 403
db.add(proj2); db.commit(); db.refresh(proj2)

minio_keys = []


def login(u):
    r = requests.post(f"{BASE}/api/auth/login", data={"username": u, "password": "Passw0rd!123"}, timeout=15)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


ok = []


def step(name, cond, detail=""):
    ok.append((name, cond))
    print(("PASS" if cond else "FAIL"), name, detail)


try:
    ut = login(f"{TAG}_user")
    at = login(f"{TAG}_admin")

    # 1) 多格式上传（txt/md/csv）
    files = [
        ("files", ("报告.txt", io.BytesIO("知识图谱本体测试文档。".encode() * 40), "text/plain")),
        ("files", ("notes.md", io.BytesIO("# 标题\n- 要点一\n- 要点二\n".encode() * 20), "text/markdown")),
        ("files", ("data.csv", io.BytesIO("name,age\n张三,30\n李四,25\n".encode() * 10), "text/csv")),
    ]
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/upload", headers=ut, files=files, timeout=60)
    step("多格式上传 200", r.status_code == 200, r.text[:120])
    saved = r.json()["saved"]
    step("3 个文档入库", len(saved) == 3)
    step("新字段齐全", all(d["storage_key"] and len(d["sha256"]) == 64 and d["parse_status"] == "uploaded" for d in saved))
    mc = get_minio_client()
    db.commit()  # 结束 REPEATABLE_READ 快照，读取 API 提交的行
    for d in saved:
        minio_keys.append((settings.MINIO_BUCKET_UPLOADS, d["storage_key"]))
        st = mc.stat_object(settings.MINIO_BUCKET_UPLOADS, d["storage_key"])
        row = db.query(UploadedDocument).filter(UploadedDocument.id == d["id"]).first()
        step(f"MinIO 对象字节一致 {d['filename']}", st is not None and st["size"] == d["file_size"] == row.file_size,
             f"minio={st['size'] if st else None}")

    # 2) 秒传 409（整批拒绝：同批一个重复 + 一个新内容）
    r = requests.post(
        f"{BASE}/api/projects/{proj.id}/documents/upload", headers=ut,
        files=[("files", ("dup.txt", io.BytesIO("知识图谱本体测试文档。".encode() * 40), "text/plain")),
               ("files", ("new.txt", io.BytesIO(b"brand new content xyz"), "text/plain"))], timeout=60)
    err = r.json().get("error", {})
    step("重复+新内容 整批 409", r.status_code == 409 and err.get("code") == "DUPLICATE_UPLOAD", str(err)[:150])
    step("409 带既有 doc", bool(err.get("detail", {}).get("duplicates")))
    dup_names = err.get("detail", {}).get("uploaded_filenames", [])
    step("409 指明重复文件", "dup.txt" in dup_names and "new.txt" not in dup_names, str(dup_names))
    cnt = db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id,
                                            UploadedDocument.deleted_at.is_(None)).count()
    step("整批拒绝未落任何行", cnt == 3, f"count={cnt}")

    # 3) 302 预签名 + 真实取回字节比对 sha
    rl = requests.get(f"{BASE}/api/projects/{proj.id}/documents", headers=ut, timeout=15)
    docs = rl.json()["documents"]
    step("列表含 chunk_count/新字段", all("chunk_count" in d and "storage_key" in d for d in docs))
    d0 = docs[0]
    rd = requests.get(f"{BASE}/api/projects/{proj.id}/documents/{d0['id']}/download",
                      headers=ut, timeout=15, allow_redirects=False)
    step("下载 302 预签名", rd.status_code == 302 and "X-Amz-Signature" in rd.headers.get("location", ""),
         rd.headers.get("location", "")[:80])
    got = requests.get(rd.headers["location"], timeout=30).content
    import hashlib
    step("预签名 URL 取回字节 sha 一致", hashlib.sha256(got).hexdigest() == d0["sha256"], f"{len(got)} bytes")

    # 4) 软删 → 列表不含 → 同内容可重传
    r = requests.delete(f"{BASE}/api/projects/{proj.id}/documents/{d0['id']}", headers=ut, timeout=15)
    step("软删 200", r.status_code == 200)
    db.commit()
    row = db.query(UploadedDocument).filter(UploadedDocument.id == d0["id"]).first()
    step("行仍在且 deleted_at 非空", row is not None and row.deleted_at is not None)
    rl2 = requests.get(f"{BASE}/api/projects/{proj.id}/documents", headers=ut, timeout=15)
    step("列表默认不含软删", d0["id"] not in [x["id"] for x in rl2.json()["documents"]])
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/upload", headers=ut,
                      files=[("files", (d0["filename"], io.BytesIO(got), "text/plain"))], timeout=60)
    step("软删后同内容可重传", r.status_code == 200, r.text[:100])
    if r.status_code == 200:
        minio_keys.append((settings.MINIO_BUCKET_UPLOADS, r.json()["saved"][0]["storage_key"]))

    # 5) clear-all（收编后的软删语义）
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/clear-all", headers=ut, timeout=15)
    step("clear-all 200", r.status_code == 200, r.text[:100])
    rl3 = requests.get(f"{BASE}/api/projects/{proj.id}/documents", headers=ut, timeout=15)
    step("clear 后列表为空", rl3.json()["total"] == 0, f"total={rl3.json().get('total')}")

    # 6) admin 直通 / 非成员 403
    ra = requests.get(f"{BASE}/api/projects/{proj.id}/documents", headers=at, timeout=15)
    step("admin 直通列表 200", ra.status_code == 200)
    ru = requests.get(f"{BASE}/api/projects/{proj2.id}/documents", headers=ut, timeout=15)
    body = ru.json()
    code = body.get("error", {}).get("code") or body.get("detail")
    step("非成员 403 PROJECT_FORBIDDEN", ru.status_code == 403 and code == "PROJECT_FORBIDDEN", str(body)[:120])
    # 旧路由已收编：响应是新形状（status/total）
    step("旧 ontology 文档路由已收编（新形状）", "status" in rl3.json() and "total" in rl3.json())
finally:
    (db.query(UploadedDocument).filter(UploadedDocument.project_id.in_([proj.id, proj2.id]))
     .delete(synchronize_session=False))
    for p in (proj, proj2):
        row = db.query(Project).filter(Project.id == p.id).first()
        if row:
            db.delete(row)
    db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).delete()
    for u in (admin, user):
        row = db.query(User).filter(User.id == u.id).first()
        if row:
            db.delete(row)
    db.commit()
    db.close()
    for b, k in minio_keys:
        get_minio_client().remove_object(b, k)

fails = [n for n, c in ok if not c]
print(f"\n== M3-1 真实 API 验证: {len(ok) - len(fails)}/{len(ok)} 通过 ==")
if fails:
    print("FAIL:", fails)
    sys.exit(1)
