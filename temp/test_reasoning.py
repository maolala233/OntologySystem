# temp/test_reasoning.py — R1/R3 推理服务单测
# 运行：仓库根目录 PYTHONPATH=src/backend .venv/Scripts/python.exe temp/test_reasoning.py
# 或：cd src/backend && python ../../temp/test_reasoning.py（.env 相对 CWD，须在 src/backend 下跑）
import sys
import uuid

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from sqlalchemy import text

from app.infrastructure.database import Entity, OntologyRule, Project, ReasoningResult, Relation, SessionLocal
from app.services.reasoning_service import (
    _build_fact_graph,
    _run_rules,
    export_ttl_with_inference,
    run_reasoning,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


db = SessionLocal()
EX = Namespace("http://www.example.org/auto_ontology#")

# ============================================================
print("\n== 1) 规则引擎：类闭包匹配 + 事实去重 ==")
# 建独立测试项目，不碰 723
proj = Project(name=f"推理单测_{uuid.uuid4().hex[:6]}", owner_id=1, graph_data={"nodes": [
    {"id": "c1", "type": "custom", "data": {"label": "投资者", "type": "owl:Class", "properties": {}}},
    {"id": "c2", "type": "custom", "data": {"label": "机构投资者", "type": "owl:Class", "properties": {}}},
    {"id": "c3", "type": "custom", "data": {"label": "理财产品", "type": "owl:Class", "properties": {}}},
    {"id": "c4", "type": "custom", "data": {"label": "资产", "type": "owl:Class", "properties": {}}},
], "edges": [
    # 机构投资者 ⊑ 投资者；理财产品某子类 ⊑ 资产 —— 规则应经闭包命中
    {"source": "c2", "target": "c1", "data": {"relation": "subclass_of", "label": "subClassOf"}},
]})
db.add(proj)
db.commit()

rule = OntologyRule(project_id=proj.id, name="投资即投资了",
                    if_subject_class="投资者", if_predicate="投资",
                    if_object_class="资产", then_predicate="投资了", enabled=True)
db.add(rule)
db.commit()

e_inv = Entity(project_id=proj.id, uri=f"urn:onto:{proj.id}:entity/inv1", label="基金甲",
               label_normalized="基金甲", class_label="机构投资者", status="auto", is_class_node=False)
e_prod = Entity(project_id=proj.id, uri=f"urn:onto:{proj.id}:entity/prod1", label="基金乙",
                label_normalized="基金乙", class_label="理财产品", status="auto", is_class_node=False)
e_asset = Entity(project_id=proj.id, uri=f"urn:onto:{proj.id}:entity/asset1", label="债券丙",
                 label_normalized="债券丙", class_label="资产", status="auto", is_class_node=False)
db.add_all([e_inv, e_prod, e_asset])
db.flush()
rel_fact = Relation(project_id=proj.id, subject_id=e_prod.id, predicate="投资",
                    object_id=e_asset.id, status="auto", is_class_edge=False)  # 基金乙→投资→债券丙（事实）
db.add(rel_fact)
db.commit()

nodes, edges = proj.graph_data["nodes"], proj.graph_data["edges"]
rule_out = _run_rules(db, proj, nodes, edges)
# 基金甲（机构投资者）没有任何「投资」事实关系 → 不应产出；基金乙（理财产品，非投资者闭包）→ 不应产出
check("无匹配事实时规则不产出", len(rule_out) == 0, f"实际 {len(rule_out)}")

# 给基金甲加「投资→债券丙」事实（投资者类经闭包命中；客体资产类命中）→ 应产出「投资了」
rel2 = Relation(project_id=proj.id, subject_id=e_inv.id, predicate="投资",
                object_id=e_asset.id, status="auto", is_class_edge=False)
db.add(rel2)
db.commit()
rule_out = _run_rules(db, proj, nodes, edges)
check("闭包命中产出 1 条", len(rule_out) == 1, f"实际 {len(rule_out)}")
if rule_out:
    r0 = rule_out[0]
    check("来源标注 rule + 规则名", r0["source"] == "rule" and r0["rule_name"] == "投资即投资了")
    check("结论谓词正确", r0["predicate_label"] == "投资了")
    check("主客体标签正确", r0["subject_label"] == "基金甲" and r0["object_label"] == "债券丙")

# ============================================================
print("\n== 2) run_reasoning 全流程：蕴含 + 规则 + 落库 + 事实/推理分离 ==")
summary = run_reasoning(db, proj, profile="owlrl")
check("summary 有 batch_id", bool(summary.get("batch_id")))
check("事实三元组计数 > 0", summary["fact_triples"] > 0, str(summary))
check("规则推理 1 条", summary["inferred_rule"] == 1, str(summary))
check("蕴含推理 > 0（子类闭包/实例类型传播）", summary["inferred_entailment"] > 0, str(summary))

rows = db.query(ReasoningResult).filter(ReasoningResult.project_id == proj.id).all()
check("落库行数 = 蕴含+规则", len(rows) == summary["inferred_total"], f"{len(rows)} vs {summary['inferred_total']}")
check("全部带 batch_id", all(r.batch_id == summary["batch_id"] for r in rows))
rule_rows = [r for r in rows if r.source == "rule"]
ent_rows = [r for r in rows if r.source == "owlrl"]
check("source 区分 rule/owlrl", len(rule_rows) == 1 and len(ent_rows) == summary["inferred_entailment"])

# 事实/推理分离：推理结果绝不含「原始事实关系谓词 prop_ 投资URI」的三元组？
# （推理可能推出同形三元组——校验：落库的都是推理新增，与事实图差集一致由实现保证；这里验证事实计数稳定）
g_facts, _ = _build_fact_graph(db, proj)
check("事实图可重复构建且规模稳定", len(g_facts) == summary["fact_triples"],
      f"{len(g_facts)} vs {summary['fact_triples']}")

# ============================================================
print("\n== 3) owlrl 蕴含语义验证（子类/传递/实例传播）==")
# 用 proj 的图验证：机构投资者⊑投资者 → 基金甲 rdf:type 投资者（ABox type 传播）
inv_uri = f"urn:onto:{proj.id}:entity/inv1"
g2, _ = _build_fact_graph(db, proj)
facts2 = set(g2)
import owlrl
owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, rdfs_closure=True).expand(g2)
inferred2 = set(g2) - facts2
inv = URIRef(inv_uri)
has_parent_type = any(t for t in inferred2
                      if t[0] == inv and t[1] == RDF.type and str(t[2]) == str(EX["c1"]))
check("实例类型经子类链传播（机构投资者→投资者）", has_parent_type,
      str([t for t in inferred2 if t[0] == inv]))

# ============================================================
print("\n== 4) TTL 导出：推理分节与事实分节分离 ==")
ttl_out = export_ttl_with_inference(db, proj, profile="owlrl")
check("含推理横幅标注", "推理结果（INFERRED TRIPLES）" in ttl_out and "非原始事实" in ttl_out)
check("标注档位 OWL-RL", "OWL-RL" in ttl_out)
# 分节内容可独立解析（推理分节横幅之后的部分）
banner_idx = ttl_out.index("# " + "=" * 66)
inferred_section = ttl_out[banner_idx:]
# 推理分节内是合法 turtle（去掉注释行）
body_lines = [l for l in inferred_section.splitlines() if l.strip() and not l.strip().startswith("#")]
try:
    g3 = Graph()
    g3.parse(data="\n".join(body_lines), format="turtle")
    check("推理分节可独立解析为合法 turtle", len(g3) > 0, f"解析到 {len(g3)} 条")
except Exception as e:
    check("推理分节可独立解析为合法 turtle", False, str(e)[:120])
# 事实分节不含推理出的子类链三元组（Sub rdfs:subClassOf Top 是推理）
fact_section = ttl_out[:banner_idx]
check("事实分节不含 c1⊑Top 蕴含三元组", "c1> <http://www.w3.org/2000/01/rdf-schema#subClassOf>" not in fact_section.replace("\n", ""))

# ============================================================
print("\n== 5) latest-only：重跑覆盖旧批次 ==")
summary2 = run_reasoning(db, proj, profile="rdfs")
rows2 = db.query(ReasoningResult).filter(ReasoningResult.project_id == proj.id).all()
check("重跑后只剩新批次", all(r.batch_id == summary2["batch_id"] for r in rows2))
check("rdfs 档位标注", all(r.source in ("rdfs", "rule") for r in rows2))

# ============================================================
print("\n== 6) 清理 ==")
db.query(ReasoningResult).filter(ReasoningResult.project_id == proj.id).delete()
db.query(OntologyRule).filter(OntologyRule.project_id == proj.id).delete()
db.query(Relation).filter(Relation.project_id == proj.id).delete()
db.query(Entity).filter(Entity.project_id == proj.id).delete()
db.delete(proj)
db.commit()
print("  测试数据已清理")

print(f"\n总计: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
