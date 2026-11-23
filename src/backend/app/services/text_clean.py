# app/services/text_clean.py - 解析后文本清洗（水印/版式文字）
#
# 背景：带水印的 PDF（斜向平铺文字、整页背景图）会把「内部资料/禁止外传/姓名/日期」
# 混进正文：docling 文本路径会把它当正文（实测 2 页文档多出 12 处、字符 +38%），
# OCR 路径会从位图里读出水印（实测 24 处、字符翻倍）。这些噪声会进切片、抽取、
# 向量化，污染 TBox/ABox 与证据引用。
#
# 本模块只做一件事：识别「跨页高频重复的短版式串」并从正文移除；表格行、代码块
# 一律不碰，标题只剔除行尾被拼进来的水印；带提示词的强水印优先处理，无提示词的
# 可疑重复串有删除比例安全阀；删除字符占比过高时整体放弃（避免把正文清空）。
from __future__ import annotations

import re
from collections import Counter

# 水印/密级常见词：出现即视为强信号（用组合词，避免「内部审批」这类正文误伤）
WATERMARK_HINTS = (
    "内部资料", "仅供内部", "禁止外传", "请勿外传", "不得复制", "禁止复制",
    "水印", "样本", "样例", "作废",
)
# 密级词单独出现也可能是正文（如「保密制度」），只在短行里认
WEAK_WATERMARK_HINTS = ("机密", "密级", "保密")
WEAK_HINT_MAX_LEN = 20

MIN_REPEAT = 3              # 最少重复次数
MAX_LINE_LEN = 90           # 只处理短行（水印/页眉页脚级别）
MAX_PHRASE_LEN = 60         # 行内嵌短语的长度上限
MAX_AMBIGUOUS_RATIO = 0.30  # 无提示词的可疑重复串：删除行占比上限
MAX_REMOVED_CHAR_RATIO = 0.60  # 删除字符占比上限（超过视为误判，整体放弃）


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _has_hint(text: str) -> bool:
    if any(h in text for h in WATERMARK_HINTS):
        return True
    return len(text) <= WEAK_HINT_MAX_LEN and any(h in text for h in WEAK_WATERMARK_HINTS)


def _looks_like_watermark(norm: str, count: int) -> bool:
    """判断一条重复短串是否像版式文字（而不是业务内容）。"""
    if not norm or len(norm) < 4:
        return False
    if _has_hint(norm):
        return True
    # 无提示词时保守：只有「短 + 多次重复 + 含数字（日期/编号）」才认
    if count >= 6 and len(norm) <= 40 and _has_digit(norm):
        return True
    # 极短且高频的重复行（页眉页脚样式），如公司名/页码组合
    if count >= 8 and len(norm) <= 20:
        return True
    return False


def _phrase_from_line(norm: str) -> str | None:
    """从重复行里取出水印短语。

    - 提示词在行首/短行内：取“提示词到行尾”那一段（行内嵌水印也能抠掉，不误伤前缀正文）；
    - 整行都像水印且足够短：整行作为短语；
    - 其余返回 None（不处理长句里的偶发提示词）。
    """
    if not _has_hint(norm):
        return None
    hints = WATERMARK_HINTS + WEAK_WATERMARK_HINTS
    best = -1
    for h in hints:
        idx = norm.find(h)
        if idx < 0:
            continue
        tail = norm[idx:]
        # 尾部短且无句末标点 → 视为拼在正文尾部的水印（长段落末尾也能处理）
        if 4 <= len(tail) <= MAX_LINE_LEN and not re.search(r"[。！？；]", tail):
            best = idx if best < 0 else min(best, idx)
    if best >= 0:
        return norm[best:]
    if len(norm) <= MAX_PHRASE_LEN:
        return norm
    return None


def strip_watermarks(text: str, *, min_repeat: int = MIN_REPEAT,
                     max_line_len: int = MAX_LINE_LEN) -> tuple[str, dict]:
    """移除跨页高频重复的水印/版式短串。

    返回 (清洗后文本, 统计)。统计含 lines_removed / phrases_removed / skipped。
    不变量：表格行（以 | 开头）、代码围栏内内容一律不修改；标题保留标题本身，
    只剔除行尾被拼进来的水印。
    """
    stats = {"lines_removed": 0, "phrases_removed": 0, "skipped": False}
    if not text or not text.strip():
        return text, stats

    lines = text.split("\n")
    norms = [_norm(ln) for ln in lines]

    counts: Counter[str] = Counter()
    for raw, n in zip(lines, norms):
        s = raw.lstrip()
        if not n or s.startswith(("|", "#", "```")):
            continue
        if len(n) > max_line_len:
            continue
        counts[n] += 1

    candidates = {n for n, c in counts.items()
                  if c >= min_repeat and _looks_like_watermark(n, c)}
    if not candidates:
        return text, stats

    # 待删除短语：候选行本身 + 行内嵌的水印尾段（按长度倒序，先长后短）
    phrases: set[str] = set()
    for n in candidates:
        p = _phrase_from_line(n)
        if p:
            phrases.add(p)
    ordered_phrases = sorted(phrases, key=len, reverse=True)

    out: list[str] = []
    in_code = False
    removed_hinted = removed_ambiguous = removed_chars = 0
    for raw, n in zip(lines, norms):
        s = raw.lstrip()
        if s.startswith("```"):
            in_code = not in_code
            out.append(raw)
            continue
        if in_code or s.startswith(("|", ">")):
            out.append(raw)
            continue
        if s.startswith("#"):
            # 标题行保留标题本身，只剔除行尾被拼进来的水印（docling 常把水印并到段落/标题尾部）
            cleaned_h = n
            for p in ordered_phrases:
                idx = cleaned_h.find(p)
                if idx > 0:
                    tail = cleaned_h[idx:]
                    if len(tail) <= 70 and not re.search(r"[。！？；]", tail):
                        cleaned_h = cleaned_h[:idx].rstrip(" ，,;；")
                        removed_chars += len(tail)
                        stats["phrases_removed"] += 1
                    break
            out.append(cleaned_h if cleaned_h != n else raw)
            continue
        if not n:
            out.append(raw)
            continue

        cleaned = n
        hit_phrase = False
        for p in ordered_phrases:
            idx = cleaned.find(p)
            if idx < 0:
                continue
            tail = cleaned[idx:]
            # 行内/行尾水印：尾部短且没有句末标点 → 从水印起到行尾一起删（保留前缀正文）；
            # 否则只抠掉短语本身。
            if len(tail) <= 70 and not re.search(r"[。！？；]", tail):
                cleaned = cleaned[:idx].rstrip(" ，,;；")
                removed_chars += len(tail)
            else:
                cleaned = cleaned.replace(p, "")
                removed_chars += len(p)
            hit_phrase = True
            break
        if hit_phrase:
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
            if not cleaned:
                stats["lines_removed"] += 1
                removed_hinted += 1
                continue
            stats["phrases_removed"] += 1
            out.append(cleaned)
            continue

        if n in candidates and _looks_like_watermark(n, counts[n]):
            stats["lines_removed"] += 1
            removed_chars += len(n)
            if _has_hint(n):
                removed_hinted += 1
            else:
                removed_ambiguous += 1
            continue
        out.append(raw)

    nonempty = sum(1 for ln in lines if ln.strip())
    total_chars = sum(len(n) for n in norms if n)
    # 安全阀：无提示词的可疑重复串删太多 → 误判；删除字符占比过高 → 会把正文清空
    if nonempty and (removed_ambiguous > nonempty * MAX_AMBIGUOUS_RATIO
                     or (total_chars and removed_chars > total_chars * MAX_REMOVED_CHAR_RATIO)):
        return text, {"lines_removed": 0, "phrases_removed": 0, "skipped": True}

    return "\n".join(out), stats


__all__ = ["strip_watermarks", "WATERMARK_HINTS"]
