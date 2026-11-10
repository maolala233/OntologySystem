# app/services/reasoning_service.py — 蕴含推理 + 规则引擎（推理期 R1/R3）
# 核心原则：事实与推理物理分离 —— 本服务只产出「推导出的」三元组，写 reasoning_results
# 行表（source 区分 owlrl 语义闭包 / 自定义规则），绝不回写 entities/relations 事实表。
# 蕴含推理用 owlrl（RDFS / OWL-RL 语义闭包）；规则引擎是 IF 类-[谓词]->类 THEN [新谓词]
# 的产生式求值（SWRL 常用子集），类匹配带子类闭包。

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
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


# 元层级类型黑名单：owlrl/RDFS 闭包会把万物都推成这些类型的实例
# （一切 URI ⊑ rdfs:Resource、owl:Class ⊑ rdfs:Class 等）——技术上必然、
# 业务上零信息量，且曾把推理列表刷成 297 条噪音（2026-11-04 项目 246 实测）。
_META_TYPE_OBJECTS: frozenset[str] = frozenset(str(u) for u in (
    RDFS.Resource, RDFS.Class, RDFS.Datatype, RDF.Property,
    OWL.Class, OWL.Thing, OWL.Nothing,
    OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
    OWL.FunctionalProperty, OWL.InverseFunctionalProperty,
    OWL.SymmetricProperty, OWL.TransitiveProperty, OWL.NamedIndividual,
))

# 结构/公理谓词：任一端落在元词汇命名空间即为公理级噪音（用户词汇间的
# 子类/子属性/互斥推导才有业务价值，须保留）
_STRUCTURAL_PREDS: frozenset[str] = frozenset(str(u) for u in (
    RDFS.subClassOf, RDFS.subPropertyOf, RDFS.domain, RDFS.range,
    OWL.equivalentClass, OWL.equivalentProperty, OWL.inverseOf, OWL.disjointWith,
))

_META_NS = (str(RDF), str(RDFS), str(OWL), "http://www.w3.org/2001/XMLSchema#")


def _filter_noise(s: URIRef, p: URIRef, o: Any) -> bool:
    """过滤 owlrl 闭包噪音：sameAs 自反、内置词汇主语上的公理三元组、
    字面量数据类型标注（如 0.98 → xsd:double）、一切自指三元组、
    rdf:type 落到元层级类型（万物皆 Resource/Class/Property/Datatype）、
    结构谓词任一端是元词汇。"""
    if s == o:
        return True
    if isinstance(s, Literal):
        return True
    if any(str(s).startswith(ns) for ns in _META_NS):
        return True  # RDF/RDFS/OWL/XSD 词汇自身的公理三元组（含 xsd 数据类型）
    if p == RDF.type:
        if isinstance(o, URIRef):
            if str(o) in _META_TYPE_OBJECTS:
                return True
            if str(o).startswith("http://www.w3.org/2001/XMLSchema#"):
                return True
    if isinstance(o, URIRef) and str(o) == str(OWL.Thing):
        return True
    if str(s).startswith(str(RDF)) or str(s).startswith(str(RDFS)) or str(s).startswith(str(OWL)):
        return True
    # 结构谓词的宾语侧元词汇（如「系统 子类→ rdfs:Resource」）：主语是用户
    # 词汇但推导对象是语义基础设施，对业务无意义
    if str(p) in _STRUCTURAL_PREDS and isinstance(o, URIRef) \
            and any(str(o).startswith(ns) for ns in _META_NS):
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
    # instance_of 边的宾语是类节点（不在 ents 里）：单独取 id→label，翻译成 rdf:type。
    # 不翻译的话双类型实例只剩 class_label 一个类型，disjoint 检测永远无从触发。
    class_label_by_id = {e.id: e.label for e in db.query(Entity).filter(
        Entity.project_id == project.id, Entity.is_class_node.is_(True)).all()}
    pred_uri_by_label: dict[str, URIRef] = {}
    for r in rels:
        s_ent, o_ent = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if r.predicate in ("instance_of", "rdf:type", "type"):
            cls_uri = class_uri_by_label.get(class_label_by_id.get(r.object_id) or "")
            if cls_uri is not None and s_ent is not None and s_ent.uri:
                g.add((URIRef(s_ent.uri), RDF.type, cls_uri))
            continue
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

    # ── schema 权威键声明的公理 → OWL 词汇（TTL 只带画布边级 axioms；权威键里的
    #    特征/互逆/互斥/等价在此补齐，否则推理闭包对 schema 声明失明——函数性多值
    #    矛盾会静默扩散成 sameAs 错误合并而不被任何闸看见）──
    schema = graph_data.get("schema") if isinstance(graph_data.get("schema"), dict) else None
    if schema:
        for op in (schema.get("object_properties") or []):
            if not (isinstance(op, dict) and op.get("label")):
                continue
            p_uri = EX[_safe_prop_id(str(op["label"]))]
            if op.get("functional"):
                g.add((p_uri, RDF.type, OWL.FunctionalProperty))
            if op.get("inverse_functional"):
                g.add((p_uri, RDF.type, OWL.InverseFunctionalProperty))
            if op.get("symmetric"):
                g.add((p_uri, RDF.type, OWL.SymmetricProperty))
            if op.get("transitive"):
                g.add((p_uri, RDF.type, OWL.TransitiveProperty))
            inv = str(op.get("inverse_of") or "")
            if inv and inv != str(op["label"]):
                g.add((p_uri, OWL.inverseOf, EX[_safe_prop_id(inv)]))
        class_uris = _class_uris_by_label(nodes)
        for c in (schema.get("classes") or []):
            if not (isinstance(c, dict) and c.get("label")):
                continue
            c_uri = class_uris.get(str(c["label"]))
            if c_uri is None:
                continue
            for peer in (c.get("disjoint_with") or []):
                peer_uri = class_uris.get(str(peer))
                if peer_uri is not None and peer_uri != c_uri:
                    g.add((c_uri, OWL.disjointWith, peer_uri))
            for peer in (c.get("equivalent_to") or []):
                peer_uri = class_uris.get(str(peer))
                if peer_uri is not None and peer_uri != c_uri:
                    g.add((c_uri, OWL.equivalentClass, peer_uri))

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
               rule_ids: Optional[list[int]] = None,
               entailment_triples: Optional[list[dict]] = None) -> list[dict]:
    """规则引擎（R3）：IF 主语类-[谓词]->宾语类 THEN 推断(主语, 结论谓词, 宾语)。

    类匹配带子类闭包；已存在同谓词事实关系的不重复推导。S9 链式推理：求值对象
    除实例级事实外还包含蕴含三元组（实例级）——语义闭包先行的产出可以继续触发
    规则；类级蕴含（cls→cls）无实体对应，天然跳过。返回推理三元组 dict 列表。
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
    ent_by_uri = {e.uri: e for e in ents if e.uri}
    rels = db.query(Relation).filter(
        Relation.project_id == project.id,
        Relation.is_class_edge.is_(False),
        Relation.status.notin_(("rejected",)),
    ).all()
    # 事实谓词去重集：规则推导不重复已存在的事实
    fact_keys = {(r.subject_id, r.predicate, r.object_id) for r in rels}

    closure_cache: dict[str, set[str]] = {}
    produced: set[tuple[int, int, str, int]] = set()  # (rule_id, s, then_p, o) 同规则去重
    results: list[dict] = []

    def _emit(rule: OntologyRule, s_ent: Entity, o_ent: Entity) -> None:
        s_cls = s_ent.class_label or ""
        o_cls = o_ent.class_label or ""
        s_closure = closure_cache.setdefault(s_cls, _class_closure(s_cls, parent_map))
        o_closure = closure_cache.setdefault(o_cls, _class_closure(o_cls, parent_map))
        if rule.if_subject_class not in s_closure or rule.if_object_class not in o_closure:
            return
        key = (rule.id, s_ent.id, rule.then_predicate, o_ent.id)
        if key in produced or (s_ent.id, rule.then_predicate, o_ent.id) in fact_keys:
            return
        produced.add(key)
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

    for r in rels:
        s_ent, o_ent = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if not s_ent or not o_ent:
            continue
        for rule in rules:
            if r.predicate == rule.if_predicate:
                _emit(rule, s_ent, o_ent)

    # 链式：蕴含三元组（实例级）继续触发规则
    for t in (entailment_triples or []):
        s_ent = ent_by_uri.get(t.get("subject_uri") or "")
        o_ent = ent_by_uri.get(t.get("object_uri") or "")
        if not s_ent or not o_ent:
            continue
        for rule in rules:
            if t.get("predicate_label") == rule.if_predicate:
                _emit(rule, s_ent, o_ent)
    return results


def _majority(seq: list[str]) -> str:
    seq = [x for x in seq if x]
    return Counter(seq).most_common(1)[0][0] if seq else ""


def mine_rule_suggestions(db: Session, project: Project,
                          min_support: int = 2, top_k: int = 10) -> list[dict]:
    """AI 规则挖掘（S9）：从实例事实的「物质化蕴含」中挖候选规则。

    若带谓词 p 的实例同时全部带谓词 q（支撑 ≥ min_support，置信度 100%），
    则建议规则 主语类-[p]->宾语类 ⇒ 新谓词 q——抽取只配对出现时，q 对 p 是隐性
    可推导知识。只出建议不落库，采纳走既有 createRule（人工门禁，与公理建议同哲学）。
    """
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

    # subject_id → {谓词: [(宾语实体, 宾语类)]}；主语 → 主语类
    by_subj: dict[int, dict[str, list[tuple[Entity, str]]]] = {}
    for r in rels:
        s_ent, o_ent = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if not s_ent or not o_ent or not r.predicate:
            continue
        by_subj.setdefault(r.subject_id, {}).setdefault(r.predicate, []).append((o_ent, o_ent.class_label or ""))

    # 谓词 → 拥有它的主语数（置信度分母：含只有 p 没有 q 的主语）
    p_subjects: dict[str, set[int]] = {}
    for sid, preds in by_subj.items():
        for p in preds:
            p_subjects.setdefault(p, set()).add(sid)

    stats: dict[tuple[str, str], dict] = {}
    for sid, preds in by_subj.items():
        for p, _p_objs in preds.items():
            for q, q_objs in preds.items():
                if p == q:
                    continue
                st = stats.setdefault((p, q), {"n_p": len(p_subjects[p]), "both": 0,
                                               "s_cls": [], "o_cls": []})
                st["both"] += 1
                st["s_cls"].append(ent_by_id[sid].class_label or "")
                st["o_cls"].extend(c for _, c in q_objs)

    out: list[dict] = []
    for (p, q), st in stats.items():
        if st["both"] < min_support or st["both"] < st["n_p"]:
            continue  # 置信度须 100%：所有带 p 的实例都带 q
        s_cls, o_cls = _majority(st["s_cls"]), _majority(st["o_cls"])
        if not s_cls or not o_cls:
            continue
        out.append({
            "name": f"{p}蕴含{q}",
            "if_subject_class": s_cls,
            "if_predicate": p,
            "if_object_class": o_cls,
            "then_predicate": q,
            "support": st["both"],
            "confidence": round(st["both"] / st["n_p"], 2),
        })
    out.sort(key=lambda x: (-x["support"], x["name"]))
    return out[:top_k]


def llm_rule_suggestions(db: Session, project: Project,
                         llm: Optional[Any] = None,
                         max_rules: int = 10,
                         doc_rules: Optional[list[dict]] = None) -> tuple[list[dict], dict]:
    """LLM 规则抽取通道（S9 追加，规则版 O4 双通道）：素材组装 → call_llm → 解析+存在性门禁。

    IF 侧（主语类/谓词/宾语类）必须逐字命中画布 schema / 实例关系谓词白名单；
    THEN 侧为推导新关系。与既有 OntologyRule 同键的候选弃单。llm 注入供测试；
    无抽取模型配置或调用失败时优雅跳过（stats.skipped 说明原因，不抛异常）。
    doc_rules：语义采集表的文档级业务规则行（schema.business_rules，带原文证据）——
    把「文档明确写了的规则」作为先验素材喂给候选生成，模型不再只从结构反推。
    """
    from app.adapters.axiom_llm import build_materials
    from app.adapters.rule_llm import RULE_OUTPUT_JSON_SCHEMA, build_rule_prompt, parse_rule_suggestions

    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    schema = graph_data.get("schema") if isinstance(graph_data.get("schema"), dict) else None

    ents = db.query(Entity).filter(
        Entity.project_id == project.id,
        Entity.is_class_node.is_(False),
        Entity.status.notin_(("rejected", "merged")),
    ).all()
    index = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == project.id).all()}
    rel_dicts = [{"subject_id": r.subject_id, "predicate": r.predicate,
                  "object_id": r.object_id,
                  "subject_label": (index.get(r.subject_id).label if index.get(r.subject_id) else "") or "",
                  "object_label": (index.get(r.object_id).label if index.get(r.object_id) else "") or ""}
                 for r in db.query(Relation).filter(
                     Relation.project_id == project.id,
                     Relation.is_class_edge.is_(False),
                     Relation.status.notin_(("rejected",))).all()]

    if llm is None:
        llm = _build_rule_llm(db, project.id)
    if llm is None:
        return [], {"skipped": "llm_unavailable"}

    from app.adapters.conflicts import _as_ent

    materials = build_materials(schema, [_as_ent(e) for e in ents], rel_dicts)
    if doc_rules:
        # 素材预算控制：文档级规则最多带 30 条（每条已 ≤300 字符）
        # 上限 15 条：素材越大模型生成越慢（万字符 prompt 实测 10 分钟级），且
        # 规则候选本就 ≤10 条/轮，先验素材按代表性截断即可
        materials["doc_rules"] = [
            {"statement": str(r.get("statement") or "")[:300],
             "evidence": str(r.get("evidence") or "")[:200]}
            for r in doc_rules[:15] if isinstance(r, dict) and r.get("statement")
        ]
    system_prompt, user_prompt = build_rule_prompt(materials)
    from app.services.axiom_suggestion_service import AXIOM_LLM_TIMEOUT
    try:
        resp = llm.call_llm(system_prompt=system_prompt, user_prompt=user_prompt,
                            json_schema=RULE_OUTPUT_JSON_SCHEMA,
                            timeout=AXIOM_LLM_TIMEOUT, max_retries=2)
    except Exception as e:  # noqa: BLE001 - LLM 失败不阻断统计通道
        return [], {"skipped": f"llm_call_failed (timeout={AXIOM_LLM_TIMEOUT}s): {e}"}

    if isinstance(resp, dict) and "rules" in resp:
        content: Any = resp
    else:
        content = (resp or {}).get("content") or ""

    # 已有规则同键去重（采纳过的不再荐）
    existing = db.query(OntologyRule).filter(OntologyRule.project_id == project.id).all()
    from app.adapters.resolution import normalize_label
    existing_keys = {(normalize_label(r.if_subject_class), normalize_label(r.if_predicate),
                      normalize_label(r.if_object_class), normalize_label(r.then_predicate))
                     for r in existing}
    candidates, rejected = parse_rule_suggestions(
        content, schema,
        relation_predicates=[d["predicate"] for d in rel_dicts],
        existing_keys=existing_keys)
    stats = {"parsed": len(candidates), "parse_rejected": len(rejected),
             "rejected_reasons": rejected[:5]}
    return candidates, stats


def _build_rule_llm(db: Session, project_id: int):
    """按项目抽取模型配置构造 LLMClient（与公理通道同口径）；未配置返回 None。"""
    from app.services.axiom_suggestion_service import _build_llm
    return _build_llm(db, project_id)


def merge_rule_suggestions(statistical: list[dict], llm: list[dict]) -> list[dict]:
    """双通道合并：同键（IF+THEN 归一）→ source=both，llm_reason 并入统计候选。"""
    from app.adapters.resolution import normalize_label

    def _key(c: dict) -> tuple:
        return (normalize_label(c["if_subject_class"]), normalize_label(c["if_predicate"]),
                normalize_label(c["if_object_class"]), normalize_label(c["then_predicate"]))

    stat_by_key = {_key(c): c for c in statistical}
    merged = list(statistical)
    for c in llm:
        base = stat_by_key.get(_key(c))
        if base is not None:
            base["source"] = "both"
            base["llm_reason"] = c.get("llm_reason")
        else:
            merged.append(c)
    return merged


def _axiom_violation_messages(g: Graph, facts: set, label_by_uri: dict[str, str]) -> list[str]:
    """已声明函数性/反函数性公理 vs ABox 事实的矛盾（零 LLM，确定性扫描）。

    owlrl 对函数性多值不报错——按 prp-fp 把多值客体重建成同一实体（sameAs），
    矛盾被静默"解决"成错误合并（2026-11-04 项目 246：下游声明函数性 + DCBS
    三个下游 ⇒ ABCS/PRSC/URSC 全被推成同一实体）。这里把矛盾显式亮出来。
    """
    msgs: list[str] = []
    for p_type, name, side in ((OWL.FunctionalProperty, "函数性", "取值"),
                               (OWL.InverseFunctionalProperty, "反函数性", "主体")):
        for p in g.subjects(RDF.type, p_type):
            if not isinstance(p, URIRef) or str(p).startswith((str(RDF), str(RDFS), str(OWL))):
                continue  # 语义基础设施自身的函数性不是业务公理
            by_node: dict = {}
            for s, ep, o in facts:
                if ep == p and s != p:
                    by_node.setdefault(s, set()).add(o)
            multi = {k: v for k, v in by_node.items() if len(v) > 1}
            if not multi:
                continue
            p_label = label_by_uri.get(str(p)) or str(p).split("#")[-1].split("/")[-1]
            for s, objs in sorted(multi.items(), key=lambda kv: str(kv[0]))[:3]:
                s_label = label_by_uri.get(str(s), str(s).split("#")[-1].split("/")[-1])
                vals = "、".join(
                    str(o) if isinstance(o, Literal)
                    else label_by_uri.get(str(o), str(o).split("#")[-1].split("/")[-1])
                    for o in sorted(objs, key=str)[:4])
                msgs.append(
                    f"「{p_label}」声明{name}，但「{s_label}」有 {len(objs)} 个{side}"
                    f"（{vals}）——闭包已把它们强行合并为同一实体，请修正数据或撤销该公理")
    return msgs


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
    # 手动展开（不用 DeductiveClosure）：要拿到 error_messages——disjoint 共同实例等
    # 不一致走 add_error 而非产出三元组，吞掉信号 = 矛盾静默（L13 事故场景）
    sem = closure_cls(fact_graph, axioms=True, daxioms=True, rdfs=True)
    sem.closure()
    sem.flush_stored_triples()
    inconsistency = [str(m) for m in (getattr(sem, "error_messages", None) or [])]
    # 已声明公理 vs 数据的矛盾（owlrl 静默推 sameAs 场景）放在最前——
    # 比 owl 报错更需要人先看见（错误的公理会污染实体归并与 QA）
    inconsistency[:0] = _axiom_violation_messages(fact_graph, facts, label_by_uri)
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
            "predicate_label": _BUILTIN_PRED_LABELS.get(str(p))
                               or label_by_uri.get(str(p))
                               or str(p).split("#")[-1].split("/")[-1],
            "object_uri": str(o),
            "object_label": o_label,
        })

    # ── 不一致信号落结果（逻辑矛盾对使用者可见，而非仅内部日志）──
    for msg in inconsistency[:20]:
        entailment.append({
            "source": profile, "rule_name": None,
            "subject_uri": "urn:onto:consistency", "subject_label": "本体一致性",
            "predicate_uri": "urn:onto:inconsistency",
            "predicate_label": "逻辑矛盾（不一致）",
            "object_uri": "urn:onto:consistency",
            "object_label": msg[:200],
        })

    # ── 规则引擎（S9 链式：蕴含产出的实例级三元组也参与规则求值）──
    graph_data = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []
    rule_results = _run_rules(db, project, nodes, edges, rule_ids,
                              entailment_triples=entailment)

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
        "inferred_entailment": len(entailment) - len(inconsistency[:20]),
        "inferred_rule": len(rule_results),
        "inconsistency_count": len(inconsistency),
        "inferred_total": len(entailment) + len(rule_results),
        "persisted": len(rows),
        "truncated": (len(entailment) + len(rule_results)) > MAX_PERSIST_INFERRED,
        "by_rule": by_rule,
    }
    logger.info(f"[reasoning] 项目 {project.id} 推理完成：{summary}")
    return summary


def get_latest_results(db: Session, project_id: int, limit: int = 200,
                       offset: int = 0, source: Optional[str] = None) -> dict:
    """取最新批次的推理结果行（source 过滤可选，支持逗号分隔多值；offset 分页）。"""
    latest = (db.query(ReasoningResult)
              .filter(ReasoningResult.project_id == project_id)
              .order_by(ReasoningResult.id.desc()).first())
    if not latest:
        return {"batch_id": None, "items": [], "total": 0}
    q = db.query(ReasoningResult).filter(
        ReasoningResult.project_id == project_id,
        ReasoningResult.batch_id == latest.batch_id)
    if source:
        sources = [s for s in source.split(",") if s]
        if len(sources) == 1:
            q = q.filter(ReasoningResult.source == sources[0])
        else:
            q = q.filter(ReasoningResult.source.in_(sources))
    total = q.count()
    items = (q.order_by(ReasoningResult.source, ReasoningResult.id)
              .offset(offset).limit(limit).all())
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
