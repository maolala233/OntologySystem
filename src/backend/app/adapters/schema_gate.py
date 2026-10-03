# app/adapters/schema_gate.py - SchemaGate 校验闸门 + promote（docs/design/04 §4.1/§4.2）
# M3-4 实装。类型匹配四级精确化（修复缺陷 #4：废除子串双向包含）；
# confidence 为 LLM 自报值（修复缺陷 #3，本模块只按阈值生成违例，不伪造成功率）。

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# 实体置信度低于该值 → 生成 low_confidence 审核项（04 §7.1）
LOW_CONFIDENCE_THRESHOLD = 0.70
# promote "auto" 模式的触发阈值：连续 N 个 chunk 出现同类新类型（03 §8）
PROMOTE_AUTO_MIN_CHUNKS = 3


class PromotePolicy(str, Enum):
    REVIEW = "review"    # 新类型生成 review_items(new_class) 审核项
    DISCARD = "discard"  # 仅计入 discarded_count
    AUTO = "auto"        # 连续 >= PROMOTE_AUTO_MIN_CHUNKS 个 chunk 出现时自动建候选类（仍需审核）


class GateViolation(BaseModel):
    kind: str                    # unknown_class | unknown_predicate | domain_range_mismatch | low_confidence | missing_evidence
    label: str
    detail: dict[str, Any] = Field(default_factory=dict)


class GateResult(BaseModel):
    passed: list = Field(default_factory=list)      # 通过校验的实体/关系（原对象，可能被规范化 class_label）
    violations: list[GateViolation] = Field(default_factory=list)
    discarded_count: int = 0
    withheld_count: int = 0                         # review 扣留待审（不算丢弃）
    promote_candidates: list[dict] = Field(default_factory=list)


def _norm(s: str) -> str:
    """归一化（全半角/全角空格/ASCII 小写），与 match_class 的四级匹配口径一致。"""
    out = []
    for ch in (s or "").strip():
        code = ord(ch)
        if ch == "　":
            continue
        if 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII 区
            ch = chr(code - 0xFEE0)
        out.append(ch.lower() if ch.isascii() else ch)
    return "".join(out)


def match_class(label: str, tbox_classes: dict[str, list[str]]) -> Optional[str]:
    """四级类型匹配（04 §4.2）：精确 → 归一化 → 别名表 → None。

    tbox_classes: {类名: [别名...]}。返回命中的规范类名。
    ★ 缺陷 #4 回归点："投资者" 对 "机构投资者" 必须返回 None（废除子串双向包含）。
    """
    if not label:
        return None
    # 1) 精确
    if label in tbox_classes:
        return label
    # 2) 归一化
    norm_label = _norm(label)
    for cls, aliases in tbox_classes.items():
        if _norm(cls) == norm_label:
            return cls
    # 3) 别名表
    for cls, aliases in tbox_classes.items():
        for alias in aliases or []:
            if _norm(alias) == norm_label:
                return cls
    return None


def locate_quote(evidence: str, chunk_text: str) -> bool:
    """证据定位（04 §4.1）：在 chunk 文本中检索 evidence 子串。

    先原文包含，再空白不敏感兜底（LLM 常改写空格/换行）；都不中即定位失败。
    """
    if not evidence or not chunk_text:
        return False
    if evidence in chunk_text:
        return True
    ev = "".join(evidence.split())
    tx = "".join(chunk_text.split())
    return bool(ev) and ev in tx


def _item_get(item: Any, key: str, default: Any = None) -> Any:
    """item 兼容 pydantic 模型与 dict 两种形态。"""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _item_set(item: Any, key: str, value: Any) -> None:
    if isinstance(item, dict):
        item[key] = value
    else:
        setattr(item, key, value)


class _Gate:
    """apply_gate 的状态机：promote 候选按 label 聚合 chunk 出现次数（auto 阈值判定）。"""

    def __init__(self, tbox: dict, policy: PromotePolicy):
        self.tbox = tbox if isinstance(tbox, dict) else {}
        self.policy = policy
        self.classes: dict[str, list[str]] = self.tbox.get("classes") or {}
        self.props: dict[str, dict] = self.tbox.get("object_properties") or {}
        self.chunks: dict[int, str] = self.tbox.get("chunks") or {}
        # 已知实例 label → class（domain/range 校验用；调用方可预置 tbox["label_class"]）
        self.label_class: dict[str, str] = dict(self.tbox.get("label_class") or {})
        self.result = GateResult()
        # promote 聚合：label → {kind, chunk_indices:set, hold:[items]}
        self._cand: dict[tuple[str, str], dict] = {}

    # ---------- 违例处理 ----------
    def _unknown_type(self, item: Any, kind: str, label: str, chunk_index: int) -> None:
        """unknown_class / unknown_predicate 统一走 promote_policy（03 §8）。"""
        self.result.violations.append(GateViolation(
            kind=kind, label=label, detail={"chunk_index": chunk_index}))
        key = (kind, label)
        cand = self._cand.setdefault(key, {
            "kind": kind, "label": label, "chunk_indices": set(), "hold": []})
        cand["chunk_indices"].add(chunk_index)
        if self.policy == PromotePolicy.DISCARD:
            self.result.discarded_count += 1
            return  # 直接丢弃，不入候选持有
        cand["hold"].append(item)  # review/auto：先扣下，循环结束后按阈值放行

    def _settle_promote(self) -> None:
        for cand in self._cand.values():
            chunks_n = len(cand["chunk_indices"])
            auto_ok = self.policy == PromotePolicy.AUTO and chunks_n >= PROMOTE_AUTO_MIN_CHUNKS
            entry = {
                "kind": cand["kind"], "label": cand["label"],
                "chunk_indices": sorted(cand["chunk_indices"]),
                "policy": self.policy.value,
                "auto_promoted": auto_ok,
            }
            self.result.promote_candidates.append(entry)
            if auto_ok:
                # auto 且达到阈值：候选放行为通过项（类名保持 LLM 原值，等审核落 TBox）
                self.result.passed.extend(cand["hold"])
            # review / 未达阈值的 auto：hold 项不放行（转 review_items 由调用方落库）
            else:
                self.result.withheld_count += len(cand["hold"])

    # ---------- 通用校验 ----------
    def _check_confidence(self, item: Any, label: str) -> None:
        conf = _item_get(item, "confidence", 0.0)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < LOW_CONFIDENCE_THRESHOLD:
            self.result.violations.append(GateViolation(
                kind="low_confidence", label=label,
                detail={"confidence": conf, "threshold": LOW_CONFIDENCE_THRESHOLD}))

    def _check_evidence(self, item: Any, label: str, chunk_index: int) -> None:
        evidence = str(_item_get(item, "evidence", "") or "")
        chunk_text = self.chunks.get(chunk_index, "")
        if evidence and chunk_text and not locate_quote(evidence, chunk_text):
            # 降级为 chunk 摘要 + missing_evidence 审核项（04 §4.1）
            _item_set(item, "evidence", f"[chunk {chunk_index} 摘要] {chunk_text[:120]}")
            self.result.violations.append(GateViolation(
                kind="missing_evidence", label=label,
                detail={"chunk_index": chunk_index,
                        "reason": "evidence 未在 chunk 原文中定位到"}))

    # ---------- 实体 ----------
    def gate_entity(self, item: Any) -> None:
        label = str(_item_get(item, "label", "") or "")
        class_label = str(_item_get(item, "class_label", "") or "")
        chunk_index = _item_get(item, "chunk_index", 0) or 0
        cls = match_class(class_label, self.classes)
        if cls is None:
            self._unknown_type(item, "unknown_class", class_label or label, chunk_index)
            return
        _item_set(item, "class_label", cls)  # 规范化到 TBox 类名
        self.label_class[label] = cls
        self._check_confidence(item, label)
        self._check_evidence(item, label, chunk_index)
        self.result.passed.append(item)

    # ---------- 关系 ----------
    def gate_relation(self, item: Any) -> None:
        subject_label = str(_item_get(item, "subject_label", "") or "")
        predicate = str(_item_get(item, "predicate", "") or "")
        object_label = str(_item_get(item, "object_label", "") or "")
        chunk_index = _item_get(item, "chunk_index", 0) or 0

        pred = match_class(predicate, self.props)  # 四级匹配同样适用于谓词名
        if pred is None:
            self._unknown_type(item, "unknown_predicate", predicate, chunk_index)
            return
        _item_set(item, "predicate", pred)

        # domain/range 校验（04 §4.2）：两端类型已知才判，未知（unknown_class 路径）不误伤
        prop_def = self.props.get(pred) or {}
        subj_cls = self.label_class.get(subject_label)
        obj_cls = self.label_class.get(object_label)
        domain = prop_def.get("domain")
        rng = prop_def.get("range")
        mismatch = None
        if domain and subj_cls and match_class(subj_cls, {domain: []}) is None:
            mismatch = {"side": "domain", "expected": domain, "actual": subj_cls}
        elif rng and obj_cls and match_class(obj_cls, {rng: []}) is None:
            mismatch = {"side": "range", "expected": rng, "actual": obj_cls}
        if mismatch:
            self.result.violations.append(GateViolation(
                kind="domain_range_mismatch", label=predicate,
                detail={**mismatch, "subject": subject_label, "object": object_label}))
            self.result.discarded_count += 1
            return

        self._check_confidence(item, f"{subject_label}-{predicate}->{object_label}")
        self._check_evidence(item, f"{subject_label}-{predicate}->{object_label}", chunk_index)
        self.result.passed.append(item)


def apply_gate(items: list, tbox: dict,
               promote_policy: PromotePolicy | str = PromotePolicy.REVIEW) -> GateResult:
    """对抽取结果执行闸门（04 §4.1）：类/谓词四级匹配、domain-range 校验、
    confidence 阈值、evidence 定位；违例按 promote_policy 处理。

    tbox 结构：
      classes:            {类名: [别名...]}
      object_properties:  {谓词名: {"domain": 类名|None, "range": 类名|None}}
      chunks:             {chunk_index: 原文}（可选，missing_evidence 定位用）
      label_class:        {实例label: 类名}（可选，预置已知实例类型）
    items 元素：ExtractedEntity / ExtractedRelation 或同形 dict。
    通过项原样进 result.passed（class_label/predicate 被规范化为 TBox 名）；
    review 候选不进 passed（调用方落 review_items）；auto 达阈值进 passed。
    """
    policy = PromotePolicy(promote_policy) if isinstance(promote_policy, str) else promote_policy
    gate = _Gate(tbox, policy)
    for item in items or []:
        if _item_get(item, "predicate") is not None:
            gate.gate_relation(item)
        else:
            gate.gate_entity(item)
    gate._settle_promote()
    return gate.result


__all__ = [
    "PromotePolicy", "GateViolation", "GateResult", "match_class", "locate_quote", "apply_gate",
    "LOW_CONFIDENCE_THRESHOLD", "PROMOTE_AUTO_MIN_CHUNKS",
]
