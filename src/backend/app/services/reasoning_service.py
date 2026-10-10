# app/services/reasoning_service.py — 蕴含推理 + 规则引擎（推理期 R1/R3）
# 核心原则：事实与推理物理分离 —— 本服务只产出「推导出的」三元组，写 reasoning_results
# 行表（source 区分 owlrl 语义闭包 / 自定义规则），绝不回写 entities/relations 事实表。
# 蕴含推理用 owlrl（RDFS / OWL-RL 语义闭包）；规则引擎是 IF 类-[谓词]->类 THEN [新谓词]
# 的产生式求值（SWRL 常用子集），类匹配带子类闭包。

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Optional

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.infrastructure.database import Entity, OntologyRule, Project, ReasoningResult, Relation

EX = Namespace("http://www.example.org/auto_ontology#")

# 单次推理持久化的推理三元组上限（防超大闭包刷库；超出部分截断并在 summary 标注）
MAX_PERSIST_INFERRED = 5000

# 内置谓词的中文展示名（owlrl 推理三元组的谓词多为内置词汇）
_BUILTIN_PRED_LABELS: dict[str, str] = {
    str(RDF.type): "类型",
    str(RDFS.subClassOf): "子类（蕴含）",
    str(RDFS.subPropertyOf): "子属性（蕴含）",
    str(RDFS.domain): "定义域",
    str(RDFS.range): "值域",
    str(OWL.sameAs): "同一实体",
    str(OWL.equivalentClass): "等价类",
    str(OWL.equivalentProperty): "等价属性",
    str(OWL.inverseOf): "互逆",
    str(OWL.disjointWith): "互斥",
}


def _safe_prop_id(label: str) -> str:
    """与 generate_ttl_from_graph_data 的属性 URI 约定一致：prop_{md5 前 8 位}。"""
    return f"prop_{hashlib.md5(label.encode('utf-8')).hexdigest()[:8]}"


def _node_uri(node_id: str) -> URIRef:
    """与 generate_ttl_from_graph_data 的 make_uri 一致（画布节点 id → 类 URI）。"""
    import re

    if "#" in node_id or node_id.startswith("http"):
        return URIRef(node_id)
    node_id = node_id.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", node_id):
        return EX[node_id]
    md5 = hashlib.md5(node_id.encode("utf-8")).hexdigest()[:8]
    prefix = "Node"
    if node_id.startswith("C_"):
        prefix = "C"
    elif node_id.startswith("I_"):
        prefix = "I"
    elif node_id.startswith("OP_"):
        prefix = "OP"
    elif node_id.startswith("DP_"):
        prefix = "DP"
    return EX[f"{prefix}_{md5}"]


def _filter_noise(s: URIRef, p: URIRef, o: Any) -> bool:
    """过滤 owlrl 闭包噪音：sameAs 自反、内置词汇主语上的公理三元组、
    字面量数据类型标注（如 0.98 → xsd:double）、一切自指三元组。"""
    if s == o:
        return True
    if isinstance(s, Literal):
        return True
    if p == RDF.type and isinstance(o, URIRef) and str(o).startswith("http://www.w3.org/2001/XMLSchema#"):
        return True
    if isinstance(o, URIRef) and str(o) == str(OWL.Thing):
        return True
    if str(s).startswith(str(RDF)) or str(s).startswith(str(RDFS)) or str(s).startswith(str(OWL)):
        return True
    return False


def _class_uris_by_label(nodes: list[dict]) -> dict[str, URIRef]:
    """画布类节点 label → URI（与 TTL 导出的 make_uri 约定一致）。"""
    out: dict[str, URIRef] = {}
    for node in nodes:
        node_data = node.get("data", {})
        if str(node_data.get("type", "")) in ("owl:Class", "Class", "owl:ActionType"):
            out[str(node_data.get("label", ""))] = _node_uri(str(node["id"]))
    return out


def _abox_into_graph(g: Graph, db: Session, project: Project) -> dict[str, str]:
    """ABox（实体 + 实例关系）三元组并入 g；返回 uri→label 映射。"""
    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    class_uri_by_label = _class_uris_by_label(nodes)
    label_by_uri: dict[str, str] = {}

    ents = db.query(Entity).filter(
        Entity.project_id == project.id,
        Entity.is_class_node.is_(False),
        Entity.status.notin_(("rejected", "merged")),
    ).all()
    for e in ents:
        if e.uri:
            g.add((URIRef(e.uri), RDF.type, OWL.NamedIndividual))
            if e.class_label and e.class_label in class_uri_by_label:
                g.add((URIRef(e.uri), RDF.type, class_uri_by_label[e.class_label]))
            g.add((URIRef(e.uri), RDFS.label, Literal(e.label or "", lang="zh")))
            label_by_uri[e.uri] = e.label or ""

    rels = db.query(Relation).filter(
        Relation.project_id == project.id,
        Relation.is_class_edge.is_(False),
        Relation.status.notin_(("rejected",)),
    ).all()
    ent_by_id = {e.id: e for e in ents}
    pred_uri_by_label: dict[str, URIRef] = {}
    for r in rels:
        s_ent, o_ent = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if not s_ent or not o_ent or not s_ent.uri or not o_ent.uri:
            continue
        p_uri = pred_uri_by_label.get(r.predicate)
        if p_uri is None:
            p_uri = EX[_safe_prop_id(r.predicate)]
            pred_uri_by_label[r.predicate] = p_uri
            g.add((p_uri, RDF.type, OWL.ObjectProperty))
            g.add((p_uri, RDFS.label, Literal(r.predicate, lang="zh")))
            label_by_uri[str(p_uri)] = r.predicate
        g.add((URIRef(s_ent.uri), p_uri, URIRef(o_ent.uri)))
    return label_by_uri


def _build_fact_graph(db: Session, project: Project) -> tuple[Graph, dict[str, str]]:
    """事实图 = TBox（画布 TTL）+ ABox（entities/relations 行表）。返回 (图, uri→label 映射)。"""
    from app.api.ontology import generate_ttl_from_graph_data

    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []

    g = Graph()
    # ── TBox：复用统一 TTL 生成（含公理导出：disjointWith / 特征 / 基数 Restriction）──
    tbox_ttl = generate_ttl_from_graph_data(nodes, edges)
    g.parse(data=tbox_ttl, format="turtle")
    label_by_uri = {str(s): str(lbl) for s, _, lbl in g.triples((None, RDFS.label, None))}

    # ── ABox：实例 + 实例关系 ──
    label_by_uri.update(_abox_into_graph(g, db, project))
    return g, label_by_uri


def _build_fact_graph(db: Session, project: Project) -> tuple[Graph, dict[str, str]]:
    """事实图 = TBox（画布 TTL）+ ABox（entities/relations 行表）。返回 (图, uri→label 映射)。"""
    from app.api.ontology import generate_ttl_from_graph_data

    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []

    g = Graph()
    # ── TBox：复用统一 TTL 生成（含公理导出：disjointWith / 特征 / 基数 Restriction）──
    tbox_ttl = generate_ttl_from_graph_data(nodes, edges)
    g.parse(data=tbox_ttl, format="turtle")
    label_by_uri = {str(s): str(lbl) for s, _, lbl in g.triples((None, RDFS.label, None))}

    # ── ABox：实例 + 实例关系 ──
    label_by_uri.update(_abox_into_graph(g, db, project))
    return g, label_by_uri


def _class_closure(class_label: str, parent_map: dict[str, list[str]]) -> set[str]:
    """类标签 → 自身 + 全部祖先标签（规则匹配用，带环保护）。"""
    closure = {class_label}
    stack = [class_label]
    while stack:
        cur = stack.pop()
        for parent in parent_map.get(cur, []):
            if parent and parent not in closure:
                closure.add(parent)
                stack.append(parent)
    return closure


def _build_parent_map(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """画布 subclass_of 边 → label → 父类 label 列表。"""
    id_to_label = {str(n["id"]): str(n.get("data", {}).get("label", n["id"])) for n in nodes}
    parent_map: dict[str, list[str]] = {}
    for edge in edges:
        ed = edge.get("data", {})
        if ed.get("relation") == "subclass_of" or ed.get("label") in ("subClassOf", "subclass_of"):
            child = id_to_label.get(str(edge.get("source")))
            parent = id_to_label.get(str(edge.get("target")))
            if child and parent:
                parent_map.setdefault(child, []).append(parent)
    return parent_map


def _run_rules(db: Session, project: Project, nodes: list[dict], edges: list[dict],
               rule_ids: Optional[list[int]] = None) -> list[dict]:
    """规则引擎（R3）：IF 主语类-[谓词]->宾语类 THEN 推断(主语, 结论谓词, 宾语)。

    类匹配带子类闭包；已存在同谓词事实关系的不重复推导。返回推理三元组 dict 列表。
    """
    rules_q = db.query(OntologyRule).filter(
        OntologyRule.project_id == project.id, OntologyRule.enabled.is_(True))
    if rule_ids:
        rules_q = rules_q.filter(OntologyRule.id.in_(rule_ids))
    rules = rules_q.all()
    if not rules:
        return []

    parent_map = _build_parent_map(nodes, edges)
    ents = db.query(Entity).filter(
        Entity.project_id == project.id,
        Entity.is_class_node.is_(False),
        Entity.status.notin_(("rejected", "merged")),
    ).all()
    ent_by_id = {e.id: e for e in ents}
    rels = db.query(Relation).filter(
        Relation.project_id == project.id,
        Relation.is_class_edge.is_(False),
        Relation.status.notin_(("rejected",)),
    ).all()
    # 事实谓词去重集：规则推导不重复已存在的事实
    fact_keys = {(r.subject_id, r.predicate, r.object_id) for r in rels}

    closure_cache: dict[str, set[str]] = {}
    results: list[dict] = []
    for r in rels:
        s_ent, o_ent = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if not s_ent or not o_ent:
            continue
        for rule in rules:
            if r.predicate != rule.if_predicate:
                continue
            s_cls = s_ent.class_label or ""
            o_cls = o_ent.class_label or ""
            s_closure = closure_cache.setdefault(s_cls, _class_closure(s_cls, parent_map))
            o_closure = closure_cache.setdefault(o_cls, _class_closure(o_cls, parent_map))
            if rule.if_subject_class not in s_closure or rule.if_object_class not in o_closure:
                continue
            if (r.subject_id, rule.then_predicate, r.object_id) in fact_keys:
                continue
            results.append({
                "source": "rule",
                "rule_name": rule.name,
                "subject_uri": s_ent.uri or "",
                "subject_label": s_ent.label or "",
                "predicate_uri": str(EX[_safe_prop_id(rule.then_predicate)]),
                "predicate_label": rule.then_predicate,
                "object_uri": o_ent.uri or "",
                "object_label": o_ent.label or "",
            })
    return results


def run_reasoning(db: Session, project: Project, profile: str = "owlrl",
                  rule_ids: Optional[list[int]] = None) -> dict:
    """执行一次推理批次：owlrl 语义闭包 + 规则引擎，结果落 reasoning_results（latest-only）。

    profile: 'owlrl'（OWL-RL + RDFS）或 'rdfs'（仅 RDFS）。
    """
    import owlrl

    fact_graph, label_by_uri = _build_fact_graph(db, project)
    facts = set(fact_graph)
    fact_count = len(facts)

    closure_cls = owlrl.RDFS_Semantics if profile == "rdfs" else owlrl.OWLRL_Semantics
    owlrl.DeductiveClosure(closure_cls, rdfs_closure=True).expand(fact_graph)
    raw_inferred = set(fact_graph) - facts

    # ── owlrl 蕴含 → dict 列表（过滤噪音 + 标注内置谓词中文名）──
    entailment: list[dict] = []
    for s, p, o in sorted(raw_inferred, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        if _filter_noise(s, p, o):
            continue
        s_label = label_by_uri.get(str(s), str(s).split("#")[-1].split("/")[-1])
        o_label = str(o) if isinstance(o, Literal) else \
            label_by_uri.get(str(o), str(o).split("#")[-1].split("/")[-1])
        entailment.append({
            "source": profile,
            "rule_name": None,
            "subject_uri": str(s),
            "subject_label": s_label,
            "predicate_uri": str(p),
            "predicate_label": _BUILTIN_PRED_LABELS.get(str(p), str(p).split("#")[-1].split("/")[-1]),
            "object_uri": str(o),
            "object_label": o_label,
        })

    # ── 规则引擎 ──
    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []
    rule_results = _run_rules(db, project, nodes, edges, rule_ids)

    # ── 落库（latest-only：先清旧批次）──
    batch_id = str(uuid.uuid4())
    db.query(ReasoningResult).filter(ReasoningResult.project_id == project.id).delete()
    rows = (entailment + rule_results)[:MAX_PERSIST_INFERRED]
    for item in rows:
        db.add(ReasoningResult(project_id=project.id, batch_id=batch_id, **item))
    db.commit()

    by_rule: dict[str, int] = {}
    for item in rule_results:
        by_rule[item["rule_name"]] = by_rule.get(item["rule_name"], 0) + 1
    summary = {
        "batch_id": batch_id,
        "profile": profile,
        "fact_triples": fact_count,
        "inferred_entailment": len(entailment),
        "inferred_rule": len(rule_results),
        "inferred_total": len(entailment) + len(rule_results),
        "persisted": len(rows),
        "truncated": (len(entailment) + len(rule_results)) > MAX_PERSIST_INFERRED,
        "by_rule": by_rule,
    }
    logger.info(f"[reasoning] 项目 {project.id} 推理完成：{summary}")
    return summary


def get_latest_results(db: Session, project_id: int, limit: int = 200,
                       source: Optional[str] = None) -> dict:
    """取最新批次的推理结果行（source 过滤可选）。"""
    latest = (db.query(ReasoningResult)
              .filter(ReasoningResult.project_id == project_id)
              .order_by(ReasoningResult.id.desc()).first())
    if not latest:
        return {"batch_id": None, "items": [], "total": 0}
    q = db.query(ReasoningResult).filter(
        ReasoningResult.project_id == project_id,
        ReasoningResult.batch_id == latest.batch_id)
    if source:
        q = q.filter(ReasoningResult.source == source)
    total = q.count()
    items = q.order_by(ReasoningResult.source, ReasoningResult.id).limit(limit).all()
    return {
        "batch_id": latest.batch_id,
        "total": total,
        "items": [
            {"id": it.id, "source": it.source, "rule_name": it.rule_name,
             "subject_label": it.subject_label, "subject_uri": it.subject_uri,
             "predicate_label": it.predicate_label, "predicate_uri": it.predicate_uri,
             "object_label": it.object_label, "object_uri": it.object_uri}
            for it in items
        ],
    }


def export_ttl_with_inference(db: Session, project: Project, profile: str = "owlrl") -> str:
    """R2：事实 TTL + 横幅注释分隔的推理分节。推理三元组与原始事实在文件内物理分节。"""
    import owlrl

    fact_graph, _ = _build_fact_graph(db, project)
    facts = set(fact_graph)
    fact_count = len(facts)

    closure_cls = owlrl.RDFS_Semantics if profile == "rdfs" else owlrl.OWLRL_Semantics
    owlrl.DeductiveClosure(closure_cls, rdfs_closure=True).expand(fact_graph)
    raw_inferred = set(fact_graph) - facts

    ig = Graph()
    kept = 0
    for t in raw_inferred:
        if _filter_noise(*t):
            continue
        ig.add(t)
        kept += 1
    for prefix, ns in (("ex", EX), ("owl", OWL), ("rdfs", RDFS), ("rdf", RDF)):
        ig.bind(prefix, ns)
    profile_name = "RDFS" if profile == "rdfs" else "OWL-RL"
    banner = (
        "\n"
        "# " + "=" * 66 + "\n"
        "# ⚠ 推理结果（INFERRED TRIPLES）—— 本节三元组为语义推导产物，非原始事实\n"
        f"# 推理档位：{profile_name} ｜ 原始事实 {fact_count} 条 ｜ 推理得出 {kept} 条\n"
        "# 原始事实见上方分节；消费方请勿将本节内容当作已确认的数据。\n"
        "# " + "=" * 66 + "\n\n"
    )
    fact_ttl = generate_fact_ttl_only(db, project)
    return fact_ttl + banner + ig.serialize(format="turtle")


def generate_fact_ttl_only(db: Session, project: Project) -> str:
    """仅事实（TBox+ABox）的 TTL——供含推理导出的分节 1 使用。"""
    fact_graph, _ = _build_fact_graph(db, project)
    return fact_graph.serialize(format="turtle")
