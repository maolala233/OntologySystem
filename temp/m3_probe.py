# M3-1 调试探针：复现 fixture → 上传 → stat → admin 访问 → 软删 → 查行
import io
import sys
import uuid

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")

import bcrypt
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.infrastructure.database import Project, SessionLocal, UploadedDocument, User, UserModuleGrant

TAG = f"m3p{uuid.uuid4().hex[:6]}"
client = TestClient("main")  # placeholder, replaced below


def main():
    from main import app  # noqa
    globals()["client"] = TestClient(app)

    db = SessionLocal()
    admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    user = User(username=f"{TAG}_user", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add_all([admin, user])
    db.commit()
    db.refresh(admin)
    db.refresh(user)
    db.add(UserModuleGrant(user_id=user.id, module_code="documents", allowed=1, granted_by=admin.id))
    db.commit()
    proj = Project(name=f"{TAG}_proj", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    try:
        def login(u):
            r = client.post("/api/auth/login", data={"username": u, "password": "Passw0rd!123"})
            return r.json()["access_token"]

        ut = login(f"{TAG}_user")
        at = login(f"{TAG}_admin")

        body = "星辰科技成立于2020年，主营人工智能产品。" * 10
        data = [("files", ("report.txt", io.BytesIO(body.encode()), "application/octet-stream"))]
        r = client.post(f"/api/projects/{proj.id}/documents/upload",
                        headers={"Authorization": f"Bearer {ut}"}, files=data)
        print("UPLOAD", r.status_code, r.json())
        saved = r.json()["saved"][0]
        key = saved["storage_key"]

        from app.core.config import settings
        from app.infrastructure.minio_client import get_minio_client
        st = get_minio_client().stat_object(settings.MINIO_BUCKET_UPLOADS, key)
        print("STAT", st, "expect size", saved["file_size"])
        print("SHA match empty-stream?", saved["sha256"] ==
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

        db.commit()
        row = db.query(UploadedDocument).filter(UploadedDocument.id == saved["id"]).first()
        print("ROW after upload:", row is not None and row.file_size)

        # admin 访问
        ra = client.get(f"/api/projects/{proj.id}/documents",
                        headers={"Authorization": f"Bearer {at}"})
        print("ADMIN LIST", ra.status_code, ra.text[:300])

        # 普通用户列表 + 软删 + 查行
        rl = client.get(f"/api/projects/{proj.id}/documents", headers={"Authorization": f"Bearer {ut}"})
        print("USER LIST", rl.status_code)
        docs = rl.json()["documents"]
        did = docs[0]["id"]
        rd = client.delete(f"/api/projects/{proj.id}/documents/{did}", headers={"Authorization": f"Bearer {ut}"})
        print("DELETE", rd.status_code, rd.text[:200])
        db.commit()
        row2 = db.query(UploadedDocument).filter(UploadedDocument.id == did).first()
        print("ROW after delete:", row2, getattr(row2, "deleted_at", None))
        # 原生 SQL 双保险
        raw = db.execute(text("SELECT id, deleted_at, file_size, sha256 FROM uploaded_documents WHERE id=:i"),
                         {"i": did}).fetchall()
        print("RAW SQL:", raw)
    finally:
        (db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id)
         .delete(synchronize_session=False))
        p = db.query(Project).filter(Project.id == proj.id).first()
        if p:
            db.delete(p)
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).delete()
        for u in (admin, user):
            db.query(User).filter(User.id == u.id).delete()
        db.commit()
        db.close()


if __name__ == "__main__":
    main()
