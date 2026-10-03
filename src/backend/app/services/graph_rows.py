# app/services/graph_rows.py - graph_data blob → 行表双写（M3-3，docs/design/02 §4）
# 三阶段迁移 S1：行表建好、双写（blob 为准，行表对账）、读方仍走 blob。
# sync_project_rows 是唯一拆行入口：幂等全量重建（删旧插新），被
# before_flush 监听器（同事务，覆盖全部 graph_data 写入点）与回填任务共用。

from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.resolution import normalize_label
from app.core.logging import logger
from app.core.security import sha256_hex
from app.infrastructure.database import (
    Entity, ProvenanceRecord, Project, Relation, UploadedDocument,
)

# TBox/ABox 判据（与 extractor/画布/inject_service 的现有约定对齐）
CLASS_TYPES = {"owl:Class", "owl:ActionType", "Class"}
INDIVIDUAL_TYPES = {"owl:NamedIndividual", "Instance"}


def _node_data(node: dict) -> dict:
    d = node.get("data")
    return d if isinstance(d, dict) else {}


def _is_class_node(node: dict) -> bool:
    t = _node_data(node).get("type")
    return t in CLASS_TYPES or (t is None and not _node_data(node).get("class_label"))


def _edge_relation(edge: dict) -> str:
    d = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    rel = d.get("relation") or edge.get("label") or "related"
    return str(rel)[:128]


def entity_uri(project_id: int, node_id: str) -> str:
    """uri 规范（02 §3.5）：urn:onto:{project}:entity/{稳定节点 id}。"""
    return f"urn:onto:{project_id}:entity/{quote(str(node_id), safe='')}"


def sync_project_rows(db: Session, project_id: int, graph_data: dict | None) -> dict:
    """把 graph_data 全量重建为行表（幂等，同事务，调用方 commit 提交）。

    S1 语义：blob 为准 —— 实体行 status 全部 'auto'、confidence 取节点自报（现链路无则 NULL）。
    返回 {entities, relations, provenance, skipped_edges, skipped_prov}。
    """
    graph_data = graph_data if isinstance(graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []

    # 清旧行（对账口径：行表 == 当前 blob 投影）
    db.query(ProvenanceRecord).filter(
        ProvenanceRecord.project_id == project_id,
        ProvenanceRecord.target_type.in_(["entity", "relation"]),
    ).delete(synchronize_session=False)
    db.query(Relation).filter(Relation.project_id == project_id).delete(synchronize_session=False)
    db.query(Entity).filter(Entity.project_id == project_id).delete(synchronize_session=False)
    db.flush()

    # 1) 节点 → entities
    id_map: dict[str, int] = {}
    class_map: dict[str, bool] = {}
    n_entities = 0
    for node in nodes:
        data = _node_data(node)
        node_id = str(node.get("id") or data.get("label") or "")
        label = str(data.get("label") or node.get("id") or "").strip()
        if not label or not node_id:
            continue
        is_class = _is_class_node(node)
        class_label = str(data.get("class_label") or (label if is_class else "未分类"))[:128]
        row = Entity(
            project_id=project_id,
            uri=entity_uri(project_id, node_id),
            label=label[:255],
            label_normalized=normalize_label(label)[:255],
            class_label=class_label,
            aliases=data.get("aliases"),
            props=data.get("properties") if isinstance(data.get("properties"), dict) else {},
            confidence=data.get("confidence"),
            status="auto",
            is_class_node=is_class,
        )
        db.add(row)
        db.flush()  # 取自增 id；量级（百~千节点）可接受
        id_map[node_id] = row.id
        class_map[node_id] = is_class
        n_entities += 1

    # 2) 边 → relations（端点缺失的边跳过并计数，不产生悬空 FK）
    n_relations = 0
    skipped_edges = 0
    seen_edges: set[tuple[int, str, int]] = set()
    for edge in edges:
        s_id = id_map.get(str(edge.get("source")))
        o_id = id_map.get(str(edge.get("target")))
        if s_id is None or o_id is None:
            skipped_edges += 1
            continue
        key = (s_id, _edge_relation(edge), o_id)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        db.add(Relation(
            project_id=project_id,
            subject_id=s_id,
            predicate=_edge_relation(edge),
            object_id=o_id,
            props={k: v for k, v in data.items() if k not in ("label", "relation")},
            is_class_edge=class_map.get(str(edge.get("source")), False)
            and class_map.get(str(edge.get("target")), False),
        ))
        n_relations += 1

    # 3) 溯源（实体级）：data.source_document（文件名）→ 项目内 uploaded_documents
    doc_by_name = _doc_index(db, project_id)
    n_prov = 0
    skipped_prov = 0
    for node in nodes:
        data = _node_data(node)
        eid = id_map.get(str(node.get("id")))
        if eid is None:
            continue
        doc_id = doc_by_name.get(str(data.get("source_document") or ""))
        if doc_id is None:
            if data.get("source_document"):
                skipped_prov += 1
            continue
        evidence = str(data.get("source_quote") or "")[:1024]
        chunk_index = data.get("source_chunk_index")
        db.add(ProvenanceRecord(
            project_id=project_id,
            target_type="entity",
            target_id=eid,
            source_document_id=doc_id,
            chunk_index=int(chunk_index) if isinstance(chunk_index, int) else None,
            evidence_text=evidence or None,
            extraction_method="llm_ner",
            # S1 回填无精确偏移：checksum 绑定 文档|chunk|证据（M3-6 接精确 char_start/end）
            checksum=sha256_hex(f"{data.get('source_document')}|{chunk_index}||{evidence}"),
        ))
        n_prov += 1

    db.flush()
    return {"entities": n_entities, "relations": n_relations,
            "provenance": n_prov, "skipped_edges": skipped_edges,
            "skipped_prov": skipped_prov}


def _doc_index(db: Session, project_id: int) -> dict[str, int]:
    rows = db.execute(
        select(UploadedDocument.id, UploadedDocument.filename)
        .where(UploadedDocument.project_id == project_id,
               UploadedDocument.deleted_at.is_(None))
    ).all()
    idx: dict[str, int] = {}
    for doc_id, filename in rows:
        idx.setdefault(str(filename), doc_id)
    return idx


def reconcile_project(db: Session, project_id: int) -> dict:
    """对账（02 §4）：行表 vs blob 的节点/边计数与 uri 集合 diff。ok=True 才可切读（S2）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return {"ok": False, "reason": "project 不存在"}
    gd = project.graph_data if isinstance(project.graph_data, dict) else {}
    nodes = gd.get("nodes") or []
    edges = gd.get("edges") or []
    blob_uris = sorted(entity_uri(project_id, str(n.get("id")))
                       for n in nodes if str(_node_data(n).get("label") or n.get("id") or "").strip())
    blob_edges = len(edges)

    rows = db.query(Entity).filter(Entity.project_id == project_id).all()
    row_uris = sorted(r.uri for r in rows)
    n_rel = db.query(Relation).filter(Relation.project_id == project_id).count()

    return {
        "project_id": project_id,
        "nodes_blob": len(blob_uris), "nodes_rows": len(rows),
        "edges_blob": blob_edges, "edges_rows": n_rel,
        "uri_missing": sorted(set(blob_uris) - set(row_uris)),   # blob 有行表无
        "uri_extra": sorted(set(row_uris) - set(blob_uris)),     # 行表有 blob 无
        "ok": len(blob_uris) == len(rows) and blob_uris == row_uris,
    }


def backfill_all_projects(db: Session) -> list[dict]:
    """存量回填 + 对账（S1 一次性任务，Celery graph 队列 / 脚本两用）。"""
    results = []
    for pid, gd in db.query(Project.id, Project.graph_data).all():
        try:
            stats = sync_project_rows(db, pid, gd)
            db.commit()
            rec = reconcile_project(db, pid)
            results.append({"project_id": pid, **stats, "reconcile_ok": rec["ok"]})
            logger.info(f"[graph-rows] 回填项目 {pid}: {stats} 对账ok={rec['ok']}")
        except Exception as e:  # noqa: BLE001 —— 单项目失败不阻断整体
            db.rollback()
            logger.error(f"[graph-rows] 项目 {pid} 回填失败: {e}")
            results.append({"project_id": pid, "error": str(e)[:300]})
    return results
