# app/api/documents.py - 文档上传/秒传/预签名下载（docs/design/03 §7，M3-1）
# 权限：[M:documents] 模块码 + [PR:editor/viewer] 项目角色（core/deps）。
# 存储：原件落 MinIO ontology-uploads，storage_key={project}/{uuid8}.{ext}；
#       秒传 = 服务端 SHA-256 命中同项目未删文档 → 409 DUPLICATE_UPLOAD（带既有 doc）。
# 解析执行（parse 队列/SSE/text_key 回填）为 M3-2，本文件只落数据面：parse_status='uploaded'。
import hashlib
import mimetypes
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.core.logging import logger
from app.infrastructure.database import DocumentChunk, UploadedDocument, User, get_db
from app.infrastructure.minio_client import get_minio_client

router = APIRouter(prefix="/api/projects/{project_id}/documents", tags=["documents"])

# M3-1 可接收的格式（与 04 §2.1 矩阵一致；解析执行在 M3-2）
ALLOWED_EXTS = {"txt", "md", "pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "html", "json"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB


def _ext_of(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def _load_doc_or_404(project_id: int, doc_id: int, db: Session, include_deleted: bool = False) -> UploadedDocument:
    doc = db.query(UploadedDocument).filter(
        UploadedDocument.id == doc_id,
        UploadedDocument.project_id == project_id,
    ).first()
    if doc is None or (doc.deleted_at is not None and not include_deleted):
        raise NotFoundError(f"文档不存在: {doc_id}")
    return doc


def _doc_out(doc: UploadedDocument, chunk_count: int | None = None) -> dict:
    out = {
        "id": doc.id,
        "filename": doc.filename,
        "file_size": doc.file_size,
        "file_type": doc.file_type,
        "sha256": doc.sha256,
        "storage_key": doc.storage_key,
        "text_key": doc.text_key,
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "page_count": doc.page_count,
        "language": doc.language,
        "parse_backend": doc.parse_backend,
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }
    if chunk_count is not None:
        out["chunk_count"] = chunk_count
    return out


@router.post("/upload", dependencies=[Depends(require_module("documents"))])
async def upload_documents(
    project_id: int,
    files: list[UploadFile] = File(...),
    auto_parse: bool = Query(True, description="上传后自动触发解析（parse 队列，SSE 订阅 parse-events）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """多文件上传：服务端算 sha256 → 同项目命中即 409（带既有 doc，整批拒绝不落半批）→ MinIO → 记录 →（可选）parse 队列。"""
    require_project_role(project_id, "editor", current_user, db)
    if not files:
        raise APIError("请至少上传一个文件", code="EMPTY_UPLOAD", http_status=400)

    minio = get_minio_client()
    os.makedirs(settings.TEMP_DIR, exist_ok=True)

    staged = []  # (tmp_path, filename, ext, size, sha)
    try:
        for f in files:
            ext = _ext_of(f.filename)
            if ext not in ALLOWED_EXTS:
                raise APIError(f"不支持的文件类型: {f.filename}", code="UNSUPPORTED_FILE_TYPE", http_status=400,
                               detail={"filename": f.filename, "ext": ext, "allowed": sorted(ALLOWED_EXTS)})
            tmp_path = os.path.join(settings.TEMP_DIR, f"m3up_{uuid.uuid4().hex}_{os.path.basename(f.filename)}")
            sha = hashlib.sha256()
            size = 0
            with open(tmp_path, "wb") as buf:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise APIError(f"文件超过大小上限 200MB: {f.filename}", code="FILE_TOO_LARGE", http_status=413)
                    buf.write(chunk)
                    sha.update(chunk)
            staged.append({"tmp": tmp_path, "filename": f.filename, "ext": ext,
                           "size": size, "sha": sha.hexdigest()})

        # 秒传判重：同项目、未删、同 sha256 → 整批 409（03 §7）
        shas = {s["sha"] for s in staged}
        existing = (db.query(UploadedDocument)
                    .filter(UploadedDocument.project_id == project_id,
                            UploadedDocument.sha256.in_(shas),
                            UploadedDocument.deleted_at.is_(None))
                    .all())
        if existing:
            raise APIError("存在内容相同的文档（秒传命中）", code="DUPLICATE_UPLOAD", http_status=409,
                           detail={"duplicates": [_doc_out(d) for d in existing],
                                   "uploaded_filenames": [s["filename"] for s in staged
                                                          if s["sha"] in {e.sha256 for e in existing}]})

        saved = []
        for s in staged:
            key = minio.build_key(project_id, s["filename"])
            content_type = mimetypes.guess_type(s["filename"])[0] or "application/octet-stream"
            minio.put_file(settings.MINIO_BUCKET_UPLOADS, key, s["tmp"], content_type=content_type)
            doc = UploadedDocument(
                project_id=project_id,
                filename=s["filename"],
                file_path=key,  # 兼容期：新上传 file_path 记 MinIO 键，旧本地解析路径不再使用
                file_size=s["size"],
                file_type=s["ext"],
                sha256=s["sha"],
                storage_key=key,
                parse_status="uploaded",
            )
            db.add(doc)
            saved.append(doc)
        db.commit()
        for d in saved:
            db.refresh(d)
        # M3-2：auto_parse → parse 队列（每文档独立任务，失败隔离 04 §2.4）
        task_ids: dict[int, str] = {}
        if auto_parse:
            from app.tasks.parse_tasks import parse_document

            for d in saved:
                try:
                    task_ids[d.id] = parse_document.delay(d.id).id
                except Exception as e:  # noqa: BLE001 —— broker 不可达不阻断上传
                    logger.warning(f"[documents] 文档 {d.id} 解析任务派发失败（可手动 POST parse 重试）: {e}")
        logger.info(f"[documents] 项目 {project_id} 上传 {len(saved)} 个文档 by user {current_user.id}"
                    + (f"，派发 {len(task_ids)} 个解析任务" if auto_parse else ""))
        return {
            "status": "success",
            "saved": [{**_doc_out(d), "task_id": task_ids.get(d.id)} for d in saved],
            "auto_parse": auto_parse,
            "message": (f"已存储 {len(saved)} 个文档，解析任务已派发"
                        if task_ids else f"已存储 {len(saved)} 个文档"),
        }
    finally:
        for s in staged:
            try:
                os.remove(s["tmp"])
            except OSError:
                pass


@router.get("", dependencies=[Depends(require_module("documents"))])
def list_documents(
    project_id: int,
    parse_status: str | None = Query(None),
    include_deleted: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """文档列表：parse_status/语言/页数/分块数（03 §7）。"""
    require_project_role(project_id, "viewer", current_user, db)
    q = db.query(UploadedDocument).filter(UploadedDocument.project_id == project_id)
    if not include_deleted:
        q = q.filter(UploadedDocument.deleted_at.is_(None))
    if parse_status:
        q = q.filter(UploadedDocument.parse_status == parse_status)
    docs = q.order_by(UploadedDocument.id.desc()).all()
    from sqlalchemy import func
    rows = (db.query(DocumentChunk.document_id, func.count(DocumentChunk.id))
            .join(UploadedDocument, UploadedDocument.id == DocumentChunk.document_id)
            .filter(UploadedDocument.project_id == project_id)
            .group_by(DocumentChunk.document_id).all())
    counts = dict(rows)
    return {"status": "success", "total": len(docs),
            "documents": [_doc_out(d, chunk_count=counts.get(d.id, 0)) for d in docs]}


@router.get("/parse-events")
def parse_events(
    project_id: int,
    task_id: str = Query(...),
    ticket: str = Query(..., description="一次性 SSE ticket（GET /api/auth/sse-ticket 签发）"),
    db: Session = Depends(get_db),
):
    """SSE：parse 队列进度（Redis 进度键 → text/event-stream，03 §7）。

    EventSource 无法携带 Authorization 头，鉴权靠 ticket（M1 auth.sse-ticket，60s 一次性）。
    """
    import json as _json
    import time

    import redis as _redis
    from fastapi.responses import StreamingResponse

    from app.core.config import settings
    from app.infrastructure.database import User

    from app.services.env_config_service import redis_url
    client = _redis.Redis.from_url(redis_url(), socket_connect_timeout=2)
    raw = client.get(f"sse:ticket:{ticket}")
    if not raw:
        raise APIError("SSE ticket 无效或已过期", code="TICKET_INVALID", http_status=401)
    client.delete(f"sse:ticket:{ticket}")  # 一次性
    payload = _json.loads(raw)
    user = db.query(User).filter(User.id == payload.get("user_id")).first()
    if user is None or not user.is_active:
        raise APIError("SSE ticket 用户无效", code="TICKET_INVALID", http_status=401)
    # ticket 已证明身份；这里补模块码校验（admin 直通）
    if user.role != "admin":
        from app.core.deps import _user_module_context
        from app.core.permissions import resolve_user_modules

        grants, default_on, all_codes = _user_module_context(db, user)
        if "documents" not in resolve_user_modules("user", grants, default_on, all_codes):
            raise APIError("未开通模块: documents", code="MODULE_NOT_GRANTED", http_status=403)

    from app.tasks.progress import get_task_progress

    def _stream():
        started = time.time()
        last_sent = ""
        heartbeat_at = time.time()
        while time.time() - started < 600:  # 上限 10 分钟
            prog = get_task_progress(task_id)
            if prog is None:
                if last_sent:
                    yield "event: gone\ndata: {}\n\n"
                    return
                # 任务尚未写入进度（排队中）：继续等，不发事件
            else:
                line = _json.dumps(prog, ensure_ascii=False)
                if line != last_sent:
                    last_sent = line
                    yield f"data: {line}\n\n"
                if prog.get("status") in ("completed", "failed", "cancelled"):
                    return
            if time.time() - heartbeat_at > 15:
                heartbeat_at = time.time()
                yield ": heartbeat\n\n"
            time.sleep(1.0)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{doc_id}/chunks/{chunk_index}")
def get_chunk_context(
    project_id: int,
    doc_id: int,
    chunk_index: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """切片原文上下文（溯源"查看原文"用，04 §8）：返回切片全文 + 文档级偏移。

    详情抽屉拿切片文本后在 source_char_start/end（切片内偏移）处高亮证据原句；
    char_start/char_end 是切片在原文档中的绝对偏移，可用于将来跳转原文档定位。
    """
    require_project_role(project_id, "viewer", current_user, db)
    doc = _load_doc_or_404(project_id, doc_id, db)
    if chunk_index < 0:
        raise APIError("chunk_index 不能为负", code="INVALID_CHUNK_INDEX", http_status=400)
    chunk = (db.query(DocumentChunk)
             .filter(DocumentChunk.document_id == doc.id,
                     DocumentChunk.chunk_index == chunk_index)
             .first())
    if chunk is None:
        raise NotFoundError(f"切片不存在: 文档 {doc.id} chunk {chunk_index}")
    return {"status": "success", "chunk": {
        "document_id": doc.id,
        "document_name": doc.filename,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
    }}


@router.post("/{doc_id}/parse", dependencies=[Depends(require_module("documents"))])
def reparse_document(
    project_id: int,
    doc_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动（重）解析：body 可指定 backend（auto/docling_ocr/docling/vl_model/native）与 chunk 参数（03 §7）。"""
    require_project_role(project_id, "editor", current_user, db)
    doc = _load_doc_or_404(project_id, doc_id, db)
    if not doc.storage_key:
        raise NotFoundError("该文档无 MinIO 原件（历史本地文档请重新上传）")
    if doc.parse_status == "parsing":
        raise APIError("该文档正在解析中", code="PARSE_IN_PROGRESS", http_status=409)
    body = body or {}
    backend = body.get("backend", "auto")
    allowed_backends = {"auto", "docling_ocr", "docling", "vl_model", "native"}
    if backend not in allowed_backends:
        raise APIError(f"未知 backend: {backend}", code="INVALID_BACKEND", http_status=400,
                       detail={"allowed": sorted(allowed_backends)})
    chunk_params = {k: v for k, v in (("chunk_size", body.get("chunk_size")),
                                      ("overlap_ratio", body.get("overlap_ratio"))) if v is not None}
    from app.tasks.parse_tasks import parse_document

    try:
        task_id = parse_document.delay(doc_id, backend=backend, chunk_params=chunk_params or None).id
    except Exception as e:  # noqa: BLE001 —— broker 不可达
        logger.error(f"[documents] 文档 {doc_id} 解析任务派发失败: {e}")
        raise APIError("解析任务派发失败（任务队列不可用）", code="TASK_DISPATCH_FAILED", http_status=503)
    return {"status": "success", "task_id": task_id,
            "message": "解析任务已派发；进度请订阅 parse-events"}


@router.get("/{doc_id}/chunks", dependencies=[Depends(require_module("documents"))])
def list_chunks(
    project_id: int,
    doc_id: int,
    cursor: int | None = Query(None, ge=0, description="上一页最后一个 chunk_index；缺省从第 0 块开始"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分块列表（游标分页，供溯源定位与人工查看）。"""
    require_project_role(project_id, "viewer", current_user, db)
    _load_doc_or_404(project_id, doc_id, db)
    q = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id)
    if cursor is not None:
        q = q.filter(DocumentChunk.chunk_index > cursor)
    # 多取 1 行判断是否有下一页（03 §7 游标分页契约：末页 next_cursor=null）
    rows = (q.order_by(DocumentChunk.chunk_index.asc()).limit(limit + 1).all())
    has_more = len(rows) > limit
    chunks = rows[:limit]
    next_cursor = chunks[-1].chunk_index if has_more and chunks else None
    return {
        "status": "success",
        "chunks": [{"chunk_index": c.chunk_index, "text": c.text,
                    "char_start": c.char_start, "char_end": c.char_end,
                    "token_count": c.token_count, "meta": c.meta} for c in chunks],
        "next_cursor": next_cursor,
    }


@router.get("/{doc_id}/download", dependencies=[Depends(require_module("documents"))])
def download_document(
    project_id: int,
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """原件预签名 URL（302 直跳 MinIO，API 不代理字节流，03 §7）。"""
    require_project_role(project_id, "viewer", current_user, db)
    doc = _load_doc_or_404(project_id, doc_id, db)
    if not doc.storage_key:
        raise NotFoundError("该文档无 MinIO 原件（历史本地文档）")
    url = get_minio_client().get_presigned_url(settings.MINIO_BUCKET_UPLOADS, doc.storage_key)
    return RedirectResponse(url, status_code=302)


@router.post("/clear-all", dependencies=[Depends(require_module("documents"))])
def clear_all_documents(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """清空项目文档（旧 ontology.py 路由收编，03 §18）：软删全部未删文档。"""
    require_project_role(project_id, "editor", current_user, db)
    now = datetime.utcnow()
    count = (db.query(UploadedDocument)
             .filter(UploadedDocument.project_id == project_id,
                     UploadedDocument.deleted_at.is_(None))
             .update({"deleted_at": now}, synchronize_session=False))
    db.commit()
    logger.info(f"[documents] 项目 {project_id} 清空 {count} 个文档（软删）by user {current_user.id}")
    return {"message": f"已清空 {count} 个文档", "deleted_count": count}


@router.delete("/{doc_id}", dependencies=[Depends(require_module("documents"))])
def delete_document(
    project_id: int,
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """软删：置 deleted_at；chunks 保留（向量清理随 M4 outbox 接入）。"""
    require_project_role(project_id, "editor", current_user, db)
    doc = _load_doc_or_404(project_id, doc_id, db)
    doc.deleted_at = datetime.utcnow()
    db.commit()
    logger.info(f"[documents] 文档 {doc_id} 软删 by user {current_user.id}")
    return {"status": "success", "id": doc_id, "deleted": True}
