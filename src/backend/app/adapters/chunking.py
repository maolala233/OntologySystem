# app/adapters/chunking.py - 中文切片（docs/design/04 §2.3）
# 里程碑：M3 实现（分句状态机的边界逻辑已在 M0 落地并测试）。
#
# 修复缺陷 #13：semantica split 的分句正则只认英文句号，中文长文必须走
# 本模块的中文分句状态机；修复缺陷 #8：chunk_size 从 15000 降为 2000。

from __future__ import annotations

from pydantic import BaseModel, Field

# 中文优先的分隔符序列（recursive 策略；注意句号后无空格 —— 修复缺陷 #13）
CHINESE_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ".", " "]

# 平台默认切片参数（03 §7 可被请求覆盖）
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP_RATIO = 0.15

# 中文分句状态机的终结符与引号处理（04 §2.3 伪代码落地）
_SENTENCE_ENDINGS = "。！？；"
_QUOTE_OPEN = "“"
_QUOTE_CLOSE = "”"
_DASH_CHARS = "…—"


class Chunk(BaseModel):
    text: str
    start_index: int  # 原文 char 偏移（Unicode 码点，溯源定位基准）
    end_index: int
    chunk_index: int = 0
    meta: dict = Field(default_factory=dict)  # 章节路径/页码/表格标记


class ChunkParams(BaseModel):
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap_ratio: float = DEFAULT_CHUNK_OVERLAP_RATIO
    strategy: str = "recursive"  # recursive | entity_aware | structural


def split_sentences_cn(text: str) -> list[str]:
    """中文分句状态机（04 §2.3）。

    规则：。！？； 为句界；引号内不切分，收尾引号后切分；…— 视为句界。
    M0 已实现并可测试；M3 接入切片主流程。
    """
    sentences: list[str] = []
    buf: list[str] = []
    state_quote = False
    prev_dash = False
    for ch in text:
        buf.append(ch)
        if ch == _QUOTE_OPEN:
            state_quote = True
            continue
        if ch == _QUOTE_CLOSE:
            state_quote = False
            sentences.append("".join(buf))
            buf = []
            continue
        is_dash = ch in _DASH_CHARS
        if not state_quote and (ch in _SENTENCE_ENDINGS or (is_dash and not prev_dash)):
            sentences.append("".join(buf))
            buf = []
        prev_dash = is_dash
    if buf:
        sentences.append("".join(buf))
    return [s for s in (x.strip() for x in sentences) if s]


def chunk_text(text: str, params: ChunkParams | None = None) -> list[Chunk]:
    """recursive 切片主流程（04 §2.3）。

    算法：先按结构块（markdown 表格/代码围栏）分段——结构块整块保留不切断，
    超限时才退化为硬切；普通文本按 CHINESE_SEPARATORS 递归下钻
    （段落 → 行 → 句 → 硬切），块内把相邻原子片段窗口化合并至 chunk_size，
    相邻窗口按 overlap 比例回看重叠。
    不变量：每块 text 恒为原文 text[start_index:end_index] 的精确子串
    （溯源 char 定位与前端高亮的硬前提）。
    """
    p = params or ChunkParams()
    size = max(1, int(p.chunk_size))
    overlap = max(0, int(size * p.overlap_ratio))
    if not text or not text.strip():
        return []

    spans: list[tuple[int, int, str | None]] = []  # (start, end, structural)
    for off, content, kind in _structural_blocks(text):
        if kind is not None:
            spans.extend((s, e, kind) for s, e in _hard_spans(content, off, size, overlap))
        else:
            spans.extend((s, e, None) for s, e in _recursive_spans(content, off, size, overlap))

    chunks: list[Chunk] = []
    for s, e, kind in spans:
        content = text[s:e].rstrip()
        if not content.strip():
            continue
        meta: dict = {"structural": kind} if kind else {}
        chunks.append(Chunk(text=content, start_index=s, end_index=s + len(content), meta=meta))
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _structural_blocks(text: str) -> list[tuple[int, str, str | None]]:
    """把文本切成结构块：markdown 表格（连续 | 行）与代码围栏整块保留，其余为普通块。"""
    blocks: list[tuple[int, str, str | None]] = []
    lines = text.split("\n")
    off = 0
    kind: str | None = None
    buf: list[str] = []
    buf_off = 0
    in_code = False

    def flush():
        nonlocal buf
        if buf:
            blocks.append((buf_off, "\n".join(buf), kind))
            buf = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if in_code:  # 收尾围栏行并入代码块
                buf.append(line)
                off += len(line) + 1
                flush()
                kind = None
                in_code = False
                continue
            flush()
            kind, in_code, buf_off = "code", True, off
            buf = [line]
            off += len(line) + 1
            continue
        if not in_code and stripped.startswith("|") and stripped.endswith("|"):
            if kind != "table":
                flush()
                kind, buf_off = "table", off
            buf.append(line)
            off += len(line) + 1
            continue
        if kind == "table":
            flush()
            kind = None
        buf.append(line)
        off += len(line) + 1
    flush()
    return [(o, c.rstrip("\n"), k) for o, c, k in blocks if c.strip()]


def _recursive_spans(text: str, base: int, size: int,
                     overlap: int) -> list[tuple[int, int]]:
    """按分隔符序列递归下钻：> size 的段落按下一级分隔符再切，最后硬切。

    返回 (start, end) 绝对偏移对；相邻片段窗口化合并时保留中间分隔符，
    保证 text[s:e] 精确还原。
    """
    if len(text) <= size:
        return [(base, base + len(text))]
    for sep in CHINESE_SEPARATORS:
        if sep and sep in text:
            spans: list[tuple[int, int]] = []
            off = base
            for part in text.split(sep):
                if part.strip():
                    if len(part) > size:
                        spans.extend(_recursive_spans(part, off, size, overlap))
                    else:
                        spans.append((off, off + len(part)))
                off += len(part) + len(sep)
            return _window(spans, size, overlap)
    return _hard_spans(text, base, size, overlap)


def _window(spans: list[tuple[int, int]], size: int, overlap: int) -> list[tuple[int, int]]:
    """把有序原子片段合并为 ≤ size 的窗口，相邻窗口按 overlap 回看重叠片段。"""
    out: list[tuple[int, int]] = []
    n = len(spans)
    i = 0
    while i < n:
        s = spans[i][0]
        j = i
        while j + 1 < n and spans[j + 1][1] - s <= size:
            j += 1
        out.append((s, spans[j][1]))
        if j + 1 >= n:
            break
        # 下一个窗口起点：回看到 overlap 界内最早的片段（且必须严格前进）
        k = j + 1
        if overlap:
            limit = spans[j][1] - overlap
            while k > i + 1 and spans[k - 1][0] > limit:
                k -= 1
        i = k
        while i < n and spans[i][0] <= s:  # 单片段超长等异常时的前进保证
            i += 1
    return out


def _hard_spans(text: str, base: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """无分隔符可用时的定长硬切（窗口间带 overlap）。"""
    step = max(1, size - overlap)
    out = []
    for j in range(0, len(text), step):
        if text[j:j + size].strip():
            out.append((base + j, base + min(j + size, len(text))))
        if j + size >= len(text):
            break
    return out


__all__ = [
    "CHINESE_SEPARATORS", "DEFAULT_CHUNK_SIZE", "DEFAULT_CHUNK_OVERLAP_RATIO",
    "Chunk", "ChunkParams", "split_sentences_cn", "chunk_text",
]
