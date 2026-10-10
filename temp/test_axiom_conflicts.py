# temp/test_axiom_conflicts.py - M2 公理校验单测（互斥/函数性/基数 + 子类闭包 + 集成）
# 运行: .venv/Scripts/python temp/test_axiom_conflicts.py
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.adapters.conflicts import ConflictType, ConflictRecord, detect_conflicts, detect_axiom_conflicts

_failures = []


def check(name: str, cond: bool):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        _failures.append(name)


# 与 M1 画布/同款 schema 形状（build_schema_from_graph_data / extract_schema_from_ttl 产物）
SCHEMA = {
    "classes": [
        {"id": "n1", "label": "熔断事件", "parent_classes": [], "disjoint_with": ["恢复决策", "恢复条件"]},
        {"id": "n2", "label": "恢复决策", "parent_classes": [], "disjoint_with": []},
        {"id": "n3", "label": "子类事件", "parent_classes": ["n1"], "disjoint_with": []},  # 熔断事件的子类
        {"id": "n4", "label": "基金", "parent_classes": [], "disjoint_with": []},
    ],
    "object_properties": [
        {"label": "触发熔断", "domain": "基金", "range": "熔断事件",
         "functional": True, "inverse_of": "被基金触发",
         "cardinality_restrictions": [{"class": "基金", "min": 0, "max": 1}]},
        {"label": "持有份额", "domain": "基金", "range": "资产",
         "cardinality_restrictions": [{"class": "基金", "min": 1, "max": 2}]},
    ],
}

ENTS = [
    {"id": 1, "uri": "u/e1", "label": "事件A", "class_label": "熔断事件", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 2, "uri": "u/e2", "label": "决策B", "class_label": "恢复决策", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 3, "uri": "u/e3", "label": "事件C", "class_label": "子类事件", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 4, "uri": "u/e4", "label": "决策D", "class_label": "恢复决策", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 5, "uri": "u/f1", "label": "基金甲", "class_label": "基金", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 6, "uri": "u/f2", "label": "基金乙", "class_label": "基金", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 7, "uri": "u/f3", "label": "基金丙", "class_label": "基金", "props": {}, "confidence": 0.9, "status": "auto"},
]

RELS = [
    # 违例：事件A(熔断事件) 与 决策B(恢复决策) —— 但互斥针对的是"同一实例"，
    # 这里真正要打的是"单实例同时属于互斥两类"，见 case1 直接构造。
    {"subject_id": 5, "predicate": "触发熔断", "object_id": 1, "subject_label": "基金甲", "object_label": "事件A"},
    {"subject_id": 5, "predicate": "触发熔断", "object_id": 2, "subject_label": "基金甲", "object_label": "决策B"},  # functional 多值
    {"subject_id": 6, "predicate": "触发熔断", "object_id": 1, "subject_label": "基金乙", "object_label": "事件A"},  # 正常
    {"subject_id": 7, "predicate": "持有份额", "object_id": 1, "subject_label": "基金丙", "object_label": "资产X"},
    {"subject_id": 7, "predicate": "持有份额", "object_id": 2, "subject_label": "基金丙", "object_label": "资产Y"},
    {"subject_id": 7, "predicate": "持有份额", "object_id": 3, "subject_label": "基金丙", "object_label": "资产Z"},  # max=2 超限
    {"subject_id": 6, "predicate": "持有份额", "object_id": 3, "subject_label": "基金乙", "object_label": "资产W"},  # min=1 满足
]


def _only(records, prop):
    return [r for r in records if r.property_name == prop]


print("== 1) disjointWith 违例 ==")
# 实体同时属于互斥两类（消解归一残留：同一实体 class_label 无法同时两个，
# 因此真实场景是"类型闭包同时命中"——构造一个类同时是两互斥类的子类）
SCHEMA2 = {
    "classes": [
        {"id": "c1", "label": "熔断事件", "parent_classes": [], "disjoint_with": ["恢复决策"]},
        {"id": "c2", "label": "恢复决策", "parent_classes": [], "disjoint_with": []},
        {"id": "c3", "label": "歧义类", "parent_classes": ["c1", "c2"], "disjoint_with": []},  # 同时继承互斥两类
        {"id": "c4", "label": "正常类", "parent_classes": ["c1"], "disjoint_with": []},
    ],
    "object_properties": [],
}
ents2 = [
    {"id": 1, "uri": "u/x", "label": "歧义实体", "class_label": "歧义类", "props": {}, "confidence": 0.9, "status": "auto"},
    {"id": 2, "uri": "u/y", "label": "正常实体", "class_label": "正常类", "props": {}, "confidence": 0.9, "status": "auto"},
]
recs2 = detect_axiom_conflicts(ents2, [], {1: ents2[0], 2: ents2[1]}, SCHEMA2)
disj = _only(recs2, "owl:disjointWith")
check("歧义类(两互斥类之子) → 1 条互斥违例", len(disj) == 1)
if disj:
    check("违例值 = [恢复决策, 熔断事件]", sorted(disj[0].conflicting_values) == ["恢复决策", "熔断事件"])
    check("severity=high", disj[0].severity == "high")
    check("类型=AXIOM", disj[0].conflict_type == ConflictType.AXIOM)
    check("guide 含操作指引", len(disj[0].guide.get("steps", [])) >= 2)

print("== 2) disjointWith 无误报 ==")
check("正常类(仅继承熔断事件) 不报", len(disj) == 1)  # 只有一条（歧义实体）

print("== 3) 函数性违例 ==")
recs = detect_axiom_conflicts(ENTS, RELS, {e["id"]: e for e in ENTS}, SCHEMA)
func = _only(recs, "触发熔断")
check("基金甲 触发熔断 2 客体 → 函数性违例", any(
    r.entity_uri == "u/f1" and sorted(r.conflicting_values) == ["事件A", "决策B"] for r in func))
check("基金乙 单客体不报函数性", not any(r.entity_uri == "u/f2" and "函数" for r in func))

print("== 4) 基数超限/不足 ==")
over = [r for r in _only(recs, "持有份额") if r.entity_uri == "u/f3" and len(r.conflicting_values) == 3]
check("基金丙 持有份额 3 客体 > max 2 → 超限", len(over) == 1 and over[0].severity == "medium")
check("基金乙 持有份额 1 客体 ≥ min 1 不报", not any(
    r.entity_uri == "u/f2" and r.property_name == "持有份额" for r in recs))

print("== 5) min 不足（min=2 仅 1 客体）==")
SCHEMA3 = {
    "classes": [{"id": "c1", "label": "基金", "parent_classes": [], "disjoint_with": []}],
    "object_properties": [
        {"label": "持有份额",
         "cardinality_restrictions": [{"class": "基金", "min": 2, "max": None}]},
    ],
}
recs3 = detect_axiom_conflicts(
    [{"id": 1, "uri": "u/g1", "label": "基金丁", "class_label": "基金", "props": {}, "confidence": 0.9, "status": "auto"}],
    [{"subject_id": 1, "predicate": "持有份额", "object_id": 9, "subject_label": "基金丁", "object_label": "资产Q"}],
    {1: {"id": 1, "uri": "u/g1", "label": "基金丁", "class_label": "基金", "props": {}, "confidence": 0.9, "status": "auto"}},
    SCHEMA3)
under = [r for r in recs3 if r.property_name == "持有份额"]
check("min=2 仅 1 客体 → 基数不足(low)", len(under) == 1 and under[0].severity == "low")

print("== 6) 限制仅作用于声明的类 ==")
recs4 = detect_axiom_conflicts(
    [{"id": 1, "uri": "u/h1", "label": "账户E", "class_label": "账户", "props": {}, "confidence": 0.9, "status": "auto"}],
    [{"subject_id": 1, "predicate": "触发熔断", "object_id": 5, "subject_label": "账户E", "object_label": "事件A"},
     {"subject_id": 1, "predicate": "触发熔断", "object_id": 6, "subject_label": "账户E", "object_label": "事件B"}],
    {1: {"id": 1, "uri": "u/h1", "label": "账户E", "class_label": "账户", "props": {}, "confidence": 0.9, "status": "auto"}},
    {"classes": [{"id": "c1", "label": "账户", "parent_classes": [], "disjoint_with": []}],
     "object_properties": [
         {"label": "触发熔断", "functional": True},  # 无基数限制
     ]})
# 函数性仍应报（与类无关）；基数限制因账户类未声明而不报
check("函数性对所有类生效", len(_only(recs4, "触发熔断")) == 1)

print("== 7) detect_conflicts 集成（types 含 AXIOM + schema 参数）==")
recs5 = detect_conflicts(ENTS, RELS, types=(ConflictType.AXIOM,), schema=SCHEMA)
check("集成入口产出 AXIOM 记录", all(r.conflict_type == ConflictType.AXIOM for r in recs5) and len(recs5) >= 3)
recs6 = detect_conflicts(ENTS, RELS, types=(ConflictType.AXIOM,), schema=None)
check("无 schema 不炸不误报", recs6 == [])
recs7 = detect_conflicts(ENTS, RELS, types=(ConflictType.VALUE, ConflictType.TYPE), schema=SCHEMA)
check("默认类型不含 AXIOM 时不校验公理", all(r.conflict_type != ConflictType.AXIOM for r in recs7))

print()
if _failures:
    print(f"结果：{len(_failures)} 项失败 → {_failures}")
    sys.exit(1)
print("结果：全部通过")
