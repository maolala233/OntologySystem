# app/services/build_report.py - 构建报告（人话版，2026-11-08 方法论对齐）
# 外部 skill 交付物对照：每次构建产出一份人能直接读的报告（类树/统计/CQ 验证/
# 业务规则/采集表规则层/待确认项）。纯模板渲染，零 LLM 成本。
# 存放：graph_data["build_report"]（随项目快照走）；API 暴露 markdown 文本。
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

MAX_TREE_NODES = 120      # 类树渲染上限（超长截断）
MAX_RULE_ROWS = 40        # 采集表规则层渲染上限
MAX_CQ_ROWS = 20


def _class_tree(schema: dict) -> list[str]:
    """自顶向下类树（缩进文本）。确定性：同级按名称排序。"""
    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    labels = [str(c.get("label") or "").strip() for c in classes]
    label_set = {l for l in labels if l}
    children: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}
    # 关系边参与的类不算孤岛（与 schema_checks.isolated_class 口径一致）
    op_linked: set[str] = set()
    for op in schema.get("object_properties") or []:
        if not isinstance(op, dict):
            continue
        for side in ("domain", "range"):
            v = str(op.get(side) or "").strip()
            if v in label_set:
                op_linked.add(v)
    for c in classes:
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        parents = [str(p).strip() for p in (c.get("parent_classes") or [])
                   if str(p).strip() in label_set and str(p).strip() != label]
        if parents:
            parent_of[label] = sorted(parents)[0]  # 多继承取规范父类渲染一次
    for child, parent in parent_of.items():
        children.setdefault(parent, []).append(child)
    roots = sorted(l for l in label_set if l not in parent_of)
    lines = []
    count = 0

    def _walk(label: str, depth: int) -> None:
        nonlocal count
        if count >= MAX_TREE_NODES:
            return
        count += 1
        # 孤岛标记（无父无子且不在任何关系上）：与 schema_checks.isolated_class 同口径
        note = "（孤岛：无父类也无关系）" \
            if label not in parent_of and not children.get(label) \
            and label not in op_linked else ""
        lines.append("  " * depth + f"- {label}{note}")
        for kid in sorted(children.get(label) or []):
            _walk(kid, depth + 1)

    for r in roots:
        _walk(r, 0)
    if count >= MAX_TREE_NODES:
        lines.append(f"…（类树超 {MAX_TREE_NODES} 节点已截断）")
    return lines


def _stats(db: Session, project_id: int, schema: dict) -> dict:
    from sqlalchemy import func as sa_func

    from app.infrastructure.database import Entity, Relation

    n_cls = len([c for c in (schema.get("classes") or [])
                 if isinstance(c, dict) and str(c.get("label") or "").strip()])
    n_sub = sum(1 for c in (schema.get("classes") or [])
                if isinstance(c, dict) and (c.get("parent_classes") or []))
    n_inst = (db.query(sa_func.count(Entity.id))
              .filter(Entity.project_id == project_id, Entity.is_class_node.is_(False)).scalar() or 0)
    n_rel = (db.query(sa_func.count(Relation.id))
             .filter(Relation.project_id == project_id).scalar() or 0)
    inst_classes = (db.query(sa_func.count(sa_func.distinct(Entity.class_label)))
                    .filter(Entity.project_id == project_id,
                            Entity.is_class_node.is_(False)).scalar() or 0)
    return {"classes": n_cls, "hierarchy": n_sub, "instances": n_inst,
            "relations": n_rel, "inst_classes": inst_classes}


def generate_build_report(db: Session, project_id: int,
                          depth_label: str = "", model_name: str = "") -> dict:
    """渲染构建报告并写入 graph_data["build_report"]。返回 {sections: N} 概要。

    数据全部来自库内已有资产（零 LLM）：schema 类树 / 行表统计 / CQ 最近评测 /
    推理规则 / 采集表规则层 / 待确认审核项。失败静默返回 skipped，不阻断管线。
    """
    from app.infrastructure.database import (
        CqEvaluation, Entity, OntologyRule, Project, ReviewItem,
    )

    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"skipped": "project_not_found"}
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}

    st = _stats(db, project_id, schema)
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [f"# 构建报告 · {proj.name}", ""]
    meta = [f"- 构建时间：{now} (UTC)"]
    if model_name:
        meta.append(f"- 抽取模型：{model_name}")
    if depth_label:
        meta.append(f"- 构建档位：{depth_label}")
    lines += meta + ["", "## 一、规模统计", "",
                     f"类 **{st['classes']}**（层次关系 {st['hierarchy']} 条，"
                     f"{st['inst_classes']} 个类有实例）｜实例 **{st['instances']}**｜"
                     f"关系 **{st['relations']}**", ""]

    tree = _class_tree(schema)
    lines += ["## 二、类树（自顶向下）", ""] + tree + [""]

    # CQ 验证（最近一次）
    ev = (db.query(CqEvaluation).filter(CqEvaluation.project_id == project_id)
          .order_by(CqEvaluation.id.desc()).first())
    lines += ["## 三、能力问题验证", ""]
    if ev is not None:
        mode_note = "（SPARQL 实查/检索式回落）" if ev.mode == "sparql+retrieval" \
            else ("（检索式）" if ev.mode == "retrieval" else "")
        lines.append(f"验证通过 **{ev.answerable_count}/{ev.total_cq}**{mode_note}，"
                     f"证据覆盖率 {float(ev.coverage_rate or 0):.0%}")
        lines.append("")
        for r in (ev.results or [])[:MAX_CQ_ROWS]:
            mark = "✅" if r.get("answerable") else "⚠️"
            mode = r.get("mode") or "retrieval"
            mode_txt = "SPARQL 实查" if mode == "sparql" else "检索"
            lines.append(f"- {mark} {r.get('question')}（{mode_txt}）")
    else:
        lines.append("未执行能力问题评测")
    lines.append("")

    # 推理规则
    rules = (db.query(OntologyRule).filter(OntologyRule.project_id == project_id)
             .order_by(OntologyRule.id.asc()).all())
    lines += ["## 四、业务规则（推理引擎）", ""]
    if rules:
        for r in rules:
            lines.append(f"- 若「{r.if_subject_class}」经「{r.if_predicate}」连接"
                         f"「{r.if_object_class}」，则两者存在「{r.then_predicate}」关系"
                         + ("" if r.enabled else "（已停用）"))
    else:
        lines.append("本次构建未产出推理规则")
    lines.append("")

    # 采集表·规则层（文档原文锚定）
    doc_rules = [r for r in (schema.get("business_rules") or []) if isinstance(r, dict)]
    lines += ["## 五、语义采集表 · 规则层（原文出处可回溯）", ""]
    if doc_rules:
        for r in doc_rules[:MAX_RULE_ROWS]:
            ev_txt = str(r.get("evidence") or "").strip()
            src = f" —— 出处：{ev_txt[:60]}" if ev_txt else ""
            lines.append(f"- [{r.get('kind') or '约束'}] {r.get('statement')}{src}")
        if len(doc_rules) > MAX_RULE_ROWS:
            lines.append(f"…（共 {len(doc_rules)} 条，已截断）")
    else:
        lines.append("骨架阶段未采集到业务规则")
    lines.append("")

    # 待确认项
    pending = (db.query(ReviewItem)
               .filter(ReviewItem.project_id == project_id,
                       ReviewItem.status == "pending")
               .order_by(ReviewItem.priority.desc(), ReviewItem.id.asc()).all())
    lines += ["## 六、待确认项", ""]
    if pending:
        for it in pending[:MAX_RULE_ROWS]:
            lines.append(f"- [{it.priority}] {it.reason or it.item_type}")
        if len(pending) > MAX_RULE_ROWS:
            lines.append(f"…（共 {len(pending)} 条）")
    else:
        lines.append("无 —— 全部结果已自动确认")

    markdown = "\n".join(lines)
    gd["build_report"] = {"markdown": markdown,
                          "generated_at": datetime.datetime.utcnow().isoformat() + "Z"}
    proj.graph_data = gd
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(proj, "graph_data")
    db.commit()  # 报告是管线收尾的最后写入：自带提交，调用方不再依赖外层 commit
    return {"sections": 6, "chars": len(markdown),
            "pending": len(pending), "rules": len(rules), "doc_rules": len(doc_rules)}


__all__ = ["generate_build_report"]
