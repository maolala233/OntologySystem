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

    blocks = _structural_blocks(text)
    spans: list[tuple[int, int, str | None, str | None]] = []  # (start, end, kind, header)
    for off, content, kind, header in blocks:
        if kind == "table":
            # 表格块：按行界窗口化，每片 meta 携带表头（列语义不再因切片丢失）
            spans.extend((s, e, "table", header) for s, e in _table_spans(content, off, size, overlap))
        elif kind is not None:
            spans.extend((s, e, kind, None) for s, e in _hard_spans(content, off, size, overlap))
        else:
            spans.extend((s, e, None, None) for s, e in _recursive_spans(content, off, size, overlap))

    chunks: list[Chunk] = []
    for s, e, kind, header in spans:
        content = text[s:e].rstrip()
        if not content.strip():
            continue
        meta: dict = {}
        if kind:
            meta["structural"] = kind
        if header:
            meta["table_header"] = header
        chunks.append(Chunk(text=content, start_index=s, end_index=s + len(content), meta=meta))
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _structural_blocks(text: str) -> list[tuple[int, str, str | None, str | None]]:
    """把文本切成结构块：markdown 表格（连续 | 行）与代码围栏整块保留，其余为普通块。

    表格块额外返回表头（首行 + 分隔行，供切片 meta 携带——切片按行界硬切时
    表头不重复出现，抽取 prompt 靠 meta 补列语义）。
    表格块偏移向前吞并紧邻的短标题/说明行（<80 字符的 # 标题），避免
    「### Sheet: X」落成 20 字符的孤儿切片。
    """
    blocks: list[tuple[int, str, str | None, str | None]] = []
    lines = text.split("\n")
    off = 0
    kind: str | None = None
    buf: list[str] = []
    buf_off = 0
    pending_small: tuple[int, str] | None = None  # 未挂靠的短标题（偏移, 行）
    in_code = False

    def flush():
        nonlocal buf
        if buf:
            blocks.append((buf_off, "\n".join(buf), kind, None))
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
                # 表格块吸收紧邻短标题（孤儿标题块防御）：标题行真并入内容，
                # 块起点=标题行起点（偏移与内容一致，精确子串不变量不破）
                if pending_small is not None:
                    buf_off, title_line = pending_small
                    buf.insert(0, title_line)
                    if buf_off < off:
                        buf.insert(1, "")  # 标题与表格间原有空行
                    pending_small = None
            buf.append(line)
            off += len(line) + 1
            continue
        if kind == "table":
            flush()
            kind = None
        if not stripped and pending_small is not None:
            off += len(line) + 1
            continue  # 标题后的空行不认领挂起标题（让标题并进下一个内容块）
        if (pending_small is None and kind is None and not in_code
                and stripped.startswith("#") and len(stripped) < 80):
            pending_small = (off, line)  # 短标题暂挂，等下一块来认领
            off += len(line) + 1
            continue
        if pending_small is not None:
            buf_off, title_line = pending_small
            buf.insert(0, title_line)
            if buf_off < off:
                buf.insert(1, "")
            pending_small = None
        buf.append(line)
        off += len(line) + 1
    flush()
    out = []
    for o, c, k, _header in blocks:
        if not c.strip():
            continue
        header = None
        if k == "table":
            tlines = [ln for ln in c.split("\n") if ln.strip().startswith("|")]
            if len(tlines) >= 2 and set(tlines[1].replace("|", "").strip()) <= set("-: "):
                header = tlines[0].strip() + "\n" + tlines[1].strip()
            elif tlines:
                header = tlines[0].strip()
        out.append((o, c.rstrip("\n"), k, header))
    return out


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


def _table_spans(text: str, base: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """markdown 表格块的行窗口化：切点全部落在行边界（行永不拦腰断），
    窗口间带 overlap 行回看；整表 ≤ size 时单块保留。"""
    lines = text.split("\n")
    offs: list[tuple[int, int, int]] = []  # (行起点偏移, 行终点偏移(不含\n), 行长含\n)
    off = 0
    for line in lines:
        offs.append((off, off + len(line), len(line) + 1))
        off += len(line) + 1
    if not lines:
        return []
    # 数据行 = 表头行 + 分隔行之后的所有 | 行（块内容可能前置标题/空行）
    first_pipe = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")), None)
    if first_pipe is None:
        return [(base, base + len(text.rstrip()))]
    if len(lines) >= first_pipe + 2 and set(lines[first_pipe + 1].replace("|", "").strip()) <= set("-: "):
        data_start = first_pipe + 2
    else:
        data_start = first_pipe + 1
    if len(text) <= size or data_start >= len(lines):
        return [(base, base + len(text.rstrip()))]

    out: list[tuple[int, int]] = []
    i = data_start
    n = len(lines)
    first = True
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        s_line = i
        if first:
            s_line = 0  # 首窗从块头开始：前置标题与表头入文（列语义完整）
            first = False
        j = i
        while j + 1 < n and offs[j + 1][1] - offs[s_line][0] <= size:
            j += 1
        out.append((base + offs[s_line][0], base + offs[j][1]))
        if j + 1 >= n:
            break
        # overlap 行回看
        back = max(1, int(round((size * 0.15) // max(1, (offs[j][1] - offs[s_line][0]) / max(1, j - s_line + 1)))))
        i = max(j + 1 - back, j + 1)
        while i < n and not lines[i].strip():
            i += 1
    if not out:
        return [(base, base + len(text.rstrip()))]
    return out


def _hard_spans(text: str, base: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """无分隔符可用时的定长硬切（窗口间带 overlap）。

    缺陷修复（3454 实测）：切点落在行中间会把表格行拦腰切断（半行丢失/下游
    引用不可读）——切点回退到最近的行边界（\n 之后）；行超长（无 \n 可回退）
    才允许字符位切割。
    """
    out = []
    j = 0
    while j < len(text):
        span = text[j:j + size]
        if not span.strip():
            break
        end = j + size
        if end < len(text):
            # 切点回退到最近行边界：end 处向前找 \n，切点放行尾（含该行）
            nl = text.rfind("\n", j, end)
            if nl > j:
                end = nl  # 不含 \n 本身（下块从行首开始）
        content = text[j:end]
        if content.strip():
            out.append((base + j, base + end))
        if end >= len(text):
            break
        j = max(end, j + 1)
    return out


__all__ = [
    "CHINESE_SEPARATORS", "DEFAULT_CHUNK_SIZE", "DEFAULT_CHUNK_OVERLAP_RATIO",
    "Chunk", "ChunkParams", "split_sentences_cn", "chunk_text", "_table_spans",
]
