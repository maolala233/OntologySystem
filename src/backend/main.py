from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn
from app.api.v1.api import api_router

from app.api import auth, ontology, system, domains
from app.infrastructure.database import init_db, SessionLocal, UploadedDocument, User
from app.core.config import ensure_dirs, cleanup_dir_if_exceeded, settings, start_periodic_cleanup
import bcrypt

init_db()

ensure_dirs()

cleanup_dir_if_exceeded(settings.UPLOAD_DIR, settings.UPLOAD_MAX_SIZE_MB)
cleanup_dir_if_exceeded(settings.TEMP_DIR, settings.UPLOAD_MAX_SIZE_MB)

db = SessionLocal()
try:
    old_docs = db.query(UploadedDocument).filter(
        UploadedDocument.file_path.like("src/backend/uploads/%")
    ).all()
    for doc in old_docs:
        old_path = doc.file_path
        new_path = old_path.replace("src/backend/uploads/", "uploads/", 1)
        if old_path != new_path:
            if os.path.exists(old_path) and not os.path.exists(new_path):
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                import shutil
                shutil.move(old_path, new_path)
            doc.file_path = new_path
    if old_docs:
        db.commit()

    all_users = db.query(User).all()
    for user in all_users:
        if not user.hashed_password.startswith("$2b$"):
            user.hashed_password = bcrypt.hashpw(user.hashed_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db.commit()
finally:
    db.close()

start_periodic_cleanup(interval_seconds=600)

app = FastAPI(title="AI 本体构建系统 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ontology.router)
app.include_router(system.router)
app.include_router(domains.router)
app.include_router(api_router, prefix="/api/v1")


@app.get('/health')
def get_health():
    return {'status': 'OK'}


if __name__ == '__main__':
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3001,
        workers=1,
        limit_concurrency=100,
        timeout_keep_alive=30,
        log_level="info",
    )
