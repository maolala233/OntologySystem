# app/services/class_membership.py - 多类成员口径（2026-11-19）
# 当前 Entity.class_label 是单值主类；同一个业务对象可能同时属于多个类
# （C250405N 既是 BP接口 又是 前端接口）。多类成员用 instance_of 关系表达，
# 这里统一提供“某类的实例 id 集合 / 全图类分布”，避免各处只读 class_label。
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, Relation

STRUCTURAL_MEMBERSHIP = ("instance_of", "rdf:type", "type")


def class_node(db: Session, project_id: int, label: str) -> Entity | None:
    return (db.query(Entity)
            .filter(Entity.project_id == project_id,
                    Entity.is_class_node.is_(True),
                    Entity.label == str(label).strip())
            .first())


def membership_ids(db: Session, project_id: int, label: str) -> set[int]:
    """某类的成员实例 id：主类 class_label + instance_of 边指向该类节点的实例。"""
    label = str(label or "").strip()
    if not label:
        return set()
    ids = {int(i) for (i,) in db.query(Entity.id).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status.notin_(("merged", "rejected")),
        Entity.class_label == label).all()}
    cls = class_node(db, project_id, label)
    if cls is not None:
        ids |= {int(i) for (i,) in db.query(Relation.subject_id).filter(
            Relation.project_id == project_id,
            Relation.predicate.in_(STRUCTURAL_MEMBERSHIP),
            Relation.object_id == cls.id,
            Relation.status != "rejected").all()}
    return ids


def class_distribution(db: Session, project_id: int) -> dict[str, int]:
    """全图类分布：主类 + 多类成员去重；类节点自身不计入实例数。"""
    out: dict[str, set[int]] = {}
    for label, eid in (db.query(Entity.class_label, Entity.id).filter(
            Entity.project_id == project_id,
            Entity.is_class_node.is_(False),
            Entity.status.notin_(("merged", "rejected"))).all()):
        out.setdefault(str(label or ""), set()).add(int(eid))
    rows = (db.query(Entity.label, Relation.subject_id)
            .join(Entity, Entity.id == Relation.object_id)
            .filter(Relation.project_id == project_id,
                    Relation.predicate.in_(STRUCTURAL_MEMBERSHIP),
                    Relation.status != "rejected",
                    Entity.is_class_node.is_(True)).all())
    for label, eid in rows:
        out.setdefault(str(label or ""), set()).add(int(eid))
    return {k: len(v) for k, v in out.items() if k and v}


def add_membership(db: Session, project_id: int, entity_id: int, class_label: str) -> bool:
    """补一条 instance_of 多类成员边；已有/类节点不存在返回 False。"""
    cls = class_node(db, project_id, class_label)
    if cls is None:
        return False
    exists = (db.query(Relation.id).filter(
        Relation.project_id == project_id,
        Relation.subject_id == entity_id,
        Relation.predicate == "instance_of",
        Relation.object_id == cls.id).first())
    if exists:
        return False
    db.add(Relation(project_id=project_id, subject_id=entity_id,
                    predicate="instance_of", object_id=cls.id,
                    confidence=1.0, status="auto", is_class_edge=False))
    return True


__all__ = ["class_node", "membership_ids", "class_distribution", "add_membership"]
