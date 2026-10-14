# app/services/axiom_suggestion_service.py - 公理建议编排（O2-B8）
# adapters 层纯函数统计候选 → 本服务层物化 review_items(item_type='axiom_suggestion')；
# 批准落地在 review_service._approve_axiom_suggestion（写回画布公理），
# 写入后由 B6 SHACL / B2 schema_checks 复查（既有机检在下次检测/发布时自动覆盖）。
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.adapters.axiom_suggestions import suggest_axioms
from app.infrastructure.database import Entity, Project, Relation, ReviewItem

MAX_REVIEW_ITEMS = 50  # 单次物化上限（与其余审核物化同口径）


def _pending_suggestion_key(item) -> tuple:
    p = item.payload or {}
    kind = p.get("kind")
    target = tuple(sorted(p.get("predicates") or p.get("classes") or [])) or \
        (p.get("predicate"),)
    return (kind, target)


def run_axiom_suggestions(db: Session, project_id: int,
                          min_instances_note: str = "") -> dict:
    """统计推断公理候选并物化审核项（同 kind+target 待审去重）。"""
    ents = db.query(Entity).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status != "merged",
    ).all()
    rels = db.query(Relation).filter(Relation.project_id == project_id).all()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"candidates": 0, "review_created": 0, "deduped": 0, "skipped": "project_not_found"}
    schema = (proj.graph_data or {}).get("schema") \
        if isinstance(proj.graph_data, dict) else None

    rel_dicts = [{"subject_id": r.subject_id, "predicate": r.predicate,
                  "object_id": r.object_id,
                  "subject_label": "", "object_label": ""}
                 for r in rels]
    # 客体/主体标签富化（suggest_inverse_of 用 label 对）
    index = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == project_id).all()}
    for r in rel_dicts:
        r["subject_label"] = (index.get(r["subject_id"]) or {}).label or ""
        r["object_label"] = (index.get(r["object_id"]) or {}).label or ""

    candidates = suggest_axioms(ents, rel_dicts, schema)

    existing = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "axiom_suggestion",
        ReviewItem.status.in_(("pending", "claimed")),
    ).all()
    seen = {_pending_suggestion_key(r) for r in existing}

    created = deduped = 0
    for cand in candidates:
        kind = cand.get("kind")
        target = tuple(sorted(cand.get("predicates") or cand.get("classes") or [])) or \
            (cand.get("predicate"),)
        key = (kind, target)
        if key in seen:
            deduped += 1
            continue
        if created >= MAX_REVIEW_ITEMS:
            deduped += 1
            continue
        seen.add(key)
        db.add(ReviewItem(
            project_id=project_id,
            item_type="axiom_suggestion",
            payload=cand,
            reason=cand.get("summary") or f"公理建议（{kind}）",
            priority="medium",
            status="pending",
            suggested_action={"action": "declare_axiom", "kind": kind},
        ))
        created += 1
    db.commit()
    return {"candidates": len(candidates), "review_created": created,
            "deduped": deduped, "entities_scanned": len(ents),
            "relations_scanned": len(rels)}


__all__ = ["run_axiom_suggestions"]
