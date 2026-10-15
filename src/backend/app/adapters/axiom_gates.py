# app/adapters/axiom_gates.py - LLM 公理候选机检守门（O4-B19）
# 四道闸顺序执行（docs/design/13 §4）：
#   ① 存在性：axiom_llm.parse 已做（幻觉弃单），本层形式化幂等复查
#   ② 已声明冲突：与画布已有公理矛盾（disjoint↔equivalent、子类环、重复声明）直接拒
#   ③ 影子 SHACL：候选临时并入 schema 副本跑 pyshacl，违例激增即拒；只操作副本不触画布
#   ④ 高风险加验：subclass_of/equivalent_classes/cardinality 过 owlrl 一致性（owl:Nothing 检测）
# 纯函数可测；pyshacl/owlrl 懒加载；批量闸不过时逐条定位（拒绝肇事者、放行无辜者）。

from __future__ import annotations

import copy
from typing import Any

from app.adapters.axiom_llm import KIND_META

SHADOW_NEW_VIOLATION_LIMIT = 3   # 影子校验：新增违例 ≥3 拒
SHADOW_ABSOLUTE_LIMIT = 10       # 基线为零时绝对上限（宁宽勿严，精细判断给人审）


# ── schema 应用（副本操作，与 review_service._approve_axiom_suggestion 语义一致）──

def apply_candidate_to_schema(schema: dict, cand: dict) -> dict:
    """深拷贝 schema 并应用单条公理候选，返回副本（不改入参）。"""
    s = copy.deepcopy(schema or {})
    kind = cand.get("kind")
    ops = [op for op in (s.get("object_properties") or []) if isinstance(op, dict)]
    ops_by_label = {str(op.get("label") or ""): op for op in ops if op.get("label")}
    classes = [c for c in (s.get("classes") or []) if isinstance(c, dict)]
    classes_by_label = {str(c.get("label") or ""): c for c in classes if c.get("label")}

    if kind == "functional":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None:
            op_["functional"] = True
    elif kind == "inverse_of":
        pair = cand.get("predicates") or []
        if len(pair) == 2:
            op_p, op_q = ops_by_label.get(pair[0]), ops_by_label.get(pair[1])
            if op_p is not None and op_q is not None:
                op_p["inverse_of"], op_q["inverse_of"] = pair[1], pair[0]
    elif kind == "disjoint_with":
        pair = cand.get("classes") or []
        if len(pair) == 2:
            ca, cb = classes_by_label.get(pair[0]), classes_by_label.get(pair[1])
            if ca is not None and cb is not None:
                ca.setdefault("disjoint_with", [])
                cb.setdefault("disjoint_with", [])
                if pair[1] not in ca["disjoint_with"]:
                    ca["disjoint_with"].append(pair[1])
                if pair[0] not in cb["disjoint_with"]:
                    cb["disjoint_with"].append(pair[0])
    elif kind in ("domain_fix", "range_fix"):
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None:
            op_["domain" if kind == "domain_fix" else "range"] = cand.get("value")
    elif kind == "subclass_of":
        pair = cand.get("classes") or []
        if len(pair) == 2:
            ca = classes_by_label.get(pair[0])
            if ca is not None:
                parents = ca.setdefault("parent_classes", [])
                if pair[1] not in parents:
                    parents.append(pair[1])
    elif kind == "equivalent_classes":
        pair = cand.get("classes") or []
        if len(pair) == 2:
            ca, cb = classes_by_label.get(pair[0]), classes_by_label.get(pair[1])
            if ca is not None and cb is not None:
                ca.setdefault("equivalent_to", [])
                cb.setdefault("equivalent_to", [])
                if pair[1] not in ca["equivalent_to"]:
                    ca["equivalent_to"].append(pair[1])
                if pair[0] not in cb["equivalent_to"]:
                    cb["equivalent_to"].append(pair[0])
    elif kind == "cardinality":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None:
            op_.setdefault("cardinality_restrictions", []).append(
                {"class": str(op_.get("domain") or ""), "min": cand.get("min_card"),
                 "max": cand.get("max_card")})
    return s


# ── 闸① 存在性（幂等复查，normalize 后必须命中画布 label）──

def gate_existence(candidates: list[dict], schema: dict | None) -> tuple[list[dict], list[dict]]:
    """返回 (幸存候选, 拒绝列表 [{candidate, reason}])。"""
    from app.adapters.resolution import normalize_label

    schema = schema or {}
    class_norms = {normalize_label(str(c.get("label") or ""))
                   for c in (schema.get("classes") or []) if isinstance(c, dict) and c.get("label")}
    prop_norms = {normalize_label(str(op.get("label") or ""))
                  for op in (schema.get("object_properties") or [])
                  if isinstance(op, dict) and op.get("label")}

    survivors, rejected = [], []
    for cand in candidates:
        kind = cand.get("kind")
        # 按字段归属决定查哪张表（predicate* 查谓词表，classes/value 查类表）
        prop_targets = [str(cand["predicate"])] if cand.get("predicate") else []
        prop_targets += [str(x) for x in (cand.get("predicates") or [])]
        class_targets = [str(x) for x in (cand.get("classes") or [])]
        if kind in ("domain_fix", "range_fix") and cand.get("value"):
            class_targets.append(str(cand["value"]))
        bad = [t for t in prop_targets if normalize_label(t) not in prop_norms]
        bad += [t for t in class_targets if normalize_label(t) not in class_norms]
        if bad:
            rejected.append({"candidate": cand, "reason": f"存在性: 引用未知名 {bad}"})
        else:
            survivors.append(cand)
    return survivors, rejected


# ── 闸② 已声明冲突 ──

def _ancestors(label: str, parent_map: dict[str, list[str]], seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if label in seen:
        return seen
    seen.add(label)
    for p in parent_map.get(label, []):
        _ancestors(p, parent_map, seen)
    return seen


def gate_declared_conflicts(candidates: list[dict],
                            schema: dict | None) -> tuple[list[dict], list[dict]]:
    """候选 vs 画布已有公理：矛盾（disjoint↔equivalent、环、重复声明）拒。"""
    schema = schema or {}
    ops_by_label = {str(op.get("label") or ""): op
                    for op in (schema.get("object_properties") or [])
                    if isinstance(op, dict) and op.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}
    declared_disjoint: set[frozenset] = set()
    declared_equiv: set[frozenset] = set()
    for c in classes_by_label.values():
        for peer in (c.get("disjoint_with") or []):
            if peer != c.get("label"):
                declared_disjoint.add(frozenset((c["label"], peer)))
        for peer in (c.get("equivalent_to") or []):
            if peer != c.get("label"):
                declared_equiv.add(frozenset((c["label"], peer)))
    parent_map = {lbl: list((c or {}).get("parent_classes") or [])
                  for lbl, c in classes_by_label.items()}

    survivors, rejected = [], []
    for cand in candidates:
        reason = _declared_conflict_reason(cand, ops_by_label, classes_by_label,
                                           declared_disjoint, declared_equiv, parent_map)
        if reason:
            rejected.append({"candidate": cand, "reason": f"已声明冲突: {reason}"})
        else:
            survivors.append(cand)
    return survivors, rejected


def _declared_conflict_reason(cand: dict, ops_by_label: dict, classes_by_label: dict,
                              declared_disjoint: set, declared_equiv: set,
                              parent_map: dict) -> str | None:
    kind = cand.get("kind")
    if kind == "functional":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None and op_.get("functional"):
            return f"「{op_['label']}」已声明函数性"
    elif kind == "inverse_of":
        p, q = (cand.get("predicates") or ["", ""])[:2]
        op_p = ops_by_label.get(str(p))
        existing = str((op_p or {}).get("inverse_of") or "")
        if existing and existing != str(q):
            return f"「{p}」已与「{existing}」互逆"
    elif kind == "disjoint_with":
        a, b = (cand.get("classes") or ["", ""])[:2]
        if frozenset((str(a), str(b))) in declared_equiv:
            return f"「{a}」与「{b}」已声明等价，不能再互斥"
        anc = _ancestors(str(a), parent_map)
        if str(b) in anc or str(a) in _ancestors(str(b), parent_map):
            return f"「{a}」与「{b}」存在父子链，互斥会使父类自斥"
    elif kind in ("domain_fix", "range_fix"):
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None:
            key = "domain" if kind == "domain_fix" else "range"
            if str(op_.get(key) or "") == str(cand.get("value") or ""):
                return f"「{op_['label']}」{key} 已是「{cand.get('value')}」"
    elif kind == "subclass_of":
        child, parent = (cand.get("classes") or ["", ""])[:2]
        if str(parent) in _ancestors(str(child), parent_map):
            return f"「{child}」已是「{parent}」的后代（重复或成环）"
        if str(child) in _ancestors(str(parent), parent_map):
            return f"声明子类会成环：「{parent}」已是「{child}」的后代"
    elif kind == "equivalent_classes":
        a, b = (cand.get("classes") or ["", ""])[:2]
        if frozenset((str(a), str(b))) in declared_disjoint:
            return f"「{a}」与「{b}」已声明互斥，不能等价"
        if str(b) in _ancestors(str(a), parent_map) or str(a) in _ancestors(str(b), parent_map):
            return f"「{a}」与「{b}」存在父子链，等价会塌缩层次"
    elif kind == "cardinality":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        mn, mx = cand.get("min_card"), cand.get("max_card")
        for restr in (op_ or {}).get("cardinality_restrictions") or []:
            if (restr.get("min") == mn and restr.get("max") == mx) if mn is not None or mx is not None else False:
                return f"「{op_['label']}」已声明相同基数约束"
    return None


# ── 闸③ 影子 SHACL ──

def gate_shadow_shacl(candidates: list[dict], schema: dict | None,
                      ents: list, rels: list,
                      entity_index: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """候选批量并入 schema 副本跑 pyshacl；违例激增则逐条定位拒肇事者。

    只操作 schema 副本与 ABox 只读图，绝不触碰画布。返回 (幸存, 拒绝, 统计)。
    """
    from app.adapters.shacl_checks import detect_shacl_conflicts

    schema = schema or {}
    stats: dict[str, Any] = {"enabled": True, "validations": 0}
    try:
        base = detect_shacl_conflicts(ents, rels, schema, entity_index)
        stats["validations"] += 1
        stats["baseline_violations"] = len(base)
    except Exception as e:  # noqa: BLE001 —— 基线失败则整闸放行（不阻断）
        stats["skipped"] = f"baseline_failed: {e}"
        return candidates, [], stats

    def _viol(s: dict) -> int:
        stats["validations"] += 1
        try:
            return len(detect_shacl_conflicts(ents, rels, s, entity_index))
        except Exception:  # noqa: BLE001
            stats["shape_error"] = True
            return 0

    batch_schema = schema
    for cand in candidates:
        batch_schema = apply_candidate_to_schema(batch_schema, cand)
    batch_n = _viol(batch_schema)
    new_viol = batch_n - len(base)
    stats["batch_violations"] = batch_n
    if new_viol < SHADOW_NEW_VIOLATION_LIMIT:
        return candidates, [], stats

    # 批量超标 → 逐条定位：肇事者拒，无辜者留（附各自影子违例数供观察）
    survivors, rejected = [], []
    for cand in candidates:
        solo_schema = apply_candidate_to_schema(schema, cand)
        solo_n = _viol(solo_schema)
        solo_new = solo_n - len(base)
        if solo_new >= SHADOW_NEW_VIOLATION_LIMIT or \
                (not base and solo_n >= SHADOW_ABSOLUTE_LIMIT):
            rejected.append({"candidate": cand,
                             "reason": f"影子校验: 新增违例 {solo_new} 条"
                                       f"（基线 {len(base)} → 影子 {solo_n}）"})
        else:
            survivors.append(cand)
    return survivors, rejected, stats


# ── 闸④ 高风险 owlrl 一致性 ──

def _fact_graph(schema: dict, ents: list, rels: list, entity_index: dict | None) -> Any:
    """候选并入后的 schema 公理 + 真实 ABox → 单一 rdflib 图（与 shacl_checks 同一
    URI 方案：urn:o:cls / urn:o:prop / urn:o:inst），供 owlrl 闭包检查。

    注意：owlrl 的 owl:Nothing 只在实例上触发——纯 TBox 矛盾（无实例）不可判，
    闸② 已覆盖声明级矛盾，本闸专盯「候选让已有实例陷入逻辑矛盾」。
    """
    from urllib.parse import quote

    from rdflib import OWL, RDF, RDFS, Literal, URIRef

    from app.adapters.shacl_checks import _cls_ref, _prop_ref, build_data_graph

    g, _ = build_data_graph(ents, rels, schema, entity_index)
    classes = {str(c.get("label")): _cls_ref(str(c.get("label")))
               for c in (schema.get("classes") or [])
               if isinstance(c, dict) and c.get("label")}
    # 补注真实 RDFS/OWL 词汇的类层公理（build_data_graph 里的 subClassOf/disjointWith
    # 是 urn:o:prop 自定义 URI，owlrl 不识别），实例类型已按父类闭包显式化
    for c in (schema.get("classes") or []):
        if not (isinstance(c, dict) and c.get("label")):
            continue
        s = classes[str(c["label"])]
        for p_lbl in (c.get("parent_classes") or []):
            if p_lbl in classes and p_lbl != c["label"]:
                g.add((s, RDFS.subClassOf, classes[str(p_lbl)]))
        for peer in (c.get("disjoint_with") or []):
            if peer in classes and peer != c["label"]:
                g.add((s, OWL.disjointWith, classes[str(peer)]))
        for peer in (c.get("equivalent_to") or []):
            if peer in classes and peer != c["label"]:
                g.add((s, OWL.equivalentClass, classes[str(peer)]))
    prop_labels = {str(op.get("label")) for op in (schema.get("object_properties") or [])
                   if isinstance(op, dict) and op.get("label")}
    for op in (schema.get("object_properties") or []):
        if not (isinstance(op, dict) and op.get("label")):
            continue
        p = _prop_ref(str(op["label"]))
        if op.get("domain") in classes:
            g.add((p, RDFS.domain, classes[str(op["domain"])]))
        if op.get("range") in classes:
            g.add((p, RDFS.range, classes[str(op["range"])]))
        if op.get("functional"):
            g.add((p, RDF.type, OWL.FunctionalProperty))
        inv = str(op.get("inverse_of") or "")
        if inv in prop_labels and inv != op["label"]:
            g.add((p, OWL.inverseOf, _prop_ref(inv)))
        for restr in (op.get("cardinality_restrictions") or []):
            if not (isinstance(restr, dict) and restr.get("class") in classes):
                continue
            key = f"restr_{op['label']}_{restr.get('class')}_{restr.get('min')}_{restr.get('max')}"
            b = URIRef("urn:o:" + quote(key, safe=""))
            g.add((classes[str(restr["class"])], RDFS.subClassOf, b))
            g.add((b, RDF.type, OWL.Restriction))
            g.add((b, OWL.onProperty, p))
            if restr.get("min") is not None:
                g.add((b, OWL.minCardinality, Literal(int(restr["min"]))))
            if restr.get("max") is not None:
                g.add((b, OWL.maxCardinality, Literal(int(restr["max"]))))
    return g


def _owl_nothing_ids(graph: Any) -> frozenset:
    from rdflib import RDF, Namespace

    OWL = Namespace("http://www.w3.org/2002/07/owl#")
    return frozenset(str(s) for s in graph.subjects(RDF.type, OWL.Nothing))


def gate_owlrl_consistency(candidates: list[dict], schema: dict | None,
                           ents: list | None = None, rels: list | None = None,
                           entity_index: dict | None = None
                           ) -> tuple[list[dict], list[dict], dict]:
    """仅 HIGH 风险候选：公理并入副本图（含真实 ABox）跑 owlrl，出现基线之外的
    owl:Nothing 即逻辑矛盾；批量不过则逐条定位。owlrl 缺失时整闸放行。"""
    schema = schema or {}
    high = [c for c in candidates if KIND_META.get(c.get("kind"), {}).get("risk") == "high"]
    low = [c for c in candidates if KIND_META.get(c.get("kind"), {}).get("risk") != "high"]
    if not high:
        return candidates, [], {"enabled": True, "checked": 0}
    stats: dict[str, Any] = {"enabled": True}
    try:
        import owlrl  # noqa: F401
    except Exception:  # noqa: BLE001
        stats["skipped"] = "owlrl_unavailable"
        return candidates, [], stats

    def _inconsistency_ids(s: dict) -> frozenset:
        """不一致信号集：owl:Nothing 实例 + owlrl error_messages（disjoint 共同实例等
        规则走 add_error 而非产出 Nothing 三元组，两路都要查）。空集 = 一致。"""
        g = _fact_graph(s, ents or [], rels or [], entity_index)
        try:
            closure = owlrl.OWLRL_Semantics(g, axioms=True, daxioms=True, rdfs=True)
            closure.closure()
        except Exception:  # noqa: BLE001 —— 病态图按全量矛盾处理（宁可错拒）
            return frozenset({"*"})
        sig = set(_owl_nothing_ids(g))
        if closure.error_messages:
            sig.add("*")
        return frozenset(sig)

    base_ids = _inconsistency_ids(schema)
    stats["baseline_nothing"] = len(base_ids)
    if base_ids == {"*"}:
        stats["skipped"] = "baseline_expand_failed"
        return candidates, [], stats

    batch = schema
    for cand in high:
        batch = apply_candidate_to_schema(batch, cand)
    if not (_inconsistency_ids(batch) - base_ids):
        stats["checked"] = len(high)
        return candidates, [], stats

    survivors, rejected = low, []
    for cand in high:
        if _inconsistency_ids(apply_candidate_to_schema(schema, cand)) - base_ids:
            rejected.append({"candidate": cand,
                             "reason": "owlrl 一致性: 并入后实例陷入 owl:Nothing（逻辑矛盾）"})
        else:
            survivors.append(cand)
    stats["checked"] = len(high)
    return survivors, rejected, stats


# ── 编排：四道闸顺序执行 ──

def run_gates(candidates: list[dict], schema: dict | None,
              ents: list, rels: list,
              entity_index: dict | None = None) -> tuple[list[dict], dict]:
    """四道闸流水线。返回 (幸存候选, 各闸统计)。

    ents/rels 支持 ORM 行或 _as_ent 归一形状（内部统一归一）。
    """
    from app.adapters.conflicts import _as_ent, _as_rel

    e_dicts = [_as_ent(e) for e in (ents or [])]
    e_dicts = [e for e in e_dicts if e["label"] and e["status"] != "merged"]
    r_dicts = [_as_rel(r) for r in (rels or [])]
    r_dicts = [r for r in r_dicts
               if r.get("subject_id") is not None and r.get("predicate")]

    stats: dict[str, Any] = {}
    survivors, rejected = gate_existence(candidates, schema)
    stats["existence"] = {"passed": len(survivors), "rejected": len(rejected)}
    survivors, rej2 = gate_declared_conflicts(survivors, schema)
    stats["declared_conflicts"] = {"passed": len(survivors), "rejected": len(rej2)}
    rejected = rejected + rej2
    survivors, rej3, shacl_stats = gate_shadow_shacl(survivors, schema,
                                                     e_dicts, r_dicts, entity_index)
    stats["shadow_shacl"] = {**shacl_stats, "rejected": len(rej3)}
    rejected = rejected + rej3
    survivors, rej4, owl_stats = gate_owlrl_consistency(survivors, schema,
                                                        e_dicts, r_dicts, entity_index)
    stats["owlrl"] = {**owl_stats, "rejected": len(rej4)}
    rejected = rejected + rej4
    stats["rejected_reasons"] = [r["reason"] for r in rejected][:20]
    return survivors, stats


__all__ = [
    "SHADOW_NEW_VIOLATION_LIMIT", "SHADOW_ABSOLUTE_LIMIT",
    "apply_candidate_to_schema", "gate_existence", "gate_declared_conflicts",
    "gate_shadow_shacl", "gate_owlrl_consistency", "_fact_graph",
    "run_gates",
]
