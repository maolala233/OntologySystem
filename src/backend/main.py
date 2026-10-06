import os

import bcrypt
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    documents,
    domains,
    env_configs,
    extraction,
    graph_view,
    mcp_gateway,
    mcp_tokens,
    model_configs,
    modules,
    ontology,
    publications,
    qa,
    reports,
    resolution,
    reviews,
    roles,
    system,
    users,
    versions,
)
from app.core.config import cleanup_dir_if_exceeded, ensure_dirs, settings, start_periodic_cleanup
from app.infrastructure.database import SessionLocal, UploadedDocument, User, init_db

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

# M0 安全收口：CORS 由 "*" 改白名单（docs/design/01 §8），来源列表经 .env CORS_ORIGINS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# M0：统一错误码响应包络（docs/design/03 §2.3）
from fastapi.responses import JSONResponse  # noqa: E402

from app.core.exceptions import APIError  # noqa: E402


@app.exception_handler(APIError)
async def api_error_handler(request, exc: APIError):
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_payload(trace_id=request.headers.get("x-request-id")),
    )

app.include_router(auth.router)
app.include_router(ontology.router)
app.include_router(system.router)
app.include_router(domains.router)
app.include_router(users.router)    # M1：用户管理（admin）
app.include_router(modules.router)  # M1：模块授权矩阵（admin）
app.include_router(roles.router)  # R8：角色配置（模块授权预设，admin）
app.include_router(model_configs.router)  # M2：模型配置
app.include_router(documents.router)  # M3-1：文档上传/秒传/预签名（03 §7）
app.include_router(extraction.router)  # M3-4/5：Schema/Instance 抽取 + 任务进度/SSE/取消（03 §8）
app.include_router(resolution.router)  # M3-5：实体消解与冲突（03 §9）
app.include_router(reviews.router)  # M3-6：审核队列与裁决（03 §11，状态机 pending→claimed→终态）
app.include_router(versions.router)  # M3-6：版本/快照/diff/回滚/时间轴（03 §11，04 §9）
app.include_router(publications.router)  # M3-6：发布与公共区只读（03 §12，04 §10）
app.include_router(graph_view.router)  # M4：图视图数据契约 + 节点/边详情（03 §13，06 §4）
app.include_router(qa.router)  # M5：本体问答 SSE 流式 + 历史/溯源（03 §15，07 §4）
app.include_router(reports.router)  # R8：本体报告/PPT 生成（工具层扩展）
app.include_router(mcp_gateway.router)  # M5：MCP 网关（HTTP Streamable，03 §16，07 §3）
app.include_router(mcp_tokens.router)  # M5：MCP 令牌管理（admin，03 §16）
app.include_router(env_configs.router)  # 环境配置（admin）：中间件连接参数 UI 可改


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
