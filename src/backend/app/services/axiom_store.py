# app/services/axiom_store.py - 公理声明统一存取（O4 后扩展：规则与公理统一管理）
# 单一事实源 = Project.graph_data：schema 权威键 + 画布 node/edge data.axioms 双写。
# 写入路径有三条：审核批准（review_service._approve_axiom_suggestion 委托本模块）、
# 推理弹窗手工新增/编辑（api/reasoning.py 公理 CRUD）、抽取批准链路。
# TTL 导出与 owlrl 推理经既有钩子消费同一份数据。

from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure.database import Project

AXIOM_KINDS = ("functional", "inverse_of", "disjoint_with", "domain_fix",
               "range_fix", "subclass_of", "equivalent_classes", "cardinality",
               "transitive", "symmetric", "inverse_functional", "subproperty_of")


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
        edges_marked = _mark_edges(label, lambda ax: ax.update(functional=True))
    elif kind == "inverse_of":
        p, q = (str(x or "").strip() for x in (payload.get("predicates") or ["", ""])[:2])
        op_p, op_q = ops_by_label.get(p), ops_by_label.get(q)
        if op_p is None or op_q is None or p == q:
            return {"skipped": f"互逆谓词不在 TBox: {p} / {q}"}
        op_p["inverse_of"] = q
        op_q["inverse_of"] = p
        edges_marked = (_mark_edges(p, lambda ax: ax.update(inverse_of=q))
                        + _mark_edges(q, lambda ax: ax.update(inverse_of=p)))
    elif kind == "disjoint_with":
        a, b = (str(x or "").strip() for x in (payload.get("classes") or ["", ""])[:2])
        ca, cb = classes_by_label.get(a), classes_by_label.get(b)
        if ca is None or cb is None or a == b:
            return {"skipped": f"互斥类不在 TBox: {a} / {b}"}
        ca.setdefault("disjoint_with", [])
        cb.setdefault("disjoint_with", [])
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
        def _card_mutator(ax: dict) -> None:
            if mn is not None:
                ax["min_cardinality"] = mn
            if mx is not None:
                ax["max_cardinality"] = mx
        edges_marked = _mark_edges(label, _card_mutator)
    elif kind == "transitive":
        # 传递关系（如 下游/包含/位于）：schema op.transitive + 画布边标记 +
        # **闭包落图**——推断边显式写入（data.inferred=True + axioms.transitive），
        # 图谱浏览/问答沿边遍历即可答"从 A 能传到哪"，撤销公理时一并清理。
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if op_.get("transitive"):
            return {"skipped": f"「{label}」已声明传递性"}
        op_["transitive"] = True
        edges_marked = _mark_edges(label, lambda ax: ax.update(transitive=True))
        inferred = _materialize_transitive_closure(gd, label)
        edges_marked += inferred
    elif kind == "symmetric":
        # 对称关系（相邻/互为对接）：声明 + **镜像边落图**（A—X→B ⇒ B—X→A，
        # 推断边打标，撤销时清理）
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if op_.get("symmetric"):
            return {"skipped": f"「{label}」已声明对称性"}
        op_["symmetric"] = True
        edges_marked = _mark_edges(label, lambda ax: ax.update(symmetric=True))
        edges_marked += _materialize_symmetric_closure(gd, label)
    elif kind == "inverse_functional":
        # 反函数性（客体唯一确定主体，如 账号→账户）：声明即生效（消解/QA 信号）
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if op_.get("inverse_functional"):
            return {"skipped": f"「{label}」已声明反函数性"}
        op_["inverse_functional"] = True
        edges_marked = _mark_edges(label, lambda ax: ax.update(inverse_functional=True))
    elif kind == "subproperty_of":
        # 关系层级（转账给 ⊑ 付款给）：schema 权威键（owlrl/TTL 消费；画布无边可挂）
        label = str(payload.get("predicate") or "").strip()
        parent = str(payload.get("value") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        if parent not in ops_by_label or parent == label:
            return {"skipped": f"父关系不在 TBox: {parent}"}
        op_["subproperty_of"] = parent
        edges_marked = 0
    else:
        return {"skipped": f"未知公理建议类型: {kind}"}

    # ── 申报留痕：来源（llm 文档抽取/statistical 统计推断/manual 人工）+ 证据 ──
    # 公理列表据此展示"从哪来、依据什么"（O4 公理质量强化 2026-11）
    _record_axiom(gd, payload)

    # ── 派生公理：从已有公理逻辑蕴含的新公理（sound 推导，自动落库留痕）──
    # 单层推导不递归：派生公理的再派生在下次申报时补全
    derived_n = _derive_implied_axioms(gd)

    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()

    out = {"axiom_declared": kind, "edges_marked": edges_marked}
    if derived_n:
        out["derived_axioms"] = derived_n
    if kind == "transitive":
        out["transitive_edges_inferred"] = len([e for e in gd.get("edges") or []
                                                if (e.get("data") or {}).get("inferred")
                                                and str((e.get("data") or {}).get("relation") or "") == str(payload.get("predicate") or "")])
    return out


def _materialize_symmetric_closure(gd: dict, predicate: str) -> int:
    """对称关系镜像边：A—X→B ⇒ B—X→A（推断边打标，撤销时清理）。"""
    existing = {(str(e.get("source")), str(e.get("target")))
                for e in gd.get("edges") or []}
    added = 0
    for e in list(gd.get("edges") or []):
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if str(d.get("relation") or d.get("label") or "") != predicate:
            continue
        s, t = str(e.get("source")), str(e.get("target"))
        if s == t or (t, s) in existing:
            continue
        existing.add((t, s))
        gd.setdefault("edges", []).append({
            "id": f"e_inf_{abs(hash(f'{t}|{predicate}|{s}')) % 10 ** 12}",
            "source": t, "target": s, "type": "smoothstep",
            "label": predicate,
            "data": {"relation": predicate, "type": "ObjectProperty",
                     "inferred": True, "axioms": {"symmetric": True},
                     "inference": "symmetric-mirror"}})
        added += 1
        if added > TRANSITIVE_MAX_INFERRED:
            break
    return added


# 传递闭包护栏：单次声明最多物化的推断边数 / 参与闭包的最大边数（防大图 O(n³) 爆炸）
TRANSITIVE_MAX_REL_EDGES = 3000
TRANSITIVE_MAX_INFERRED = 5000


def _materialize_transitive_closure(gd: dict, predicate: str) -> int:
    """对画布上 relation==predicate 的实例边求传递闭包，推断边写入 blob 并打标。

    打标（inferred=True + axioms.transitive）供撤销时识别清理；已有直接边的
    点对不重复加。返回新增推断边数。"""
    adj: dict[str, set[str]] = {}
    rel_edge_count = 0
    for e in gd.get("edges") or []:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if str(d.get("relation") or d.get("label") or "") == predicate:
            rel_edge_count += 1
            if rel_edge_count > TRANSITIVE_MAX_REL_EDGES:
                return 0  # 超护栏：只声明不物化（保持声明，浏览侧不展开）
            adj.setdefault(str(e.get("source")), set()).add(str(e.get("target")))

    existing_pairs = {(str(e.get("source")), str(e.get("target")))
                      for e in gd.get("edges") or []}
    closure: set[tuple[str, str]] = set()

    def _walk(start: str, cur: str, depth: int) -> None:
        if depth > 16:  # 深度护栏
            return
        for nxt in adj.get(cur, ()):
            if (start, nxt) not in existing_pairs and (start, nxt) not in closure:
                closure.add((start, nxt))
                if len(closure) > TRANSITIVE_MAX_INFERRED:
                    return
            _walk(start, nxt, depth + 1)

    for start in list(adj):
        _walk(start, start, 0)
        if len(closure) > TRANSITIVE_MAX_INFERRED:
            break

    for (s, t) in closure:
        gd.setdefault("edges", []).append({
            "id": f"e_inf_{abs(hash(f'{s}|{predicate}|{t}')) % 10 ** 12}",
            "source": s, "target": t, "type": "smoothstep",
            "label": predicate,
            "data": {"relation": predicate, "type": "ObjectProperty",
                     "inferred": True, "axioms": {"transitive": True},
                     "inference": "transitive-closure"},
        })
    return len(closure)


# ── 申报留痕与派生公理（O4 公理质量强化 2026-11）──

def _axiom_record_key(gd: dict, payload: dict) -> str:
    """按 list_axioms 的 key 口径预计算申报键（cardinality 序号取 restrictions 尾序）。"""
    kind = str(payload.get("kind") or "")

    def _pair(a: str, b: str) -> str:
        return f"{min(a, b)}|{max(a, b)}"

    if kind in ("functional", "transitive", "symmetric", "inverse_functional"):
        p = str(payload.get("predicate") or "").strip()
        return f"{kind}:{p}" if p else ""
    if kind == "inverse_of":
        ps = [str(x or "").strip() for x in (payload.get("predicates") or [])[:2]]
        return f"inverse_of:{_pair(*ps)}" if len(ps) == 2 and ps[0] != ps[1] else ""
    if kind in ("disjoint_with", "equivalent_classes"):
        cs = [str(x or "").strip() for x in (payload.get("classes") or [])[:2]]
        return f"{kind}:{_pair(*cs)}" if len(cs) == 2 and cs[0] != cs[1] else ""
    if kind == "subclass_of":
        cs = [str(x or "").strip() for x in (payload.get("classes") or [])[:2]]
        return f"subclass_of:{cs[0]}|{cs[1]}" if len(cs) == 2 and cs[0] != cs[1] else ""
    if kind == "subproperty_of":
        p = str(payload.get("predicate") or "").strip()
        v = str(payload.get("value") or "").strip()
        return f"subproperty_of:{p}|{v}" if p and v else ""
    if kind == "cardinality":
        p = str(payload.get("predicate") or "").strip()
        schema = (gd.get("schema") or {})
        op_ = next((o for o in schema.get("object_properties") or []
                    if isinstance(o, dict) and o.get("label") == p), None)
        idx = max(0, len((op_ or {}).get("cardinality_restrictions") or []) - 1)
        return f"cardinality:{p}#{idx}" if p else ""
    if kind in ("domain_fix", "range_fix"):
        p = str(payload.get("predicate") or "").strip()
        return f"{kind}:{p}" if p else ""
    return ""


def _record_axiom(gd: dict, payload: dict) -> None:
    """申报留痕：gd.data.axiom_records[key] = 来源 + 证据 + 时间。

    origin ∈ document（LLM 通道，依据文档表述与实例样本）/ statistical（统计
    推断，依据数据分布）/ manual（人工声明）。evidence 含 llm_reason/examples/
    doc_evidence（文档原文锚定，axiom_doc_evidence 产出）。
    """
    from datetime import datetime

    key = _axiom_record_key(gd, payload)
    if not key:
        return
    src = str(payload.get("source") or "").strip()
    origin = src if src in ("llm", "statistical") else "manual"
    if origin == "llm":
        origin = "document"
    records = gd.setdefault("data", {}).setdefault("axiom_records", {})
    records[key] = {
        "origin": origin,
        "evidence": payload.get("evidence") or {},
        "summary": payload.get("summary") or "",
        "declared_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def _existing_axiom_keys(gd: dict) -> set[str]:
    """当前已声明公理键集合（与 list_axioms 同口径，供派生去重）。"""

    def _pair(a: str, b: str) -> str:
        return f"{min(a, b)}|{max(a, b)}"

    keys = set()
    schema = gd.get("schema") or {}
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    for lbl, op_ in ops_by_label.items():
        for feat in ("functional", "transitive", "symmetric", "inverse_functional"):
            if op_.get(feat):
                keys.add(f"{feat}:{lbl}")
        if op_.get("subproperty_of"):
            keys.add(f"subproperty_of:{lbl}|{op_['subproperty_of']}")
        p = str(op_.get("inverse_of") or "")
        if p in ops_by_label:
            keys.add(f"inverse_of:{_pair(p, lbl)}")
        for i, _r in enumerate(op_.get("cardinality_restrictions") or []):
            keys.add(f"cardinality:{lbl}#{i}")
    for c in (schema.get("classes") or []):
        if not (isinstance(c, dict) and c.get("label")):
            continue
        lbl = str(c["label"])
        for peer in (c.get("disjoint_with") or []):
            keys.add(f"disjoint_with:{_pair(lbl, str(peer))}")
        for peer in (c.get("equivalent_to") or []):
            keys.add(f"equivalent_classes:{_pair(lbl, str(peer))}")
        for parent in (c.get("parent_classes") or []):
            keys.add(f"subclass_of:{lbl}|{str(parent)}")
    return keys


def _derive_implied_axioms(gd: dict) -> int:
    """从已有公理推导逻辑蕴含的新公理（sound：不会引入新矛盾，直接落库留痕）。

    三条推导规则：
    ① 子属性传递：p⊑q 且 q⊑r ⇒ p⊑r
    ② 互斥下传：A⊥B 且 C⊑A ⇒ C⊥B（对称侧同理）——互斥沿类层次向下传播
    ③ 互逆×特征耦合：p 函数性且 p↔q ⇒ q 反函数性；p 反函数性且 p↔q ⇒ q 函数性
    派生公理记 origin=derived + derived_from（依据哪几条已有公理），单层不递归。
    """
    from datetime import datetime

    def _pair(a: str, b: str) -> str:
        return f"{min(a, b)}|{max(a, b)}"

    schema = gd.get("schema") or {}
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}
    records = gd.setdefault("data", {}).setdefault("axiom_records", {})
    existing = _existing_axiom_keys(gd)
    now = datetime.utcnow().isoformat(timespec="seconds")
    added = 0

    def _declare(key: str, derived_from: list[str]) -> None:
        nonlocal added
        if not key or key in existing:
            return
        existing.add(key)
        records[key] = {"origin": "derived", "evidence": {},
                        "summary": "", "declared_at": now,
                        "derived_from": derived_from}
        added += 1

    # ① 子属性传递
    for lbl, op_ in ops_by_label.items():
        q = str(op_.get("subproperty_of") or "")
        r = str((ops_by_label.get(q) or {}).get("subproperty_of") or "")
        if q and r and r != lbl:
            op_["subproperty_of"] = r
            _declare(f"subproperty_of:{lbl}|{r}",
                     [f"subproperty_of:{lbl}|{q}", f"subproperty_of:{q}|{r}"])
    # ② 互斥沿类层次下传（子类继承父类的互斥约束）
    parent_map = {lbl: [str(x) for x in ((c or {}).get("parent_classes") or [])]
                  for lbl, c in classes_by_label.items()}
    disjoint_pairs: set[frozenset] = set()
    for lbl, c in classes_by_label.items():
        for peer in (c.get("disjoint_with") or []):
            if peer and peer != lbl:
                disjoint_pairs.add(frozenset((lbl, str(peer))))

    def _descendants(label: str) -> set[str]:
        out: set[str] = set()
        stack = [label]
        while stack:
            cur = stack.pop()
            for child, parents in parent_map.items():
                if cur in parents and child not in out:
                    out.add(child)
                    stack.append(child)
        return out

    def _ancestors_of(label: str) -> set[str]:
        out: set[str] = set()
        stack = [label]
        while stack:
            cur = stack.pop()
            for parent in parent_map.get(cur, []):
                if parent not in out:
                    out.add(parent)
                    stack.append(parent)
        return out

    for pair in list(disjoint_pairs):
        a, b = sorted(pair)
        for child in sorted(_descendants(a)):
            # 父子链上的互斥派生跳过（B⊑A 时声明 A⊥B 会使 B 自斥，病态不加）
            if b == child or b in _ancestors_of(child) or child in _ancestors_of(b):
                continue
            writer = classes_by_label.get(child) or {}
            peers = writer.setdefault("disjoint_with", [])
            if b not in peers:
                peers.append(b)
            if b in classes_by_label:
                back = classes_by_label[b].setdefault("disjoint_with", [])
                if child not in back:
                    back.append(child)
            _declare(f"disjoint_with:{_pair(child, b)}",
                     [f"disjoint_with:{_pair(a, b)}", f"subclass_of:{child}|{a}"])
        for child in sorted(_descendants(b)):
            if a == child or a in _ancestors_of(child) or child in _ancestors_of(a):
                continue
            writer = classes_by_label.get(child) or {}
            peers = writer.setdefault("disjoint_with", [])
            if a not in peers:
                peers.append(a)
            if a in classes_by_label:
                back = classes_by_label[a].setdefault("disjoint_with", [])
                if child not in back:
                    back.append(child)
            _declare(f"disjoint_with:{_pair(a, child)}",
                     [f"disjoint_with:{_pair(a, b)}", f"subclass_of:{child}|{b}"])
    # ③ 互逆×特征耦合
    for lbl, op_ in ops_by_label.items():
        inv = str(op_.get("inverse_of") or "")
        if not inv or inv not in ops_by_label or inv == lbl:
            continue
        if op_.get("functional"):
            target = ops_by_label[inv]
            if not target.get("inverse_functional"):
                target["inverse_functional"] = True
                _declare(f"inverse_functional:{inv}",
                         [f"functional:{lbl}", f"inverse_of:{_pair(lbl, inv)}"])
        if op_.get("inverse_functional"):
            target = ops_by_label[inv]
            if not target.get("functional"):
                target["functional"] = True
                _declare(f"functional:{inv}",
                         [f"inverse_functional:{lbl}", f"inverse_of:{_pair(lbl, inv)}"])
    return added


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
        if op_.get("transitive"):
            items.append({"key": f"transitive:{label}", "kind": "transitive",
                          "predicate": label})
        if op_.get("symmetric"):
            items.append({"key": f"symmetric:{label}", "kind": "symmetric",
                          "predicate": label})
        if op_.get("inverse_functional"):
            items.append({"key": f"inverse_functional:{label}", "kind": "inverse_functional",
                          "predicate": label})
        if op_.get("subproperty_of"):
            items.append({"key": f"subproperty_of:{label}|{op_['subproperty_of']}",
                          "kind": "subproperty_of", "predicate": label,
                          "value": op_["subproperty_of"]})
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

    # ── 申报留痕合并：来源（document/statistical/derived/manual）+ 证据 + 派生依据 ──
    # 无留痕的键：subclass_of（画布原生层次）= 抽取声明；其余 = 早期人工申报
    records = (gd.get("data") or {}).get("axiom_records") or {}
    for it in items:
        rec = records.get(it["key"])
        if rec:
            it["origin"] = rec.get("origin") or "manual"
            if rec.get("evidence"):
                it["evidence"] = rec["evidence"]
            if rec.get("summary"):
                it["summary"] = rec["summary"]
            if rec.get("derived_from"):
                it["derived_from"] = rec["derived_from"]
            if rec.get("declared_at"):
                it["declared_at"] = rec["declared_at"]
        else:
            it["origin"] = "schema" if it["kind"] == "subclass_of" else "manual"
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
    elif kind == "transitive":
        op_ = ops_by_label.get(rest)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {rest}"}
        op_.pop("transitive", None)
        # 先清闭包推断边（靠 inferred 标识别），再解直接边上的 transitive 标记
        gd["edges"] = [e for e in gd.get("edges") or []
                       if not (str((e.get("data") or {}).get("relation") or "") == rest
                               and (e.get("data") or {}).get("inferred"))]
        _unmark_edges(rest, lambda ax: (ax.pop("transitive", None),
                                        ax.pop("inferred", None)))
    elif kind == "symmetric":
        op_ = ops_by_label.get(rest)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {rest}"}
        op_.pop("symmetric", None)
        gd["edges"] = [e for e in gd.get("edges") or []
                       if not (str((e.get("data") or {}).get("relation") or "") == rest
                               and (e.get("data") or {}).get("inferred"))]
        _unmark_edges(rest, lambda ax: (ax.pop("symmetric", None),
                                        ax.pop("inferred", None)))
    elif kind == "inverse_functional":
        op_ = ops_by_label.get(rest)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {rest}"}
        op_.pop("inverse_functional", None)
        _unmark_edges(rest, lambda ax: ax.pop("inverse_functional", None))
    elif kind == "subproperty_of":
        op_ = ops_by_label.get(rest.partition("|")[0])
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {rest.partition('|')[0]}"}
        op_.pop("subproperty_of", None)
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
                if not c[field]:  # 撤干净：不留空列表（保持 schema 键面整洁）
                    c.pop(field, None)
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

    # ── 留痕清理：无序对类 key 两种顺序的留痕一并清（防陈旧记录残留）──
    kind_pair_variants = {key}
    if kind in ("inverse_of", "disjoint_with", "equivalent_classes"):
        a_, _, b_ = rest.partition("|")
        kind_pair_variants = {f"{kind}:{a_}|{b_}", f"{kind}:{b_}|{a_}"}
    records_all = (gd.get("data") or {}).get("axiom_records") or {}
    for k in kind_pair_variants:
        records_all.pop(k, None)

    # ── 级联清理：派生公理（origin=derived 且 derived_from 引用本键）一并撤销 ──
    # 派生公理的逻辑依据没了就该跟着走，否则撤回原公理后矛盾残留（2026-11-04
    # test_axiom_auto_declare_rollback_on_unsatisfiable 揭示）
    cascade_n = _cascade_derived_axioms(gd, kind_pair_variants)

    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    out = {"removed": key}
    if cascade_n:
        out["derived_removed"] = cascade_n
    return out


def _remove_derived_key(gd: dict, key: str) -> None:
    """按 key 撤销派生公理的 schema 写入（派生仅四类，无推断边要清理）。"""
    kind, _, rest = key.partition(":")
    schema = gd.get("schema") or {}
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}
    if kind in ("functional", "transitive", "symmetric", "inverse_functional"):
        op_ = ops_by_label.get(rest)
        if op_ is not None:
            op_.pop(kind, None)
    elif kind == "subproperty_of":
        op_ = ops_by_label.get(rest.partition("|")[0])
        if op_ is not None:
            op_.pop("subproperty_of", None)
    elif kind == "disjoint_with":
        a, _, b = rest.partition("|")
        for lbl, peer in ((a, b), (b, a)):
            c = classes_by_label.get(lbl)
            if c is not None and peer in (c.get("disjoint_with") or []):
                c["disjoint_with"].remove(peer)
                if not c["disjoint_with"]:
                    c.pop("disjoint_with", None)


def _cascade_derived_axioms(gd: dict, keys: set[str]) -> int:
    """递归撤销依赖 keys 中任一公理的派生公理（派生的派生也跟进）。返回撤销数。"""
    records = ((gd.get("data") or {}).get("axiom_records") or {})
    removed = 0
    stack = list(keys)
    while stack:
        k = stack.pop()
        for rk, rec in list(records.items()):
            if rec.get("origin") == "derived" and k in (rec.get("derived_from") or []):
                _remove_derived_key(gd, rk)
                records.pop(rk, None)
                stack.append(rk)
                removed += 1
    return removed


__all__ = ["AXIOM_KINDS", "declare_axiom", "list_axioms", "remove_axiom"]
