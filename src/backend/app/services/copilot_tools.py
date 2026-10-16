# app/services/copilot_tools.py - Copilot 本体只读工具集（O5-B23，docs/design/14 §5 S1）
# 七件工具全部本地实现（不调 LLM，毫秒级）：统计/检索/溯源走既有 service/adapters。
# 纯函数签名 (db, project, args) -> dict；异常由 tool_loop 回喂，模型可换路。
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.database import (
    CqEvaluation,
    DocumentChunk,
    Entity,
    Project,
    ProvenanceRecord,
    Relation,
    ReviewItem,
    UploadedDocument,
)

# 清单/列表类输出的截断上限（token 预算，14 §3.3）
MAX_LIST = 30
MAX_MATCH = 8


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _is_class_node(node: dict) -> bool:
    return str((node.get("data") or {}).get("type") or "") in ("owl:Class", "Class")


def _schema_of(project: Project) -> dict:
    return (project.graph_data or {}).get("schema") or {}


def _instance_counts(db: Session, project_id: int) -> dict[str, int]:
    rows = (db.query(Entity.class_label, func.count(Entity.id))
            .filter(Entity.project_id == project_id,
                    Entity.is_class_node.is_(False),
                    Entity.status != "merged")
            .group_by(Entity.class_label).all())
    return {str(k): int(v) for k, v in rows}


# ── 1. get_schema_stats：类/关系/实例/公理计数 + 清单 ──

def get_schema_stats(db: Session, project: Project, args: dict) -> dict:
    from app.services.axiom_store import list_axioms

    schema = _schema_of(project)
    classes = [c for c in (schema.get("classes") or [])
               if isinstance(c, dict) and c.get("label")]
    ops = [o for o in (schema.get("object_properties") or [])
           if isinstance(o, dict) and o.get("label")]
    inst_counts = _instance_counts(db, project.id)
    axioms = list_axioms(project)

    cls_list = [{
        "label": str(c["label"]),
        "parents": c.get("parent_classes") or ([c["sub_class_of"]] if c.get("sub_class_of") else []),
        "instance_count": inst_counts.get(str(c["label"]), 0),
    } for c in classes[:MAX_LIST]]
    op_list = [{
        "label": str(o["label"]),
        "domain": o.get("domain"), "range": o.get("range"),
        "functional": bool(o.get("functional")),
    } for o in ops[:MAX_LIST]]
    return {
        "class_count": len(classes),
        "object_property_count": len(ops),
        "instance_count": sum(inst_counts.values()),
        "axiom_count": len(axioms),
        "axioms": [{"kind": a.get("kind"), "summary": a.get("summary") or a.get("key")}
                   for a in axioms[:MAX_LIST]],
        "classes": cls_list,
        "object_properties": op_list,
        "truncated": {"classes": max(0, len(classes) - MAX_LIST),
                      "object_properties": max(0, len(ops) - MAX_LIST)},
    }


# ── 2. find_entity：label 模糊 + 向量双路 ──

def find_entity(db: Session, project: Project, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or MAX_MATCH), MAX_MATCH))
    if not query:
        return {"error": "bad_args", "message": "query 不能为空"}

    # 路一：行表 label 模糊（LIKE，快）
    like = f"%{query}%"
    rows = (db.query(Entity)
            .filter(Entity.project_id == project.id,
                    Entity.status != "merged",
                    Entity.label.like(like))
            .order_by(Entity.id).limit(limit).all())
    matches = [{
        "id": r.id, "label": r.label, "kind": "class" if r.is_class_node else "instance",
        "class_label": r.class_label,
    } for r in rows]

    # 画布 schema 类名补充命中（类可能未下行表）
    schema = _schema_of(project)
    hit_labels = {m["label"] for m in matches}
    for c in (schema.get("classes") or []):
        if not isinstance(c, dict) or not c.get("label"):
            continue
        label = str(c["label"])
        if query in label and label not in hit_labels and len(matches) < limit:
            matches.append({"id": None, "label": label, "kind": "class", "class_label": ""})
            hit_labels.add(label)

    # 路二：向量检索兜底（Milvus 不可用则静默跳过，注明口径）
    vector_note = "label 模糊命中"
    if len(matches) < limit:
        try:
            from app.adapters.retrieval import RetrievalQuery, retrieve
            sources = retrieve(RetrievalQuery(
                project_id=project.id, question=query, top_k=limit,
                use_graph=False, use_inferred=False), db=db)
            for s in sources:
                if len(matches) >= limit:
                    break
                text = (s.quote or "")[:60]
                matches.append({"id": None, "label": f"向量片段：{text}",
                                "kind": "vector_chunk", "class_label": ""})
            if matches:
                vector_note = "label 模糊 + 向量检索"
        except Exception as exc:  # noqa: BLE001 —— 向量路失败不影响标签路结果
            vector_note = f"label 模糊命中（向量路不可用：{str(exc)[:80]}）"
    if not matches:
        vector_note = "label 模糊与向量检索均无命中"
    return {"matches": matches, "note": vector_note}


def _find_entity_row(db: Session, project_id: int, args: dict) -> Entity | None:
    """按 entity_id 优先、label 模糊其次定位实体行。"""
    eid = args.get("entity_id")
    if eid:
        return db.query(Entity).filter(Entity.id == int(eid),
                                       Entity.project_id == project_id).first()
    label = str(args.get("label") or "").strip()
    if not label:
        return None
    return (db.query(Entity)
            .filter(Entity.project_id == project_id, Entity.label == label).first()
            or db.query(Entity)
            .filter(Entity.project_id == project_id, Entity.label.like(f"%{label}%"))
            .order_by(Entity.id).first())


# ── 3. trace_relation：某实例的关系链 ──

def trace_relation(db: Session, project: Project, args: dict) -> dict:
    ent = _find_entity_row(db, project.id, args)
    if ent is None:
        return {"error": "not_found",
                "message": f"未找到实体：{args.get('entity_id') or args.get('label')}"}
    subj_rel = (db.query(Relation, Entity)
                .join(Entity, Relation.object_id == Entity.id)
                .filter(Relation.subject_id == ent.id).all())
    obj_rel = (db.query(Relation, Entity)
               .join(Entity, Relation.subject_id == Entity.id)
               .filter(Relation.object_id == ent.id).all())
    return {
        "entity": {"id": ent.id, "label": ent.label, "class_label": ent.class_label},
        "outgoing": [{"predicate": r.predicate, "object": o.label,
                      "object_class": o.class_label}
                     for r, o in subj_rel[:MAX_LIST]],
        "incoming": [{"predicate": r.predicate, "subject": s.label,
                      "subject_class": s.class_label}
                     for r, s in obj_rel[:MAX_LIST]],
        "outgoing_count": len(subj_rel), "incoming_count": len(obj_rel),
    }


# ── 4. get_scorecard：记分卡核心指标（O2-F7 口径） ──

def get_scorecard(db: Session, project: Project, args: dict) -> dict:
    from app.services.scorecard_service import compute_scorecard

    card = compute_scorecard(db, project.id, project.graph_data or {})
    keep = ("consistency_violations", "cycles", "dangling_refs",
            "evidence_closure_rate", "instance_coverage", "orphan_count",
            "naming_consistency", "class_count", "instance_count",
            "pending_reviews")
    return {k: card.get(k) for k in keep}


# ── 5. get_conflicts：推理冲突 + SHACL 违例现况 ──

def get_conflicts(db: Session, project: Project, args: dict) -> dict:
    conflict_types = ("conflict_value", "conflict_type", "conflict_relationship",
                      "conflict_axiom")
    items = (db.query(ReviewItem)
             .filter(ReviewItem.project_id == project.id,
                     ReviewItem.item_type.in_(conflict_types),
                     ReviewItem.status.in_(("pending", "claimed")))
             .order_by(ReviewItem.id.desc()).limit(10).all())
    open_conflicts = [{"id": i.id, "type": i.item_type, "reason": i.reason,
                       "summary": str((i.payload or {}).get("summary")
                                      or (i.payload or {}).get("message") or "")[:200]}
                      for i in items]

    from app.services.reasoning_service import get_latest_results
    res = get_latest_results(db, project.id, limit=200)
    errors = [it for it in res.get("items", [])
              if (it.get("predicate_label") or "").lower() in ("error", "错误")]
    inferred = [{"subject": it["subject_label"], "predicate": it["predicate_label"],
                 "object": it["object_label"]} for it in errors[:10]]

    shacl = {"violations": None, "note": "未运行"}
    try:
        from app.adapters.shacl_checks import run_shacl_summary
        ents = (db.query(Entity).filter(Entity.project_id == project.id).all())
        rels = (db.query(Relation).filter(Relation.project_id == project.id).all())
        ents_d = [{"id": e.id, "label": e.label, "class_label": e.class_label,
                   "props": e.props or {}} for e in ents]
        rels_d = [{"subject_id": r.subject_id, "predicate": r.predicate,
                   "object_id": r.object_id} for r in rels]
        summary = run_shacl_summary(ents_d, rels_d, _schema_of(project))
        shacl = {"violations": summary.get("violations", 0),
                 "by_kind": summary.get("by_kind", {}), "note": "pyshacl 实测"}
    except Exception as exc:  # noqa: BLE001 —— SHACL 失败不阻断冲突概览
        shacl = {"violations": None, "note": f"SHACL 运行失败：{str(exc)[:80]}"}

    return {"open_review_conflicts": open_conflicts,
            "open_conflict_count": len(open_conflicts),
            "reasoning_errors": inferred,
            "reasoning_batch_id": res.get("batch_id"),
            "shacl": shacl}


# ── 6. get_cq_results：最近 CQ 评测 ──

def get_cq_results(db: Session, project: Project, args: dict) -> dict:
    row = (db.query(CqEvaluation)
           .filter(CqEvaluation.project_id == project.id)
           .order_by(CqEvaluation.id.desc()).first())
    if row is None:
        return {"error": "no_evaluation",
                "message": "本项目尚未运行 CQ 评测（CQ 管理面板可手动触发）"}
    return {"id": row.id, "trigger": row.trigger, "mode": row.mode,
            "total_cq": row.total_cq, "answerable_count": row.answerable_count,
            "coverage_rate": float(row.coverage_rate or 0),
            "results": (row.results or [])[:MAX_LIST],
            "created_at": row.created_at.isoformat() if row.created_at else None}


# ── 7. get_evidence：按实体/类/关系拉 chunk 原文 ──

def get_evidence(db: Session, project: Project, args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 3), 5))
    doc_ids = {d.id for d in db.query(UploadedDocument.id)
               .filter(UploadedDocument.project_id == project.id,
                       UploadedDocument.deleted_at.is_(None)).all()}
    anchors: list[dict] = []
    target = str(args.get("target") or "")

    if args.get("entity_id") or args.get("label") or target:
        ent = _find_entity_row(db, project.id, args) if (args.get("entity_id") or args.get("label")) else None
        if ent is not None:
            target = ent.label
            provs = (db.query(ProvenanceRecord)
                     .filter(ProvenanceRecord.project_id == project.id,
                             ProvenanceRecord.target_type == "entity",
                             ProvenanceRecord.target_id == ent.id)
                     .order_by(ProvenanceRecord.id).limit(limit).all())
            anchors = [{"source_document_id": p.source_document_id,
                        "chunk_index": p.chunk_index,
                        "evidence_text": p.evidence_text}
                       for p in provs]

    # 类/关系：schema 证据锚点兜底（B9 闭合口径）
    if not anchors and target:
        schema = _schema_of(project)
        for coll in ("classes", "object_properties"):
            for obj in (schema.get(coll) or []):
                if isinstance(obj, dict) and _norm(obj.get("label")) == _norm(target):
                    ev = obj.get("evidence")
                    if isinstance(ev, dict) and ev.get("source_document_id") is not None:
                        anchors.append({"source_document_id": ev.get("source_document_id"),
                                        "chunk_index": ev.get("source_chunk_no"),
                                        "evidence_text": ev.get("quote") or ev.get("evidence_text")})
                    if anchors:
                        break
            if anchors:
                break

    if not anchors:
        return {"error": "no_evidence",
                "message": f"「{target or args}」没有可用的证据锚点（provenance/schema evidence 均为空）"}

    out = []
    for a in anchors[:limit]:
        doc_id = a.get("source_document_id")
        chunk_no = a.get("chunk_index")
        text = a.get("evidence_text") or ""
        if doc_id in doc_ids and chunk_no is not None:
            chunk = (db.query(DocumentChunk)
                     .filter(DocumentChunk.document_id == doc_id,
                             DocumentChunk.chunk_index == int(chunk_no)).first())
            if chunk is not None:
                text = chunk.text[:500] if not text else f"{text}\n（chunk 原文）{chunk.text[:400]}"
        out.append({"target": target, "source_document_id": doc_id,
                    "chunk_index": chunk_no, "evidence": text[:600],
                    "alive": doc_id in doc_ids})
    return {"target": target, "evidence": out, "count": len(out)}


def build_toolbox(db: Session, project: Project) -> dict[str, dict]:
    """组装 tool-loop 工具表：{name: {description, parameters, fn}}，fn 闭包 db/project。"""
    def wrap(fn):
        return lambda args: fn(db, project, args or {})

    return {
        "get_schema_stats": {
            "description": "获取本体结构统计：类/对象属性/实例/公理的计数与清单（含每类实例数、domain/range）",
            "parameters": {"type": "object", "properties": {}},
            "fn": wrap(get_schema_stats),
        },
        "find_entity": {
            "description": "按名称模糊检索实体（类与实例），label 模糊 + 向量双路",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "名称关键词"},
                "limit": {"type": "integer", "description": "返回上限，默认 8"}},
                "required": ["query"]},
            "fn": wrap(find_entity),
        },
        "trace_relation": {
            "description": "查某实体（按 label 或 entity_id）的全部关系链（出边/入边及对端实体）",
            "parameters": {"type": "object", "properties": {
                "label": {"type": "string", "description": "实体名称（entity_id 缺省时用）"},
                "entity_id": {"type": "integer", "description": "实体行 id（优先）"}}},
            "fn": wrap(trace_relation),
        },
        "get_scorecard": {
            "description": "本体健康记分卡核心指标：一致性违例/环/悬空引用/证据闭合率/实例覆盖率/孤立类等",
            "parameters": {"type": "object", "properties": {}},
            "fn": wrap(get_scorecard),
        },
        "get_conflicts": {
            "description": "冲突现况：待处理冲突审核项 + 最新 owlrl 推理 error + SHACL 违例统计",
            "parameters": {"type": "object", "properties": {}},
            "fn": wrap(get_conflicts),
        },
        "get_cq_results": {
            "description": "最近一次 Competency Question 评测结果（跑通率/证据覆盖率）",
            "parameters": {"type": "object", "properties": {}},
            "fn": wrap(get_cq_results),
        },
        "get_evidence": {
            "description": "按实体（label/entity_id）或类/关系名拉取证据原文（chunk 引文），拿不到时明确报缺",
            "parameters": {"type": "object", "properties": {
                "label": {"type": "string", "description": "实体/类/关系名称"},
                "entity_id": {"type": "integer", "description": "实体行 id（优先）"},
                "limit": {"type": "integer", "description": "证据条数上限，默认 3"}}},
            "fn": wrap(get_evidence),
        },
    }


__all__ = ["build_toolbox", "get_schema_stats", "find_entity", "trace_relation",
           "get_scorecard", "get_conflicts", "get_cq_results", "get_evidence"]
