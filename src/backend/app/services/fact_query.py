# app/services/fact_query.py - 确定性事实查询工具（2026-11-19）
# 背景：问答上下文里的清单/计数被 40/200/4000 字截断后，模型容易把“可见部分”
# 当成全集。这里把“清单/计数”做成工具：按类/标签/谓词/方向过滤，返回 total +
# 分页 items，调用方可以按 offset 取全量，不再依赖 prompt 里塞完整清单。
#
# 只读、零 LLM；MCP query_facts 与 REST /graph/query-facts 共用本服务。
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, Relation


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _entity_out(e: Entity) -> dict:
    return {
        "id": e.id,
        "uri": e.uri,
        "label": e.label,
        "class_label": e.class_label,
        "aliases": e.aliases or [],
        "props": e.props or {},
        "confidence": float(e.confidence) if e.confidence is not None else None,
        "status": e.status,
        "is_class": bool(e.is_class_node),
    }


def query_entities(db: Session, project_id: int, *,
                   class_label: Optional[str] = None,
                   label_contains: Optional[str] = None,
                   uri: Optional[str] = None,
                   include_classes: bool = False,
                   limit: int = 50, offset: int = 0) -> dict:
    """实体清单：返回 total + 当前页；不把“当前页”说成全集。"""
    q = db.query(Entity).filter(Entity.project_id == project_id,
                                Entity.status != "rejected")
    if not include_classes:
        q = q.filter(Entity.is_class_node.is_(False))
    if class_label:
        from app.services.class_membership import membership_ids

        ids = membership_ids(db, project_id, str(class_label).strip())
        q = q.filter(Entity.id.in_(ids or [-1]))
    if uri:
        q = q.filter(Entity.uri == str(uri).strip())
    if label_contains:
        key = str(label_contains).strip()
        if key:
            q = q.filter(or_(Entity.label.contains(key),
                             Entity.label_normalized.contains(key.lower())))
    total = q.count()
    limit = _clamp(limit, 1, 500, 50)
    offset = _clamp(offset, 0, 10_000_000, 0)
    rows = (q.order_by(Entity.class_label, Entity.label)
            .offset(offset).limit(limit).all())
    return {"kind": "entities", "total": int(total), "offset": offset,
            "limit": limit, "returned": len(rows),
            "has_more": offset + len(rows) < total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "items": [_entity_out(e) for e in rows]}


def query_relations(db: Session, project_id: int, *,
                    predicate: Optional[str] = None,
                    subject_label: Optional[str] = None,
                    object_label: Optional[str] = None,
                    entity_label: Optional[str] = None,
                    subject_id: Optional[int] = None,
                    object_id: Optional[int] = None,
                    include_structural: bool = False,
                    limit: int = 50, offset: int = 0) -> dict:
    """关系清单：支持按主体/宾语标签、谓词、方向过滤；total 永远是全集计数。

    entity_label：主体 **或** 宾语命中该标签（“C2504051 的输入项”这类双向问题
    一次查全，避免调用方猜方向）。
    """
    q = db.query(Relation).filter(Relation.project_id == project_id,
                                  Relation.status != "rejected")
    if not include_structural:
        q = q.filter(~Relation.predicate.in_(("instance_of", "subclass_of")))
    if predicate:
        q = q.filter(Relation.predicate == str(predicate).strip())
    if subject_label:
        ids = _entity_ids_by_label(db, project_id, subject_label)
        q = q.filter(Relation.subject_id.in_(ids or [-1]))
    if object_label:
        ids = _entity_ids_by_label(db, project_id, object_label)
        q = q.filter(Relation.object_id.in_(ids or [-1]))
    if entity_label:
        ids = _entity_ids_by_label(db, project_id, entity_label)
        q = q.filter(or_(Relation.subject_id.in_(ids or [-1]),
                         Relation.object_id.in_(ids or [-1])))
    if subject_id is not None:
        q = q.filter(Relation.subject_id == int(subject_id))
    if object_id is not None:
        q = q.filter(Relation.object_id == int(object_id))
    total = q.count()
    limit = _clamp(limit, 1, 500, 50)
    offset = _clamp(offset, 0, 10_000_000, 0)
    rows = q.order_by(Relation.id).offset(offset).limit(limit).all()
    ids = {r.subject_id for r in rows} | {r.object_id for r in rows}
    ents = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == project_id, Entity.id.in_(ids)).all()} if ids else {}
    items = []
    for r in rows:
        s = ents.get(r.subject_id)
        o = ents.get(r.object_id)
        items.append({
            "id": r.id,
            "subject_id": r.subject_id,
            "subject": s.label if s else "",
            "subject_class": s.class_label if s else "",
            "predicate": r.predicate,
            "object_id": r.object_id,
            "object": o.label if o else "",
            "object_class": o.class_label if o else "",
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "is_class_edge": bool(r.is_class_edge),
        })
    return {"kind": "relations", "total": int(total), "offset": offset,
            "limit": limit, "returned": len(rows),
            "has_more": offset + len(rows) < total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "items": items}


def _entity_ids_by_label(db: Session, project_id: int, label: str) -> list[int]:
    key = str(label or "").strip()
    if not key:
        return []
    rows = (db.query(Entity.id)
            .filter(Entity.project_id == project_id,
                    Entity.status != "rejected",
                    or_(Entity.label == key,
                        Entity.label.contains(key),
                        Entity.label_normalized.contains(key.lower())))
            .limit(500).all())
    return [int(i) for (i,) in rows]


def query_facts(db: Session, project_id: int, payload: dict | None = None) -> dict:
    """统一入口：kind=entities|relations；MCP/REST 共用。"""
    payload = payload or {}
    kind = str(payload.get("kind") or "entities").strip().lower()
    if kind in ("entity", "entities", "实体"):
        return query_entities(
            db, project_id,
            class_label=payload.get("class_label"),
            label_contains=payload.get("label_contains") or payload.get("query"),
            uri=payload.get("uri"),
            include_classes=bool(payload.get("include_classes", False)),
            limit=payload.get("limit", 50),
            offset=payload.get("offset", 0),
        )
    if kind in ("relation", "relations", "关系"):
        return query_relations(
            db, project_id,
            predicate=payload.get("predicate"),
            subject_label=payload.get("subject_label"),
            object_label=payload.get("object_label"),
            entity_label=payload.get("entity_label"),
            subject_id=payload.get("subject_id"),
            object_id=payload.get("object_id"),
            include_structural=bool(payload.get("include_structural", False)),
            limit=payload.get("limit", 50),
            offset=payload.get("offset", 0),
        )
    raise ValueError(f"kind 必须是 entities 或 relations，收到: {kind}")


__all__ = ["query_facts", "query_entities", "query_relations"]
