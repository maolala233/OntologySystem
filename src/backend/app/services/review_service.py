# app/services/review_service.py - 审核队列状态机与裁决落地（docs/design/04 §7.2 / 03 §10，M3-6）
# pending ──claim──▶ claimed ──decide──▶ approved | rejected | edited
# 裁决落地即业务：entity_merge.approve→合并执行；new_class.approve→TBox 回写；
# conflict_*/low_confidence_*/missing_evidence.approve→留痕关闭。
# 每次裁决写 audit_logs(review.decide) + outbox 事件（消费链 M4 接线）。
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.audit_service import log_action, put_outbox

# 裁决动作
ACTIONS = ("approve", "reject", "edit")
# 发布门禁口径（04 §10）：pending/claimed 的 critical/high 阻断发布
PUBLISH_BLOCKING_STATUSES = ("pending", "claimed")
PUBLISH_BLOCKING_PRIORITIES = ("critical", "high")


class ReviewStateError(Exception):
    """状态机违例（错误码层转 409）。"""

    def __init__(self, msg: str, code: str = "INVALID_REVIEW_STATE"):
        super().__init__(msg)
        self.code = code


def claim_item(item, user, db: Session):
    """认领 pending→claimed（防重复裁决：他人已认领→409 ALREADY_CLAIMED）。"""
    if item.status != "pending":
        raise ReviewStateError(
            f"仅 pending 可认领（当前 {item.status}）",
            code="ALREADY_CLAIMED" if item.status == "claimed" else "INVALID_REVIEW_STATE")
    item.status = "claimed"
    item.assigned_to = user.id
    db.commit()
    return item


def decide_item(item, user, action: str, edited_payload: dict | None = None,
                note: str | None = None, db: Session = None, ip: str | None = None) -> dict:
    """裁决（04 §7.2）：pending/claimed → approved|rejected|edited，并落地对应业务。"""
    if action not in ACTIONS:
        raise ReviewStateError(f"action 取值限 {ACTIONS}", code="INVALID_ACTION")
    if item.status not in ("pending", "claimed"):
        raise ReviewStateError(f"终态不可再裁决（当前 {item.status}）")
    if item.status == "claimed" and item.assigned_to not in (None, user.id):
        raise ReviewStateError("已被他人认领", code="ALREADY_CLAIMED")

    effect: dict[str, Any] = {}
    payload = item.payload or {}
    if action == "edit" and edited_payload:
        payload = edited_payload
        item.payload = edited_payload
    elif action == "approve" and edited_payload:
        # approve 可携带人工选择项（如 conflict_type 的 chosen_class），与原 payload 合并后落地
        payload = {**(item.payload or {}), **edited_payload}
    if action == "approve":
        effect = _apply_approval(item, user, payload, db)

    item.status = {"approve": "approved", "reject": "rejected", "edit": "edited"}[action]
    item.decided_by = user.id
    item.decided_at = item.decided_at or datetime.datetime.utcnow()
    if note:
        item.result_ref = {**(item.result_ref or {}), "note": note}
    put_outbox(db, "project", item.project_id,
               f"review.{action}",
               {"review_id": item.id, "item_type": item.item_type,
                "decided_by": user.id, "effect": effect})
    log_action(db, user.id, f"review.{action}", "review_item", item.id,
               {"item_type": item.item_type, "project_id": item.project_id,
                "effect": effect, "note": note}, ip=ip)
    db.commit()
    return {"id": item.id, "status": item.status, "effect": effect}


def _apply_approval(item, user, payload: dict, db: Session) -> dict:
    """approve 的业务落地（按 item_type 分派）。"""
    kind = item.item_type
    if kind == "entity_merge":
        return _approve_entity_merge(item, user, payload, db)
    if kind == "new_class":
        return _approve_new_class(item, user, payload, db)
    if kind == "conflict_type":
        return _approve_conflict_type(item, user, payload, db)
    # conflict_value/conflict_relationship / low_confidence_* / missing_evidence：
    # approve 即"确认处理"，留痕关闭
    return {"closed": kind}


def _approve_conflict_type(item, user, payload: dict, db: Session) -> dict:
    """类型冲突裁决：人工选定正确类别 → 回写画布节点 class_label + rdf:type 边。

    行表（entities/relations/provenance）由 graph_data 双写监听器自动重建。
    未选择类别（chosen_class 缺失，如批量通过）时退化为留痕关闭。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project

    chosen = str(payload.get("chosen_class") or "").strip()
    sources = payload.get("sources") or []
    if not chosen or not sources:
        return {"closed": "conflict_type"}
    proj = db.query(Project).filter(Project.id == item.project_id).first()
    if proj is None:
        return {"skipped": "项目不存在"}
    gd = proj.graph_data or {}
    nodes = gd.get("nodes") or []

    # sources 的 uri 尾段即画布节点 id：urn:onto:{pid}:entity/{canvas_id}
    by_canvas: dict[str, dict] = {}
    for s in sources:
        uri = str(s.get("uri") or "")
        cid = uri.rsplit(":entity/", 1)[-1] if ":entity/" in uri else ""
        if cid:
            by_canvas[cid] = s

    reclassified = 0
    for n in nodes:
        s = by_canvas.get(str(n.get("id")))
        if s and str(s.get("class") or "") != chosen:
            data = n.get("data") or {}
            data["class_label"] = chosen
            n["data"] = data
            reclassified += 1

    # rdf:type 边跟随新类别（目标类节点按 label 匹配，兼容 owl:Class/Class 两种历史取值）
    class_node = next((n for n in nodes
                       if str((n.get("data") or {}).get("label") or "") == chosen
                       and (n.get("data") or {}).get("type") in ("owl:Class", "Class")), None)
    retyped = 0
    if class_node is not None:
        for e in gd.get("edges") or []:
            d = e.get("data") or {}
            s = by_canvas.get(str(e.get("source")))
            if s and str(s.get("class") or "") != chosen and d.get("relation") == "instance_of":
                e["target"] = class_node.get("id")
                d["label"] = "rdf:type"
                retyped += 1

    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()  # 监听器双写在 commit 时触发（decide_item 统一 commit）
    return {"reclassified": reclassified, "retyped_edges": retyped, "chosen_class": chosen}


def _approve_entity_merge(item, user, payload: dict, db: Session) -> dict:
    """合并执行（可逆，merge_entities 自带留痕）。"""
    from app.adapters.resolution import merge_entities

    ref = item.result_ref or payload or {}
    canonical_id = ref.get("canonical_id") or payload.get("canonical_id")
    duplicate_ids = ref.get("merged_ids") or payload.get("duplicate_ids") or []
    if not canonical_id or not duplicate_ids:
        return {"skipped": "payload 缺 canonical_id/duplicate_ids"}
    res = merge_entities(int(canonical_id), [int(i) for i in duplicate_ids], db=db)
    return {"merged": len(res.merged_ids), "relations_migrated": res.relations_migrated}


def _approve_new_class(item, user, payload: dict, db: Session) -> dict:
    """类回写 TBox（03 §10：新类批准 → 落 graph_data.schema + 画布节点）。"""
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project

    label = payload.get("label") or ""
    if not label:
        return {"skipped": "payload 缺 label"}
    proj = db.query(Project).filter(Project.id == item.project_id).first()
    if proj is None:
        return {"skipped": "项目不存在"}
    gd = proj.graph_data or {}
    gd.setdefault("schema", {}).setdefault("classes", [])
    gd["schema"].setdefault("object_properties", [])
    existing = {c.get("label") for c in gd["schema"]["classes"]}
    if label not in existing:
        gd["schema"]["classes"].append({
            "label": label,
            "definition": payload.get("definition") or f"审核通过的实例新类型（{label}）",
            "properties": [], "parent": None, "aliases": []})
        nodes = gd.setdefault("nodes", [])
        ids = {n.get("id") for n in nodes}
        node_id = f"class_{abs(hash(label)) % (10 ** 12):012d}"
        while node_id in ids:  # 碰撞兜底
            node_id += "x"
        nodes.append({"id": node_id, "type": "default", "position": {"x": 0, "y": 0},
                      "data": {"label": label, "type": "Class", "approved_from": "new_class"},
                      "style": {"background": "#eff6ff", "border": "2px solid #3b82f6"}})
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()  # 监听器双写在 commit 时触发（decide_item 统一 commit）
    return {"class_upserted": label}


def batch_decide(project_id: int, item_ids: list[int], action: str, user, db: Session,
                 note: str | None = None) -> dict:
    """批量裁决（04 §7.2）：同类型同动作 ≤50；entity_merge 批量仅 approve 且 similarity≥0.75。"""
    from app.infrastructure.database import ReviewItem

    if action not in ACTIONS:
        raise ReviewStateError(f"action 取值限 {ACTIONS}", code="INVALID_ACTION")
    if not item_ids or len(item_ids) > 50:
        raise ReviewStateError("批量裁决 1-50 条", code="BATCH_SIZE_INVALID")
    rows = (db.query(ReviewItem)
            .filter(ReviewItem.id.in_(item_ids),
                    ReviewItem.project_id == project_id).all())
    if len(rows) != len(set(item_ids)):
        raise ReviewStateError("存在不属于本项目的审核项", code="REVIEW_NOT_FOUND")
    types = {r.item_type for r in rows}
    if len(types) > 1:
        raise ReviewStateError("批量裁决要求同类型", code="BATCH_TYPE_MISMATCH")
    if rows[0].item_type == "entity_merge":
        if action != "approve":
            raise ReviewStateError("entity_merge 批量仅允许 approve", code="BATCH_ACTION_INVALID")
        for r in rows:
            sim = float((r.payload or {}).get("similarity") or 0)
            if sim < 0.75:
                raise ReviewStateError(
                    f"item#{r.id} similarity={sim} < 0.75 不允许批量合并",
                    code="BATCH_SIMILARITY_TOO_LOW")
    results = []
    for r in rows:
        results.append(decide_item(r, user, action, note=note, db=db))
    return {"batch": len(results),
            "approved": sum(1 for x in results if x["status"] == "approved"),
            "rejected": sum(1 for x in results if x["status"] == "rejected"),
            "edited": sum(1 for x in results if x["status"] == "edited")}


def publish_blockers(db: Session, project_id: int) -> list[dict]:
    """发布门禁（04 §10）：pending/claimed 的 critical/high 审核项。"""
    from app.infrastructure.database import ReviewItem

    rows = (db.query(ReviewItem)
            .filter(ReviewItem.project_id == project_id,
                    ReviewItem.status.in_(PUBLISH_BLOCKING_STATUSES),
                    ReviewItem.priority.in_(PUBLISH_BLOCKING_PRIORITIES)).all())
    return [{"id": r.id, "item_type": r.item_type, "priority": r.priority,
             "status": r.status, "reason": r.reason} for r in rows]


__all__ = [
    "ACTIONS", "ReviewStateError", "claim_item", "decide_item", "batch_decide",
    "publish_blockers",
]
