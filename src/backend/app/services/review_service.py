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
    if kind == "new_predicate":
        return _approve_new_predicate(item, user, payload, db)
    if kind == "conflict_type":
        return _approve_conflict_type(item, user, payload, db)
    if kind == "axiom_suggestion":
        return _approve_axiom_suggestion(item, user, payload, db)
    if kind == "cq_suggestion":
        return _approve_cq_suggestion(item, user, payload, db)
    if kind == "schema_match_suggestion":
        return _approve_schema_match(item, user, payload, db)
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
    """类回写 TBox（03 §10：新类批准 → 落 graph_data.schema + 画布节点）。

    B9：批准时把抽取时的 chunk 证据固化为 TBox 锚点（schema 类 evidence 字段），
    供证据闭合发布检查（悬空阻断）与画布/审核界面证据视图。
    """
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
        anchor = _evidence_anchor(db, item.project_id,
                                  payload.get("chunk_indices") or [], label)
        entry = {
            "label": label,
            "definition": payload.get("definition") or f"审核通过的实例新类型（{label}）",
            "properties": [], "parent": None, "aliases": []}
        if anchor:
            entry["evidence"] = anchor
        gd["schema"]["classes"].append(entry)
        nodes = gd.setdefault("nodes", [])
        ids = {n.get("id") for n in nodes}
        node_id = f"class_{abs(hash(label)) % (10 ** 12):012d}"
        while node_id in ids:  # 碰撞兜底
            node_id += "x"
        node_data: dict = {"label": label, "type": "Class", "approved_from": "new_class"}
        if anchor:
            node_data["source_document_id"] = anchor["source_document_id"]
            node_data["source_chunk_no"] = anchor["source_chunk_no"]
            node_data["source_quote"] = anchor.get("quote") or ""
            if "char_start" in anchor:
                node_data["source_char_start"] = anchor["char_start"]
                node_data["source_char_end"] = anchor["char_end"]
        nodes.append({"id": node_id, "type": "default", "position": {"x": 0, "y": 0},
                      "data": node_data,
                      "style": {"background": "#eff6ff", "border": "2px solid #3b82f6"}})
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()  # 监听器双写在 commit 时触发（decide_item 统一 commit）
    return {"class_upserted": label}


def _approve_new_predicate(item, user, payload: dict, db: Session) -> dict:
    """谓词回写 TBox（O1-B3：新关系批准 → 落 graph_data.schema.object_properties）。

    payload 可带裁决时编辑的 domain/range（缺省 None，由用户后续在画布补全）；
    仅写 schema 权威键，画布边需两端类节点，由用户在画布上拖接。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project

    label = str(payload.get("label") or "").strip()
    if not label:
        return {"skipped": "payload 缺 label"}
    proj = db.query(Project).filter(Project.id == item.project_id).first()
    if proj is None:
        return {"skipped": "项目不存在"}
    gd = proj.graph_data or {}
    gd.setdefault("schema", {}).setdefault("object_properties", [])
    existing = {str(op_.get("label") or "") for op_ in gd["schema"]["object_properties"]}
    if label not in existing:
        # B9：新谓词批准时固化 chunk 证据锚点（schema 级 evidence 字段）
        anchor = _evidence_anchor(db, item.project_id,
                                  payload.get("chunk_indices") or [], label)
        entry = {
            "label": label,
            "domain": payload.get("domain") or None,
            "range": payload.get("range") or None,
            "definition": payload.get("definition") or f"审核通过的实例新关系（{label}）",
        }
        if anchor:
            entry["evidence"] = anchor
        gd["schema"]["object_properties"].append(entry)
        proj.graph_data = gd
        flag_modified(proj, "graph_data")
        db.flush()  # 监听器双写在 commit 时触发（decide_item 统一 commit）
    return {"predicate_upserted": label}


def _evidence_anchor(db: Session, project_id: int, chunk_indices: list,
                     locate_text: str) -> dict | None:
    """B9：chunk 全量序号 → 证据锚点 {source_document_id, source_chunk_no, quote, char_*}。

    chunk_indices 是抽取时 enumerate 的全量序号（跨文档拼接序）；
    locate_text 在 chunk 原文中逐字定位（locate_quote），失败只落 chunk 定位不落字符区间。
    """
    from app.adapters.provenance import locate_quote
    from app.infrastructure.database import DocumentChunk, UploadedDocument

    idx = next((int(i) for i in (chunk_indices or []) if str(i).isdigit()), None)
    if idx is None or idx < 0:
        return None
    rows = (db.query(DocumentChunk, UploadedDocument.id)
            .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
            .filter(UploadedDocument.project_id == project_id,
                    UploadedDocument.deleted_at.is_(None))
            .order_by(UploadedDocument.id, DocumentChunk.chunk_index).all())
    if idx >= len(rows):
        return None  # 悬空：抽取后文档被删，锚点不落（发布闭合检查会另行告警）
    chunk_row, doc_id = rows[idx]
    anchor = {"source_document_id": int(doc_id), "source_chunk_no": int(chunk_row.chunk_index)}
    text = str(chunk_row.text or "")
    pos = locate_quote(text, str(locate_text or "")) if locate_text else None
    if pos is not None:
        anchor["quote"] = text[pos[0]:pos[1]]
        anchor["char_start"], anchor["char_end"] = pos
    return anchor


def _approve_axiom_suggestion(item, user, payload: dict, db: Session) -> dict:
    """公理建议批准 → 写入画布公理（O2-B8）。

    payload.kind ∈ functional | inverse_of | disjoint_with；
    写入 schema 权威键 + 画布 node/edge data.axioms（导出 TTL 消费），
    写入后 B6 SHACL / B2 schema_checks 在下次检测与发布门禁自动复查。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project

    kind = str(payload.get("kind") or "").strip()
    proj = db.query(Project).filter(Project.id == item.project_id).first()
    if proj is None:
        return {"skipped": "项目不存在"}
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    ops_by_label = {str(op_.get("label") or ""): op_
                    for op_ in (schema.get("object_properties") or [])
                    if isinstance(op_, dict) and op_.get("label")}
    classes_by_label = {str(c.get("label") or ""): c
                        for c in (schema.get("classes") or [])
                        if isinstance(c, dict) and c.get("label")}

    def _mark_edges(predicate: str, mutator) -> int:
        n = 0
        for e in gd.get("edges") or []:
            d = e.get("data") or {}
            if str(d.get("relation") or "") == predicate:
                ax = d.setdefault("axioms", {})
                mutator(ax)
                e["data"] = d
                n += 1
        return n

    if kind == "functional":
        label = str(payload.get("predicate") or "").strip()
        op_ = ops_by_label.get(label)
        if op_ is None:
            return {"skipped": f"谓词不在 TBox: {label}"}
        op_["functional"] = True
        edges_marked = _mark_edges(label, lambda ax: ax.update(functional=True))
    elif kind == "inverse_of":
        p, q = (str(x or "").strip() for x in (payload.get("predicates") or ["", ""])[:2])
        op_p, op_q = ops_by_label.get(p), ops_by_label.get(q)
        if op_p is None or op_q is None or p == q:
            return {"skipped": f"互逆谓词不在 TBox: {p} / {q}"}
        op_p["inverse_of"] = q
        op_q["inverse_of"] = p
        edges_marked = (_mark_edges(p, lambda ax: ax.update(inverse_of=q))
                        + _mark_edges(q, lambda ax: ax.update(inverse_of=p)))
    elif kind == "disjoint_with":
        a, b = (str(x or "").strip() for x in (payload.get("classes") or ["", ""])[:2])
        ca, cb = classes_by_label.get(a), classes_by_label.get(b)
        if ca is None or cb is None or a == b:
            return {"skipped": f"互斥类不在 TBox: {a} / {b}"}
        ca.setdefault("disjoint_with", [])
        cb.setdefault("disjoint_with", [])
        if b not in ca["disjoint_with"]:
            ca["disjoint_with"].append(b)
        if a not in cb["disjoint_with"]:
            cb["disjoint_with"].append(a)
        edges_marked = 0
        # 画布类节点 data.axioms.disjoint_with 同步（画布是活编辑面）
        for n in gd.get("nodes") or []:
            d = n.get("data") or {}
            lbl = str(d.get("label") or "")
            if lbl in (a, b) and d.get("type") in ("owl:Class", "Class"):
                ax = d.setdefault("axioms", {})
                peers = ax.setdefault("disjoint_with", [])
                peer = b if lbl == a else a
                if peer not in peers:
                    peers.append(peer)
                n["data"] = d
    else:
        return {"skipped": f"未知公理建议类型: {kind}"}

    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"axiom_declared": kind, "edges_marked": edges_marked}


def _approve_cq_suggestion(item, user, payload: dict, db: Session) -> dict:
    """进化建议批准（O3-B16）→ suggested_cq 转正为 CompetencyQuestion 资产。

    B12 抽取前 guidance 注入 / 抽取后自动评测 / B14 门控 GT 均以该资产为输入。
    """
    from app.infrastructure.database import CompetencyQuestion

    question = str(payload.get("suggested_cq") or "").strip()
    if not question:
        return {"skipped": "建议 CQ 为空"}
    exists = (db.query(CompetencyQuestion)
              .filter(CompetencyQuestion.project_id == item.project_id,
                      CompetencyQuestion.enabled.is_(True))
              .all())
    if any(c.question.strip().lower() == question.lower() for c in exists):
        return {"skipped": "同名 CQ 已存在"}
    cq = CompetencyQuestion(project_id=item.project_id, question=question,
                            source="evolution", created_by=user.id,
                            sort_order=len(exists))
    db.add(cq)
    db.flush()
    return {"cq_created": cq.id}


def _approve_schema_match(item, user, payload: dict, db: Session) -> dict:
    """对齐建议批准（O3-B17）→ 导入类 label 注入目标类别名（match_class 即刻生效）。"""
    from app.services.ontology_match import apply_mapping

    return apply_mapping(db, _project_by_id(item.project_id, db), payload)


def _project_by_id(project_id: int, db: Session):
    from app.infrastructure.database import Project

    return db.query(Project).filter(Project.id == project_id).first()


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
