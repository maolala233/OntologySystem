# app/services/resolution_service.py - 消解/冲突业务编排（docs/design/04 §5/§6，M3-5）
# adapters 层保持纯函数；本服务层负责：行表装载、embedding 调用、review_items 物化去重。
# 被Celery 任务（extract 队列）与 API（merge/split/conflicts detect）共用。

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.conflicts import ConflictType, detect_conflicts
from app.adapters.resolution import (
    AUTO_MERGE_THRESHOLD,
    MANUAL_REVIEW_THRESHOLD,
    DuplicateCluster,
    detect_duplicates,
    merge_entities,
)
from app.core.logging import logger
from app.infrastructure.database import Entity, ProvenanceRecord, Project, Relation, ReviewItem

# 单次消解/冲突检测物化的审核项上限（防审核队列被一次大批次刷爆）
MAX_REVIEW_ITEMS_PER_TYPE = 50

_CONFLICT_ITEM_TYPE = {
    ConflictType.VALUE: "conflict_value",
    ConflictType.TYPE: "conflict_type",
    ConflictType.RELATIONSHIP: "conflict_relationship",
    ConflictType.TEMPORAL: "conflict_value",  # 04 §6：temporal 归入 conflict_value(kind=temporal)
    ConflictType.AXIOM: "conflict_axiom",     # 公理 2 期：TBox 公理违例转审核
}


def load_active_entities(db: Session, project_id: int, scope: str = "all") -> list[Entity]:
    """参与消解的实体：非类节点、未合并；scope='class:X' 过滤单类。"""
    q = db.query(Entity).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status != "merged",
    )
    if scope and scope.startswith("class:"):
        q = q.filter(Entity.class_label == scope.split(":", 1)[1])
    return q.all()


def _embeddings_fn(project_id: Optional[int] = None):
    """bge-m3 同步嵌入函数；provider 未配置/调用失败返回 None（消解退回拼音层）。"""
    try:
        from openai import OpenAI

        from app.adapters.provider import ModelPurpose, resolve_embedding
        from app.infrastructure.database import SessionLocal

        s = SessionLocal()
        try:
            resolved = resolve_embedding(project_id, db=s)
        finally:
            s.close()
        client = OpenAI(base_url=resolved.base_url, api_key=resolved.api_key or "EMPTY",
                        timeout=60)

        def _embed(texts: list[str]) -> list[list[float]]:
            resp = client.embeddings.create(input=texts, model=resolved.model_name)
            return [d.embedding for d in resp.data]

        return _embed
    except Exception as e:  # noqa: BLE001 —— 向量层不可用不阻断消解
        logger.warning(f"[resolution] 向量层不可用，退回拼音层: {e}")
        return None


def run_resolution(project_id: int, scope: str = "all",
                   thresholds: Optional[dict] = None,
                   blocking: str = "pinyin",
                   db: Optional[Session] = None) -> dict:
    """三层消解执行：自动合并 + 待审聚类（review_items entity_merge）。

    可同步调用（API merge 的小规模场景）或由 Celery 任务调用。
    """
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        auto_th = float((thresholds or {}).get("auto", AUTO_MERGE_THRESHOLD))
        review_th = float((thresholds or {}).get("manual", MANUAL_REVIEW_THRESHOLD))
        ents = load_active_entities(db, project_id, scope)
        if len(ents) < 2:
            return {"clusters_total": 0, "auto_merged": 0, "review_created": 0,
                    "relations_migrated": 0, "entities_scanned": len(ents)}
        clusters = detect_duplicates(
            ents, blocking=blocking, embeddings_fn=_embeddings_fn(project_id),
            auto_threshold=auto_th, review_threshold=review_th)

        relations_migrated = 0
        auto_merged = 0
        review_created = 0
        for c in clusters:
            if c.needs_review or c.canonical_id is None or len(c.member_ids) < 2:
                continue
            rest = [i for i in c.member_ids if i != c.canonical_id]
            res = merge_entities(c.canonical_id, rest, db=db)
            relations_migrated += res.relations_migrated
            auto_merged += 1
        for c in clusters:
            if not c.needs_review:
                continue
            if _pending_merge_item_exists(db, project_id, c):
                continue
            db.add(ReviewItem(
                project_id=project_id,
                item_type="entity_merge",
                payload={"cluster": c.model_dump()},
                reason=f"相似度 {c.similarity} 落在人工复核区间（{c.layer} 层）",
                priority="medium",
                status="pending",
                suggested_action={"action": "merge", "canonical_id": c.canonical_id,
                                  "duplicate_ids": [i for i in c.member_ids
                                                    if i != c.canonical_id]},
            ))
            review_created += 1
        db.commit()
        return {"clusters_total": len(clusters), "auto_merged": auto_merged,
                "review_created": review_created,
                "relations_migrated": relations_migrated,
                "entities_scanned": len(ents)}
    except Exception:
        if own:
            db.rollback()
        raise
    finally:
        if own:
            db.close()


def _pending_merge_item_exists(db: Session, project_id: int, cluster: DuplicateCluster) -> bool:
    """同聚类（canonical+成员集合一致）的待审项已存在 → 不重复生成。"""
    rows = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "entity_merge",
        ReviewItem.status == "pending",
    ).all()
    want = set(cluster.member_ids)
    for r in rows:
        got = (r.payload or {}).get("cluster", {}).get("member_ids") or []
        if set(got) == want:
            return True
    return False


def run_conflict_detection(project_id: int, db: Optional[Session] = None,
                           types: Optional[list[str]] = None) -> dict:
    """冲突检测 + review_items 物化（04 §6：消解之后运行，减少假冲突）。"""
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        ents = db.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.is_class_node.is_(False),
            Entity.status != "merged",
        ).all()
        rels = db.query(Relation).filter(Relation.project_id == project_id).all()
        # 行表 relation 无 label 列：用全量实体索引（含类节点/已合并）富化主客体标签，
        # 否则按客体聚合的检测（RELATIONSHIP/AXIOM 函数性、基数）全部空转
        entity_index = {e.id: {"uri": str(e.uri or ""), "label": e.label or "",
                               "class_label": e.class_label or ""}
                        for e in db.query(Entity).filter(Entity.project_id == project_id).all()}
        rel_dicts = [{"subject_id": r.subject_id, "predicate": r.predicate,
                      "object_id": r.object_id,
                      "subject_label": (entity_index.get(r.subject_id) or {}).get("label", ""),
                      "object_label": (entity_index.get(r.object_id) or {}).get("label", "")}
                     for r in rels]
        object_properties = {}
        proj = db.query(Project).filter(Project.id == project_id).first()
        schema = (proj.graph_data or {}).get("schema") if proj else None
        if isinstance(schema, dict):
            object_properties = {op.get("label"): op for op in
                                 (schema.get("object_properties") or [])
                                 if isinstance(op, dict) and op.get("label")}
        else:
            schema = None
        want_types = tuple(
            t for t in (ConflictType(x) for x in
                        (types or ["value", "type", "relationship", "axiom"])))
        records = detect_conflicts(ents, rel_dicts, types=want_types,
                                   object_properties=object_properties, schema=schema,
                                   entity_index=entity_index)

        # 物化去重：同 entity_uri+property+values 的待审冲突项不重复生成
        existing = db.query(ReviewItem).filter(
            ReviewItem.project_id == project_id,
            ReviewItem.item_type.in_(["conflict_value", "conflict_type",
                                      "conflict_relationship", "conflict_axiom"]),
            ReviewItem.status == "pending",
        ).all()
        seen_keys = {
            ((r.payload or {}).get("entity_uri"), (r.payload or {}).get("property_name"),
             tuple(sorted((r.payload or {}).get("conflicting_values") or [])))
            for r in existing}
        created = 0
        skipped = 0
        per_type_count: dict[str, int] = {}
        for rec in records:
            item_type = _CONFLICT_ITEM_TYPE[rec.conflict_type]
            key = (rec.entity_uri, rec.property_name, tuple(sorted(rec.conflicting_values)))
            if key in seen_keys:
                skipped += 1
                continue
            if per_type_count.get(item_type, 0) >= MAX_REVIEW_ITEMS_PER_TYPE:
                skipped += 1
                continue
            per_type_count[item_type] = per_type_count.get(item_type, 0) + 1
            payload = rec.model_dump()
            if rec.conflict_type == ConflictType.TEMPORAL:
                payload["kind"] = "temporal"
            db.add(ReviewItem(
                project_id=project_id,
                item_type=item_type,
                payload=payload,
                reason=f"{rec.conflict_type.value} 冲突：{rec.property_name or ''}",
                priority="critical" if rec.severity == "critical" else
                         ("high" if rec.severity == "high" else "medium"),
                status="pending",
                suggested_action={"action": rec.recommended_action.value},
            ))
            seen_keys.add(key)
            created += 1
        db.commit()
        return {"detected": len(records), "review_created": created, "deduped": skipped,
                "entities_scanned": len(ents), "relations_scanned": len(rels)}
    except Exception:
        if own:
            db.rollback()
        raise
    finally:
        if own:
            db.close()
