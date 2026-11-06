# app/adapters/axiom_doc_evidence.py - 公理候选的文档证据锚定（O4 公理质量强化）
# 统计/LLM 双通道产出的公理候选原本只带「实例统计」（subjects/chains/rate），
# 审核员看不到文档里的原始表述。本模块把候选涉及的类/关系名回到文档切片里
# 找原文语句，锚定为 doc_evidence（文档名 + 原句片段 + 文档 id）——
# 公理不再是无出处的统计推断，审核员可点开原文核对业务表述。
# 纯函数可测；切片由服务层一次拉取传入（防每候选一查）。

from __future__ import annotations

from typing import Any

# 单候选最多锚定的文档原句数
MAX_QUOTES_PER_CANDIDATE = 2
# 原句片段半径（首个命中词前后的字符数），超长切片截成审核卡可读片段
SNIPPET_RADIUS = 90
# 参与匹配的最短标签长度（单字标签如「人」会在全文乱命中）
MIN_LABEL_LEN = 2


def _involved_labels(cand: dict) -> list[str]:
    """候选涉及的类/关系/实例标签（去重保序）：targets + examples 里的实体名。"""
    labels: list[str] = []
    for key in ("predicate", "value"):
        v = str(cand.get(key) or "").strip()
        if v:
            labels.append(v)
    for key in ("predicates", "classes"):
        for v in (cand.get(key) or []):
            v = str(v or "").strip()
            if v:
                labels.append(v)
    ev = cand.get("evidence") or {}
    for ex in (ev.get("examples") or []) + (ev.get("llm_examples") or []):
        if isinstance(ex, dict):
            for key in ("entity", "subject", "object"):
                v = str(ex.get(key) or "").strip()
                if v and key != "note":
                    labels.append(v)
            # 形如 {关系A: "主体", 关系B: "客体"} 的互逆样本
            for k, v in ex.items():
                if k not in ("entity", "subject", "object", "note"):
                    v = str(v or "").strip()
                    if v:
                        labels.append(v)
    seen: set[str] = set()
    return [x for x in labels if len(x) >= MIN_LABEL_LEN
            and not (x in seen or seen.add(x))]


def _snippet(text: str, pos: int) -> str:
    """命中位置 → 可读片段（回退到最近的句/行边界，带省略号提示）。"""
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(text), pos + SNIPPET_RADIUS)
    cut = text[start:end].strip()
    for sep in ("。", "\n"):
        idx = cut.find(sep)
        if 0 < idx < len(cut) - 1:
            cut = cut[:idx + 1]
            break
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{cut}{suffix}"


def collect_doc_evidence(chunks: list[dict], cand: dict,
                         max_quotes: int = MAX_QUOTES_PER_CANDIDATE) -> list[dict]:
    """候选 → 文档原句证据列表（按命中标签数降序，同分取靠前切片）。

    chunks 形状：[{document_id, filename, text}]；至少命中 2 个不同标签的
    切片才算证据（单标签 = 只是提到了某个类名，成对共现才与公理相关）。
    """
    labels = _involved_labels(cand)
    if len(labels) < 2:
        return []  # 互逆/互斥等至少涉及两个名字；单标签候选无共现证据
    scored: list[tuple[int, int, dict]] = []  # (-hits, chunk_idx, item)
    for idx, ch in enumerate(chunks):
        text = str(ch.get("text") or "")
        if not text:
            continue
        hits = [(text.find(lbl), lbl) for lbl in labels if lbl in text]
        hit_labels = len({lbl for _, lbl in hits})
        if hit_labels < 2:
            continue
        first_pos = min((p for p, _ in hits if p >= 0), default=-1)
        if first_pos < 0:
            continue
        scored.append((-hit_labels, idx, {
            "document_id": ch.get("document_id"),
            "doc_file": ch.get("filename") or "未知文档",
            "quote": _snippet(text, first_pos),
        }))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in scored[:max_quotes]]


def attach_doc_evidence(candidates: list[dict], chunks: list[dict]) -> int:
    """就地给候选挂 doc_evidence；返回挂上证据的候选数。无证据的候选保持
    原状（统计证据仍可人审，不算废单）。"""
    attached = 0
    for cand in candidates:
        quotes = collect_doc_evidence(chunks, cand)
        if quotes:
            ev = cand.get("evidence")
            if not isinstance(ev, dict):
                ev = {}
                cand["evidence"] = ev
            ev["doc_evidence"] = quotes
            attached += 1
    return attached


__all__ = ["attach_doc_evidence", "collect_doc_evidence",
           "MAX_QUOTES_PER_CANDIDATE"]
