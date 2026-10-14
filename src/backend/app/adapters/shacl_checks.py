# app/adapters/shacl_checks.py - SHACL 验证层（O2-B6，LOGICAL 实装）
# 从画布公理（disjointWith/函数性/基数/domain/range）自动生成 SHACL shapes，
# 对 ABox 验证并产出 ConflictRecord(conflict_type=LOGICAL)（conflicts.py 分发）。
# 与 owlrl 分工：推理补事实（reasoning_service），SHACL 查违例（本模块）。
# 纯函数可测；pyshacl 懒加载（导入约 1-2s，不用 SHACL 时不付成本）。

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.adapters.conflicts import ConflictRecord, ConflictType, ResolutionStrategy
from app.adapters.resolution import normalize_label

# 数据图规模护栏：超过后跳过验证（记 warning 违例），防超大项目拖死检测任务
MAX_DATA_TRIPLES = 60000

_SEVERITY = {
    "functional": "high",
    "disjoint": "high",
    "cardinality-max": "medium",
    "cardinality-min": "low",
    "range": "medium",
    "domain": "medium",
}


def _cls_ref(label: str):
    from rdflib import URIRef

    return URIRef("urn:o:cls:" + quote(str(label), safe=""))


def _prop_ref(label: str):
    from rdflib import URIRef

    return URIRef("urn:o:prop:" + quote(str(label), safe=""))


def _inst_ref(entity_id: Any):
    from rdflib import URIRef

    return URIRef(f"urn:o:inst:{entity_id}")


def _class_ancestors(class_label: str, classes_by_label: dict, classes_by_id: dict) -> set[str]:
    """父类闭包（parent_classes 混存 id/label），与 conflicts._class_closure 同口径。"""
    seen: set[str] = set()
    stack = [class_label]
    while stack:
        cur = stack.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        info = classes_by_label.get(cur) or classes_by_id.get(cur)
        if info:
            if info.get("label"):
                stack.append(info["label"])
            stack.extend(info.get("parent_classes") or [])
    return seen


def build_data_graph(ents: list[dict], rels: list[dict], schema: dict,
                     entity_index: dict | None = None) -> tuple[Any, dict]:
    """ABox → rdflib Graph（实体 rdf:type 含父类闭包显式化，免去推理依赖）。

    返回 (graph, inst_by_id)；inst_by_id: {实体id: {uri,label,class_label}} 供违例回填。
    """
    from rdflib import Graph, Literal, RDF

    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    classes_by_label = {c.get("label"): c for c in classes if c.get("label")}
    classes_by_id = {c.get("id"): c for c in classes if c.get("id")}

    g = Graph()
    for c in classes:
        label = c.get("label")
        if not label:
            continue
        g.add((_cls_ref(label), RDF.type, _cls_ref("owl:Class")))
        for p in (c.get("parent_classes") or []):
            p_info = classes_by_label.get(p) or classes_by_id.get(p) or {}
            p_label = p_info.get("label") or p
            if p_label:
                g.add((_cls_ref(label), _prop_ref("subClassOf"), _cls_ref(p_label)))
        for peer in (c.get("disjoint_with") or []):
            if peer and peer != label:
                # 数据图携带 disjointWith（owl 事实），便于结果图可读；校验走 shapes
                g.add((_cls_ref(label), _prop_ref("disjointWith"), _cls_ref(peer)))

    inst_by_id: dict[Any, dict] = {}
    for e in ents:
        eid = e.get("id")
        cls = str(e.get("class_label") or "").strip()
        if eid is None or not cls:
            continue
        node = _inst_ref(eid)
        inst_by_id[eid] = {"uri": str(e.get("uri") or ""), "label": str(e.get("label") or ""),
                           "class_label": cls}
        for anc in _class_ancestors(cls, classes_by_label, classes_by_id):
            g.add((node, RDF.type, _cls_ref(anc)))
        for k, v in (e.get("props") or {}).items():
            if v in (None, ""):
                continue
            g.add((node, _prop_ref(str(k)), Literal(str(v))))

    index = entity_index or {}
    for r in rels:
        s_id, o_id = r.get("subject_id"), r.get("object_id")
        pred = str(r.get("predicate") or "").strip()
        if s_id is None or o_id is None or not pred:
            continue
        s_node = _inst_ref(s_id) if (s_id in inst_by_id or s_id in index) else None
        o_node = _inst_ref(o_id) if (o_id in inst_by_id or o_id in index) else None
        if s_node is None or o_node is None:
            continue
        # 索引侧实体（类节点/已合并）补类型，保证端点可校验
        for eid, node in ((s_id, s_node), (o_id, o_node)):
            if eid not in inst_by_id:
                info = index.get(eid) or {}
                cls = str(info.get("class_label") or "").strip()
                if cls:
                    inst_by_id[eid] = {"uri": str(info.get("uri") or ""),
                                       "label": str(info.get("label") or ""),
                                       "class_label": cls}
                    for anc in _class_ancestors(cls, classes_by_label, classes_by_id):
                        g.add((node, RDF.type, _cls_ref(anc)))
        g.add((s_node, _prop_ref(pred), o_node))
    return g, inst_by_id


def build_shacl_shapes(schema: dict) -> Any:
    """画布公理 → SHACL shapes 图。

    覆盖：函数性（maxCount 1）、基数 min/max（按声明类定向）、domain/range
    （targetSubjectsOf/targetObjectsOf + sh:class）、disjointWith（sh:not sh:class）。
    每条 shape 的 sh:message 以 [tag] 前缀编码违例类别，供结果解析回填。
    """
    from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

    SH = Namespace("http://www.w3.org/ns/shacl#")
    classes_by_label = {c.get("label"): c for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}
    shapes = Graph()
    shapes.bind("sh", SH)

    for op in (schema.get("object_properties") or []):
        if not isinstance(op, dict) or not op.get("label"):
            continue
        pred = _prop_ref(op["label"])
        rng = op.get("range")
        dom = op.get("domain")

        # range：客体必须是声明的类（含子类——数据图已显式展开父类类型）
        if rng and rng in classes_by_label:
            ns = BNode()
            shapes.add((ns, RDF.type, SH.NodeShape))
            shapes.add((ns, SH.targetObjectsOf, pred))
            shapes.add((ns, SH["class"], _cls_ref(rng)))
            shapes.add((ns, SH.message,
                        Literal(f"[range] 谓词「{op['label']}」的客体必须是类「{rng}」及其子类的实例")))

        # domain：主体必须是声明的类
        if dom and dom in classes_by_label:
            ns = BNode()
            shapes.add((ns, RDF.type, SH.NodeShape))
            shapes.add((ns, SH.targetSubjectsOf, pred))
            shapes.add((ns, SH["class"], _cls_ref(dom)))
            shapes.add((ns, SH.message,
                        Literal(f"[domain] 谓词「{op['label']}」的主体必须是类「{dom}」及其子类的实例")))

        # 函数性：同主体至多一个客体
        if op.get("functional"):
            ns = BNode()
            shapes.add((ns, RDF.type, SH.NodeShape))
            if dom and dom in classes_by_label:
                shapes.add((ns, SH.targetClass, _cls_ref(dom)))
            else:
                shapes.add((ns, SH.targetSubjectsOf, pred))
            ps = BNode()
            shapes.add((ns, SH.property, ps))
            shapes.add((ps, SH.path, pred))
            shapes.add((ps, SH.maxCount, Literal(1)))
            shapes.add((ps, SH.message,
                        Literal(f"[functional] 谓词「{op['label']}」声明为函数性，每个主体至多一个客体")))

        # 基数：按声明类定向的 min/max
        for restr in (op.get("cardinality_restrictions") or []):
            if not isinstance(restr, dict):
                continue
            rcls = restr.get("class")
            rmin, rmax = restr.get("min"), restr.get("max")
            if rcls not in classes_by_label:
                continue
            if rmax is not None:
                ns = BNode()
                shapes.add((ns, RDF.type, SH.NodeShape))
                shapes.add((ns, SH.targetClass, _cls_ref(rcls)))
                ps = BNode()
                shapes.add((ns, SH.property, ps))
                shapes.add((ps, SH.path, pred))
                shapes.add((ps, SH.maxCount, Literal(int(rmax))))
                shapes.add((ps, SH.message,
                            Literal(f"[cardinality-max] 类「{rcls}」上「{op['label']}」至多 {int(rmax)} 个客体")))
            if rmin is not None:
                ns = BNode()
                shapes.add((ns, RDF.type, SH.NodeShape))
                shapes.add((ns, SH.targetClass, _cls_ref(rcls)))
                ps = BNode()
                shapes.add((ns, SH.property, ps))
                shapes.add((ps, SH.path, pred))
                shapes.add((ps, SH.minCount, Literal(int(rmin))))
                shapes.add((ps, SH.message,
                            Literal(f"[cardinality-min] 类「{rcls}」上「{op['label']}」至少 {int(rmin)} 个客体")))

    # disjointWith：A 的实例不得同时是 B 的实例（父类闭包已在数据图展开）
    for c in (schema.get("classes") or []):
        if not isinstance(c, dict) or not c.get("label"):
            continue
        for peer in (c.get("disjoint_with") or []):
            if not peer or peer == c["label"] or peer not in classes_by_label:
                continue
            ns = BNode()
            shapes.add((ns, RDF.type, SH.NodeShape))
            shapes.add((ns, SH.targetClass, _cls_ref(c["label"])))
            not_node = BNode()
            shapes.add((ns, SH["not"], not_node))
            shapes.add((not_node, SH["class"], _cls_ref(peer)))
            shapes.add((ns, SH.message,
                        Literal(f"[disjoint] 类「{c['label']}」与「{peer}」互斥（disjointWith），"
                                f"同一实例不得同时属于两类")))
    return shapes


def detect_shacl_conflicts(ents: list, rels: list, schema: dict | None = None,
                           entity_index: dict | None = None) -> list[ConflictRecord]:
    """SHACL 验证（LOGICAL）：画布公理 → shapes，ABox → data graph，pyshacl 校验。

    ents/rels：_as_ent/_as_rel 形状（detect_conflicts 已归一）；
    schema：graph_data.schema；entity_index：{实体id: {uri,label,class_label}} 全量索引。
    pyshacl 不可用或数据图超护栏时返回空（不阻断其它冲突类型）。
    """
    schema = schema or {}
    if not (schema.get("classes") or schema.get("object_properties")):
        return []
    try:
        from pyshacl import validate
        from rdflib import Namespace, RDF
    except Exception:  # noqa: BLE001 —— pyshacl 缺失不阻断
        return []

    data, inst_by_id = build_data_graph(ents, rels, schema, entity_index)
    if len(data) == 0:
        return []
    if len(data) > MAX_DATA_TRIPLES:
        return [ConflictRecord(
            conflict_type=ConflictType.LOGICAL, severity="low",
            entity_uri="*", property_name="shacl",
            conflicting_values=[f"数据图 {len(data)} 三元组超过护栏 {MAX_DATA_TRIPLES}，"
                                "本轮跳过 SHACL 验证"],
            sources=[], recommended_action=ResolutionStrategy.MANUAL_REVIEW,
            guide={"steps": [], "generated_by": "pyshacl"})]

    shapes = build_shacl_shapes(schema)
    try:
        conforms, results_graph, _text = validate(
            data, shacl_graph=shapes, inference="none", advanced=True, inplace=False)
    except Exception:  # noqa: BLE001 —— 形状病态（如空 targetClass）不阻断
        return []
    if conforms:
        return []

    SH = Namespace("http://www.w3.org/ns/shacl#")
    # predicate label 反查：urn:o:prop:<quoted>
    from urllib.parse import unquote

    def _pred_label(node) -> str:
        if node is None:
            return ""
        s = str(node)
        if s.startswith("urn:o:prop:"):
            return unquote(s[len("urn:o:prop:"):])
        return ""

    records: list[ConflictRecord] = []
    seen: set[tuple] = set()
    for res in results_graph.subjects(RDF.type, SH.ValidationResult):
        focus = results_graph.value(res, SH.focusNode)
        msg = results_graph.value(res, SH.resultMessage)
        path = results_graph.value(res, SH.resultPath)
        message = str(msg) if msg else ""
        tag = message.split("]", 1)[0].lstrip("[").strip() if message.startswith("[") else "logical"
        eid = str(focus).rsplit(":", 1)[-1] if focus is not None else ""
        eid_key: Any = int(eid) if eid.isdigit() else eid
        info = inst_by_id.get(eid_key) or {}
        prop = _pred_label(path)
        key = (tag, eid_key, prop, message)
        if key in seen:
            continue
        seen.add(key)
        entity_uri = str(info.get("uri") or eid_key)
        records.append(ConflictRecord(
            conflict_type=ConflictType.LOGICAL,
            severity=_SEVERITY.get(tag, "medium"),
            entity_uri=entity_uri,
            property_name=prop or "shacl",
            conflicting_values=[message],
            sources=[{"uri": entity_uri, "label": info.get("label") or "",
                      "class": info.get("class_label") or ""}] if info else [],
            recommended_action=ResolutionStrategy.MANUAL_REVIEW,
            guide=_guide_for(tag, message),
        ))
    return records


def _guide_for(tag: str, message: str) -> dict:
    steps = {
        "functional": [
            "核对各客体关系的证据原句，保留正确客体",
            "若关系本就多值，在画布边公理中取消函数性",
        ],
        "disjoint": [
            "确认实体真实归属并修正类标注",
            "若互斥声明有误，在画布节点公理中调整 disjointWith",
        ],
        "cardinality-max": ["删除错误关系或将边基数上限调大"],
        "cardinality-min": ["核对原文补齐关系，或确认后调低最小基数"],
        "range": ["修正关系客体或调整谓词的 range 声明"],
        "domain": ["修正关系主体或调整谓词的 domain 声明"],
    }.get(tag, ["查看违例详情，修正实例数据或公理声明"])
    return {"steps": steps, "generated_by": "pyshacl(O2-B6)"}


def run_shacl_summary(ents: list, rels: list, schema: dict | None = None,
                      entity_index: dict | None = None) -> dict:
    """记分卡口径（F7）：SHACL 违例统计摘要。"""
    records = detect_shacl_conflicts(ents, rels, schema, entity_index)
    by_kind: dict[str, int] = {}
    for r in records:
        tag = (r.conflicting_values[0].split("]", 1)[0].lstrip("[").strip()
               if r.conflicting_values and str(r.conflicting_values[0]).startswith("[")
               else "logical")
        by_kind[tag] = by_kind.get(tag, 0) + 1
    return {"violations": len(records), "conforms": not records, "by_kind": by_kind}


__all__ = ["build_data_graph", "build_shacl_shapes", "detect_shacl_conflicts",
           "run_shacl_summary", "MAX_DATA_TRIPLES"]
