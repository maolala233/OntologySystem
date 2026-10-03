# tests/test_chunking_parsing.py - M3-2 适配器单测（04 §2.3 切片 / §2 解析 / §2.2 扫描件判定）
import io

import pytest

from app.adapters.chunking import (
    DEFAULT_CHUNK_SIZE,
    ChunkParams,
    chunk_text,
    split_sentences_cn,
)
from app.adapters.parsing import (
    ParseBackend,
    detect_language,
    looks_like_scanned,
    parse_document,
)

# ---------- chunk_text ----------

def test_chunk_offsets_roundtrip():
    """每块 (start, end) 必须能从原文精确还原出块文本（溯源定位的硬前提）。"""
    text = ("第一章 星辰科技概况。" * 300) + "\n\n" + ("第二节 产品线介绍！" * 400)
    chunks = chunk_text(text)
    assert len(chunks) > 1
    for c in chunks:
        assert text[c.start_index:c.end_index] == c.text


def test_chunk_size_limit_and_order():
    text = "这是一段用于切片的中文文本。" * 500  # ~14000 字符
    chunks = chunk_text(text, ChunkParams(chunk_size=2000, overlap_ratio=0.15))
    assert all(len(c.text) <= 2000 for c in chunks)  # 窗口化保证不超 chunk_size
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start_index == 0
    # 覆盖全文：最后一块的 end 不早于文本尾部（允许尾部裁剪）
    assert chunks[-1].end_index >= len(text) - 2100


def test_chunk_overlap_backfill():
    text = ("甲句内容。乙句内容。丙句内容。" * 200)
    chunks = chunk_text(text, ChunkParams(chunk_size=600, overlap_ratio=0.15))
    assert len(chunks) > 2
    # 相邻窗口存在原文重叠：后块开头是前块尾部的延续
    tail = chunks[0].text[-40:]
    assert chunks[1].text[:20] in chunks[0].text or tail[:20] in chunks[1].text[:60]


def test_chunk_table_not_merged_with_prose():
    """表格结构块独立成块，且带 structural=table 标记（04 §2.3 表格不切断）。"""
    table = "| 列1 | 列2 |\n|---|---|\n" + "\n".join(f"| a{i} | b{i} |" for i in range(30))
    prose = "正文段落内容。" * 50
    chunks = chunk_text(prose + "\n" + table + "\n" + prose)
    tchunks = [c for c in chunks if c.meta.get("structural") == "table"]
    assert len(tchunks) == 1 and "列1" in tchunks[0].text


def test_chunk_code_fence_kept_whole():
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(40)) + "\n```"
    chunks = chunk_text("前文。" * 20 + "\n" + code)
    code_chunks = [c for c in chunks if c.meta.get("structural") == "code"]
    assert code_chunks and code_chunks[0].text.startswith("```python")


def test_chunk_empty_and_no_separator_hard_split():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []
    # 无任何分隔符的超长文本 → 硬切
    chunks = chunk_text("无分隔符" * 3000, ChunkParams(chunk_size=1000, overlap_ratio=0.1))
    assert len(chunks) > 10
    for c in chunks:
        assert len(c.text) <= 1000


def test_chunk_split_sentences_cn_quote_aware():
    sents = split_sentences_cn("他说：“今天下雨，路滑。”然后回家了！好。")
    assert sents == ["他说：“今天下雨，路滑。”", "然后回家了！", "好。"]


# ---------- parse_document ----------

def test_parse_txt_md_json_language():
    r = parse_document("这是中文内容，用于语言检测。".encode(), "a.txt")
    assert r.backend == ParseBackend.NATIVE and r.language == "zh"
    r = parse_document("# 标题\n- 中文要点一\n- 中文要点二".encode(), "b.md")
    assert r.language == "zh" and r.page_count >= 1
    r = parse_document(b'{"k": "v"}', "c.json")
    assert r.language == "en"


def test_parse_csv_to_markdown():
    buf = io.StringIO()
    buf.write("名称,数量\n产品A,3\n产品B,7\n")
    r = parse_document(buf.getvalue().encode("utf-8"), "data.csv")
    assert r.full_text_md.startswith("| 名称 | 数量 |")
    assert "| 产品A | 3 |" in r.full_text_md
    assert r.language == "zh"


def test_parse_xlsx_to_markdown():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备表"
    ws.append(["设备", "型号"])
    ws.append(["交换机", "X1"])
    import os
    import tempfile

    p = os.path.join(tempfile.gettempdir(), "m3test.xlsx")
    wb.save(p)
    try:
        with open(p, "rb") as f:
            r = parse_document(f.read(), "m3test.xlsx")
    finally:
        os.remove(p)
    assert "设备" in r.full_text_md and "交换机" in r.full_text_md


def test_parse_pdf_text_layer_and_scanned():
    import pymupdf

    # 文本型 PDF（docling 熔断预触发：本环境无 HuggingFace 网络，见 parsing._docling_broken）
    from app.adapters import parsing as _p

    _p._docling_broken["flag"] = True
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF text layer test page with enough ascii characters here.")
    import os
    import tempfile

    p = os.path.join(tempfile.gettempdir(), "m3text.pdf")
    doc.save(p)
    doc.close()
    with open(p, "rb") as f:
        content = f.read()
    r = parse_document(content, "m3text.pdf")
    assert r.backend == ParseBackend.NATIVE
    assert "PDF text layer" in r.full_text_md
    assert r.page_count == 1

    # 扫描件：整页图片 + 无文字层 → 判定命中；OCR 后端不可用 → 明确报错（04 §2.2）
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 400))
    pix.set_rect(pix.irect, (128, 128, 128))
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    p2 = os.path.join(tempfile.gettempdir(), "m3scan.pdf")
    doc.save(p2)
    doc.close()
    with open(p2, "rb") as f:
        scanned = f.read()
    with pytest.raises(Exception) as ei:
        parse_document(scanned, "m3scan.pdf")
    assert "扫描件" in str(ei.value) or "OCR" in str(ei.value)
    os.remove(p)
    os.remove(p2)


def test_looks_like_scanned_thresholds():
    assert looks_like_scanned(["", ""]) is True
    assert looks_like_scanned(["x" * 200, "y" * 200]) is False
    assert looks_like_scanned(["x" * 200], image_ratio=0.7) is True


def test_detect_language():
    assert detect_language("纯中文内容判断") == "zh"
    assert detect_language("plain english text") == "en"
    assert detect_language("这是一段中文内容，其中夹杂 English words 来制造均衡混合") == "mixed"
    assert detect_language("") == "unknown"


def test_default_chunk_size_is_2000():
    """缺陷 #8：chunk_size 默认 2000（旧实现 15000 过大）。"""
    assert DEFAULT_CHUNK_SIZE == 2000
