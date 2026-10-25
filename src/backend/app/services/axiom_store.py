# app/services/axiom_store.py - 公理声明统一存取（O4 后扩展：规则与公理统一管理）
# 单一事实源 = Project.graph_data：schema 权威键 + 画布 node/edge data.axioms 双写。
# 写入路径有三条：审核批准（review_service._approve_axiom_suggestion 委托本模块）、
# 推理弹窗手工新增/编辑（api/reasoning.py 公理 CRUD）、抽取批准链路。
# TTL 导出与 owlrl 推理经既有钩子消费同一份数据。
# S16（docs/15）：schema.axiom_provenance 注册表按 list key 记录每条公理的
# 来源四态 + 认证位（审核批准/手工新增 = steward_specified + certified）。

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.adapters.provenance import normalize_source_type
from app.infrastructure.database import Project

AXIOM_KINDS = ("functional", "inverse_of", "disjoint_with", "domain_fix",
               "range_fix", "subclass_of", "equivalent_classes", "cardinality")


def _stamp_provenance(schema: dict, key: str, payload: dict) -> None:
    """S16：按 list key 登记公理来源（人工路径默认 steward_specified + certified）。

    payload.evidence 可带证据锚点（{source_document_id, source_chunk_no, quote...} 或其列表），
    发布门禁的证据闭合检查据此校验悬空（S16-Step2）。
    """
    entry: dict = {
        "source_type": normalize_source_type(
            payload.get("source_type") or "steward_specified"),
        "certified": bool(payload.get("certified", True)),
        "decided_by": payload.get("decided_by"),
        "decided_at": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
    ev = payload.get("evidence")
    if isinstance(ev, (dict, list)) and ev:
        entry["evidence"] = ev
    schema.setdefault("axiom_provenance", {})[key] = entry


def _class_node_ids(gd: dict) -> dict[str, str]:
    """画布类节点 label → 节点 id（subclass_of/等价类写画布用）。"""
    out: dict[str, str] = {}
    for n in (gd.get("nodes") or []):
        d = n.get("data") or {}
        if d.get("type") in ("owl:Class", "Class") and d.get("label"):
            out.setdefault(str(d["label"]), str(n.get("id")))
    return out


def declare_axiom(db: Session, proj: Project, payload: dict) -> dict:
    """按 payload.kind 把公理写入画布 graph_data（schema 权威键 + data.axioms）。

    payload.kind ∈ AXIOM_KINDS；kind 其余字段：functional/domain_fix/range_fix/
    cardinality 用 payload.predicate（cardinality 另带 min_card/max_card），
    inverse_of 用 payload.predicates=[p,q]，disjoint_with/subclass_of/
    equivalent_classes 用 payload.classes=[a,b]（subclass_of 有序：子,父）。
    返回 {"axiom_declared": kind, "edges_marked": n} 或 {"skipped": 原因}。
    """
    from sqlalchemy.orm.attributes import flag_modified

    kind = str(payload.get("kind") or "").strip()
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    prov_key: str | None = None  # S16：登记 axiom_provenance 的 list key
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}

    def _mark_edges(predicate: str, mutator) -> int:
        n = 0
        for e in gd.get("edges") or []:
            d = e.get("data") or {}
            if str(d.get("relation") or "") == predicate:
                ax = d.setdefault("axioms", {})
                mutator(ax)
                e["data"] = d
                n += 1
        return n

    if kind == "functional":
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        op_["functional"] = True
        prov_key = f"functional:{label}"
        edges_marked = _mark_edges(label, lambda ax: ax.update(functional=True))
    elif kind == "inverse_of":
        p, q = (str(x or "").strip() for x in (payload.get("predicates") or ["", ""])[:2])
        op_p, op_q = ops_by_label.get(p), ops_by_label.get(q)
        if op_p is None or op_q is None or p == q:
            return {"skipped": f"互逆谓词不在 TBox: {p} / {q}"}
        op_p["inverse_of"] = q
        op_q["inverse_of"] = p
        prov_key = f"inverse_of:{min(p, q)}|{max(p, q)}"
        edges_marked = (_mark_edges(p, lambda ax: ax.update(inverse_of=q))
                        + _mark_edges(q, lambda ax: ax.update(inverse_of=p)))
    elif kind == "disjoint_with":
        a, b = (str(x or "").strip() for x in (payload.get("classes") or ["", ""])[:2])
        ca, cb = classes_by_label.get(a), classes_by_label.get(b)
        if ca is None or cb is None or a == b:
            return {"skipped": f"互斥类不在 TBox: {a} / {b}"}
        ca.setdefault("disjoint_with", [])
        cb.setdefault("disjoint_with", [])
        prov_key = f"disjoint_with:{min(a, b)}|{max(a, b)}"
        if b not in ca["disjoint_with"]:
            ca["disjoint_with"].append(b)
        if a not in cb["disjoint_with"]:
            cb["disjoint_with"].append(a)
        edges_marked = 0
        # 画布类节点 data.axioms.disjoint_with 同步（画布是活编辑面）
        for n in gd.get("nodes") or []:
            d = n.get("data") or {}
            lbl = str(d.get("label") or "")
            if lbl in (a, b) and d.get("type") in ("owl:Class", "Class"):
                ax = d.setdefault("axioms", {})
                peers = ax.setdefault("disjoint_with", [])
                peer = b if lbl == a else a
                if peer not in peers:
                    peers.append(peer)
                n["data"] = d
    elif kind == "domain_fix":
        # O4-B18：domain 修正 → 只写 schema 权威键（B6 SHACL 即刻生效）；
        # TTL 导出暂不落 rdfs:domain（generate_ttl_from_graph_data 无 schema 入参，见设计 13 §9）
        label = str(payload.get("predicate") or "").strip()
        value = str(payload.get("value") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if value not in classes_by_label:
            return {"skipped": f"domain 目标类不在 TBox: {value}"}
        op_["domain"] = value
        edges_marked = 0
    elif kind == "range_fix":
        label = str(payload.get("predicate") or "").strip()
        value = str(payload.get("value") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if value not in classes_by_label:
            return {"skipped": f"range 目标类不在 TBox: {value}"}
        op_["range"] = value
        edges_marked = 0
    elif kind == "subclass_of":
        # classes = [子类, 父类]（有序，axiom_llm 解析保序）；schema 权威键 + 画布
        # subclass_of 边双写——TTL 导出经边钩子自动带出 rdfs:subClassOf
        child, parent = (str(x or "").strip() for x in (payload.get("classes") or ["", ""])[:2])
        ca, cp = classes_by_label.get(child), classes_by_label.get(parent)
        if ca is None or cp is None or child == parent:
            return {"skipped": f"子类/父类不在 TBox: {child} / {parent}"}
        parents = ca.setdefault("parent_classes", [])
        prov_key = f"subclass_of:{child}|{parent}"
        edges_marked = 0
        if parent not in parents:
            parents.append(parent)
            node_by_class_label = _class_node_ids(gd)
            child_id, parent_id = node_by_class_label.get(child), node_by_class_label.get(parent)
            if child_id and parent_id:
                already = any(str(e.get("source")) == child_id and str(e.get("target")) == parent_id
                              and (e.get("data") or {}).get("relation") == "subclass_of"
                              for e in gd.get("edges") or [])
                if not already:
                    gd.setdefault("edges", []).append({
                        "id": f"e_sub_{abs(hash(f'{child_id}->{parent_id}')) % 10**10}",
                        "source": child_id, "target": parent_id,
                        "data": {"relation": "subclass_of", "label": "subClassOf",
                                 "source": "axiom_suggestion"}})
                    edges_marked = 1
    elif kind == "equivalent_classes":
        # 等价类 → schema classes[i].equivalent_to + 画布节点 data.axioms.equivalent_to
        # （TTL 导出读节点钩子落 owl:equivalentClass，O4 已扩展）
        a, b = (str(x or "").strip() for x in (payload.get("classes") or ["", ""])[:2])
        ca, cb = classes_by_label.get(a), classes_by_label.get(b)
        if ca is None or cb is None or a == b:
            return {"skipped": f"等价类不在 TBox: {a} / {b}"}
        for c, peer in ((ca, b), (cb, a)):
            equiv = c.setdefault("equivalent_to", [])
            if peer not in equiv:
                equiv.append(peer)
        prov_key = f"equivalent_classes:{min(a, b)}|{max(a, b)}"
        edges_marked = 0
        node_by_class_label = _class_node_ids(gd)
        for lbl, peer in ((a, b), (b, a)):
            nid = node_by_class_label.get(lbl)
            if not nid:
                continue
            for n in gd.get("nodes") or []:
                if str(n.get("id")) == nid:
                    d = n.get("data") or {}
                    ax = d.setdefault("axioms", {})
                    eq = ax.setdefault("equivalent_to", [])
                    if peer not in eq:
                        eq.append(peer)
                    n["data"] = d
    elif kind == "cardinality":
        # 基数建议 → schema op.cardinality_restrictions（B6 SHACL 消费）+ 画布边
        # data.min/max_cardinality（TTL Restriction 消费）；约束类取 op.domain（无则仅 schema）
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        mn, mx = payload.get("min_card"), payload.get("max_card")
        mn = int(mn) if isinstance(mn, (int, float)) else None
        mx = int(mx) if isinstance(mx, (int, float)) else None
        if mn is None and mx is None:
            return {"skipped": "cardinality 缺 min_card/max_card"}
        restr_cls = str(op_.get("domain") or "").strip()
        op_.setdefault("cardinality_restrictions", []).append(
            {"class": restr_cls, "min": mn, "max": mx})
        prov_key = f"cardinality:{label}#{len(op_['cardinality_restrictions']) - 1}"

        def _card_mutator(ax: dict) -> None:
            if mn is not None:
                ax["min_cardinality"] = mn
            if mx is not None:
                ax["max_cardinality"] = mx
        edges_marked = _mark_edges(label, _card_mutator)
    else:
        return {"skipped": f"未知公理建议类型: {kind}"}

    if prov_key:  # S16：登记来源（domain_fix/range_fix 无独立 list key，随 op 自身展示）
        _stamp_provenance(schema, prov_key, payload)
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"axiom_declared": kind, "edges_marked": edges_marked}


def list_axioms(proj: Project) -> list[dict]:
    """从 graph_data 解析已声明公理（domain_fix/range_fix 与原生 domain/range
    无法区分，不单列；它们作为属性特征随 op 自身展示）。key 为删除/编辑锚点。"""
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") or {}
    items: list[dict] = []
    seen_pairs: set[tuple] = set()

    for op_ in schema.get("object_properties") or []:
        if not isinstance(op_, dict):
            continue
        label = str(op_.get("label") or "")
        if not label:
            continue
        if op_.get("functional"):
            items.append({"key": f"functional:{label}", "kind": "functional",
                          "predicate": label})
        p, q = str(op_.get("inverse_of") or ""), label
        if p and p in {str(x.get("label")) for x in schema.get("object_properties") or []} \
                and (min(p, q), max(p, q)) not in seen_pairs:
            seen_pairs.add((min(p, q), max(p, q)))
            items.append({"key": f"inverse_of:{min(p, q)}|{max(p, q)}",
                          "kind": "inverse_of", "predicates": [p, q] if p == label else [q, p]})
        for i, r in enumerate(op_.get("cardinality_restrictions") or []):
            items.append({"key": f"cardinality:{label}#{i}", "kind": "cardinality",
                          "predicate": label, "class": str(r.get("class") or ""),
                          "min": r.get("min"), "max": r.get("max")})

    for c in schema.get("classes") or []:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or "")
        if not label:
            continue
        for peer in c.get("disjoint_with") or []:
            pair = (min(label, str(peer)), max(label, str(peer)))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                items.append({"key": f"disjoint_with:{pair[0]}|{pair[1]}",
                              "kind": "disjoint_with", "classes": list(pair)})
        for peer in c.get("equivalent_to") or []:
            pair = (min(label, str(peer)), max(label, str(peer)))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                items.append({"key": f"equivalent_classes:{pair[0]}|{pair[1]}",
                              "kind": "equivalent_classes", "classes": list(pair)})
        for parent in c.get("parent_classes") or []:
            items.append({"key": f"subclass_of:{label}|{parent}",
                          "kind": "subclass_of", "classes": [label, str(parent)]})

    # S16：挂接来源登记（无登记的存量公理 → ai/未认证，grandfather 不阻断仅提示）
    prov = schema.get("axiom_provenance") or {}
    for it in items:
        meta = prov.get(str(it.get("key") or ""))
        it["source_type"] = str((meta or {}).get("source_type") or "ai")
        it["certified"] = bool((meta or {}).get("certified"))
        if isinstance((meta or {}).get("evidence"), (dict, list)):
            it["evidence"] = meta["evidence"]
    return items


def remove_axiom(db: Session, proj: Project, key: str) -> dict:
    """按 list_axioms 的 key 撤销一条公理声明（schema 权威键 + 画布双清）。"""
    from sqlalchemy.orm.attributes import flag_modified

    kind, _, rest = key.partition(":")
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}

    def _unmark_edges(predicate: str, mutator) -> int:
        n = 0
        for e in gd.get("edges") or []:
            d = e.get("data") or {}
            if str(d.get("relation") or "") == predicate:
                mutator(d.setdefault("axioms", {}))
                e["data"] = d
                n += 1
        return n

    def _unmark_nodes(label: str, mutator) -> int:
        n = 0
        for n_ in gd.get("nodes") or []:
            d = n_.get("data") or {}
            if str(d.get("label") or "") == label and d.get("type") in ("owl:Class", "Class"):
                mutator(d.setdefault("axioms", {}))
                n_["data"] = d
                n += 1
        return n

    if kind == "functional":
        op_ = ops_by_label.get(rest)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {rest}"}
        op_.pop("functional", None)
        _unmark_edges(rest, lambda ax: ax.pop("functional", None))
    elif kind == "inverse_of":
        p, _, q = rest.partition("|")
        op_p, op_q = ops_by_label.get(p), ops_by_label.get(q)
        if op_p is None or op_q is None:
            return {"skipped": f"互逆谓词不在 TBox: {p} / {q}"}
        if op_p.get("inverse_of") == q:
            op_p.pop("inverse_of", None)
        if op_q.get("inverse_of") == p:
            op_q.pop("inverse_of", None)
        _unmark_edges(p, lambda ax: ax.pop("inverse_of", None))
        _unmark_edges(q, lambda ax: ax.pop("inverse_of", None))
    elif kind in ("disjoint_with", "equivalent_classes"):
        a, _, b = rest.partition("|")
        field = "disjoint_with" if kind == "disjoint_with" else "equivalent_to"
        for lbl, peer in ((a, b), (b, a)):
            c = classes_by_label.get(lbl)
            if c is not None and peer in (c.get(field) or []):
                c[field].remove(peer)
        def _peer_remover(peer: str):
            def _m(ax: dict) -> None:
                peers = ax.get(field) or []
                if peer in peers:
                    peers.remove(peer)
            return _m
        _unmark_nodes(a, _peer_remover(b))
        _unmark_nodes(b, _peer_remover(a))
    elif kind == "subclass_of":
        child, _, parent = rest.partition("|")
        c = classes_by_label.get(child)
        if c is None:
            return {"skipped": f"子类不在 TBox: {child}"}
        if parent in (c.get("parent_classes") or []):
            c["parent_classes"].remove(parent)
        node_by_class_label = _class_node_ids(gd)
        child_id, parent_id = node_by_class_label.get(child), node_by_class_label.get(parent)
        if child_id and parent_id:
            gd["edges"] = [e for e in gd.get("edges") or []
                           if not (str(e.get("source")) == child_id
                                   and str(e.get("target")) == parent_id
                                   and (e.get("data") or {}).get("relation") == "subclass_of")]
    elif kind == "cardinality":
        label, _, idx_s = rest.partition("#")
        op_ = ops_by_label.get(label)
        try:
            idx = int(idx_s)
        except ValueError:
            return {"skipped": f"基数公理 key 非法: {key}"}
        if op_ is None or idx >= len(op_.get("cardinality_restrictions") or []):
            return {"skipped": f"基数公理不存在: {key}"}
        op_["cardinality_restrictions"].pop(idx)
        _unmark_edges(label, lambda ax: (ax.pop("min_cardinality", None),
                                         ax.pop("max_cardinality", None)))
    else:
        return {"skipped": f"未知公理 key: {key}"}

    prov = schema.get("axiom_provenance")
    if isinstance(prov, dict):
        prov.pop(key, None)
        if kind == "cardinality":  # 索引 key 随删除位移：整组清空（list 渲染回落默认值）
            label = rest.partition("#")[0]
            for stale in [k for k in prov if k.startswith(f"cardinality:{label}#")]:
                prov.pop(stale, None)
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"removed": key}


def certify_axiom(db: Session, proj: Project, key: str, certified: bool = True,
                  decided_by: int | None = None) -> dict:
    """S16：公理一键认证/撤销（存量公理一次性盘点的入口）。

    认证 = axiom_provenance[key].certified=True + 来源升为 steward_edited（首次）；
    撤销 = certified=False（来源回落 ai，重新开放机器覆盖）。key 必须是 list_axioms 的锚点。
    """
    from sqlalchemy.orm.attributes import flag_modified

    items = {i["key"] for i in list_axioms(proj)}
    if key not in items:
        return {"skipped": f"公理不存在: {key}"}
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    meta = schema.setdefault("axiom_provenance", {}).setdefault(key, {})
    meta["certified"] = bool(certified)
    if certified:
        if meta.get("source_type") not in ("steward_edited", "steward_specified"):
            meta["source_type"] = "steward_edited"
        meta["decided_by"] = decided_by
        meta["decided_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    else:
        meta["source_type"] = "ai"
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"key": key, "certified": bool(certified),
            "source_type": meta.get("source_type", "ai")}


__all__ = ["AXIOM_KINDS", "declare_axiom", "list_axioms", "remove_axiom", "certify_axiom"]
