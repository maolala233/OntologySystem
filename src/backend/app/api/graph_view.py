# app/api/graph_view.py - 图视图数据契约（docs/design/03 §13 / 06 §4，M4-a）
# 行表（entities/relations/provenance_records）为事实源；游标分页 + 服务端双向边判定
# + 邻域/搜索 + ★节点/边详情（M4 用户增强：点击节点/关系连接点看全量信息）。
# 权限：[PR:view]（读）；[PR:editor]（布局受理）。
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.core.security import sha256_hex
from app.infrastructure.database import (
    Entity, GraphLayout, ProvenanceRecord, Project, Relation, ReviewItem,
    UploadedDocument, User,
)

router = APIRouter(prefix="/api/projects/{project_id}/graph", tags=["graph-view"])

CLASS_TYPE_SET = {"owl:Class", "Class"}


def _project_or_404(project_id: int, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


def _entity_or_404(project_id: int, node_id: str, db: Session) -> Entity:
    """node_id 兼容行表 id 与 uri（06 §4.1 契约：id=uri 或行表 id 字符串）。"""
    ent = None
    if str(node_id).isdigit():
        ent = db.query(Entity).filter(Entity.id == int(node_id),
                                      Entity.project_id == project_id).first()
    if ent is None:
        uri = node_id if node_id.startswith("urn:") else \
            f"urn:onto:{project_id}:entity/{quote(str(node_id), safe='')}"
        ent = db.query(Entity).filter(Entity.uri == uri,
                                      Entity.project_id == project_id).first()
    if ent is None:
        raise NotFoundError(f"节点不存在: {node_id}")
    return ent


def _degree_map(db: Session, project_id: int) -> dict[int, int]:
    rows = (db.query(Relation.subject_id, func.count(Relation.id))
            .filter(Relation.project_id == project_id).group_by(Relation.subject_id).all())
    deg = {sid: c for sid, c in rows}
    rows = (db.query(Relation.object_id, func.count(Relation.id))
            .filter(Relation.project_id == project_id).group_by(Relation.object_id).all())
    for oid, c in rows:
        deg[oid] = deg.get(oid, 0) + c
    return deg


def _node_kind(ent: Entity) -> str:
    return "class" if ent.is_class_node else "instance"


def _node_out(ent: Entity, degree: int, x=None, y=None) -> dict:
    """06 §4.1 节点 7 字段契约。"""
    return {"id": str(ent.id), "uri": ent.uri, "label": ent.label,
            "kind": _node_kind(ent), "group": ent.class_label,
            "class_label": ent.class_label, "degree": degree,
            "status": ent.status, "confidence": float(ent.confidence) if ent.confidence is not None else None,
            "x": x, "y": y}


def _edge_out(rel: Relation, subj: Entity, obj: Entity,
              bidi_groups: dict[tuple[int, int, str], int]) -> dict:
    """06 §4.2 边契约：双向边判定在服务端（同 (min,max,predicate) 分组计数>1）。"""
    pair_key = (min(rel.subject_id, rel.object_id), max(rel.subject_id, rel.object_id), rel.predicate)
    is_bidi = bidi_groups.get(pair_key, 0) > 1
    pair_index = 0 if rel.subject_id <= rel.object_id else 1
    return {"id": str(rel.id), "source": str(rel.subject_id), "target": str(rel.object_id),
            "label": rel.predicate,
            "kind": "type" if rel.predicate in ("instance_of", "rdf:type", "type")
            else ("relation" if not (subj.is_class_node and obj.is_class_node) else "relation"),
            "directed": True, "bidirectional": is_bidi, "pairIndex": pair_index,
            "weight": float(rel.confidence) if rel.confidence is not None else None,
            "group": rel.predicate,
            "status": rel.status,
            "is_class_edge": bool(subj.is_class_node and obj.is_class_node)}


def _bidi_groups(db: Session, project_id: int) -> dict[tuple[int, int, str], int]:
    rows = (db.query(Relation.subject_id, Relation.object_id, Relation.predicate)
            .filter(Relation.project_id == project_id).all())
    groups: dict[tuple[int, int, str], int] = {}
    for s, o, p in rows:
        groups[(min(s, o), max(s, o), p)] = groups.get((min(s, o), max(s, o), p), 0) + 1
    return groups


@router.get("/meta")
def graph_meta(project_id: int, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """画布元信息（06 §4.3 装载流程第一步）。"""
    require_project_role(project_id, "viewer", current_user, db)
    proj = _project_or_404(project_id, db)
    n_nodes = db.query(Entity).filter(Entity.project_id == project_id).count()
    n_edges = db.query(Relation).filter(Relation.project_id == project_id).count()
    dist_rows = (db.query(Entity.class_label, func.count(Entity.id))
                 .filter(Entity.project_id == project_id)
                 .group_by(Entity.class_label)
                 .order_by(func.count(Entity.id).desc()).limit(24).all())
    src_rows = (db.query(Entity.props).filter(Entity.project_id == project_id).all())
    sources = sorted({p.get("source_document") for (r,) in src_rows
                      for p in [r or {}] if p.get("source_document")})
    layout = (db.query(GraphLayout).filter(GraphLayout.project_id == project_id,
                                           GraphLayout.status == "done")
              .order_by(GraphLayout.id.desc()).first())
    return {"node_count": n_nodes, "edge_count": n_edges,
            "class_distribution": [{"class_label": c, "count": n} for c, n in dist_rows],
            "sources": sources,
            "layout_available": layout is not None,
            "layout_id": layout.id if layout else None,
            "readonly": proj.status == "published"}


@router.get("/nodes")
def graph_nodes(project_id: int,
                cursor: int = Query(0, ge=0),
                limit: int = Query(500, ge=1, le=2000),
                class_: str | None = Query(None, alias="class"),
                status: str | None = None,
                source_doc: str | None = None,
                q: str | None = None,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """节点游标分页（ORDER BY degree DESC → 首屏=骨架+高度数实例）。"""
    require_project_role(project_id, "viewer", current_user, db)
    deg = _degree_map(db, project_id)
    qy = db.query(Entity).filter(Entity.project_id == project_id)
    if class_:
        qy = qy.filter(Entity.class_label == class_)
    if status:
        qy = qy.filter(Entity.status == status)
    if source_doc:
        qy = qy.filter(func.json_unquote(
            func.json_extract(Entity.props, "$.source_document")) == source_doc)
    if q:
        from sqlalchemy import String as _String

        qy = qy.filter(or_(Entity.label.contains(q), Entity.label_normalized.contains(q),
                           func.cast(Entity.aliases, _String).contains(q)))
    rows = (qy.order_by(Entity.id.asc()).offset(cursor).limit(limit + 1).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_node_out(e, deg.get(e.id, 0)) for e in rows]
    # 度数排序（客户端首屏渲染顺序；分页游标仍按 id 稳定推进）
    items.sort(key=lambda n: -n["degree"])
    return {"items": items, "total": len(items),
            "next_cursor": cursor + limit if has_more else None}


@router.get("/edges")
def graph_edges(project_id: int,
                node_ids: str | None = Query(None, description="逗号分隔；缺省=全项目边"),
                cursor: int = Query(0, ge=0),
                limit: int = Query(1000, ge=1, le=5000),
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """边游标分页；node_ids 给定时只取这些节点的关联边（分页装载第 3 步）。"""
    require_project_role(project_id, "viewer", current_user, db)
    bidi = _bidi_groups(db, project_id)
    qy = db.query(Relation).filter(Relation.project_id == project_id)
    if node_ids:
        ids = [int(i) for i in node_ids.split(",") if i.strip().isdigit()]
        if not ids:
            return {"items": [], "total": 0, "next_cursor": None}
        qy = qy.filter(or_(Relation.subject_id.in_(ids), Relation.object_id.in_(ids)))
    rows = (qy.order_by(Relation.id.asc()).offset(cursor).limit(limit + 1).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    ent_ids = {r.subject_id for r in rows} | {r.object_id for r in rows}
    ents = {e.id: e for e in db.query(Entity).filter(Entity.id.in_(ent_ids)).all()} if ent_ids else {}
    items = []
    for r in rows:
        s, o = ents.get(r.subject_id), ents.get(r.object_id)
        if s is None or o is None:
            continue
        items.append(_edge_out(r, s, o, bidi))
    return {"items": items, "total": len(items),
            "next_cursor": cursor + limit if has_more else None}


@router.get("/neighbors/{node_id}")
def graph_neighbors(project_id: int, node_id: str,
                    hops: int = Query(1, ge=1, le=2),
                    limit: int = Query(200, ge=1, le=1000),
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """邻域展开（探索视图"展开实例"）。"""
    require_project_role(project_id, "viewer", current_user, db)
    root = _entity_or_404(project_id, node_id, db)
    frontier = {root.id}
    visited = {root.id}
    rel_rows: list[Relation] = []
    for _ in range(hops):
        if not frontier:
            break
        rows = (db.query(Relation)
                .filter(Relation.project_id == project_id,
                        or_(Relation.subject_id.in_(frontier),
                            Relation.object_id.in_(frontier)))
                .limit(limit).all())
        new_ids = set()
        for r in rows:
            rel_rows.append(r)
            other = r.object_id if r.subject_id in frontier else r.subject_id
            if other not in visited:
                new_ids.add(other)
        visited |= new_ids
        frontier = new_ids
    ent_ids = visited
    ents = {e.id: e for e in db.query(Entity).filter(Entity.id.in_(ent_ids)).all()} if ent_ids else {}
    deg = _degree_map(db, project_id)
    nodes = [_node_out(e, deg.get(e.id, 0)) for e in ents.values()]
    seen_rel, edges = set(), []
    for r in rel_rows:
        if r.id in seen_rel or r.subject_id not in visited or r.object_id not in visited:
            continue
        seen_rel.add(r.id)
        s, o = ents.get(r.subject_id), ents.get(r.object_id)
        if s and o:
            edges.append(_edge_out(r, s, o, _bidi_groups(db, project_id)))
    return {"root_id": str(root.id), "nodes": nodes, "edges": edges}


@router.get("/search")
def graph_search(project_id: int, q: str = Query(..., min_length=1),
                 class_: str | None = Query(None, alias="class"),
                 limit: int = Query(20, ge=1, le=100),
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """画布内搜索（label/alias 前缀+包含）。"""
    require_project_role(project_id, "viewer", current_user, db)
    qy = db.query(Entity).filter(Entity.project_id == project_id,
                                 or_(Entity.label.contains(q),
                                     Entity.label_normalized.contains(q)))
    if class_:
        qy = qy.filter(Entity.class_label == class_)
    rows = qy.order_by(Entity.id.asc()).limit(limit).all()
    deg = _degree_map(db, project_id)
    return {"items": [_node_out(e, deg.get(e.id, 0)) for e in rows]}


# ── ★节点/边详情（M4 用户增强：点击节点/关系连接点看全量信息）──


def _entity_provenance(db: Session, project_id: int, entity_id: int) -> list[dict]:
    rows = (db.query(ProvenanceRecord, UploadedDocument)
            .outerjoin(UploadedDocument, UploadedDocument.id == ProvenanceRecord.source_document_id)
            .filter(ProvenanceRecord.project_id == project_id,
                    ProvenanceRecord.target_type == "entity",
                    ProvenanceRecord.target_id == entity_id).all())
    return [{"id": p.id, "document_id": d.id if d else None,
             "document_name": d.filename if d else None,
             "chunk_index": p.chunk_index, "evidence": p.evidence_text,
             "char_start": p.char_start, "char_end": p.char_end,
             "method": p.extraction_method, "checksum": p.checksum,
             "created_at": p.created_at.isoformat() if p.created_at else None}
            for p, d in rows]


def _entity_relations(db: Session, project_id: int, entity_id: int, limit: int = 100) -> list[dict]:
    rows = (db.query(Relation)
            .filter(Relation.project_id == project_id,
                    or_(Relation.subject_id == entity_id, Relation.object_id == entity_id))
            .order_by(Relation.id.asc()).limit(limit).all())
    ent_ids = {r.subject_id for r in rows} | {r.object_id for r in rows}
    ents = {e.id: e for e in db.query(Entity).filter(Entity.id.in_(ent_ids)).all()} if ent_ids else {}
    out = []
    for r in rows:
        direction = "out" if r.subject_id == entity_id else "in"
        other = ents.get(r.object_id if direction == "out" else r.subject_id)
        out.append({"id": str(r.id), "predicate": r.predicate, "direction": direction,
                    "other_id": str(other.id) if other else None,
                    "other_label": other.label if other else None,
                    "other_class": other.class_label if other else None,
                    "confidence": float(r.confidence) if r.confidence is not None else None,
                    "status": r.status, "is_class_edge": r.is_class_edge})
    return out


def _entity_merge_info(db: Session, project_id: int, ent: Entity) -> dict:
    merged_children = (db.query(Entity.id, Entity.label)
                       .filter(Entity.canonical_id == ent.id).all())
    merges = (db.query(ReviewItem)
              .filter(ReviewItem.project_id == project_id,
                      ReviewItem.item_type == "entity_merge").all())
    involved = []
    for m in merges:
        ref = m.result_ref or {}
        if ent.id == ref.get("canonical_id") or ent.id in (ref.get("merged_ids") or []):
            involved.append({"review_id": m.id, "status": m.status,
                             "decided_at": m.decided_at.isoformat() if m.decided_at else None,
                             "canonical_id": ref.get("canonical_id"),
                             "merged_ids": ref.get("merged_ids")})
    return {"canonical_id": str(ent.canonical_id) if ent.canonical_id else None,
            "merged_children": [{"id": str(i), "label": lb} for i, lb in merged_children],
            "merge_reviews": involved}


def _entity_reviews(db: Session, project_id: int, ent: Entity, limit: int = 20) -> list[dict]:
    """与该实体相关的审核项：payload 候选/规范 id 命中。"""
    rows = (db.query(ReviewItem)
            .filter(ReviewItem.project_id == project_id,
                    ReviewItem.status.in_(("pending", "claimed")))
            .order_by(ReviewItem.id.desc()).limit(200).all())
    out = []
    for r in rows:
        p = r.payload or {}
        hit = (str(ent.id) in (str(p.get("canonical_id")), str(p.get("entity_id")))
               or ent.id in (p.get("merged_ids") or [])
               or ent.label in [p.get("label"), p.get("subject"), p.get("object")])
        if hit:
            out.append({"review_id": r.id, "item_type": r.item_type,
                        "priority": r.priority, "status": r.status, "reason": r.reason})
        if len(out) >= limit:
            break
    return out


def _hidden_props(props: dict) -> dict:
    return {k: v for k, v in (props or {}).items()
            if not k.startswith("_") and k not in ("source_chunk_index",)}


@router.get("/node/{node_id}/detail")
def node_detail(project_id: int, node_id: str,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """节点详情（越详细越好）：行表 + 溯源 + 关系 + 合并 + 审核 + 时间线属性。"""
    require_project_role(project_id, "viewer", current_user, db)
    ent = _entity_or_404(project_id, node_id, db)
    deg = _degree_map(db, project_id)
    props = ent.props or {}
    inst_count = None
    if ent.is_class_node:
        inst_count = db.query(Entity).filter(Entity.project_id == project_id,
                                             Entity.class_label == ent.label,
                                             Entity.is_class_node == False).count()  # noqa: E712
    return {
        "node": _node_out(ent, deg.get(ent.id, 0)),
        "entity": {"id": str(ent.id), "uri": ent.uri, "label": ent.label,
                   "label_normalized": ent.label_normalized,
                   "class_label": ent.class_label, "kind": _node_kind(ent),
                   "status": ent.status, "confidence": float(ent.confidence) if ent.confidence is not None else None,
                   "created_at": ent.created_at.isoformat() if ent.created_at else None,
                   "updated_at": ent.updated_at.isoformat() if ent.updated_at else None,
                   "instance_count": inst_count},
        "properties": _hidden_props(props),
        "raw_props": {k: v for k, v in props.items() if k.startswith("_")},
        "provenance": _entity_provenance(db, project_id, ent.id),
        "relations": _entity_relations(db, project_id, ent.id),
        "degree": deg.get(ent.id, 0),
        "merge": _entity_merge_info(db, project_id, ent),
        "reviews": _entity_reviews(db, project_id, ent),
        "timeline": {"valid_from": props.get("valid_from"), "valid_until": props.get("valid_until")},
        "source_docs": [{"document_id": p["document_id"], "name": p["document_name"]}
                        for p in _entity_provenance(db, project_id, ent.id) if p["document_id"]],
    }


@router.get("/edge/{edge_id}/detail")
def edge_detail(project_id: int, edge_id: int,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """边详情：谓词/两端实体/置信度/溯源证据。"""
    require_project_role(project_id, "viewer", current_user, db)
    rel = db.query(Relation).filter(Relation.id == edge_id,
                                    Relation.project_id == project_id).first()
    if rel is None:
        raise NotFoundError(f"关系不存在: {edge_id}")
    subj = db.query(Entity).filter(Entity.id == rel.subject_id).first()
    obj = db.query(Entity).filter(Entity.id == rel.object_id).first()
    prov_rows = (db.query(ProvenanceRecord, UploadedDocument)
                 .outerjoin(UploadedDocument, UploadedDocument.id == ProvenanceRecord.source_document_id)
                 .filter(ProvenanceRecord.project_id == project_id,
                         ProvenanceRecord.target_type == "relation",
                         ProvenanceRecord.target_id == rel.id).all())
    deg = _degree_map(db, project_id)

    def _side(e: Entity | None) -> dict | None:
        if e is None:
            return None
        return {"id": str(e.id), "label": e.label, "class_label": e.class_label,
                "kind": _node_kind(e), "degree": deg.get(e.id, 0),
                "uri": e.uri, "status": e.status}

    return {"edge": _edge_out(rel, subj, obj, _bidi_groups(db, project_id)),
            "subject": _side(subj), "object": _side(obj),
            "props": _hidden_props(rel.props or {}),
            "confidence": float(rel.confidence) if rel.confidence is not None else None,
            "status": rel.status,
            "provenance": [{"id": p.id, "document_id": d.id if d else None,
                            "document_name": d.filename if d else None,
                            "chunk_index": p.chunk_index, "evidence": p.evidence_text,
                            "method": p.extraction_method,
                            "created_at": p.created_at.isoformat() if p.created_at else None}
                           for p, d in prov_rows]}


# ── 服务端预布局（06 §10：>25k 受理；networkx spring → graph_layouts + MinIO）──


def _spring_layout_coords(nodes: list[dict], edges: list[dict], seed: int = 42) -> dict[str, list[float]]:
    """确定性 spring 布局（networkx；快照坐标供前端直接用）。"""
    import networkx as nx

    g = nx.Graph()
    for n in nodes:
        g.add_node(str(n["id"]))
    for e in edges:
        if g.has_node(str(e["source"])) and g.has_node(str(e["target"])):
            g.add_edge(str(e["source"]), str(e["target"]), weight=e.get("weight") or 1.0)
    pos = nx.spring_layout(g, seed=seed, iterations=50, weight="weight")
    return {k: [float(v[0]), float(v[1])] for k, v in pos.items()}


@router.post("/layout")
def request_layout(project_id: int, body: dict | None = None,
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """请求服务端预布局（06 §10：>25,000 节点才受理；写 graph_layouts + MinIO）。"""
    require_project_role(project_id, "editor", current_user, db)
    _project_or_404(project_id, db)
    n_nodes = db.query(Entity).filter(Entity.project_id == project_id).count()
    if n_nodes <= 25000:
        raise APIError(f"当前节点数 {n_nodes} ≤ 25000，前端实时布局即可",
                       code="LAYOUT_NOT_NEEDED", http_status=400,
                       detail={"node_count": n_nodes})
    from app.tasks.graph_tasks import run_graph_layout

    try:
        task = run_graph_layout.delay(project_id)
    except Exception as e:  # noqa: BLE001
        raise APIError(f"布局任务派发失败: {e}", code="TASK_DISPATCH_FAILED", http_status=503)
    return {"task_id": task.id, "node_count": n_nodes, "algorithm": "spring"}


@router.get("/layout")
def get_layout(project_id: int, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """最新完成的预布局坐标（前端免布局直接用）。"""
    require_project_role(project_id, "viewer", current_user, db)
    layout = (db.query(GraphLayout).filter(GraphLayout.project_id == project_id,
                                           GraphLayout.status == "done")
              .order_by(GraphLayout.id.desc()).first())
    if layout is None:
        return {"available": False}
    coords = {}
    if layout.layout:
        coords = layout.layout.get("coords") or {}
    return {"available": True, "layout_id": layout.id, "algorithm": layout.algorithm,
            "node_count": layout.node_count, "coords": coords}


# 供 worker 调用的布局执行（与 API 解耦，便于单测）
def compute_and_store_layout(db: Session, project_id: int, algorithm: str = "spring") -> dict:
    ents = db.query(Entity).filter(Entity.project_id == project_id).all()
    deg = _degree_map(db, project_id)
    nodes = [_node_out(e, deg.get(e.id, 0)) for e in ents]
    rels = db.query(Relation).filter(Relation.project_id == project_id).all()
    ent_by_id = {e.id: e for e in ents}
    edges = []
    for r in rels:
        s, o = ent_by_id.get(r.subject_id), ent_by_id.get(r.object_id)
        if s and o:
            edges.append(_edge_out(r, s, o, _bidi_groups(db, project_id)))
    coords = _spring_layout_coords(nodes, edges)
    # 布局坐标入 MinIO（02 §7 键规范）+ 行表快照
    payload = {"project_id": project_id, "algorithm": algorithm, "coords": coords}
    try:
        from app.infrastructure.minio_client import get_minio_client
        from app.core.config import settings as st

        key = f"layouts/{project_id}/{sha256_hex(json.dumps(payload, sort_keys=True))[:16]}.json"
        get_minio_client().put_bytes(st.MINIO_BUCKET_PARSED, key,
                                     json.dumps(payload).encode(), content_type="application/json")
        payload["storage_key"] = key
    except Exception:  # noqa: BLE001 —— MinIO 不可达时坐标仍写行表
        pass
    row = GraphLayout(project_id=project_id, algorithm=algorithm,
                      layout={"coords": coords, "storage_key": payload.get("storage_key")},
                      node_count=len(nodes), status="done")
    db.add(row)
    db.commit()
    return {"layout_id": row.id, "node_count": len(nodes), "coords": len(coords)}
