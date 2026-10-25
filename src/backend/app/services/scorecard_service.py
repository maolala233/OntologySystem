# app/services/scorecard_service.py - 本体健康记分卡聚合（O2-F7）
# 指标口径见 docs/design/09 §3：内在质量（一致性违例/环/悬空引用/命名一致率/
# 证据闭合率）+ 覆盖效果（实例覆盖率/孤立类）+ 人工信号（审核通过率趋势）。
# 数据来源：B2 schema_checks、B9 evidence_closure、审核工作流、实体行表。
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

# 审核通过率趋势窗口
TREND_MONTHS = 6


def _norm_label(s) -> str:
    return str(s or "").strip().lower()


def _naming_consistency(schema: dict) -> tuple[float, list[str]]:
    """命名一致率：TBox 对象标签非空、无首尾空白、归一后不重复的比例。

    归一（小写去空白）后重复的标签 = 引用歧义风险，计为不一致。
    """
    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    ops = [o for o in (schema.get("object_properties") or [])
           if isinstance(o, dict)]
    labels: list[str] = []
    bad: list[str] = []
    for obj in classes + ops:
        raw = obj.get("label")
        label = str(raw or "").strip()
        if not label or label != str(raw):
            bad.append(label or "(空标签)")
        labels.append(_norm_label(label))
    seen: set[str] = set()
    for lab in labels:
        if not lab:
            continue
        if lab in seen:
            bad.append(lab)
        seen.add(lab)
    total = len(classes) + len(ops)
    rate = round(1 - len(bad) / total, 3) if total else 1.0
    return rate, bad[:10]


def _dangling_domain_range(schema: dict) -> list[str]:
    """对象属性 domain/range 引用了不存在的类。"""
    class_labels = {_norm_label(c.get("label")) for c in
                    (schema.get("classes") or []) if isinstance(c, dict)}
    out: list[str] = []
    for o in (schema.get("object_properties") or []):
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "")
        for side in ("domain", "range"):
            ref = _norm_label(o.get(side))
            if ref and ref not in class_labels:
                out.append(f"{label}.{side}→{o.get(side)}")
    return out


def compute_scorecard(db: Session, project_id: int, gd: dict) -> dict:
    """聚合记分卡指标（实时计算，不落库）。"""
    from app.adapters.schema_checks import run_schema_checks
    from app.infrastructure.database import Entity, Relation, ReviewItem
    from app.services.evidence_closure import tbox_evidence_closure

    gd = gd if isinstance(gd, dict) else {}
    schema = gd.get("schema") or {}
    classes = [c for c in (schema.get("classes") or [])
               if isinstance(c, dict) and c.get("label")]
    ops = [o for o in (schema.get("object_properties") or [])
           if isinstance(o, dict) and o.get("label")]
    class_labels = {str(c["label"]) for c in classes}

    # ── 内在质量 ──
    checks = run_schema_checks(schema).summary()
    cycle_count = sum(1 for i in checks.get("issues", [])
                      if i.get("rule") == "class_hierarchy_cycle")
    dangling_parent = sum(1 for i in checks.get("issues", [])
                          if i.get("rule") in ("dangling_parent_ref",
                                               "self_parent_ref"))
    dangling_dr = _dangling_domain_range(schema)
    naming_rate, naming_bad = _naming_consistency(schema)
    # 一致性违例数：待处理冲突审核项（B6 LOGICAL / value / type / relationship / axiom）
    conflict_types = ("conflict_value", "conflict_type", "conflict_relationship",
                      "conflict_axiom")
    open_conflicts = (db.query(func.count(ReviewItem.id))
                      .filter(ReviewItem.project_id == project_id,
                              ReviewItem.item_type.in_(conflict_types),
                              ReviewItem.status.in_(("pending", "claimed")))
                      .scalar() or 0)

    # 证据闭合率（B9）
    closure = tbox_evidence_closure(db, project_id, gd)
    checked = closure.get("checked") or 0
    # S18 口径统一（UX 走查 P0-2）：闭合率只按"悬空"计（悬空=阻断，与发布门禁同源）；
    # missing（无锚点）仅建议补齐，不计入率值——否则与门禁 ✓ 打架。
    if checked:
        evidence_rate = round((checked - len(closure.get("dangling") or [])) / checked, 3)
    else:
        evidence_rate = 1.0

    # ── 覆盖效果（实例行表） ──
    inst_counts: dict[str, int] = dict(
        db.query(Entity.class_label, func.count(Entity.id))
        .filter(Entity.project_id == project_id,
                Entity.is_class_node.is_(False),
                Entity.status != "merged")
        .group_by(Entity.class_label).all())
    # 关系触达的类（TBox 引用 + 实例关系两端）
    rel_touched: set[str] = set()
    for o in ops:
        for side in ("domain", "range"):
            if o.get(side):
                rel_touched.add(str(o[side]))
    rel_pairs = (db.query(Entity.class_label)
                 .join(Relation, Relation.subject_id == Entity.id)
                 .filter(Relation.project_id == project_id).all())
    for (s_cls,) in rel_pairs:
        if s_cls:
            rel_touched.add(str(s_cls))
    obj_ids = [r.object_id for r in db.query(Relation)
               .filter(Relation.project_id == project_id).all()]
    if obj_ids:
        for (o_cls,) in (db.query(Entity.class_label)
                         .filter(Entity.id.in_(obj_ids)).all()):
            if o_cls:
                rel_touched.add(str(o_cls))

    covered = [c for c in class_labels if (inst_counts.get(c) or 0) > 0]
    instance_coverage = round(len(covered) / len(class_labels), 3) \
        if class_labels else 1.0
    orphan = sorted(
        c for c in class_labels
        if (inst_counts.get(c) or 0) == 0
        and str(c) not in rel_touched
        and not _has_hierarchy_link(schema, c))

    # ── 人工信号：审核通过率（按月，近 6 个月） ──
    since = datetime.utcnow() - timedelta(days=30 * TREND_MONTHS)
    rows = (db.query(ReviewItem.status, ReviewItem.decided_at)
            .filter(ReviewItem.project_id == project_id,
                    ReviewItem.status.in_(("approved", "rejected")),
                    ReviewItem.decided_at.isnot(None),
                    ReviewItem.decided_at >= since).all())
    by_month: dict[str, dict[str, int]] = defaultdict(
        lambda: {"approved": 0, "rejected": 0})
    for status, decided_at in rows:
        key = decided_at.strftime("%Y-%m")
        by_month[key][status] += 1
    trend = []
    for i in range(TREND_MONTHS):
        m = datetime.utcnow().replace(day=1) - timedelta(days=30 * i)
        key = m.strftime("%Y-%m")
        a, r = by_month[key]["approved"], by_month[key]["rejected"]
        trend.append({"month": key, "approved": a, "rejected": r,
                      "pass_rate": round(a / (a + r), 3) if (a + r) else None})
    trend.reverse()
    pending_count = (db.query(func.count(ReviewItem.id))
                     .filter(ReviewItem.project_id == project_id,
                             ReviewItem.status.in_(("pending", "claimed")))
                     .scalar() or 0)

    # ── S18 OntoQA 结构成熟度（docs/15 Tier2：只打分不阻断） ──
    total_items = len(classes) + len(ops)
    relationship_richness = round(len(ops) / total_items, 3) if total_items else 0.0
    prop_entries = sum(len(c.get("properties") or [])
                       for c in classes if isinstance(c, dict))
    attribute_richness = round(prop_entries / len(classes), 3) if classes else 0.0
    with_parent = sum(1 for c in classes if c.get("parent_classes"))
    inheritance_richness = round(with_parent / len(classes), 3) if classes else 0.0
    taxonomy_depth = _taxonomy_depth(schema)
    maturity_warnings: list[str] = []
    if classes and relationship_richness < 0.1:
        maturity_warnings.append("关系丰富度偏低（<0.1）：本体基本只是分类树，建议补充对象关系/属性")
    if taxonomy_depth <= 1 and len(classes) > 1:
        maturity_warnings.append("分类无层级（深度 1）：建议归纳父类形成分类体系")
    if classes and attribute_richness < 1.0:
        maturity_warnings.append("属性丰富度偏低：多数类无属性，实例问答可依据有限")

    return {
        "maturity": {
            "relationship_richness": relationship_richness,
            "attribute_richness": attribute_richness,
            "inheritance_richness": inheritance_richness,
            "taxonomy_depth": taxonomy_depth,
            "warnings": maturity_warnings,
        },
        "consistency_violations": int(open_conflicts),
        "cycles": cycle_count,
        "dangling_refs": dangling_parent + len(dangling_dr),
        "dangling_domain_range": dangling_dr[:10],
        "naming_consistency": naming_rate,
        "naming_bad": naming_bad,
        "evidence_closure_rate": evidence_rate,
        "evidence_dangling": closure.get("dangling") or [],
        "evidence_missing_count": len(closure.get("missing") or []),
        "instance_coverage": instance_coverage,
        "orphan_classes": orphan[:20],
        "orphan_count": len(orphan),
        "schema_checks": {"passed": checks.get("passed"),
                          "error_count": checks.get("error_count"),
                          "warning_count": checks.get("warning_count")},
        "schema_warnings": [
            {"rule": i.get("rule"), "message": i.get("message") or str(i)}
            for i in (checks.get("issues") or [])
            if i.get("severity") == "warning"][:10],
        "review_trend": trend,
        "pending_reviews": int(pending_count),
        "class_count": len(class_labels),
        "instance_count": sum(inst_counts.values()),
    }


def _has_hierarchy_link(schema: dict, class_label: str) -> bool:
    """该类是否出现在任何 subClassOf 关系里（父或子）。"""
    for c in (schema.get("classes") or []):
        if not isinstance(c, dict):
            continue
        if _norm_label(c.get("label")) == _norm_label(class_label):
            if c.get("parent_classes") or c.get("sub_class_of"):
                return True
        parents = [_norm_label(p) for p in
                   (c.get("parent_classes") or
                    ([c.get("sub_class_of")] if c.get("sub_class_of") else []))]
        if _norm_label(class_label) in parents:
            return True
    return False


__all__ = ["compute_scorecard"]

def _taxonomy_depth(schema: dict) -> int:
    """分类树最大深度（BFS；parent_classes 边，1 = 无层级）。"""
    children: dict[str, list[str]] = {}
    all_labels: set[str] = set()
    for c in (schema.get("classes") or []):
        if not isinstance(c, dict) or not c.get("label"):
            continue
        label = _norm_label(c["label"])
        all_labels.add(label)
        for parent in c.get("parent_classes") or []:
            children.setdefault(_norm_label(parent), []).append(label)
    if not all_labels:
        return 0
    roots = [lb for lb in all_labels if lb not in children or not children[lb]]
    # 从"没有任何子节点的类"反向走父链更直观——这里按"父 → 子"自顶向下：
    has_parent = {ch for kids in children.values() for ch in kids}
    frontier = [lb for lb in all_labels if lb not in has_parent]  # 顶层类
    depth = 0
    seen: set[str] = set()
    level = list(set(frontier))
    while level:
        depth += 1
        nxt = []
        for lb in level:
            if lb in seen:
                continue
            seen.add(lb)
            nxt.extend(children.get(lb, []))
        level = list(set(nxt))
    return depth
