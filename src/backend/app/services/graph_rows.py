# app/services/graph_rows.py - graph_data blob → 行表双写（M3-3，docs/design/02 §4）
# 三阶段迁移 S1：行表建好、双写（blob 为准，行表对账）、读方仍走 blob。
# sync_project_rows 是唯一拆行入口：幂等全量重建（删旧插新），被
# before_flush 监听器（同事务，覆盖全部 graph_data 写入点）与回填任务共用。

from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.adapters.provenance import is_protected, is_steward, normalize_source_type
from app.adapters.resolution import normalize_label
from app.core.logging import logger
from app.core.security import sha256_hex
from app.infrastructure.database import (
    Entity,
    Project,
    ProvenanceRecord,
    Relation,
    UploadedDocument,
)

# TBox/ABox 判据（与 extractor/画布/inject_service 的现有约定对齐）
CLASS_TYPES = {"owl:Class", "Class"}
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


def _blob_source(data: dict) -> tuple[str, bool]:
    """节点/边 data 上的显式来源标记 → (source_type, certified)。"""
    st = normalize_source_type(data.get("source_type"))
    cert = bool(data.get("certified"))
    return st, cert


def _capture_protected(db: Session, project_id: int) -> tuple[dict, dict]:
    """重建前捕获人工背书行（certified 或 steward_*）：uri / (s_uri,pred,o_uri) → 标记。

    S16 合同：sync 全量重建以 blob 为准，但人工背书不得被机器重跑降级——
    重建后凡 blob 未显式标注 steward 来源的人工行，标记原样回贴。
    """
    ents: dict[str, tuple[str, bool]] = {}
    rows = (db.query(Entity.uri, Entity.source_type, Entity.certified)
            .filter(Entity.project_id == project_id).all())
    for uri, st, cert in rows:
        if is_protected(st, cert):
            ents[uri] = (st, bool(cert))

    SE = aliased(Entity)
    OE = aliased(Entity)
    rels: dict[tuple[str, str, str], tuple[str, bool]] = {}
    for s_uri, pred, o_uri, st, cert in (
            db.query(SE.uri, Relation.predicate, OE.uri,
                     Relation.source_type, Relation.certified)
            .join(SE, Relation.subject_id == SE.id)
            .join(OE, Relation.object_id == OE.id)
            .filter(Relation.project_id == project_id).all()):
        if is_protected(st, cert):
            rels[(s_uri, str(pred), o_uri)] = (st, bool(cert))
    return ents, rels


def sync_project_rows(db: Session, project_id: int, graph_data: dict | None) -> dict:
    """把 graph_data 同步为行表（幂等，同事务，调用方 commit 提交）。

    S1 语义：blob 为准 —— 实体行 status 全部 'auto'、confidence 取节点自报（现链路无则 NULL）。
    S16：certified/steward_* 行重建时保留（_capture_protected），机器重跑不得降级人工内容；
    blob 显式带 certified 键 = 人工显式动作（审批/认证/撤销认证），以 blob 为准；
    显式 steward 来源标记亦优先。
    S16-upsert：按 uri/边键 原地更新匹配行（id 稳定），仅增删差异行——画布保存/认证
    不再冲刷全部行 id（详情抽屉/MCP/copilot 持有的 id 因此跨保存稳定）。
    返回 {entities, relations, provenance, skipped_edges, skipped_prov}。
    """
    graph_data = graph_data if isinstance(graph_data, dict) else {}
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []

    prev_ents, prev_rels = _capture_protected(db, project_id)

    # 溯源仍全量重建（cheap）；行表按 uri/边键 upsert（S16：id 稳定）
    db.query(ProvenanceRecord).filter(
        ProvenanceRecord.project_id == project_id,
        ProvenanceRecord.target_type.in_(["entity", "relation"]),
    ).delete(synchronize_session=False)
    db.flush()

    existing_ents = {e.uri: e for e in db.query(Entity)
                     .filter(Entity.project_id == project_id).all()}

    # 1) 节点 → entities（uri 匹配原地更新保 id）
    id_map: dict[str, int] = {}
    class_map: dict[str, bool] = {}
    n_entities = 0
    seen_uris: set[str] = set()
    for node in nodes:
        data = _node_data(node)
        node_id = str(node.get("id") or data.get("label") or "")
        label = str(data.get("label") or node.get("id") or "").strip()
        if not label or not node_id:
            continue
        is_class = _is_class_node(node)
        class_label = str(data.get("class_label") or (label if is_class else "未分类"))[:128]
        uri = entity_uri(project_id, node_id)
        st, cert = _blob_source(data)
        prev = prev_ents.get(uri)
        if prev is not None and not is_steward(st) and "certified" not in data:
            st, cert = prev  # 人工背书保留（blob 显式带 certified 键=人工动作，以 blob 为准）
        row = existing_ents.get(uri)
        if row is None:
            row = Entity(
                project_id=project_id,
                uri=uri,
                label=label[:255],
                label_normalized=normalize_label(label)[:255],
                class_label=class_label,
                aliases=data.get("aliases"),
                props=data.get("properties") if isinstance(data.get("properties"), dict) else {},
                confidence=data.get("confidence"),
                status="auto",
                is_class_node=is_class,
                source_type=st,
                certified=cert,
            )
            db.add(row)
            db.flush()
        else:
            row.label = label[:255]
            row.label_normalized = normalize_label(label)[:255]
            row.class_label = class_label
            row.aliases = data.get("aliases")
            row.props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
            row.confidence = data.get("confidence")
            row.status = "auto"
            row.is_class_node = is_class
            row.source_type = st
            row.certified = cert
        id_map[node_id] = row.id
        class_map[node_id] = is_class
        seen_uris.add(uri)
        n_entities += 1

    # 差异删除：blob 已不存在的 uri（FK 级联清 relations）
    for uri, row in existing_ents.items():
        if uri not in seen_uris:
            db.delete(row)
    db.flush()

    uri_by_id = {e.id: e.uri for e in db.query(Entity)
                 .filter(Entity.project_id == project_id).all()}
    existing_rels: dict[tuple[str, str, str], Relation] = {}
    for r in db.query(Relation).filter(Relation.project_id == project_id).all():
        s_uri, o_uri = uri_by_id.get(r.subject_id), uri_by_id.get(r.object_id)
        if s_uri and o_uri:
            existing_rels[(s_uri, r.predicate, o_uri)] = r

    # 2) 边 → relations（端点缺失的边跳过并计数，不产生悬空 FK）
    n_relations = 0
    skipped_edges = 0
    seen_edges: set[tuple[int, str, int]] = set()
    seen_rel_keys: set[tuple[str, str, str]] = set()
    relation_prov: list[tuple[Relation, dict]] = []  # (关系行, 边 data) → 溯源落库用
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
        try:
            conf = float(data.get("confidence")) if data.get("confidence") is not None else None
        except (TypeError, ValueError):
            conf = None
        s_uri = entity_uri(project_id, str(edge.get("source")))
        o_uri = entity_uri(project_id, str(edge.get("target")))
        pred = _edge_relation(edge)
        st, cert = _blob_source(data)
        prev = prev_rels.get((s_uri, pred, o_uri))
        if prev is not None and not is_steward(st) and "certified" not in data:
            st, cert = prev  # 人工背书保留（同上：显式 certified 键优先）
        rel_row = existing_rels.get((s_uri, pred, o_uri))
        rel_fields = dict(
            project_id=project_id,
            subject_id=s_id,
            predicate=pred,
            object_id=o_id,
            props={k: v for k, v in data.items()
                   if k not in ("label", "relation", "confidence",
                                "source_type", "certified")},
            confidence=conf,
            is_class_edge=class_map.get(str(edge.get("source")), False)
            and class_map.get(str(edge.get("target")), False),
            source_type=st,
            certified=cert,
        )
        if rel_row is None:
            rel_row = Relation(**rel_fields)
            db.add(rel_row)
        else:
            for k, v in rel_fields.items():
                setattr(rel_row, k, v)
        db.flush()  # 取/保 id，关系溯源 FK 需要
        seen_rel_keys.add((s_uri, pred, o_uri))
        relation_prov.append((rel_row, data))
        n_relations += 1

    # 差异删除：blob 已不存在的关系
    for rkey, rrow in existing_rels.items():
        if rkey not in seen_rel_keys:
            db.delete(rrow)
    db.flush()

    # 3) 溯源：实体级 + 关系级。文档 id 优先取节点自带 source_document_id（抽取时记录），
    #    缺失时按文件名回查；char_start/end 为切片内证据偏移（locate_quote 定位，M3-6）。
    doc_by_name = _doc_index(db, project_id)
    n_prov = 0
    skipped_prov = 0

    def _prov_target(data: dict, target_type: str, target_id: int) -> None:
        nonlocal n_prov, skipped_prov
        doc_id = data.get("source_document_id") if isinstance(data.get("source_document_id"), int) \
            else doc_by_name.get(str(data.get("source_document") or ""))
        if doc_id is None:
            if data.get("source_document"):
                skipped_prov += 1
            return
        evidence = str(data.get("source_quote") or "")[:1024]
        # 优先库内切片序号（source_chunk_no，多文档下与全量序号不同）；旧数据回退全量序号
        chunk_no = data.get("source_chunk_no")
        chunk_index = chunk_no if isinstance(chunk_no, int) else data.get("source_chunk_index")
        cs = data.get("source_char_start")
        ce = data.get("source_char_end")
        db.add(ProvenanceRecord(
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            source_document_id=doc_id,
            chunk_index=int(chunk_index) if isinstance(chunk_index, int) else None,
            evidence_text=evidence or None,
            char_start=int(cs) if isinstance(cs, int) else None,
            char_end=int(ce) if isinstance(ce, int) else None,
            extraction_method="llm_ner",
            checksum=sha256_hex(f"{data.get('source_document')}|{chunk_index}||{evidence}"),
        ))
        n_prov += 1

    for node in nodes:
        data = _node_data(node)
        eid = id_map.get(str(node.get("id")))
        if eid is None:
            continue
        _prov_target(data, "entity", eid)

    for rel_row, data in relation_prov:
        if data.get("source_quote"):
            _prov_target(data, "relation", rel_row.id)

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
