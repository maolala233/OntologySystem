# app/adapters/schema_checks.py - TBox 机检器（O1-B2，SchemaGate 的 TBox 版）
# 参考 Ontology Development 101（Noy & McGuinness）§4.2/§6 的可机检规则：
#   ①类层次环检测（A⊑B 且 B⊑A，含自环与长环）——error，阻断发布
#   ②is-a 引用校验（父类引用悬空 / 自己继承自己）——error，阻断发布
#   ③单直接子类（该层多余，建议合并或提升）与超宽父类（>12 直接子类建议分层）——warning
#   ④命名规范（兄弟类单复数混用、中英文命名风格混用）——warning
#   ⑤兄弟泛化层级（兄弟互为祖先，说明层次没对齐）——warning
# 纯函数：输入 graph_data["schema"] dict，零 DB 依赖，风格对齐 conflicts.py 的
# pydantic 记录式返回。error 只含机器可确证的硬伤；warning 全部是启发式建议。

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "SchemaCheckIssue",
    "SchemaCheckReport",
    "run_schema_checks",
    "RULE_CYCLE",
    "RULE_DANGLING_PARENT",
    "RULE_SELF_PARENT",
    "RULE_SINGLE_CHILD",
    "RULE_WIDE_PARENT",
    "RULE_NAMING_STYLE",
    "RULE_PLURAL_SIBLINGS",
    "RULE_SIBLING_LEVEL",
]

RULE_CYCLE = "class_hierarchy_cycle"
RULE_DANGLING_PARENT = "dangling_parent_ref"
RULE_SELF_PARENT = "self_parent_ref"
RULE_SINGLE_CHILD = "single_direct_child"
RULE_WIDE_PARENT = "wide_direct_children"
RULE_NAMING_STYLE = "sibling_naming_style"
RULE_PLURAL_SIBLINGS = "sibling_plural_inconsistency"
RULE_SIBLING_LEVEL = "sibling_generalization_level"

# 超过该数量的直接子类建议增设中间层（101 §4.2 的"15±"经验值取整）
MAX_DIRECT_CHILDREN = 12


class SchemaCheckIssue(BaseModel):
    rule: str
    severity: str  # error | warning
    message: str
    classes: list[str] = Field(default_factory=list)


class SchemaCheckReport(BaseModel):
    passed: bool = True          # 无 error 即通过（warning 不阻断）
    error_count: int = 0
    warning_count: int = 0
    issues: list[SchemaCheckIssue] = Field(default_factory=list)

    def add(self, rule: str, severity: str, message: str, classes: list[str]) -> None:
        self.issues.append(SchemaCheckIssue(rule=rule, severity=severity,
                                            message=message, classes=classes))
        if severity == "error":
            self.error_count += 1
            self.passed = False
        else:
            self.warning_count += 1

    def summary(self) -> dict:
        return {"passed": self.passed, "error_count": self.error_count,
                "warning_count": self.warning_count,
                "issues": [i.model_dump() for i in self.issues]}


def _norm(s: Any) -> str:
    return str(s or "").strip()


class _ClassNode:
    """schema.classes 的归一视图：id/label 双键可互相解析父类引用。"""

    def __init__(self, raw: dict):
        self.id = _norm(raw.get("id"))
        self.label = _norm(raw.get("label"))
        parents = raw.get("parent_classes")
        if not parents and raw.get("sub_class_of"):
            parents = [raw.get("sub_class_of")]
        self.parents = [_norm(p) for p in (parents or []) if _norm(p)]


def _build_nodes(schema: dict) -> tuple[list[_ClassNode], dict[str, _ClassNode]]:
    """解析 schema → (节点列表, key→节点索引)。key 兼容 id 与 label。"""
    nodes: list[_ClassNode] = []
    for raw in schema.get("classes") or []:
        if not isinstance(raw, dict):
            continue
        n = _ClassNode(raw)
        if n.id or n.label:
            nodes.append(n)
    index: dict[str, _ClassNode] = {}
    for n in nodes:
        if n.id:
            index.setdefault(n.id, n)
        if n.label:
            index.setdefault(n.label, n)
    return nodes, index


def _resolve_parents(node: _ClassNode, index: dict[str, _ClassNode]) -> list[_ClassNode]:
    """父类引用 → 节点（id 优先，label 兜底；解析不到的不在其中）。"""
    out = []
    for p in node.parents:
        target = index.get(p)
        if target is not None and target is not node:
            out.append(target)
    return out


def _check_cycles(nodes: list[_ClassNode], index: dict[str, _ClassNode],
                  report: SchemaCheckReport) -> None:
    """①类层次环：沿 parent_classes 走，回到起点即环（含自环、长环）。"""
    for start in nodes:
        path: list[_ClassNode] = []
        seen_ids: set[int] = set()
        cur = start
        while True:
            if id(cur) in seen_ids:
                loop = path[path.index(cur):] + [cur]
                names = [n.label or n.id for n in loop]
                # 同一个环会从环上每个节点各报一次，只在起点是环中最小名时报告
                if names[0] == min(names):
                    report.add(RULE_CYCLE, "error",
                               f"类层次存在环：{' → '.join(names)}（A⊑B 且 B⊑A 互相矛盾）",
                               names)
                break
            seen_ids.add(id(cur))
            path.append(cur)
            parents = _resolve_parents(cur, index)
            if not parents:
                break
            cur = parents[0]  # 多继承取第一个父类走主链；其余父类由其它起点覆盖


def _check_dangling_and_self(nodes: list[_ClassNode], index: dict[str, _ClassNode],
                             report: SchemaCheckReport) -> None:
    """②is-a 引用校验：父类引用解析不到任何类 → 悬空；引用自己 → 自父。"""
    known = {(n.id or "") for n in nodes} | {(n.label or "") for n in nodes}
    for n in nodes:
        name = n.label or n.id
        self_keys = {(n.id or ""), (n.label or "")} - {""}
        for p in n.parents:
            if p in self_keys:
                report.add(RULE_SELF_PARENT, "error",
                           f"类「{name}」的父类是它自己（is-a 方向自指）", [name])
            elif p not in known:
                report.add(RULE_DANGLING_PARENT, "error",
                           f"类「{name}」的父类「{p}」不在类清单中（悬空引用）", [name, p])


def _check_children_shape(nodes: list[_ClassNode], index: dict[str, _ClassNode],
                          report: SchemaCheckReport) -> None:
    """③单直接子类 / 超宽父类（101 §4.2）。"""
    children: dict[int, list[_ClassNode]] = defaultdict(list)
    for n in nodes:
        for p in _resolve_parents(n, index):
            children[id(p)].append(n)
    by_id = {id(n): n for n in nodes}
    for pid, kids in children.items():
        parent = by_id[pid]
        pname = parent.label or parent.id
        if len(kids) == 1:
            kid = kids[0].label or kids[0].id
            report.add(
                RULE_SINGLE_CHILD, "warning",
                f"类「{pname}」只有一个直接子类「{kid}」：该层可能多余"
                f"（合并到子类，或把「{kid}」提升一层）", [pname, kid])
        elif len(kids) > MAX_DIRECT_CHILDREN:
            kid_names = "、".join((k.label or k.id) for k in kids[:8])
            report.add(
                RULE_WIDE_PARENT, "warning",
                f"类「{pname}」有 {len(kids)} 个直接子类（{kid_names}…）："
                f"建议按子类间的显著差异增设中间层，保持每层 2-12 个分支", [pname])


def _naming_pairs(names: list[str]) -> list[tuple[str, str]]:
    """兄弟两两组合（去重）。"""
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            out.append((names[i], names[j]))
    return out


def _is_plural_pair(a: str, b: str) -> bool:
    """英文单复数差异对："Fund" vs "Funds"（小写比较，仅 ASCII 词）。"""
    la, lb = a.lower(), b.lower()
    if not (la.isascii() and lb.isascii() and la.isalpha() and lb.isalpha()):
        return False
    return la + "s" == lb or lb + "s" == la or \
        la + "es" == lb or lb + "es" == la or \
        (la.endswith("y") and la[:-1] + "ies" == lb) or \
        (lb.endswith("y") and lb[:-1] + "ies" == la)


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def _check_naming(children_by_parent: dict[int, list[_ClassNode]],
                  report: SchemaCheckReport) -> None:
    """④命名规范：兄弟类命名风格应一致（101 §6）。"""
    for kids in children_by_parent.values():
        if len(kids) < 2:
            continue
        names = [(k.label or k.id) for k in kids]
        for a, b in _naming_pairs(names):
            if _is_plural_pair(a, b):
                report.add(RULE_PLURAL_SIBLINGS, "warning",
                           f"兄弟类「{a}」与「{b}」疑似单复数不一致，同一层级应统一单复数",
                           [a, b])
        ascii_only = [x for x in names if x.isascii() and not _has_cjk(x)]
        cjk = [x for x in names if _has_cjk(x)]
        if ascii_only and cjk:
            report.add(RULE_NAMING_STYLE, "warning",
                       f"兄弟类命名风格混杂（英文 {ascii_only[:3]} 与中文 {cjk[:3]}）："
                       f"同一层级的类名应使用同一种语言/风格", names[:8])


def _check_sibling_levels(nodes: list[_ClassNode], index: dict[str, _ClassNode],
                          report: SchemaCheckReport) -> None:
    """⑤兄弟泛化层级：同一父类的兄弟里，一个类同时是另一个兄弟的（传递）父类，
    说明两者不在同一泛化层级（101 §4.2 "siblings at the same generalization level"）。"""
    children: dict[int, list[_ClassNode]] = defaultdict(list)
    for n in nodes:
        for p in _resolve_parents(n, index):
            children[id(p)].append(n)
    by_id = {id(n): n for n in nodes}
    for pid, kids in children.items():
        if len(kids) < 2:
            continue
        kid_ids = {id(k) for k in kids}
        for k in kids:
            # 遍历 k 的全部祖先（多继承不漏），若遇到兄弟节点 → 层级没对齐
            seen: set[int] = {id(k)}
            stack = _resolve_parents(k, index)
            hit = None
            while stack and hit is None:
                cur = stack.pop()
                if id(cur) in seen:
                    continue
                seen.add(id(cur))
                if id(cur) in kid_ids:
                    hit = cur
                    break
                stack.extend(_resolve_parents(cur, index))
            if hit is not None:
                a, b = k.label or k.id, hit.label or hit.id
                report.add(RULE_SIBLING_LEVEL, "warning",
                           f"「{a}」与「{b}」同为兄弟但前者是后者的子孙："
                           f"兄弟应在同一泛化层级，请调整「{a}」的位置", [a, b])


def run_schema_checks(schema: dict | None) -> SchemaCheckReport:
    """TBox 机检主入口（O1-B2）。输入 graph_data["schema"]，返回检查报告。

    五类规则：环/悬空/自父（error，阻断发布）+ 子类形态/命名/层级（warning）。
    classes 元素兼容 {id,label,parent_classes[]}、{id,label,sub_class_of} 两种形态；
    父类引用可写 id 或 label，都解析不到才判悬空。
    """
    report = SchemaCheckReport()
    schema = schema if isinstance(schema, dict) else {}
    nodes, index = _build_nodes(schema)
    if not nodes:
        return report  # 空 TBox 由 quality_gate 的"没有任何类"负责
    _check_cycles(nodes, index, report)
    _check_dangling_and_self(nodes, index, report)
    _check_children_shape(nodes, index, report)

    children: dict[int, list[_ClassNode]] = defaultdict(list)
    for n in nodes:
        for p in _resolve_parents(n, index):
            children[id(p)].append(n)
    _check_naming(children, report)
    _check_sibling_levels(nodes, index, report)
    return report
