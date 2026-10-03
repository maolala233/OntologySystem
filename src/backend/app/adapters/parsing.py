# app/adapters/parsing.py - 文件解析（docs/design/04 §2）
# 里程碑：M3 实现。首选 Docling（含扫描件 OCR），现有 services/parser.py + vl_parser.py 兜底。

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ParseBackend(str, Enum):
    AUTO = "auto"                # 按格式与扫描件判定自动选择
    DOCLING_OCR = "docling_ocr"  # Docling + OCR（扫描件）
    DOCLING = "docling"          # Docling 文本层
    VL_MODEL = "vl_model"        # VL 视觉模型兜底（现 vl_parser 链路）
    NATIVE = "native"            # 现有 parser.py（pymupdf4llm/python-docx/pptx/xlsx...）


class ParsedPage(BaseModel):
    page_no: int
    text_md: str
    ocr_used: bool = False


class ParsedDocument(BaseModel):
    """解析统一产物（对齐 semantica DocumentParser 输出 + 平台溯源字段）。"""

    full_text_md: str
    pages: list[ParsedPage] = Field(default_factory=list)
    tables: list[dict] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    backend: ParseBackend = ParseBackend.NATIVE
    language: Optional[str] = None  # normalize.detect_language：zh/en/mixed
    page_count: int = 0
    ocr_page_count: int = 0


def looks_like_scanned(pages_text: list[str], image_ratio: float = 0.0) -> bool:
    """扫描件判定（04 §2.2）：平均每页可见字符 < 50 或图片占比 > 60%。

    本函数为纯逻辑，M0 即可测试；调用链在 M3 接入。
    """
    if not pages_text:
        return False
    avg_chars = sum(len(t.strip()) for t in pages_text) / len(pages_text)
    return avg_chars < 50 or image_ratio > 0.6


def parse_document(content: bytes, filename: str,
                   backend: ParseBackend = ParseBackend.AUTO,
                   chunk_params: Optional[dict] = None) -> ParsedDocument:
    """解析主入口（04 §2）。

    AUTO 流程：按扩展名分发 → PDF 先走快速文本层（pymupdf 每页）→
    looks_like_scanned 判定 → 扫描件走 docling OCR（首选）/ VL（兜底，未配置则报错）；
    文本型 PDF/DOCX/HTML 走 docling（首选，失败自动降级 native）；
    PPTX/XLSX/CSV/TXT/MD/JSON 直接 native。
    chunk_params 仅为签名兼容（切片在 adapters.chunking，由调用方接续）。
    单文档失败向上抛 AdapterError（批处理 continue_on_error 语义由任务层处理）。
    """
    from app.adapters.errors import AdapterError

    ext = _ext_of(filename)
    backend = _normalize_backend(backend, ext)

    # 文本型格式优先专用解析器（与 backend 无关：native/auto 产出必须一致，04 §2.1）
    if ext in {"txt", "md", "json"}:
        return _finalize(_parse_text(content, filename, ext), filename)
    if ext in {"csv", "xls", "xlsx"}:
        return _finalize(_parse_tabular(content, filename), filename)
    if ext == "pptx" or ext == "ppt":
        return _finalize(_parse_pptx(content, filename), filename)

    if backend == ParseBackend.DOCLING or backend == ParseBackend.DOCLING_OCR:
        doc = _parse_docling(content, filename, ocr=(backend == ParseBackend.DOCLING_OCR))
        if doc is None:
            raise AdapterError(f"Docling 解析失败且无可用兜底: {filename}")
        return _finalize(doc, filename)

    if backend == ParseBackend.VL_MODEL:
        raise AdapterError("VL 视觉模型后端未配置（扫描件请配置 VL 或部署 docling OCR）")

    if backend == ParseBackend.NATIVE:
        return _finalize(_parse_native(content, filename, ext), filename)

    # ---- AUTO：docling 首选格式（pdf 走扫描件判定，docx/html docling 优先）----
    if ext == "pdf":
        return _parse_pdf_auto(content, filename)
    # docx / doc / html 及其他：docling 首选 → native 兜底
    doc = _parse_docling(content, filename)
    if doc is not None:
        return _finalize(doc, filename)
    return _finalize(_parse_native(content, filename, ext), filename)


def _finalize(doc: ParsedDocument, filename: str) -> ParsedDocument:
    if doc.language is None:
        doc.language = detect_language(doc.full_text_md)
    doc.page_count = doc.page_count or len(doc.pages) or 1
    doc.metadata.setdefault("filename", filename)
    return doc


def _ext_of(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def _normalize_backend(backend: ParseBackend, ext: str) -> ParseBackend:
    if backend is not ParseBackend.AUTO:
        return backend
    # AUTO 下显式分发；PDF 的 scanned 判定在 _parse_pdf_auto 内完成
    return ParseBackend.AUTO


def detect_language(text: str) -> str:
    """轻量语言检测（zh/en/mixed）：按 CJK 与拉丁字母占比判定，不引入 langdetect 依赖。"""
    sample = (text or "")[:4000]
    if not sample:
        return "unknown"
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    total = cjk + latin
    if total == 0:
        return "unknown"
    ratio = cjk / total
    if ratio > 0.7:
        return "zh"
    if ratio < 0.3:
        return "en"
    return "mixed"


# ---- docling 熔断：环境无 HuggingFace 网络时首次失败即进程级禁用，避免每次解析都等超时 ----
_docling_broken = {"flag": False}


def _docling_convert(content: bytes, filename: str, ocr: bool):
    """返回 markdown 文本；不可用/失败返回 None（调用方兜底）。"""
    if _docling_broken["flag"]:
        return None
    import os
    import tempfile

    suffix = os.path.splitext(filename)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(content)
        tmp.close()
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter

        opts = PdfPipelineOptions()
        opts.do_ocr = ocr
        converter = DocumentConverter(pipeline_options=opts) if ocr else DocumentConverter()
        result = converter.convert(tmp.name)
        return result.document.export_to_markdown()
    except Exception:  # noqa: BLE001 —— docling 任何失败（网络/格式/内存）都降级 native
        _docling_broken["flag"] = True
        from app.core.logging import logger

        logger.warning("[parsing] docling 不可用（模型下载或转换失败），本次进程内降级 native 解析")
        return None
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def _parse_docling(content: bytes, filename: str, ocr: bool = False) -> Optional[ParsedDocument]:
    md = _docling_convert(content, filename, ocr)
    if md is None:
        return None
    return ParsedDocument(full_text_md=md, backend=ParseBackend.DOCLING_OCR if ocr else ParseBackend.DOCLING)


def _parse_pdf_auto(content: bytes, filename: str) -> ParsedDocument:
    """PDF（04 §2.2）：pymupdf 每页快速文本层 → 扫描件判定 → docling_ocr/VL，文本层 → docling/native。"""
    from app.adapters.errors import AdapterError

    pages_text, image_ratio = _pdf_fast_layer(content)
    scanned = looks_like_scanned(pages_text, image_ratio)
    if scanned:
        doc = _parse_docling(content, filename, ocr=True)
        if doc is not None:
            doc.ocr_page_count = len(pages_text)
            doc.pages = [ParsedPage(page_no=i + 1, text_md=t, ocr_used=True)
                         for i, t in enumerate(pages_text)]
            return _finalize(doc, filename)
        raise AdapterError("扫描件判定命中，但 docling OCR 不可用且 VL 后端未配置（04 §2.2）")
    # 文本型：docling 版式优先 → native pymupdf4llm 兜底
    doc = _parse_docling(content, filename)
    if doc is not None:
        doc.pages = [ParsedPage(page_no=i + 1, text_md=t) for i, t in enumerate(pages_text)]
        return _finalize(doc, filename)
    md = _pdf_native_text(content)
    doc = ParsedDocument(
        full_text_md=md,
        pages=[ParsedPage(page_no=i + 1, text_md=t) for i, t in enumerate(pages_text)],
        backend=ParseBackend.NATIVE,
    )
    return _finalize(doc, filename)


def _pdf_fast_layer(content: bytes) -> tuple[list[str], float]:
    """pymupdf 每页可见字符 + 图片面积占比（扫描件判定的输入）。"""

    import pymupdf

    pages_text: list[str] = []
    image_area = page_area = 0.0
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            pages_text.append(page.get_text("text") or "")
            page_area += page.rect.width * page.rect.height
            for img in page.get_images(full=True):
                try:
                    rects = page.get_image_rects(img[0])
                    image_area += sum(r.width * r.height for r in rects)
                except Exception:  # noqa: BLE001 —— 单页图片信息失败不致命
                    continue
    return pages_text, (image_area / page_area if page_area else 0.0)


def _pdf_native_text(content: bytes) -> str:
    """native PDF 兜底：优先 pymupdf4llm（Markdown），失败退 pymupdf 纯文本。"""

    import pymupdf

    try:
        import pymupdf4llm

        with pymupdf.open(stream=content, filetype="pdf") as doc:
            return pymupdf4llm.to_markdown(doc) or ""
    except Exception:  # noqa: BLE001
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            return "\n".join(page.get_text("text") or "" for page in doc)


def _parse_text(content: bytes, filename: str, ext: str) -> ParsedDocument:
    text = content.decode("utf-8", errors="replace")
    return ParsedDocument(full_text_md=text, backend=ParseBackend.NATIVE)


def _parse_tabular(content: bytes, filename: str) -> ParsedDocument:
    """XLSX/XLS/CSV → markdown 表格（excel 复用现有 _parse_excel 的合并单元格/表头检测）。"""
    import os
    import tempfile

    ext = _ext_of(filename)
    if ext == "csv":
        md = _parse_csv(content)
    else:
        suffix = os.path.splitext(filename)[1] or ".xlsx"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(content)
            tmp.close()
            from app.services.parser import _parse_excel

            md = _parse_excel(tmp.name) or ""
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
    return ParsedDocument(full_text_md=md, backend=ParseBackend.NATIVE)


def _parse_csv(content: bytes) -> str:
    """CSV → markdown 表格（第一行作表头；编码 utf-8 失败退 gbk）。"""
    import csv as _csv
    import io

    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")
    rows = [r for r in _csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines.extend("| " + " | ".join(r) + " |" for r in body)
    return "\n".join(lines)


def _parse_pptx(content: bytes, filename: str) -> ParsedDocument:
    """PPT/PPTX（04 §2.1：python-pptx 现路径首选，保留表格/备注提取）。"""
    import os
    import tempfile

    from app.services.parser import FileParser

    suffix = os.path.splitext(filename)[1] or ".pptx"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(content)
        tmp.close()
        md = FileParser(vl_enabled=False).parse_file(tmp.name) or ""
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    return ParsedDocument(full_text_md=md, backend=ParseBackend.NATIVE)


def _parse_native(content: bytes, filename: str, ext: str) -> ParsedDocument:
    """native 兜底：docx/doc/html 等交给现有 FileParser（04 §2.1 兜底列）。"""
    import os
    import tempfile

    from app.services.parser import FileParser

    suffix = os.path.splitext(filename)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(content)
        tmp.close()
        md = FileParser(vl_enabled=False).parse_file(tmp.name) or ""
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    return ParsedDocument(full_text_md=md, backend=ParseBackend.NATIVE)


__all__ = ["ParseBackend", "ParsedPage", "ParsedDocument", "looks_like_scanned", "parse_document", "detect_language"]
