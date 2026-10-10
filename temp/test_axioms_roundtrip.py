# -*- coding: utf-8 -*-
"""M1 公理链路往返测试：画布(nodes/edges) -> TTL -> schema/画布，公理无损。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "src/backend")

from app.api.ontology import (  # noqa: E402
    convert_ttl_to_graph_data,
    extract_schema_from_ttl,
    generate_ttl_from_graph_data,
)

# ── 构造带公理的画布 ──────────────────────────────────────────
NODES = [
    {"id": "n1", "position": {"x": 0, "y": 0},
     "data": {"label": "熔断事件", "type": "owl:Class", "properties": {},
              "axioms": {"disjoint_with": ["恢复决策", "恢复条件"]}}},
    {"id": "n2", "position": {"x": 0, "y": 0},
     "data": {"label": "恢复决策", "type": "owl:Class", "properties": {}}},
    {"id": "n3", "position": {"x": 0, "y": 0},
     "data": {"label": "恢复条件", "type": "owl:Class", "properties": {}}},
    {"id": "n4", "position": {"x": 0, "y": 0},
     "data": {"label": "基金", "type": "owl:Class", "properties": {}}},
]
EDGES = [
    # 触发熔断：函数性 + 基数 [0,1] + 互逆
    {"id": "e1", "source": "n4", "target": "n1", "label": "触发熔断", "type": "custom",
     "data": {"label": "触发熔断", "relation": "触发熔断", "prop_id": "p1",
              "axioms": {"functional": True, "inverse_of": "被基金触发"},
              "min_cardinality": 0, "max_cardinality": 1}},
    {"id": "e2", "source": "n1", "target": "n4", "label": "被基金触发", "type": "custom",
     "data": {"label": "被基金触发", "relation": "被基金触发", "prop_id": "p2"}},
    # subclassOf 边（回归确认原有链路不破）
    {"id": "e3", "source": "n2", "target": "n3", "label": "subClassOf", "type": "custom",
     "data": {"label": "subClassOf", "relation": "subclass_of"}},
]

fails = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


print("== 1) 画布 -> TTL 导出 ==")
ttl = generate_ttl_from_graph_data(NODES, EDGES)
check("owl:disjointWith 导出", "disjointWith" in ttl)
check("FunctionalProperty 导出", "FunctionalProperty" in ttl)
check("inverseOf 导出", "inverseOf" in ttl)
check("maxCardinality 导出", "maxCardinality" in ttl)
check("minCardinality 导出（min=0 也导出）", "minCardinality" in ttl)
check("subClassOf 仍在", "subClassOf" in ttl)

print("== 2) TTL -> schema 导入 ==")
schema = extract_schema_from_ttl(ttl)
cls_by_label = {c["label"]: c for c in schema["classes"]}
prop_by_label = {p["label"]: p for p in schema["object_properties"]}
check("熔断事件.disjoint_with == [恢复决策, 恢复条件]",
      sorted(cls_by_label.get("熔断事件", {}).get("disjoint_with", [])) == ["恢复决策", "恢复条件"])
check("触发熔断.functional", prop_by_label.get("触发熔断", {}).get("functional") is True)
check("触发熔断.inverse_of == 被基金触发",
      prop_by_label.get("触发熔断", {}).get("inverse_of") == "被基金触发")
restr = prop_by_label.get("触发熔断", {}).get("cardinality_restrictions") or []
check("触发熔断.基数限制 class=基金 [0,1]",
      any(r.get("class") == "基金" and r.get("min") == 0 and r.get("max") == 1 for r in restr))

print("== 3) TTL -> 画布回写 ==")
nodes2, edges2 = convert_ttl_to_graph_data(ttl)
nd = {n["data"]["label"]: n["data"] for n in nodes2}
ed = {(e["data"].get("label") or e.get("label")): e["data"] for e in edges2}
check("熔断事件节点.axioms 回写",
      sorted((nd.get("熔断事件", {}).get("axioms") or {}).get("disjoint_with", [])) == ["恢复决策", "恢复条件"])
e1 = ed.get("触发熔断", {})
check("触发熔断边.axioms.functional 回写", (e1.get("axioms") or {}).get("functional") is True)
check("触发熔断边.inverse_of 回写", (e1.get("axioms") or {}).get("inverse_of") == "被基金触发")
check("触发熔断边.max_cardinality 回写", e1.get("max_cardinality") == 1)
check("触发熔断边.min_cardinality 回写", e1.get("min_cardinality") == 0)

print("== 4) 画布 -> schema（build_schema_from_graph_data 带出公理）==")
from app.api.ontology import build_schema_from_graph_data  # noqa: E402

schema2 = build_schema_from_graph_data(NODES, EDGES)
c2 = {c["label"]: c for c in schema2["classes"]}
p2 = {p["label"]: p for p in schema2["object_properties"]}
check("schema2.disjoint_with 带出",
      sorted(c2.get("熔断事件", {}).get("disjoint_with", [])) == ["恢复决策", "恢复条件"])
check("schema2.functional 带出", p2.get("触发熔断", {}).get("functional") is True)
check("schema2.inverse_of 带出", p2.get("触发熔断", {}).get("inverse_of") == "被基金触发")
r2 = p2.get("触发熔断", {}).get("cardinality_restrictions") or []
check("schema2.基数限制带出", any(r.get("class") == "基金" and r.get("max") == 1 for r in r2))

print("== 5) 无公理旧项目回归 ==")
OLD_NODES = [{"id": "a", "position": {"x": 0, "y": 0}, "data": {"label": "类A", "type": "owl:Class", "properties": {}}},
             {"id": "b", "position": {"x": 0, "y": 0}, "data": {"label": "类B", "type": "owl:Class", "properties": {}}}]
OLD_EDGES = [{"id": "e", "source": "a", "target": "b", "label": "关联", "type": "custom",
              "data": {"label": "关联", "relation": "关联"}}]
ttl_old = generate_ttl_from_graph_data(OLD_NODES, OLD_EDGES)
sch_old = extract_schema_from_ttl(ttl_old)
check("旧项目导出/导入不报错", len(sch_old["classes"]) == 2)
check("旧项目 schema 无公理字段（或为空）",
      not any(c.get("disjoint_with") for c in sch_old["classes"]))

print()
if fails:
    print(f"结果：{len(fails)} 项失败 -> {fails}")
    sys.exit(1)
print("结果：全部通过")
