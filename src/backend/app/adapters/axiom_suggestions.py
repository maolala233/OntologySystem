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
# 结构性谓词（rdf:type 边等）不是业务关系，不参与任何公理统计
STRUCTURAL_PREDICATES = ("instance_of", "rdftype", "type")
DOMAIN_MIN_TRIPLES = 3           # 谓词至少 N 条三元组才做 domain/range 分布推断
TRANSITIVE_MIN_CHAINS = 2        # 链式结构 ≥N 组才建议声明传递性
SYMMETRIC_MIN_PAIRS = 2          # 双向成对 ≥N 组
SYMMETRIC_MIN_RATE = 0.8         # 双向命中率
INVFUNC_MIN_OBJECTS = 3          # 反函数性：至少 N 个不同客体
DOMAIN_DOMINANT_RATE = 0.85      # 主导类占比阈值（semantica property_generator 思路）
SUBCLASS_TAILS = ("服务", "系统", "平台", "中心", "模块", "应用")  # 去尾词启发式可剥的泛化尾词


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
        if _norm(pred) in STRUCTURAL_PREDICATES:
            continue  # rdf:type 边不是业务关系（曾误产出「instance_of 是函数性」）
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


def suggest_domain_range(ents: list[dict], rels: list[dict],
                         schema: dict | None) -> list[dict]:
    """谓词主体/客体类分布 → domain_fix/range_fix 建议（semantica 思路，纯数据统计零 LLM）。

    证据：聚合某谓词全部实例边的主体/客体**实体类**分布；主导类占比 ≥0.85 且
    三元组数 ≥3 时——声明缺失则建议补声明，声明与主导类不一致则建议修正。
    这类候选证据强度高（可数、可查），与 LLM 通道同目标时合并为 both 双佐证。
    """
    schema = schema or {}
    cls_of = {}
    for e in ents:
        eid = e.get("id")
        cls = str(e.get("class_label") or "").strip()
        if eid is not None and cls:
            cls_of[eid] = cls
    ops_by_label = {str(op.get("label") or ""): op
                    for op in (schema.get("object_properties") or [])
                    if isinstance(op, dict) and op.get("label")}

    subj_dist: dict[str, dict[str, int]] = {}
    obj_dist: dict[str, dict[str, int]] = {}
    pred_labels: dict[str, str] = {}
    for r in rels:
        p = str(r.get("predicate") or "").strip()
        if not p:
            continue
        p_n = _norm(p)
        pred_labels.setdefault(p_n, p)
        s_cls = cls_of.get(r.get("subject_id"), "")
        o_cls = cls_of.get(r.get("object_id"), "")
        if s_cls:
            subj_dist.setdefault(p_n, {})
            subj_dist[p_n][s_cls] = subj_dist[p_n].get(s_cls, 0) + 1
        if o_cls:
            obj_dist.setdefault(p_n, {})
            obj_dist[p_n][o_cls] = obj_dist[p_n].get(o_cls, 0) + 1

    def _suggest(pred: str, side: str, dist: dict[str, int]) -> dict | None:
        total = sum(dist.values())
        if total < DOMAIN_MIN_TRIPLES:
            return None  # 样本太少不可信
        dominant, cnt = max(dist.items(), key=lambda kv: kv[1])
        rate = cnt / total
        if rate < DOMAIN_DOMINANT_RATE:
            return None
        op = ops_by_label.get(pred) or {}
        declared = str(op.get(side) or "").strip()
        if declared and _norm(declared) == _norm(dominant):
            return None  # 声明与数据一致
        side_name = "定义域" if side == "domain" else "值域"
        dist_text = "、".join(f"{c} {n / total:.0%}" for c, n in
                              sorted(dist.items(), key=lambda kv: -kv[1]))
        out = {
            "kind": f"{side}_fix", "predicate": pred, "value": dominant,
            "risk_tier": "low", "source": "statistical",
            "evidence": {"triples": total, "dominant_rate": round(rate, 2),
                         f"{side}_distribution": dist_text},
            "summary": (f"关系「{pred}」的 {total} 条实例边里 {rate:.0%} 的"
                        f"{side_name}是「{dominant}」"
                        + (f"，与当前声明（{declared}）不一致" if declared
                           else "，当前未声明该侧") + "，建议修正"),
        }
        return out

    out: list[dict] = []
    for p_n in sorted(subj_dist.keys() | obj_dist.keys()):
        pred = pred_labels.get(p_n, p_n)
        if len(out) >= MAX_SUGGESTIONS_PER_KIND * 2:
            break
        dist = subj_dist.get(p_n) or {}
        s = _suggest(pred, "domain", dist) if dist else None
        if s:
            out.append(s)
        dist = obj_dist.get(p_n) or {}
        s = _suggest(pred, "range", dist) if dist else None
        if s:
            out.append(s)
    return out


def suggest_subclass_by_suffix(ents: list[dict], rels: list[dict],
                               schema: dict | None) -> list[dict]:
    """去尾词启发式 → subclass_of 建议（semantica class_inferrer._find_parent_class 思路）。

    类名剥掉一个泛化尾词（服务/系统/平台/中心/模块/应用）后命中已有类 →
    建议声明 is-a。命名级证据（弱），供人审与 LLM 通道佐证；已声明父类的类跳过。
    """
    schema = schema or {}
    labels = [str(c.get("label") or "").strip()
              for c in (schema.get("classes") or [])
              if isinstance(c, dict) and c.get("label")]
    declared = {str(c.get("label") or ""): {str(p) for p in (c.get("parent_classes") or [])}
                for c in (schema.get("classes") or [])
                if isinstance(c, dict) and c.get("label")}
    out: list[dict] = []
    for label in sorted(labels):
        if len(out) >= MAX_SUGGESTIONS_PER_KIND:
            break
        for parent_label in sorted(labels, key=len, reverse=True):
            if parent_label == label or len(parent_label) < 2:
                continue
            # 标签 = 父类名 + 限定词（≥2 字）：「交易接口服务」⊑「服务」
            if not label.endswith(parent_label) or len(label) - len(parent_label) < 2:
                continue
            qualifier = label[:-len(parent_label)]
            if qualifier in SUBCLASS_TAILS and len(qualifier) <= 2:
                continue  # 「XX服务」这种泛化名不作为限定依据防噪声（弱信号也进人审，但最浅的跳过）
            if parent_label in declared.get(label, set()):
                continue  # 已声明
            out.append({
                "kind": "subclass_of", "classes": [label, parent_label],
                "risk_tier": "low", "source": "statistical",
                "evidence": {"rule": "label_suffix", "qualifier": qualifier},
                "summary": (f"类「{label}」名称是「{parent_label}」加限定词"
                            f"「{qualifier}」，疑似其子类"),
            })
            break  # 每类只荐一个最长父类（更具体的父）
    return out


def suggest_transitive(ents: list[dict], rels: list[dict],
                       schema: dict | None) -> list[dict]:
    """链式结构 → transitive 建议（A→B 且 B→C 的链条 ≥2 组）。

    只对语义天然可传递的关系有意义（下游/包含/位于/汇报给）；判据是**数据里
    真存在链**，是否声明由审核员/LLM 裁决。已声明传递性的谓词跳过。
    """
    schema = schema or {}
    declared = {op.get("label") for op in (schema.get("object_properties") or [])
                if isinstance(op, dict) and op.get("transitive")}
    adj: dict[str, dict[str, set[str]]] = {}  # pred_norm → {subject_norm: {object_norm}}
    pred_labels: dict[str, str] = {}
    for r in rels:
        pred = str(r.get("predicate") or "").strip()
        s = str(r.get("subject_label") or "").strip()
        o = str(r.get("object_label") or "").strip()
        if not pred or not s or not o:
            continue
        p_n = _norm(pred)
        if p_n in {_norm(str(d)) for d in declared}:
            continue
        adj.setdefault(p_n, {}).setdefault(_norm(s), set()).add(_norm(o))
        pred_labels.setdefault(p_n, pred)

    out: list[dict] = []
    for p_n in sorted(adj):
        by_subj = adj[p_n]
        # 链：A→B 且 B→C（B 同时是某边的客体与另一边的主体）
        all_objects = set()
        for objs in by_subj.values():
            all_objects |= objs
        chains = []
        for mid in all_objects:
            for a, objs in by_subj.items():
                if mid in objs:
                    for c in by_subj.get(mid, ()):  # B→C
                        if c != a:
                            chains.append((a, mid, c))
        if len(chains) < TRANSITIVE_MIN_CHAINS:
            continue
        pred = pred_labels.get(p_n, p_n)
        a, b, c = chains[0]
        out.append({
            "kind": "transitive", "predicate": pred,
            "risk_tier": "low", "source": "statistical",
            "evidence": {"chains": len(chains),
                         "example": f"{a} → {b} → {c}",
                         "subjects": len(by_subj)},
            "summary": (f"关系「{pred}」在实例数据中存在 {len(chains)} 组传递链"
                        f"（如 {a} → {b} → {c}），声明传递性后可自动补全"
                        f"「{a} → {c}」这类可直达关系"),
        })
        if len(out) >= MAX_SUGGESTIONS_PER_KIND:
            break
    return out


def suggest_symmetric(ents: list[dict], rels: list[dict],
                      schema: dict | None) -> list[dict]:
    """同谓词双向成对出现（A—X→B 且 B—X→A）→ symmetric 建议。"""
    schema = schema or {}
    declared = {op.get("label") for op in (schema.get("object_properties") or [])
                if isinstance(op, dict) and (op.get("symmetric") or op.get("inverse_of"))}
    pairs_by_pred: dict[str, set[tuple]] = {}
    pred_labels: dict[str, str] = {}
    for r in rels:
        pred = str(r.get("predicate") or "").strip()
        s = str(r.get("subject_label") or "").strip()
        o = str(r.get("object_label") or "").strip()
        if not pred or not s or not o or s == o:
            continue
        p_n = _norm(pred)
        pairs_by_pred.setdefault(p_n, set()).add((_norm(s), _norm(o)))
        pred_labels.setdefault(p_n, pred)
    out: list[dict] = []
    for p_n in sorted(pairs_by_pred):
        if pred_labels.get(p_n) in declared:
            continue
        pairs = pairs_by_pred[p_n]
        hits = pairs & {(o, s) for (s, o) in pairs}
        if len(hits) < SYMMETRIC_MIN_PAIRS:
            continue
        rate = len(hits) / len(pairs)
        if rate < SYMMETRIC_MIN_RATE:
            continue
        pred = pred_labels.get(p_n, p_n)
        example = next(iter(sorted(hits)))
        out.append({
            "kind": "symmetric", "predicate": pred,
            "risk_tier": "low", "source": "statistical",
            "evidence": {"bidirectional_pairs": len(hits), "rate": round(rate, 2)},
            "summary": (f"关系「{pred}」有 {len(hits)} 组双向成对实例"
                        f"（如 {example[0]} ↔ {example[1]}，命中率 {rate:.0%}），"
                        "疑似对称关系"),
        })
        if len(out) >= MAX_SUGGESTIONS_PER_KIND:
            break
    return out


def suggest_inverse_functional(ents: list[dict], rels: list[dict],
                               schema: dict | None) -> list[dict]:
    """客体单主体率 100%（不同客体 ≥3 个）→ inverse_functional 建议。

    语义：客体唯一确定主体（如 账号→账户、身份证号→人）——是实体归并的
    强信号（同客体两主体 = 疑似同一实体的不同称呼）。
    """
    schema = schema or {}
    declared = {op.get("label") for op in (schema.get("object_properties") or [])
                if isinstance(op, dict) and op.get("inverse_functional")}
    objs: dict[str, dict[str, set[str]]] = {}  # pred_norm → {object_norm: {subject_norm}}
    pred_labels: dict[str, str] = {}
    for r in rels:
        pred = str(r.get("predicate") or "").strip()
        s = str(r.get("subject_label") or "").strip()
        o = str(r.get("object_label") or "").strip()
        if not pred or not s or not o:
            continue
        p_n = _norm(pred)
        objs.setdefault(p_n, {}).setdefault(_norm(o), set()).add(_norm(s))
        pred_labels.setdefault(p_n, pred)
    out: list[dict] = []
    for p_n in sorted(objs):
        if pred_labels.get(p_n) in declared:
            continue
        by_obj = objs[p_n]
        if len(by_obj) < INVFUNC_MIN_OBJECTS:
            continue
        multi = sum(1 for subj_set in by_obj.values() if len(subj_set) > 1)
        if multi > 0:
            continue  # 有客体被多主体指向 → 不是反函数性
        pred = pred_labels.get(p_n, p_n)
        example = next(iter(sorted(by_obj.items())))
        out.append({
            "kind": "inverse_functional", "predicate": pred,
            "risk_tier": "low", "source": "statistical",
            "evidence": {"distinct_objects": len(by_obj), "multi_subject_objects": 0,
                         "example": f"{list(example[1])[0]} → {example[0]}"},
            "summary": (f"关系「{pred}」的 {len(by_obj)} 个客体各自只被 1 个主体指向"
                        f"（如 {list(example[1])[0]} → {example[0]}），客体可唯一确定主体，"
                        "疑似反函数性"),
        })
        if len(out) >= MAX_SUGGESTIONS_PER_KIND:
            break
    return out


def suggest_axioms(ents: list, rels: list, schema: dict | None) -> list[dict]:
    """公理建议主入口：五类统计候选（functional / inverse_of / disjoint_with /
    domain_fix / range_fix / subclass_of）。

    ents/rels：_as_ent/_as_rel 归一形状（或行表行）。每个候选必带 evidence 统计。
    """
    from app.adapters.conflicts import _as_ent, _as_rel

    e_dicts = [_as_ent(e) for e in (ents or [])]
    e_dicts = [e for e in e_dicts if e["label"] and e["status"] != "merged"]
    r_dicts = [_as_rel(r) for r in (rels or [])]
    rels_full = []
    for r in r_dicts:
        if r.get("subject_id") is None or not r.get("predicate"):
            continue
        if _norm(r["predicate"]) in STRUCTURAL_PREDICATES:
            continue  # rdf:type 边不是业务关系（曾误产出「instance_of 是函数性」）
        rels_full.append(r)
    candidates: list[dict] = []
    candidates.extend(suggest_functional(e_dicts, rels_full, schema))
    candidates.extend(suggest_inverse_of(e_dicts, rels_full, schema))
    candidates.extend(suggest_disjoint_with(e_dicts, rels_full, schema))
    candidates.extend(suggest_domain_range(e_dicts, rels_full, schema))
    candidates.extend(suggest_subclass_by_suffix(e_dicts, rels_full, schema))
    candidates.extend(suggest_transitive(e_dicts, rels_full, schema))
    candidates.extend(suggest_symmetric(e_dicts, rels_full, schema))
    candidates.extend(suggest_inverse_functional(e_dicts, rels_full, schema))
    return candidates


__all__ = ["suggest_axioms", "suggest_functional", "suggest_inverse_of",
           "suggest_disjoint_with", "suggest_domain_range",
           "suggest_subclass_by_suffix", "suggest_transitive",
           "suggest_symmetric", "suggest_inverse_functional",
           "MAX_SUGGESTIONS_PER_KIND"]
