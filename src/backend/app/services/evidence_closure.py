# app/services/evidence_closure.py - TBox 证据闭合检查（O2-B9）
# 发布门禁第四闸：TBox 对象（类/对象属性）必须有证据锚点；
# 锚点引用的 chunk 已不存在（文档被删）= 悬空 → 阻断发布；
# 完全无锚点 → warning（存量数据兼容，逐步补齐）。
from __future__ import annotations

from sqlalchemy.orm import Session


def _class_type_ok(t: str) -> bool:
    return t in ("owl:Class", "Class")


def tbox_evidence_closure(db: Session, project_id: int, gd: dict) -> dict:
    """证据闭合检查：{passed, dangling, missing, checked}。

    锚点来源（优先级）：
      1. schema 权威键：classes[].evidence / object_properties[].evidence（O2-B9 批准时写入）
      2. 画布：类节点 / TBox 边（类↔类，relation≠rdf:type）的
         source_document_id + source_chunk_no（抽取时写入）
    悬空判定：锚点引用的 (document_id, chunk_no) 在 document_chunks 中不存在。
    """
    from app.infrastructure.database import DocumentChunk, UploadedDocument

    gd = gd or {}
    schema = gd.get("schema") or {}

    # (doc_id, chunk_no) 存活集合
    rows = (db.query(DocumentChunk.document_id, DocumentChunk.chunk_index)
            .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
            .filter(UploadedDocument.project_id == project_id,
                    UploadedDocument.deleted_at.is_(None)).all())
    alive = {(int(d), int(c)) for d, c in rows}

    dangling: list[str] = []
    missing: list[str] = []
    checked = 0

    def _check_anchor(name: str, anchor) -> None:
        nonlocal checked
        checked += 1
        # 锚点可以是单个 dict 或列表（S16 公理支持多锚点）
        if isinstance(anchor, list):
            for a in anchor:
                _check_anchor(name, a)
            return
        # 存量数据 evidence 可能是字符串引文（无 doc/chunk 锚点）→ 按缺失计（不阻断）
        if not isinstance(anchor, dict):
            missing.append(name)
            return
        try:
            key = (int(anchor.get("source_document_id")),
                   int(anchor.get("source_chunk_no")))
        except (TypeError, ValueError):
            missing.append(name)
            return
        if key not in alive:
            dangling.append(name)

    # ── 类：schema 权威键优先，画布节点 source 字段兜底 ──
    schema_classes = {str(c.get("label") or ""): c
                      for c in (schema.get("classes") or []) if isinstance(c, dict)}
    for n in gd.get("nodes") or []:
        d = n.get("data") or {}
        if not _class_type_ok(str(d.get("type") or "")):
            continue
        label = str(d.get("label") or "")
        if not label:
            continue
        anchor = (schema_classes.get(label) or {}).get("evidence") or \
            ({"source_document_id": d.get("source_document_id"),
              "source_chunk_no": d.get("source_chunk_no")}
             if d.get("source_document_id") is not None
             and d.get("source_chunk_no") is not None else None)
        _check_anchor(f"类「{label}」", anchor)

    # ── 对象属性：schema 权威键；无锚点时查画布 TBox 边 ──
    canvas_edges_by_rel: dict[str, dict] = {}
    node_ids = {str(n.get("id")) for n in (gd.get("nodes") or [])
                if _class_type_ok(str((n.get("data") or {}).get("type") or ""))}
    for e in gd.get("edges") or []:
        d = e.get("data") or {}
        relation = str(d.get("relation") or "")
        if (relation in ("rdf:type", "instance_of", "subClassOf", "subclass_of", "")
                or str(e.get("source")) not in node_ids
                or str(e.get("target")) not in node_ids):
            continue
        canvas_edges_by_rel.setdefault(relation, d)
    for op_ in schema.get("object_properties") or []:
        if not isinstance(op_, dict) or not op_.get("label"):
            continue
        label = str(op_["label"])
        anchor = op_.get("evidence")
        if not anchor:
            d = canvas_edges_by_rel.get(label) or {}
            if d.get("source_document_id") is not None \
                    and d.get("source_chunk_no") is not None:
                anchor = {"source_document_id": d["source_document_id"],
                          "source_chunk_no": d["source_chunk_no"]}
        _check_anchor(f"关系「{label}」", anchor)

    # ── 公理（S16-Step2）：axiom_provenance[key].evidence 锚点。无登记=missing
    #    （grandfather 不阻断）；有登记但锚点悬空（引用的 chunk 已删）=dangling 阻断。
    for key, meta in (schema.get("axiom_provenance") or {}).items():
        if not isinstance(meta, dict):
            continue
        _check_anchor(f"公理「{key}」", meta.get("evidence"))

    return {"passed": not dangling, "dangling": dangling, "missing": missing,
            "checked": checked}


__all__ = ["tbox_evidence_closure"]
