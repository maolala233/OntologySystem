# app/adapters/axiom_suggestions.py - 公理建议引擎（O2-B8）
# 统计推断候选公理，每个候选强制携带实例统计证据（验收口径）：
#   functional     谓词对所有主体都单客体（单值率 100%）→ 建议声明函数性
#   inverse_of     谓词对反向成对出现（双向命中率 ≥ 阈值）→ 建议声明互逆
#   disjoint_with  同父兄弟类零共享实例、零同名实例 → 建议声明互斥
# 候选物化为 review_items(item_type='axiom_suggestion')，批准才写入画布公理。
# 纯函数可测；编排/物化在 services/axiom_suggestion_service.py。

from __future__ import annotations

from typing import Any

from app.adapters.resolution import normalize_label

# 建议触发阈值（宁可少荐不误荐；审核员可拒）
FUNCTIONAL_MIN_SUBJECTS = 3      # 谓词至少出现在 N 个主体上才统计有效
INVERSE_MIN_PAIRS = 2            # 反向成对至少 N 组
INVERSE_MIN_RATE = 0.8           # 双向命中率
DISJOINT_MIN_INSTANCES = 2       # 两类各自至少 N 个实例
MAX_SUGGESTIONS_PER_KIND = 20    # 每类候选上限（防刷爆审核队列）


def _norm(v: Any) -> str:
    return normalize_label(str(v or "").strip())


def suggest_functional(ents: list[dict], rels: list[dict],
                       schema: dict | None) -> list[dict]:
    """单值率 100% → functional 建议。已声明函数性的谓词跳过。"""
    schema = schema or {}
    declared = {op.get("label") for op in (schema.get("object_properties") or [])
                if isinstance(op, dict) and op.get("functional")}
    # (subject, pred) → 客体集合
    groups: dict[tuple, set[str]] = {}
    pred_labels: dict[str, str] = {}
    for r in rels:
        s_id, pred = r.get("subject_id"), str(r.get("predicate") or "").strip()
        obj = str(r.get("object_label") or "").strip()
        if s_id is None or not pred or not obj:
            continue
        groups.setdefault((s_id, _norm(pred)), set()).add(obj)
        pred_labels.setdefault(_norm(pred), pred)
    stats: dict[str, dict[str, int]] = {}
    for (_s, p_norm), objs in groups.items():
        st = stats.setdefault(p_norm, {"subjects": 0, "multi": 0})
        st["subjects"] += 1
        if len(objs) > 1:
            st["multi"] += 1
    out = []
    for p_norm, st in stats.items():
        pred = pred_labels.get(p_norm, p_norm)
        if pred in declared or st["subjects"] < FUNCTIONAL_MIN_SUBJECTS:
            continue
        if st["multi"] > 0:  # 单值率非 100% 不荐
            continue
        examples = []
        for r in rels:
            if _norm(r.get("predicate") or "") == p_norm and len(examples) < 3:
                examples.append({"subject": r.get("subject_label") or "",
                                 "object": r.get("object_label") or ""})
        out.append({
            "kind": "functional", "predicate": pred,
            "risk_tier": "low", "source": "statistical",
            "evidence": {"subjects": st["subjects"], "single_value_rate": 1.0,
                         "examples": examples},
            "summary": (f"关系「{pred}」在 {st['subjects']} 个主体上全部单客体"
                        f"（单值率 100%），符合函数性特征"),
        })
        if len(out) >= MAX_SUGGESTIONS_PER_KIND:
            break
    return out


def suggest_inverse_of(ents: list[dict], rels: list[dict],
                       schema: dict | None) -> list[dict]:
    """谓词对反向成对出现 → inverse_of 建议。已声明互逆的谓词跳过。"""
    schema = schema or {}
    declared_pairs: set[frozenset] = set()
    for op in (schema.get("object_properties") or []):
        if isinstance(op, dict) and op.get("inverse_of"):
            declared_pairs.add(frozenset((op.get("label"), op["inverse_of"])))
    pairs_by_norm: dict[str, set[tuple]] = {}  # pred_norm → {(s,o)}（label 对）
    pred_labels: dict[str, str] = {}
    for r in rels:
        p = str(r.get("predicate") or "").strip()
        s = str(r.get("subject_label") or "").strip()
        o = str(r.get("object_label") or "").strip()
        if p and s and o:
            pairs_by_norm.setdefault(_norm(p), set()).add((_norm(s), _norm(o)))
            pred_labels.setdefault(_norm(p), p)
    out = []
    norms = sorted(pairs_by_norm)
    for i, a in enumerate(norms):
        for b in norms[i + 1:]:
            if frozenset((pred_labels.get(a, a), pred_labels.get(b, b))) in declared_pairs:
                continue
            hits = pairs_by_norm[a] & {(o, s) for (s, o) in pairs_by_norm[b]}
            if len(hits) < INVERSE_MIN_PAIRS:
                continue
            rate_a = len(hits) / len(pairs_by_norm[a]) if pairs_by_norm[a] else 0
            rate_b = len(hits) / len(pairs_by_norm[b]) if pairs_by_norm[b] else 0
            if rate_a < INVERSE_MIN_RATE or rate_b < INVERSE_MIN_RATE:
                continue
            examples = []
            for (s, o) in sorted(hits)[:3]:
                examples.append({pred_labels.get(a, a): f"{s}",
                                 pred_labels.get(b, b): f"{o}"})
            out.append({
                "kind": "inverse_of", "predicates": [pred_labels.get(a, a),
                                                     pred_labels.get(b, b)],
                "risk_tier": "low", "source": "statistical",
                "evidence": {"pairs": len(hits), "rate": round(min(rate_a, rate_b), 2),
                             "examples": examples},
                "summary": (f"关系「{pred_labels.get(a, a)}」与「{pred_labels.get(b, b)}」"
                            f"有 {len(hits)} 组反向成对实例（命中率 "
                            f"{min(rate_a, rate_b):.0%}），疑似互逆"),
            })
            if len(out) >= MAX_SUGGESTIONS_PER_KIND:
                return out
    return out


def suggest_disjoint_with(ents: list[dict], rels: list[dict],
                          schema: dict | None) -> list[dict]:
    """同父兄弟类零共享实例 → disjointWith 建议。

    证据是"零共现"统计（弱证据，审核员裁定）：两类实例数、共享同名实例数 0、
    同实体双类型共现 0。已声明互斥的类对、实例不足的类对跳过。
    """
    schema = schema or {}
    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    classes_by_label = {c.get("label"): c for c in classes if c.get("label")}
    declared: set[frozenset] = set()
    for c in classes:
        for peer in (c.get("disjoint_with") or []):
            if peer and peer != c.get("label"):
                declared.add(frozenset((c["label"], peer)))

    inst_by_class: dict[str, set[str]] = {}
    ent_class: dict[str, set[str]] = {}  # label_norm → {class}（同名实例证据）
    for e in ents:
        cls = str(e.get("class_label") or "").strip()
        lbl = str(e.get("label") or "").strip()
        if not cls or not lbl:
            continue
        inst_by_class.setdefault(cls, set()).add(_norm(lbl))
        ent_class.setdefault(_norm(lbl), set()).add(cls)

    out = []
    labels = [c.get("label") for c in classes if c.get("label")]
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if frozenset((a, b)) in declared:
                continue
            ca, cb = classes_by_label.get(a) or {}, classes_by_label.get(b) or {}
            pa = {str(p) for p in (ca.get("parent_classes") or [])}
            pb = {str(p) for p in (cb.get("parent_classes") or [])}
            if not (pa & pb):  # 只对同父兄弟类荐（控制候选量）
                continue
            ia, ib = inst_by_class.get(a, set()), inst_by_class.get(b, set())
            if len(ia) < DISJOINT_MIN_INSTANCES or len(ib) < DISJOINT_MIN_INSTANCES:
                continue
            shared_labels = ia & ib
            if shared_labels:
                continue  # 有同名实例 → 反证（可能是同类），不荐
            co_typed = sum(1 for cls_set in ent_class.values()
                           if a in cls_set and b in cls_set)
            if co_typed:
                continue
            out.append({
                "kind": "disjoint_with", "classes": [a, b],
                "risk_tier": "low", "source": "statistical",
                "evidence": {"instances_a": len(ia), "instances_b": len(ib),
                             "shared_label_instances": 0, "co_typed_instances": 0},
                "summary": (f"类「{a}」（{len(ia)} 实例）与「{b}」（{len(ib)} 实例）"
                            f"同父且零同名实例、从未同体出现，疑似互斥"),
            })
            if len(out) >= MAX_SUGGESTIONS_PER_KIND:
                return out
    return out


def suggest_axioms(ents: list, rels: list, schema: dict | None) -> list[dict]:
    """公理建议主入口：三类候选（functional / inverse_of / disjoint_with）。

    ents/rels：_as_ent/_as_rel 归一形状（或行表行）。每个候选必带 evidence 统计。
    """
    from app.adapters.conflicts import _as_ent, _as_rel

    e_dicts = [_as_ent(e) for e in (ents or [])]
    e_dicts = [e for e in e_dicts if e["label"] and e["status"] != "merged"]
    r_dicts = [_as_rel(r) for r in (rels or [])]
    rels_full = []
    for r in r_dicts:
        if r.get("subject_id") is not None and r.get("predicate"):
            rels_full.append(r)
    candidates: list[dict] = []
    candidates.extend(suggest_functional(e_dicts, rels_full, schema))
    candidates.extend(suggest_inverse_of(e_dicts, rels_full, schema))
    candidates.extend(suggest_disjoint_with(e_dicts, rels_full, schema))
    return candidates


__all__ = ["suggest_axioms", "suggest_functional", "suggest_inverse_of",
           "suggest_disjoint_with", "MAX_SUGGESTIONS_PER_KIND"]
