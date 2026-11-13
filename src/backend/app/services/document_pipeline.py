# app/services/document_pipeline.py - M3-2 解析切片管道（docs/design/04 §2 / 03 §7）
# 共享执行体：Celery parse 任务（唯一解析路径；M3-7 已删除旧 parse-files 同步 shim）。
# 链路：MinIO 取原件 → parse_document（后端选择）→ 中文切片 → 全文落 ontology-parsed
#       → document_chunks 落库 → 元数据回填（text_key/page_count/language/parse_backend）。
# 兼容期：同步双写 text_content（旧 extract-* 端点直接读该列，M3-3 R5 行表双写后收口）。

import hashlib
import json
import time

from sqlalchemy.orm import Session

from app.adapters.chunking import ChunkParams, chunk_text
from app.adapters.parsing import ParseBackend, parse_document
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.database import DocumentChunk, SessionLocal, UploadedDocument
from app.infrastructure.minio_client import get_minio_client
from app.services.pipeline_profiler import profile_metric
from app.tasks.progress import finish_task_progress, set_task_progress


def estimate_tokens(text: str) -> int:
    """轻量 token 估算：CJK 按字计 1，其余按 4 字符 1 token（与主流分词器近似）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def _default_chunk_params(project_id: int | None = None) -> ChunkParams:
    """chunk_params 未显式给出时回读 extract 默认行 params（2026-11-08 接线）。

    模型配置里的「切片大小 chunk_size / 重叠 chunk_overlap」此前是死字段——
    provider 兼容层组装后全仓无消费者，切片一直走 chunking 内置默认（1200/0.15）。
    chunk_overlap 在配置里是百分数（10=10%），chunking 吃比例，这里换算。
    """
    try:
        from app.adapters.provider import ModelPurpose, resolve_provider

        db = SessionLocal()
        try:
            resolved = resolve_provider(ModelPurpose.EXTRACT, project_id=project_id, db=db)
        finally:
            db.close()
        p = resolved.params or {}
        kw: dict = {}
        try:
            size = int(p.get("chunk_size") or 0)
            if size > 0:
                kw["chunk_size"] = size
        except (TypeError, ValueError):
            pass
        try:
            overlap_pct = float(p.get("chunk_overlap") or 0)
            if overlap_pct > 0:
                kw["overlap_ratio"] = min(max(overlap_pct / 100.0, 0.0), 0.5)
        except (TypeError, ValueError):
            pass
        return ChunkParams(**kw)
    except Exception:  # noqa: BLE001 —— 配置解析失败用内置默认，不阻断解析
        return ChunkParams()


# ── 解析复用指纹（2026-11）───────────────────────────────────────────────
# 重建（含「重新构建」restart）时，已解析文档是否要重新解析由后端自己判断：
#   · 解析指纹（文件内容 + 解析入口 + 切片策略/大小/重叠）不一致 → 重新解析；
#   · 解析指纹一致、向量指纹（嵌入模型/维度/集合）不一致 → 只重嵌入，不重解析；
#   · 都一致且切片/向量条数对得上 → 直接复用。
# 指纹存 DocumentChunk.meta["fingerprint"]（首切片），避免加列迁移；旧数据没有
# 指纹，首次重建时会重解析一次，之后即走复用。
# 解析器或切片算法有行为变更时手动 +1，让历史切片自动失效。
PARSE_FINGERPRINT_VERSION = 1


def _hash_signature(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_ext(filename: str | None) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def effective_chunk_params(project_id: int | None,
                           chunk_params: dict | None = None) -> ChunkParams:
    """本次解析实际生效的切片参数（与 run_parse 内同一口径）。"""
    return ChunkParams(**chunk_params) if chunk_params else _default_chunk_params(project_id)


def build_parse_fingerprint(doc: UploadedDocument, params: ChunkParams,
                            backend: str = "auto") -> str:
    """解析结果指纹：文件内容 + 解析入口 + 切片参数，任一变化都必须重解析。"""
    return _hash_signature({
        "v": PARSE_FINGERPRINT_VERSION,
        "sha256": doc.sha256 or "",
        "ext": _file_ext(doc.filename),
        "backend": backend,
        "chunk_size": int(params.chunk_size),
        "chunk_overlap": round(float(params.overlap_ratio), 6),
        "chunk_strategy": params.strategy,
    })


def build_vector_fingerprint() -> str:
    """向量指纹：嵌入模型 + 维度 + 集合名（base_url 不参与，同模型换地址不误判）。"""
    from app.infrastructure.vector_client import get_vector_signature

    return _hash_signature({"v": PARSE_FINGERPRINT_VERSION, **get_vector_signature()})


def read_chunk_fingerprint(db: Session, document_id: int) -> dict | None:
    """读取首切片上记录的指纹（旧数据无指纹返回 None）。"""
    row = (db.query(DocumentChunk.meta)
           .filter(DocumentChunk.document_id == document_id)
           .order_by(DocumentChunk.chunk_index.asc())
           .first())
    if not row:
        return None
    meta = row[0] or {}
    fp = meta.get("fingerprint") if isinstance(meta, dict) else None
    return fp if isinstance(fp, dict) else None


def _new_vector_manager():
    """连接向量库（不建集合）；不可用返回 None（判定时按“无法判断”处理）。"""
    try:
        from app.infrastructure.vector_client import VectorStoreManager

        return VectorStoreManager(create_if_missing=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[pipeline] 向量库不可用，跳过向量新鲜度检查: {e}")
        return None


def decide_parse_action(doc: UploadedDocument, db: Session,
                        chunk_params: dict | None = None, backend: str = "auto",
                        vector_fp: str | None = None,
                        vm_getter=None) -> str:
    """已解析文档在重建时的处置：reparse / resync_vectors / reuse。

    vm_getter 为惰性向量库工厂——只有解析指纹对得上时才连接 Milvus。
    """
    if doc.parse_status != "parsed":
        return "reparse"
    if not doc.sha256:  # 历史数据无内容哈希：无法证明原件未变，保守重解析
        return "reparse"
    chunk_count = (db.query(DocumentChunk)
                   .filter(DocumentChunk.document_id == doc.id)
                   .count())
    if chunk_count <= 0:
        return "reparse"
    fp = read_chunk_fingerprint(db, doc.id)
    if not fp:
        return "reparse"
    params = effective_chunk_params(doc.project_id, chunk_params)
    if fp.get("parse") != build_parse_fingerprint(doc, params, backend):
        return "reparse"
    if not vector_fp:
        vector_fp = build_vector_fingerprint()
    if fp.get("vector") != vector_fp:
        return "resync_vectors"
    getter = vm_getter or _new_vector_manager
    vm = getter()
    if vm is None:  # Milvus 关闭/连不上：不因向量检查阻断复用
        return "reuse"
    n = vm.count_by_source(doc.project_id, doc.filename)
    if n is None:
        return "reuse"
    return "reuse" if int(n) == chunk_count else "resync_vectors"


def resync_document_vectors(doc: UploadedDocument, db: Session,
                            vector_fp: str) -> int:
    """切片仍在但向量缺失/过期（换嵌入模型、重建集合）：只重嵌入，不重解析。"""
    from app.adapters.chunking import Chunk

    rows = (db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index.asc())
            .all())
    if not rows:
        return 0
    chunks = [Chunk(text=r.text or "",
                    start_index=int(r.char_start or 0),
                    end_index=int(r.char_end or 0),
                    chunk_index=int(r.chunk_index or 0),
                    meta=dict(r.meta or {}))
              for r in rows]
    n = _sync_chunks_to_vector(doc, chunks)
    if n and n >= len(chunks):
        first = rows[0]
        meta = dict(first.meta or {})
        fp = dict(meta.get("fingerprint") or {})
        fp.update({"version": PARSE_FINGERPRINT_VERSION, "vector": vector_fp})
        meta["fingerprint"] = fp
        first.meta = meta
        try:
            db.commit()
        except Exception:  # noqa: BLE001 —— 指纹回写失败只影响下次重复重嵌入
            db.rollback()
    logger.info(f"[pipeline] 文档 {doc.id} 仅重嵌入向量: {n}/{len(rows)} 条")
    return n


def run_parse(document_id: int, backend: str = "auto",
              chunk_params: dict | None = None, task_id: str = "") -> dict:
    """解析 + 切片 + 落库全流程（自带会话，任务/请求两侧均可调）。

    失败：置 parse_status=failed + parse_error，进度置 failed，异常向上抛。
    返回：{chunks, chars, backend, language, page_count}。
    """
    def _progress(stage: str, pct: int, msg: str = "", stats: dict | None = None) -> None:
        set_task_progress(task_id, stage, pct, msg, stats or {}, queue="parse")

    # 解析子步骤计时（剖析用；无活动管线时 profile_metric 是空操作）
    _sub: dict = {}
    _last = time.perf_counter()

    def _mark(name: str) -> None:
        nonlocal _last
        now = time.perf_counter()
        _sub[name] = round(now - _last, 6)
        _last = now

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
        _mark("set_parsing")
        _progress("fetch", 5, "正在获取原件")

        minio = get_minio_client()
        content = minio.get_bytes(settings.MINIO_BUCKET_UPLOADS, doc.storage_key)
        _mark("fetch")

        _progress("parse", 30, f"解析中（{backend}）", {"filename": doc.filename})
        parsed = parse_document(content, doc.filename,
                                backend=ParseBackend(backend) if backend in {b.value for b in ParseBackend} else ParseBackend.AUTO)
        _mark("parse")

        _progress("store_text", 55, "全文写入对象存储")
        text_key = f"{doc.project_id}/{doc.id}.md"
        full_text = parsed.full_text_md or ""
        minio.put_bytes(settings.MINIO_BUCKET_PARSED, text_key, full_text.encode("utf-8"),
                        content_type="text/markdown")
        _mark("store_text")

        _progress("chunk", 70, "中文切片中", {"chars": len(full_text)})
        params = effective_chunk_params(doc.project_id, chunk_params)
        chunks = chunk_text(full_text, params)
        _mark("chunk")

        # 本次解析产物的复用指纹（首切片携带；下次构建据此决定跳过/重嵌入）
        fingerprint = {
            "version": PARSE_FINGERPRINT_VERSION,
            "parse": build_parse_fingerprint(doc, params, backend),
            "vector": build_vector_fingerprint(),
        }
        _progress("save", 88, "分块落库", {"chunks": len(chunks)})
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
            synchronize_session=False)
        for i, c in enumerate(chunks):
            meta = {**c.meta, "page": _page_of(parsed, c.start_index)}
            if i == 0:
                meta["fingerprint"] = fingerprint
            db.add(DocumentChunk(
                document_id=doc.id,
                chunk_index=i,
                text=c.text,
                char_start=c.start_index,
                char_end=c.end_index,
                token_count=estimate_tokens(c.text),
                meta=meta,
            ))
        doc.text_key = text_key
        doc.text_content = full_text  # 兼容期双写（M3-3 R5 收口）
        doc.page_count = parsed.page_count
        doc.language = parsed.language
        doc.parse_backend = parsed.backend.value if hasattr(parsed.backend, "value") else str(parsed.backend)
        doc.parse_status = "parsed"
        db.commit()
        _mark("save")

        # 07 §4 入库环节：切片写入向量库（QA 向量召回的数据源；失败不阻断解析）
        _progress("vector", 94, "切片写入向量库", {"chunks": len(chunks)})
        vector_count = _sync_chunks_to_vector(doc, chunks)
        _mark("vector")
        stats = {"chunks": len(chunks), "chars": len(full_text), "vectors": vector_count,
                 "backend": doc.parse_backend, "language": doc.language,
                 "page_count": doc.page_count}
        finish_task_progress(task_id, "completed", "解析完成", stats)
        _mark("finalize")
        _sub.update({
            "document_id": document_id,
            "filename": doc.filename,
            "backend": doc.parse_backend,
            "chunks": len(chunks),
            "chars": len(full_text),
            "vectors": vector_count,
        })
        profile_metric(f"parse_doc_{document_id}", _sub)
        logger.info(f"[pipeline] 文档 {document_id} 解析完成: {stats}")
        return {"chunks": len(chunks), "chars": len(full_text),
                "backend": doc.parse_backend, "language": doc.language,
                "page_count": doc.page_count}
    except Exception as e:
        _mark("until_error")
        _sub["error"] = str(e)[:300]
        profile_metric(f"parse_doc_{document_id}", _sub)
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


# 向量入库时间预算：embedding API（OpenAI SDK 默认无超时，单次可挂 600s）或 Milvus
# 抖动时，解析终态（completed/failed 进度事件）不能被无限期挡住——SSE 订阅方会一直
# 心跳到 10 分钟超时。超预算后解析先落终态，入库线程继续在后台写完。
VECTOR_SYNC_BUDGET_SECONDS = 120.0


def _sync_chunks_to_vector(doc: UploadedDocument, chunks) -> int:
    """切片 embedding 写入 Milvus（project_id 表达式隔离 QA 召回，07 §4）。

    重解析先清该文档旧向量防陈旧内容；任何向量库故障只记日志不阻断解析落库。
    带 VECTOR_SYNC_BUDGET_SECONDS 时间预算：超时后返回 0 并放后台线程续写。
    """
    if not chunks:
        return 0
    import threading

    result = {"count": 0}

    def _work():
        try:
            result["count"] = _sync_chunks_to_vector_now(doc, chunks)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[pipeline] 向量入库失败（不阻断解析）doc={doc.id}: {e}")

    th = threading.Thread(target=_work, name=f"vec-sync-{doc.id}", daemon=True)
    th.start()
    th.join(VECTOR_SYNC_BUDGET_SECONDS)
    if th.is_alive():
        logger.warning(f"[pipeline] 文档 {doc.id} 向量入库超 {VECTOR_SYNC_BUDGET_SECONDS:.0f}s "
                       f"预算，解析先落终态（入库线程后台续写）")
        return 0
    return result["count"]


def _sync_chunks_to_vector_now(doc: UploadedDocument, chunks) -> int:
    """向量入库的实际执行体（由 _sync_chunks_to_vector 在线程内调用）。"""
    try:
        from app.infrastructure.vector_client import VectorStoreManager
        vm = VectorStoreManager()
        if not vm.is_enabled:
            logger.warning(f"[pipeline] Milvus 未启用，文档 {doc.id} 跳过向量入库")
            return 0
        try:
            # Milvus 2.3 不支持 JSON 路径表达式（metadata["k"]），用已建索引的
            # source_file 标量字段清理；同项目同名文件的旧向量一并清除（内容哈希去重兜底）
            vm.delete_by_expr(
                f'project_id == {doc.project_id} and source_file == "{doc.filename}"')
        except Exception as e:  # noqa: BLE001 —— 清旧失败不阻断写入
            logger.warning(f"[pipeline] 文档 {doc.id} 旧向量清理失败: {e}")
        metas = [{"source_file": doc.filename,
                  "source_quote": c.text,  # 全文存入（QA 上下文 quote 上限 1500，截短会丢答案段）
                  "source_document_id": doc.id,
                  "chunk_index": i,
                  "char_start": c.start_index,
                  "char_end": c.end_index,
                  "chunk_text": c.text}
                 for i, c in enumerate(chunks)]
        vm.insert_data([c.text for c in chunks], metas, project_id=doc.project_id)
        logger.info(f"[pipeline] 文档 {doc.id} 向量入库完成: {len(chunks)} 条")
        return len(chunks)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[pipeline] 向量入库失败（不阻断解析）doc={doc.id}: {e}")
        return 0


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
