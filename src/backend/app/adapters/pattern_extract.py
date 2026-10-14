# app/adapters/pattern_extract.py - 置信度方法链 pattern 层（O2-B11）
# 结构化目标（日期/金额/编号/证件号/标准号）正则直抽：毫秒级、零 LLM 成本。
# 职责（与 LLM 的分工——LLM 只兜底隐式语义，本层做两件事）：
#   1. 直抽补全：实体附近窗口内的结构化值，补进 TBox 声明的结构化属性（LLM 漏抽时）
#   2. 校准：LLM 给的结构化属性值与 pattern 形状不符 → 降置信标记（低置信复核链路）
# 纯函数可测、零外部依赖。

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 结构化值形状（中文场景）
PATTERNS: dict[str, str] = {
    "date": (
        r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{4}-\d{1,2}-\d{1,2}"
        r"|\d{4}/\d{1,2}/\d{1,2}|\d{4}-\d{1,2}|\d{4}年\d{1,2}月底?|\d{4}年度?"),
    "amount": (
        r"[¥￥]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:亿|万)?元"
        r"|\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|万美元|亿美元)"),
    "credit_code": r"[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}",
    "id_card": r"\d{17}[\dXx]|\d{15}",
    "phone": r"(?:\+86[-\s]?)?1[3-9]\d{9}",
    "standard_no": r"GB[/T\s]*\d+(?:\.\d+)?(?:[-—]\d{4})?|Q/[A-Z0-9]{2,8}\s?\d+",
}

_COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}

# 值形状反查（校准用）：值能命中哪种结构化形状
_VALUE_KIND_TESTS = {
    "credit_code": re.compile(r"^" + PATTERNS["credit_code"] + r"$"),
    "id_card": re.compile(r"^" + PATTERNS["id_card"] + r"$"),
    "phone": re.compile(r"^" + PATTERNS["phone"] + r"$"),
    "amount": re.compile(r"^(?:[¥￥]\s?)?\d[\d,]*(?:\.\d+)?\s*(?:亿|万)?(?:元|美元)?$"
                         r"|^[¥￥]\s?\d[\d,]*(?:\.\d+)?$"),
    "date": re.compile(r"^\d{4}(?:年|-|/)\d{1,2}(?:月|-|/)\d{0,2}日?(?:底)?$|^\d{4}年度?$"),
    "standard_no": re.compile(r"^" + PATTERNS["standard_no"] + r"$"),
}

# data_type/属性名 → 期望形状（TBox 声明驱动；data_type 模糊匹配）
_DATATYPE_TO_KIND = {
    "date": "date", "datetime": "date", "日期": "date", "时间": "date",
    "amount": "amount", "money": "amount", "金额": "amount", "价格": "amount",
    "number": "amount", "数字": "amount",
    "credit_code": "credit_code", "统一社会信用代码": "credit_code",
    "id_card": "id_card", "身份证": "id_card", "证件号": "id_card",
    "phone": "phone", "电话": "phone", "手机": "phone",
    "standard_no": "standard_no", "标准号": "standard_no",
}

# 窗口：实体提及前后多少字符内找到的结构化值才算"贴近"
CONTEXT_WINDOW = 60
PATTERN_CONFIDENCE = 0.95   # pattern 直抽的置信度（确定性正则）
CALIBRATE_FLOOR = 0.55      # 校准不通过时置信度压到的下限


@dataclass
class PatternMatch:
    kind: str
    value: str
    start: int
    end: int
    context: str = ""
    matched_labels: list = field(default_factory=list)


def pattern_find(chunk_text: str, near_labels: list[str] | None = None) -> list[PatternMatch]:
    """切片内结构化值直扫；near_labels 非空时只保留实体窗口内的命中。"""
    text = str(chunk_text or "")
    out: list[PatternMatch] = []
    seen_spans: set[tuple[int, int]] = set()
    for kind, rx in _COMPILED.items():
        for m in rx.finditer(text):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            # 跨形状重叠去重：长命中优先（id_card 会覆盖部分 phone/数字）
            if any(s <= span[0] and span[1] <= e for (s, e) in seen_spans):
                continue
            seen_spans.add(span)
            lo, hi = max(0, m.start() - CONTEXT_WINDOW), min(len(text), m.end() + CONTEXT_WINDOW)
            match = PatternMatch(kind=kind, value=m.group(0), start=m.start(),
                                 end=m.end(), context=text[lo:hi])
            if near_labels:
                hit_labels = [lbl for lbl in near_labels
                              if lbl and lbl in text[lo:hi]]
                if not hit_labels:
                    continue
                match.matched_labels = hit_labels
            out.append(match)
    return out


def _class_prop_kinds(tbox_summary: dict, class_label: str) -> dict[str, str]:
    """类声明的结构化属性：{属性名: 期望kind}（class_properties/class data_type 驱动）。"""
    out: dict[str, str] = {}
    cp = (tbox_summary or {}).get("class_properties") or {}
    props = cp.get(class_label) or []
    for p in props:
        name = str((p or {}).get("name") or "").strip()
        if not name:
            continue
        dt = str((p or {}).get("data_type") or "").strip().lower()
        kind = _DATATYPE_TO_KIND.get(dt) or _DATATYPE_TO_KIND.get(
            str((p or {}).get("data_type") or "").strip())
        if kind:
            out[name] = kind
    return out


def fill_and_calibrate(entities: list, chunks: list,
                       tbox_summary: dict) -> tuple[list, int, int]:
    """pattern 层主入口（纯函数）：返回 (entities, filled, calibrated)。

    filled：LLM 漏抽、pattern 依 TBox 声明补进 props 的结构化属性数；
    calibrated：LLM 值与 pattern 形状不符、被降置信标记的实体数。
    实体：ExtractedEntity 或同形状对象（props dict、confidence float）。
    """
    if not entities:
        return entities, 0, 0
    text_by_idx = {c.get("index"): str(c.get("text") or "") for c in (chunks or [])}

    def _get(item, k, default=None):
        return item.get(k, default) if isinstance(item, dict) else getattr(item, k, default)

    def _set(item, k, v):
        if isinstance(item, dict):
            item[k] = v
        else:
            setattr(item, k, v)

    filled = calibrated = 0
    for e in entities:
        cls = str(_get(e, "class_label") or "").strip()
        props = dict(_get(e, "props") or {})
        if not cls:
            continue
        declared = _class_prop_kinds(tbox_summary, cls)
        if not declared:
            continue
        chunk = _get(e, "chunk_index", 0)
        text = text_by_idx.get(chunk, "")
        if not text:
            continue
        label = str(_get(e, "label") or "").strip()
        matches = pattern_find(text, near_labels=[label] if label else None)
        if not matches:
            continue
        changed = False
        # 1) 校准：已填的结构化属性值形状不符 → 降置信（进既有低置信复核链路）
        for prop_name, kind in declared.items():
            val = str(props.get(prop_name) or "").strip()
            if not val:
                continue
            tester = _VALUE_KIND_TESTS.get(kind)
            if tester is not None and not tester.match(val):
                conf = float(_get(e, "confidence", 0.0) or 0.0)
                if conf > CALIBRATE_FLOOR:
                    _set(e, "confidence", round(CALIBRATE_FLOOR, 2))
                    calibrated += 1
                    changed = True
        # 2) 直抽补全：声明了但 LLM 漏抽的属性，窗口内就近取值
        for prop_name, kind in declared.items():
            if props.get(prop_name) not in (None, ""):
                continue
            hit = next((m for m in matches if m.kind == kind), None)
            if hit is None:
                continue
            props[prop_name] = hit.value
            filled += 1
            changed = True
        if changed:
            _set(e, "props", props)
    return entities, filled, calibrated


__all__ = ["PATTERNS", "PatternMatch", "pattern_find", "fill_and_calibrate",
           "PATTERN_CONFIDENCE", "CALIBRATE_FLOOR"]
