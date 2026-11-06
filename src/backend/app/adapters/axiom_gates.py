# app/adapters/axiom_gates.py - LLM 公理候选机检守门（O4-B19）
# 五道闸顺序执行（docs/design/13 §4 + L13 TBox 可满足性补强）：
#   ① 存在性：axiom_llm.parse 已做（幻觉弃单），本层形式化幂等复查
#   ② 已声明冲突：与画布已有公理矛盾（disjoint↔equivalent、子类环、重复声明）直接拒
#   ③ TBox 可满足性：候选并入后出现新的不可满足类即拒（纯 TBox 矛盾、无需实例——
#      owlrl 的 owl:Nothing 只在实例上触发，本闸补齐无实例场景）
#   ④ 影子 SHACL：候选临时并入 schema 副本跑 pyshacl，违例激增即拒；只操作副本不触画布
#   ⑤ 高风险加验：subclass_of/equivalent_classes/cardinality 过 owlrl 一致性（owl:Nothing 检测）
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
        if kind == "subproperty_of" and cand.get("value"):
            prop_targets.append(str(cand["value"]))  # 父关系也是谓词
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
    elif kind == "subproperty_of":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        if op_ is not None:
            existing = str(op_.get("subproperty_of") or "")
            if existing and existing != str(cand.get("value") or ""):
                return f"「{op_['label']}」已声明 ⊑「{existing}」"
            if str(op_.get("subproperty_of") or "") == str(cand.get("value") or ""):
                return f"「{op_['label']}」已 ⊑「{cand.get('value')}」（重复）"
    elif kind == "cardinality":
        op_ = ops_by_label.get(str(cand.get("predicate") or ""))
        mn, mx = cand.get("min_card"), cand.get("max_card")
        for restr in (op_ or {}).get("cardinality_restrictions") or []:
            if (restr.get("min") == mn and restr.get("max") == mx) if mn is not None or mx is not None else False:
                return f"「{op_['label']}」已声明相同基数约束"
    return None


# ── 闸③ TBox 可满足性（纯 TBox 矛盾，无实例也可判）──

def gate_tbox_satisfiability(candidates: list[dict], schema: dict | None
                             ) -> tuple[list[dict], list[dict], dict]:
    """候选并入后出现基线之外的新不可满足类即拒；批量不过则逐条定位。

    覆盖闸②够不着的传递场景（候选 A⊥C 本身无父子链，但已有 A⊑B、B⊥C 时
    会使 A 的全部子类恒为空）。确定性多项式，零实例依赖，闸⑤的 owlrl 补位。
    """
    from app.adapters.schema_checks import find_unsatisfiable_classes

    schema = schema or {}
    stats: dict[str, Any] = {"enabled": True}
    base = {u["class"] for u in find_unsatisfiable_classes(schema)}
    stats["baseline_unsatisfiable"] = len(base)

    def _new_unsat(s: dict) -> list[dict]:
        return [u for u in find_unsatisfiable_classes(s) if u["class"] not in base]

    batch = schema
    for cand in candidates:
        batch = apply_candidate_to_schema(batch, cand)
    if not _new_unsat(batch):
        stats["checked"] = len(candidates)
        return candidates, [], stats

    survivors, rejected = [], []
    for cand in candidates:
        solo = apply_candidate_to_schema(schema, cand)
        broke = _new_unsat(solo)
        if broke:
            names = "、".join(f"「{u['class']}」" for u in broke[:5])
            rejected.append({"candidate": cand,
                             "reason": f"TBox 可满足性: 候选使 {names} 不可满足"
                                       f"（{broke[0]['reason']}）"})
        else:
            survivors.append(cand)
    stats["checked"] = len(candidates)
    return survivors, rejected, stats


# ── 闸④ 影子 SHACL ──

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


# ── 闸⑤ 高风险 owlrl 一致性 ──

def _fact_graph(schema: dict, ents: list, rels: list, entity_index: dict | None) -> Any:
    """候选并入后的 schema 公理 + 真实 ABox → 单一 rdflib 图（与 shacl_checks 同一
    URI 方案：urn:o:cls / urn:o:prop / urn:o:inst），供 owlrl 闭包检查。

    注意：owlrl 的 owl:Nothing 只在实例上触发——纯 TBox 矛盾（无实例）由
    闸③（TBox 可满足性）负责，本闸专盯「候选让已有实例陷入逻辑矛盾」。
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


# ── 闸⑥ 数据反证（已声明公理 vs 实例数据，确定性）──

def _entity_class_sets(ents: list, rels: list) -> tuple[dict, dict]:
    """实体 → 类集合（class_label + instance_of 边指向的类）与 id → label 映射。

    供互斥公理的"共同实例"反证：同一实体双类型 = 互斥被数据违反。
    """
    from app.adapters.conflicts import _as_ent

    ent_classes: dict = {}
    ent_cls: dict = {}
    id_label: dict = {}
    for e in ents:
        try:
            d = _as_ent(e)
        except Exception:  # noqa: BLE001
            continue
        eid = d.get("id")
        if eid is None:
            continue
        if d.get("status") == "merged":
            continue
        if str(d.get("class_label") or "").strip():
            ent_classes_set = ent_classes.setdefault(eid, set())
            ent_classes_set.add(str(d["class_label"]).strip())
            ent_cls[eid] = str(d["class_label"]).strip()
        id_label[eid] = str(d.get("label") or "")
    for r in rels:
        if isinstance(r, dict):
            pred = str(r.get("predicate") or "").strip()
            sid, oid = r.get("subject_id"), r.get("object_id")
            olbl = r.get("object_label")
        else:
            pred = str(getattr(r, "predicate", "") or "").strip()
            sid, oid = getattr(r, "subject_id", None), getattr(r, "object_id", None)
            olbl = getattr(r, "object_label", None)
        if pred in ("instance_of", "rdf:type", "type") and sid is not None:
            cls_name = (ent_cls.get(oid) if oid in ent_cls else None) or (str(olbl) if olbl else None)
            if cls_name:
                ent_classes.setdefault(sid, set()).add(cls_name)
    return ent_classes, id_label


def _disjoint_violation_detail(a: str, b: str, ent_classes: dict, id_label: dict) -> str | None:
    """类对 (a,b) 的共同实例反证：双类型实例优先，其次同名实例分挂两类。"""
    co_typed = [eid for eid, cls_set in ent_classes.items()
                if a in cls_set and b in cls_set]
    if co_typed:
        names = "、".join(f"「{id_label.get(eid, eid)}」" for eid in sorted(co_typed, key=str)[:3])
        return f"存在共同实例 {names}（双类型），互斥不成立"
    label_to_cls: dict[str, set] = {}
    for eid, cls_set in ent_classes.items():
        lbl_ = id_label.get(eid, "")
        if lbl_:
            for c_ in cls_set:
                label_to_cls.setdefault(lbl_, set()).add(c_)
    same_name = [lbl_ for lbl_, cs in label_to_cls.items() if {a, b} <= cs]
    if same_name:
        return f"同名实例「{same_name[0]}」分别挂两类，疑似同类，互斥不成立"
    return None


def find_data_contradictions(schema: dict | None, ents: list, rels: list) -> list[dict]:
    """已声明公理与实例数据的矛盾清单（确定性扫描，零 LLM）。

    owlrl 对「函数性多值」不报错——它按 prp-fp 把多值客体重建成同一实体
    （sameAs），矛盾被静默"解决"成错误合并（2026-11-04 项目 246：下游声明
    函数性 + DCBS 三个下游 ⇒ ABCS/PRSC/URSC 全被推成同一实体）。本扫描把
    这类矛盾显式化，供闸⑥拦截新候选、自动申报复验撤回、推理弹窗展示。
    返回条目：{axiom_kind, key, predicate|classes, message, witness}。
    """
    from app.adapters.conflicts import _as_ent

    schema = schema or {}
    ent_cls: dict = {}
    for e in ents:
        try:
            d = _as_ent(e)
        except Exception:  # noqa: BLE001
            continue
        if d.get("id") is not None and d.get("status") != "merged":
            ent_cls[d["id"]] = str(d.get("class_label") or "").strip()

    def _label_of(eid) -> str:
        for e in ents:
            d = e if isinstance(e, dict) else None
            if d is None:
                d = {c: getattr(e, c, None) for c in ("id", "label")}
            if d.get("id") == eid:
                return str(d.get("label") or "")
        return str(eid)

    # id → label（ORM/dict 双形状）
    id_label: dict = {}
    for e in ents:
        if isinstance(e, dict):
            id_label[e.get("id")] = str(e.get("label") or "")
        else:
            id_label[getattr(e, "id", None)] = str(getattr(e, "label", "") or "")

    ops_by_label = {str(op.get("label") or ""): op
                    for op in (schema.get("object_properties") or [])
                    if isinstance(op, dict) and op.get("label")}
    # 谓词 → [(subject_id, object_id)]（结构谓词 instance_of 不参与）
    by_pred: dict[str, list[tuple]] = {}
    for r in rels:
        pred = str((r.get("predicate") if isinstance(r, dict) else getattr(r, "predicate", "")) or "").strip()
        if not pred or pred in ("instance_of", "rdf:type", "type"):
            continue
        sid = r.get("subject_id") if isinstance(r, dict) else getattr(r, "subject_id", None)
        oid = r.get("object_id") if isinstance(r, dict) else getattr(r, "object_id", None)
        by_pred.setdefault(pred, []).append((sid, oid))

    out: list[dict] = []

    def _push(axiom_kind: str, key: tuple, message: str, witness: dict) -> None:
        out.append({"axiom_kind": axiom_kind, "key": key,
                    "message": message, "witness": witness})

    # 函数性：谓词存在 >1 个不同客体的主体
    for lbl, op_ in ops_by_label.items():
        if not op_.get("functional"):
            continue
        by_subj: dict = {}
        for sid, oid in by_pred.get(lbl, []):
            by_subj.setdefault(sid, set()).add(oid)
        multi = {sid: objs for sid, objs in by_subj.items() if len(objs) > 1}
        if multi:
            sid, objs = next(iter(multi.items()))
            names = [id_label.get(o, str(o)) for o in sorted(objs, key=str)][:4]
            _push("functional", ("functional", lbl), 
                  f"关系「{lbl}」声明函数性（每主体至多一个客体），但「{id_label.get(sid, sid)}」"
                  f"有 {len(objs)} 个取值（{'、'.join(names)}）——闭包会把它们强行合并为同一实体",
                  {"predicate": lbl, "violating_subjects": len(multi),
                   "example_subject": id_label.get(sid, str(sid)), "values": names})
    # 反函数性：谓词存在被 >1 个主体指向的客体
    for lbl, op_ in ops_by_label.items():
        if not op_.get("inverse_functional"):
            continue
        by_obj: dict = {}
        for sid, oid in by_pred.get(lbl, []):
            by_obj.setdefault(oid, set()).add(sid)
        multi = {oid: subj for oid, subj in by_obj.items() if len(subj) > 1}
        if multi:
            oid, subj = next(iter(multi.items()))
            names = [id_label.get(s, str(s)) for s in sorted(subj, key=str)][:4]
            _push("inverse_functional", ("inverse_functional", lbl),
                  f"关系「{lbl}」声明反函数性（客体唯一确定主体），但「{id_label.get(oid, oid)}」"
                  f"被 {len(subj)} 个主体指向（{'、'.join(names)}）",
                  {"predicate": lbl, "violating_objects": len(multi),
                   "example_object": id_label.get(oid, str(oid)), "subjects": names})
    # 互斥：两类有共同实例（同一实体双类型，或同名实例）
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}
    ent_classes, ent_id_label = _entity_class_sets(ents, rels)
    declared_disjoint: set[frozenset] = set()
    for c in classes_by_label.values():
        for peer in (c.get("disjoint_with") or []):
            if peer and peer != c.get("label"):
                declared_disjoint.add(frozenset((str(c["label"]), str(peer))))
    for pair in sorted(declared_disjoint, key=sorted):
        a, b = sorted(pair)
        detail = _disjoint_violation_detail(a, b, ent_classes, ent_id_label)
        if detail:
            _push("disjoint_with", ("disjoint_with", a, b),
                  f"类「{a}」与「{b}」声明互斥，但{detail}",
                  {"classes": [a, b]})
    return out


def gate_data_contradiction(candidates: list[dict], schema: dict | None,
                            ents: list, rels: list) -> tuple[list[dict], list[dict]]:
    """闸⑥：候选公理被实例数据直接反证即拒（宁缺毋滥——错误的公理会污染
    整个推理闭包，246 的 sameAs 事故即函数性公理与数据矛盾所致）。

    覆盖 kind：functional / inverse_functional / cardinality(max) / disjoint_with。
    其余 kind（inverse_of/symmetric/transitive 等补边型、domain/range 修正型）
    数据不会反证（声明本身会推导补全边），不适用本闸。
    """
    from app.adapters.conflicts import _as_ent

    schema = schema or {}
    e_dicts = [_as_ent(e) for e in (ents or [])]
    e_dicts = [e for e in e_dicts if e["label"] and e["status"] != "merged"]
    contradictions = find_data_contradictions(schema, e_dicts, rels)

    def _predicate_multi_values(pred: str, side: str) -> dict | None:
        """谓词数据反证：side='object' 查函数性（主体多值），'subject' 查反函数性。"""
        groups: dict = {}
        for r in rels:
            if str(r.get("predicate") or "").strip() != pred:
                continue
            k, v = ((r.get("subject_id"), r.get("object_id"))
                    if side == "object" else (r.get("object_id"), r.get("subject_id")))
            if k is None or v is None:
                continue
            groups.setdefault(k, set()).add(v)
        multi = {k: v for k, v in groups.items() if len(v) > 1}
        return multi or None

    survivors, rejected = [], []
    for cand in candidates:
        kind = cand.get("kind")
        reason = None
        if kind == "functional":
            pred = str(cand.get("predicate") or "")
            multi = _predicate_multi_values(pred, "object")
            if multi:
                sid, objs = next(iter(multi.items()))
                reason = (f"数据反证: 「{pred}」存在多值主体（某主体有 "
                          f"{len(objs)} 个不同客体：{'、'.join(sorted(str(o) for o in objs)[:4])}），"
                          "函数性不成立")
        elif kind == "inverse_functional":
            pred = str(cand.get("predicate") or "")
            multi = _predicate_multi_values(pred, "subject")
            if multi:
                oid, subj = next(iter(multi.items()))
                reason = (f"数据反证: 「{pred}」存在被 {len(subj)} 个主体指向的客体，"
                          "反函数性不成立")
        elif kind == "cardinality":
            pred = str(cand.get("predicate") or "")
            mx = cand.get("max_card")
            if mx is not None:
                groups: dict = {}
                for r in rels:
                    if str(r.get("predicate") or "").strip() == pred:
                        sid = r.get("subject_id")
                        if sid is not None:
                            groups.setdefault(sid, set()).add(r.get("object_id"))
                over = {k: v for k, v in groups.items() if len(v) > int(mx)}
                if over:
                    sid, objs = next(iter(over.items()))
                    reason = (f"数据反证: 「{pred}」存在主体取值 {len(objs)} 个，"
                              f"超过候选 max_card={mx}")
        elif kind == "disjoint_with":
            pair = [str(x or "") for x in (cand.get("classes") or [])[:2]]
            if len(pair) == 2 and all(pair):
                cls_sets, id_labels = _entity_class_sets(e_dicts, rels)
                detail = _disjoint_violation_detail(pair[0], pair[1],
                                                    cls_sets, id_labels)
                if detail:
                    reason = f"数据反证: 类「{pair[0]}」与「{pair[1]}」{detail}"
        if reason:
            rejected.append({"candidate": cand, "reason": reason})
        else:
            survivors.append(cand)
    return survivors, rejected


# ── 编排：六道闸顺序执行 ──

def run_gates(candidates: list[dict], schema: dict | None,
              ents: list, rels: list,
              entity_index: dict | None = None) -> tuple[list[dict], dict]:
    """六道闸流水线。返回 (幸存候选, 各闸统计)。

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
    survivors, rej_data = gate_data_contradiction(survivors, schema,
                                                  e_dicts, r_dicts)
    stats["data_contradiction"] = {"passed": len(survivors),
                                   "rejected": len(rej_data)}
    rejected = rejected + rej_data
    survivors, rej2 = gate_declared_conflicts(survivors, schema)
    stats["declared_conflicts"] = {"passed": len(survivors), "rejected": len(rej2)}
    rejected = rejected + rej2
    survivors, rej_tbox, tbox_stats = gate_tbox_satisfiability(survivors, schema)
    stats["tbox_satisfiability"] = {**tbox_stats, "rejected": len(rej_tbox)}
    rejected = rejected + rej_tbox
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
    "apply_candidate_to_schema", "gate_existence", "gate_data_contradiction",
    "find_data_contradictions", "gate_declared_conflicts",
    "gate_tbox_satisfiability", "gate_shadow_shacl", "gate_owlrl_consistency",
    "_fact_graph", "run_gates",
]
