# app/services/document_pipeline.py - M3-2 解析切片管道（docs/design/04 §2 / 03 §7）
# 共享执行体：Celery parse 任务（异步）与旧 parse-files shim（同步）都走这里。
# 链路：MinIO 取原件 → parse_document（后端选择）→ 中文切片 → 全文落 ontology-parsed
#       → document_chunks 落库 → 元数据回填（text_key/page_count/language/parse_backend）。
# 兼容期：同步双写 text_content（旧 extract-* 端点直接读该列，M3-3 R5 行表双写后收口）。

from sqlalchemy.orm import Session

from app.adapters.chunking import ChunkParams, chunk_text
from app.adapters.parsing import ParseBackend, parse_document
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.database import DocumentChunk, SessionLocal, UploadedDocument
from app.infrastructure.minio_client import get_minio_client
from app.tasks.progress import finish_task_progress, set_task_progress


def estimate_tokens(text: str) -> int:
    """轻量 token 估算：CJK 按字计 1，其余按 4 字符 1 token（与主流分词器近似）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def run_parse(document_id: int, backend: str = "auto",
              chunk_params: dict | None = None, task_id: str = "") -> dict:
    """解析 + 切片 + 落库全流程（自带会话，任务/请求两侧均可调）。

    失败：置 parse_status=failed + parse_error，进度置 failed，异常向上抛。
    返回：{chunks, chars, backend, language, page_count}。
    """
    def _progress(stage: str, pct: int, msg: str = "", stats: dict | None = None) -> None:
        set_task_progress(task_id, stage, pct, msg, stats or {}, queue="parse")

    db: Session = SessionLocal()
    doc = None
    try:
        doc = db.query(UploadedDocument).filter(UploadedDocument.id == document_id).first()
        if doc is None:
            raise ValueError(f"文档不存在: {document_id}")
        if not doc.storage_key:
            raise ValueError(f"文档 {document_id} 无 MinIO 原件（历史本地文档请重新上传）")

        doc.parse_status = "parsing"
        doc.parse_error = None
        db.commit()
        _progress("fetch", 5, "正在获取原件")

        minio = get_minio_client()
        content = minio.get_bytes(settings.MINIO_BUCKET_UPLOADS, doc.storage_key)

        _progress("parse", 30, f"解析中（{backend}）", {"filename": doc.filename})
        parsed = parse_document(content, doc.filename,
                                backend=ParseBackend(backend) if backend in {b.value for b in ParseBackend} else ParseBackend.AUTO)

        _progress("store_text", 55, "全文写入对象存储")
        text_key = f"{doc.project_id}/{doc.id}.md"
        full_text = parsed.full_text_md or ""
        minio.put_bytes(settings.MINIO_BUCKET_PARSED, text_key, full_text.encode("utf-8"),
                        content_type="text/markdown")

        _progress("chunk", 70, "中文切片中", {"chars": len(full_text)})
        params = ChunkParams(**chunk_params) if chunk_params else ChunkParams()
        chunks = chunk_text(full_text, params)

        _progress("save", 88, "分块落库", {"chunks": len(chunks)})
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
            synchronize_session=False)
        for i, c in enumerate(chunks):
            db.add(DocumentChunk(
                document_id=doc.id,
                chunk_index=i,
                text=c.text,
                char_start=c.start_index,
                char_end=c.end_index,
                token_count=estimate_tokens(c.text),
                meta={**c.meta, "page": _page_of(parsed, c.start_index)},
            ))
        doc.text_key = text_key
        doc.text_content = full_text  # 兼容期双写（M3-3 R5 收口）
        doc.page_count = parsed.page_count
        doc.language = parsed.language
        doc.parse_backend = parsed.backend.value if hasattr(parsed.backend, "value") else str(parsed.backend)
        doc.parse_status = "parsed"
        db.commit()

        stats = {"chunks": len(chunks), "chars": len(full_text),
                 "backend": doc.parse_backend, "language": doc.language,
                 "page_count": doc.page_count}
        finish_task_progress(task_id, "completed", "解析完成", stats)
        logger.info(f"[pipeline] 文档 {document_id} 解析完成: {stats}")
        return {"chunks": len(chunks), "chars": len(full_text),
                "backend": doc.parse_backend, "language": doc.language,
                "page_count": doc.page_count}
    except Exception as e:
        try:
            if doc is not None:
                doc.parse_status = "failed"
                doc.parse_error = str(e)[:2000]
                db.commit()
        except Exception:  # noqa: BLE001 —— 状态回写失败不影响原始异常上抛
            db.rollback()
        finish_task_progress(task_id, "failed", str(e)[:500])
        logger.error(f"[pipeline] 文档 {document_id} 解析失败: {e}")
        raise
    finally:
        db.close()


def _page_of(parsed, char_offset: int) -> int:
    """按累计页文本长度定位 chunk 所在页（1-based；无页信息返回 1）。"""
    if not parsed.pages:
        return 1
    acc = 0
    for p in parsed.pages:
        acc += len(p.text_md) + 1
        if char_offset < acc:
            return p.page_no
    return parsed.pages[-1].page_no
