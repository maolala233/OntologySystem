# app/services/schema_governance.py - 过度抽取治理（2026-11-19）
# 271 类里 128 个类零实例且未被任何关系 domain/range 引用，属于纯推测类。
# 治理策略：不删除定义——移入 schema.archived_classes；类节点/类间边置 rejected，
# 图谱默认过滤 rejected，因此有效图谱变干净，原始定义仍可审计/恢复。
from __future__ import annotations

import copy
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, Project, Relation


def _ancestor_closure(keep: set[str], parents: dict[str, set[str]],
                      labels: set[str]) -> set[str]:
    changed = True
    while changed:
        changed = False
        for lbl in list(keep):
            for p in parents.get(lbl, set()):
                if p in labels and p not in keep:
                    keep.add(p)
                    changed = True
    return keep


def govern_schema(project_id: int, db: Session, apply: bool = True) -> dict:
    # 索引对账会用 raw UPDATE 改 graph_data；同一 session 的 identity map 可能还是旧值，
    # 不刷新会把刚补的多类边/节点覆盖掉。
    db.expire_all()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"error": "project_not_found"}
    gd = copy.deepcopy(proj.graph_data) if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
    classes = [c for c in schema.get("classes") or [] if isinstance(c, dict)]
    labels = {str(c.get("label")) for c in classes if c.get("label")}
    parents = {str(c.get("label")): {str(p) for p in (c.get("parent_classes") or []) if p}
               for c in classes if c.get("label")}

    from app.services.class_membership import class_distribution

    used = set(class_distribution(db, project_id).keys())
    ops = [o for o in schema.get("object_properties") or [] if isinstance(o, dict)]
    dom = {str(o.get("domain")) for o in ops if o.get("domain") in labels}
    rng = {str(o.get("range")) for o in ops if o.get("range") in labels}
    keep = _ancestor_closure(used | dom | rng, parents, labels)
    archived = [c for c in classes if str(c.get("label")) not in keep]
    # 幂等恢复：如果 classes 已被裁剪过，archived_classes 可能为空；从类节点
    # 重建归档定义（属性/父子边仍在行表），避免治理第二次运行把归档清单覆盖没。
    all_class_labels = {str(e.label) for e in
                        db.query(Entity).filter(Entity.project_id == project_id,
                                                Entity.is_class_node.is_(True)).all()}
    existing_archived = [c for c in (schema.get("archived_classes") or [])
                         if isinstance(c, dict)]
    archived_labels = {str(c.get("label")) for c in archived}
    archived_labels |= {str(c.get("label")) for c in existing_archived}
    archived_labels |= (all_class_labels - labels)
    class_nodes = (db.query(Entity)
                   .filter(Entity.project_id == project_id,
                           Entity.is_class_node.is_(True),
                           Entity.label.in_(archived_labels or {""}))
                   .all())
    node_by_label = {e.label: e for e in class_nodes}
    for label in archived_labels:
        if any(str(c.get("label")) == label for c in existing_archived):
            continue
        node = node_by_label.get(label)
        if node is None:
            continue
        parents = {str(r[0]) for r in (db.query(Entity.label)
                                        .join(Relation, Relation.object_id == Entity.id)
                                        .filter(Relation.project_id == project_id,
                                                Relation.subject_id == node.id,
                                                Relation.predicate == "subclass_of").all())}
        existing_archived.append({
            "label": label,
            "definition": "",
            "parent_classes": sorted(parents),
            "aliases": [],
            "properties": [{"name": k, "data_type": str(v or "string")}
                           for k, v in (node.props or {}).items()],
            "archive_reason": "zero_instances_and_no_relation_reference",
            "archived_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
    archived = existing_archived

    stats = {
        "classes_before": len(classes),
        "classes_after": len(keep),
        "archived": len(archived),
        "instance_classes": len(used),
        "relation_referenced": len(dom | rng),
    }
    if not apply:
        return {**stats, "archived_labels": [c.get("label") for c in archived]}

    # 定义归档（不丢） + 图谱节点/类间边隐藏（rejected，可恢复）
    archived_rows = []
    for c in archived:
        row = dict(c)
        row["archive_reason"] = "zero_instances_and_no_relation_reference"
        row["archived_at"] = datetime.utcnow().isoformat(timespec="seconds")
        archived_rows.append(row)
    schema["archived_classes"] = archived_rows
    # 关键：schema.classes 保留全量（含归档类），否则下次构建 skip schema 后
    # 实例抽取只看到 143 类，会把接口索引行误归到泛化「接口」类（实测 1759/151
    # 回退）。归档只影响图谱/结果页的“有效类”展示；抽取仍用完整骨架。
    full_classes = [c for c in classes if str(c.get("label")) in labels]
    for c in archived:
        if str(c.get("label")) not in {str(x.get("label")) for x in full_classes}:
            full_classes.append(c)
    schema["classes"] = full_classes
    schema["quality"] = {
        **stats,
        "active_classes": len(keep),
        "policy": "keep_instance_or_relation_supported_classes",
        "archived_kept_in_schema": True,
    }
    gd["schema"] = schema
    # 直接 UPDATE blob，不走 ORM 脏检查：graph_data 的 after_flush 监听器会全量
    # 重建行表（删除关系再按 blob 重建），会把刚补的多类 instance_of 边冲掉。
    # 治理只改 schema 清单，行表/关系状态由下面显式更新，不需要整图重投影。
    db.execute(update(Project).where(Project.id == project_id).values(graph_data=gd))

    cls_ids = []
    for label in [c.get("label") for c in archived]:
        rows = (db.query(Entity)
                .filter(Entity.project_id == project_id,
                        Entity.is_class_node.is_(True),
                        Entity.label == label).all())
        for e in rows:
            e.status = "rejected"
            props = dict(e.props or {})
            props["archived"] = True
            props["archive_reason"] = "zero_instances_and_no_relation_reference"
            e.props = props
            cls_ids.append(e.id)
    if cls_ids:
        (db.query(Relation)
         .filter(Relation.project_id == project_id,
                 (Relation.subject_id.in_(cls_ids)) | (Relation.object_id.in_(cls_ids)))
         .update({"status": "rejected"}, synchronize_session=False))
    db.commit()
    return {**stats, "archived_labels": [c.get("label") for c in archived]}


__all__ = ["govern_schema"]
