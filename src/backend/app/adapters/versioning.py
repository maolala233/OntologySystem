# app/adapters/versioning.py - 版本控制与时间轴（docs/design/04 §9，M3-6）
# 版本时机（04 §9 表）：Schema 抽取完成(kind=schema) / Instance 完成(kind=full) /
# 发布前(kind=publication) / 回滚前先存 pre_rollback 快照(kind=rollback)。
# 快照正文入 MinIO（ontology-parsed/snapshots/...），行表只存 key+stats+checksum。
# diff 语义对齐 Semantica OntologyVersionManager.compare_versions（类/属性/实例/关系四级）。

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.infrastructure.database import OntologyVersion

# TBox/ABox 判据（与 graph_rows 一致）
CLASS_TYPES = {"owl:Class", "Class"}
ABOX_TYPES = {"owl:NamedIndividual", "Instance"}

SNAPSHOT_PREFIX = "snapshots"  # MinIO ontology-parsed/snapshots/{project}/v{n}.json


class VersionKind(str, Enum):
    SCHEMA = "schema"
    FULL = "full"
    PUBLICATION = "publication"
    ROLLBACK = "rollback"


class VersionDiff(BaseModel):
    classes: dict = Field(default_factory=dict)      # {added, removed, modified}
    properties: dict = Field(default_factory=dict)
    instances: dict = Field(default_factory=dict)
    relations: dict = Field(default_factory=dict)


class VersionSnapshot(BaseModel):
    project_id: int
    version_no: int
    kind: VersionKind
    label: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    checksum: str = ""


def compute_checksum(data: Any) -> str:
    """SHA-256(规范化 JSON)：快照防篡改（对齐 provenance checksum 语义）。"""
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bucket() -> str:
    from app.core.config import settings

    return settings.MINIO_BUCKET_PARSED


def snapshot_key(project_id: int, version_no: int) -> str:
    return f"{SNAPSHOT_PREFIX}/{project_id}/v{version_no}.json"


def put_snapshot(project_id: int, version_no: int, payload: dict) -> tuple[str, int, int, str]:
    """快照正文写 MinIO，返回 (key, node_count, edge_count, checksum)。"""
    from app.infrastructure.minio_client import get_minio_client

    gd = payload.get("graph_data") or {}
    key = snapshot_key(project_id, version_no)
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    get_minio_client().put_bytes(_bucket(), key, body, content_type="application/json")
    return key, len(gd.get("nodes") or []), len(gd.get("edges") or []), compute_checksum(gd)


def get_snapshot_json(storage_key: str) -> Optional[dict]:
    """按 key 读回快照（缺失/损坏返回 None，调用方走回退）。"""
    from app.infrastructure.minio_client import get_minio_client

    try:
        body = get_minio_client().get_bytes(_bucket(), storage_key)
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def graph_stats(gd: dict) -> dict:
    schema = gd.get("schema") or {}
    nodes = gd.get("nodes") or []
    edges = gd.get("edges") or []
    instances = [n for n in nodes if (n.get("data") or {}).get("type") in ABOX_TYPES]
    inst_ids = {n.get("id") for n in instances}
    inst_edges = [e for e in edges
                  if e.get("source") in inst_ids or e.get("target") in inst_ids]
    n_props = sum(len((c or {}).get("properties") or []) for c in schema.get("classes") or [])
    return {"classes": len(schema.get("classes") or []),
            "properties": n_props,
            "instances": len(instances),
            "relations": len(inst_edges)}


def next_version_no(db: Session, project_id: int) -> int:
    from app.infrastructure.database import OntologyVersion

    cur = db.query(func.max(OntologyVersion.version_no)).filter(
        OntologyVersion.project_id == project_id).scalar()
    return (cur or 0) + 1


def create_version(db: Session, project, kind: str, created_by: int,
                   label: str | None = None, description: str | None = None,
                   parent_version_id: int | None = None,
                   graph_data: dict | None = None) -> OntologyVersion:
    """落版本（04 §9）：快照 graph_data → MinIO + 行表记录 → 更新 project.current_version_id。

    graph_data 缺省用 project.graph_data（restore 时先替换再传入同值）。
    MinIO 失败抛出（调用方决定是否容忍）。
    """
    from app.infrastructure.database import GraphSnapshot, OntologyVersion

    gd = graph_data if graph_data is not None else (project.graph_data or {})
    version_no = next_version_no(db, project.id)
    key, node_count, edge_count, checksum = put_snapshot(
        project.id, version_no,
        {"project_id": project.id, "version_no": version_no, "kind": kind,
         "created_at": datetime.utcnow().isoformat(), "graph_data": gd})
    row = OntologyVersion(
        project_id=project.id, version_no=version_no, label=label, kind=kind,
        full_snapshot_key=key, stats=graph_stats(gd), checksum=checksum,
        parent_version_id=parent_version_id, created_by=created_by,
        description=description)
    db.add(row)
    db.flush()
    db.add(GraphSnapshot(project_id=project.id, version_id=row.id, purpose="version",
                         storage_key=key, node_count=node_count, edge_count=edge_count,
                         checksum=checksum))
    project.current_version_id = row.id
    return row


def load_graph_for(db: Session, project, ver) -> Optional[dict]:
    """版本图数据：优先 MinIO 快照；无快照且是当前版本时回退 project.graph_data。"""
    if ver.full_snapshot_key:
        payload = get_snapshot_json(ver.full_snapshot_key)
        if payload is not None:
            return payload.get("graph_data") or {}
    if project.current_version_id == ver.id:
        return project.graph_data or {}
    return None


def diff_graphs(a: dict, b: dict) -> dict:
    """两版 graph_data 的四级 diff（04 §9）：added/removed/modified（字段级 from→to）。"""
    sa_, sb = a.get("schema") or {}, b.get("schema") or {}

    def _cls_sig(c: dict) -> str:
        return json.dumps({"d": c.get("definition") or "",
                           "p": sorted((p.get("name"), p.get("data_type"))
                                       for p in c.get("properties") or []),
                           "parent": c.get("parent")}, ensure_ascii=False, sort_keys=True)

    classes_a = {c.get("label"): c for c in sa_.get("classes") or [] if c.get("label")}
    classes_b = {c.get("label"): c for c in sb.get("classes") or [] if c.get("label")}
    classes = {
        "added": sorted(set(classes_b) - set(classes_a)),
        "removed": sorted(set(classes_a) - set(classes_b)),
        "modified": [{"label": k, "from": _cls_sig(classes_a[k]), "to": _cls_sig(classes_b[k])}
                     for k in set(classes_a) & set(classes_b)
                     if _cls_sig(classes_a[k]) != _cls_sig(classes_b[k])],
    }

    def _op_sig(o: dict) -> str:
        return f"{o.get('domain')}→{o.get('range')}"

    ops_a = {o.get("label"): o for o in sa_.get("object_properties") or [] if o.get("label")}
    ops_b = {o.get("label"): o for o in sb.get("object_properties") or [] if o.get("label")}
    properties = {
        "added": sorted(set(ops_b) - set(ops_a)),
        "removed": sorted(set(ops_a) - set(ops_b)),
        "modified": [{"label": k, "from": _op_sig(ops_a[k]), "to": _op_sig(ops_b[k])}
                     for k in set(ops_a) & set(ops_b) if _op_sig(ops_a[k]) != _op_sig(ops_b[k])],
    }

    def _nodes(gd: dict) -> dict:
        return {n.get("id"): n for n in gd.get("nodes") or []
                if (n.get("data") or {}).get("type") in ABOX_TYPES}

    na, nb = _nodes(a), _nodes(b)

    def _inst_brief(n: dict) -> dict:
        d = n["data"]
        return {"id": n.get("id"), "label": d.get("label"),
                "class_label": d.get("class_label")}

    modified = []
    for k in set(na) & set(nb):
        da, db_ = na[k]["data"], nb[k]["data"]
        pa, pb = da.get("properties") or {}, db_.get("properties") or {}
        ca, cb = da.get("class_label"), db_.get("class_label")
        keys = set(pa) | set(pb)
        props_added = sorted(x for x in keys if x not in pa)
        props_removed = sorted(x for x in keys if x not in pb)
        props_changed = [{"key": x, "from": pa[x], "to": pb[x]}
                         for x in sorted(keys & set(pa) & set(pb)) if pa[x] != pb[x]]
        if props_added or props_removed or props_changed or ca != cb:
            modified.append({"id": k, **_inst_brief(nb[k]), "class_from": ca, "class_to": cb,
                             "props_added": props_added, "props_removed": props_removed,
                             "props_changed": props_changed})
    instances = {
        "added": [_inst_brief(nb[i]) for i in sorted(set(nb) - set(na))],
        "removed": [_inst_brief(na[i]) for i in sorted(set(na) - set(nb))],
        "modified": modified,
    }

    def _edges(gd: dict, ids: set) -> set:
        return {(e.get("source"), (e.get("data") or {}).get("relation") or e.get("label") or "",
                 e.get("target"))
                for e in gd.get("edges") or []
                if e.get("source") in ids or e.get("target") in ids}

    ea, eb = _edges(a, set(na)), _edges(b, set(nb))

    def _all_nodes(gd: dict) -> dict:
        return {n.get("id"): (n.get("data") or {}) for n in gd.get("nodes") or []}

    def _rel_rows(triples: list, id2data: dict) -> list:
        rows = []
        for s, p, o in triples:
            sd, od = id2data.get(s) or {}, id2data.get(o) or {}
            rows.append({"subject": s, "subject_label": sd.get("label") or s,
                         "subject_class": sd.get("class_label") or "",
                         "predicate": p,
                         "object": o, "object_label": od.get("label") or o,
                         "object_class": od.get("class_label") or ""})
        return rows

    relations = {
        "added": _rel_rows(sorted(eb - ea), _all_nodes(b)),
        "removed": _rel_rows(sorted(ea - eb), _all_nodes(a)),
        "modified": [],
    }
    return {"classes": classes, "properties": properties,
            "instances": instances, "relations": relations}


def diff_versions(db: Session, project, ver_a, ver_b) -> dict:
    ga = load_graph_for(db, project, ver_a)
    gb = load_graph_for(db, project, ver_b)
    if ga is None or gb is None:
        missing = [v.version_no for v, g in ((ver_a, ga), (ver_b, gb)) if g is None]
        raise ValueError(f"版本快照缺失: v{missing}")
    return diff_graphs(ga, gb)


def restore_version(db: Session, project, target, user_id: int) -> dict:
    """回滚（04 §9）：pre_rollback 快照 → graph_data 整体替换（监听器重建行表）→ rollback 版本。

    版本链不断：rollback 版本 parent=回滚前 current_version_id，任何回滚可再回滚。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import GraphSnapshot

    payload = get_snapshot_json(target.full_snapshot_key) if target.full_snapshot_key else None
    if payload is None and project.current_version_id == target.id:
        payload = {"graph_data": project.graph_data or {}}
    if payload is None:
        raise ValueError(f"目标版本快照缺失: v{target.version_no}")

    prev_version_id = project.current_version_id
    # 1) pre_rollback：回滚前状态快照（不占 version_no，purpose 区分）
    cur_gd = project.graph_data or {}
    key, node_count, edge_count, checksum = put_snapshot(
        project.id, next_version_no(db, project.id),
        {"project_id": project.id, "purpose": "pre_rollback", "graph_data": cur_gd})
    db.add(GraphSnapshot(project_id=project.id, version_id=None, purpose="pre_rollback",
                         storage_key=key, node_count=node_count, edge_count=edge_count,
                         checksum=checksum))
    db.flush()
    # 2) 恢复目标快照内容并落库（graph-rows 监听器整表重建行表）
    project.graph_data = payload.get("graph_data") or {}
    flag_modified(project, "graph_data")
    db.commit()
    # 3) rollback 版本（快照恢复后的状态）
    ver = create_version(db, project, "rollback", user_id,
                         label=f"回滚至 v{target.version_no}",
                         description=f"restore from v{target.version_no}",
                         parent_version_id=prev_version_id)
    return {"restored_from": target.version_no, "new_version_no": ver.version_no,
            "pre_rollback_key": key}


def entity_history(db: Session, project_id: int, entity_id: int) -> dict:
    """单实体变更史（03 §11）：行表状态 + 溯源 + 合并留痕拼装。"""
    from app.adapters.resolution import normalize_label
    from app.infrastructure.database import Entity, ProvenanceRecord, ReviewItem

    ent = db.query(Entity).filter(Entity.id == entity_id,
                                  Entity.project_id == project_id).first()
    if ent is None:
        raise ValueError(f"实体不存在: {entity_id}")
    provs = (db.query(ProvenanceRecord)
             .filter(ProvenanceRecord.project_id == project_id,
                     ProvenanceRecord.target_type == "entity",
                     ProvenanceRecord.target_id == entity_id).all())
    merges = (db.query(ReviewItem)
              .filter(ReviewItem.project_id == project_id,
                      ReviewItem.item_type == "entity_merge").all())
    involved = []
    for m in merges:
        ref = m.result_ref or {}
        if entity_id in (ref.get("merged_ids") or []) or entity_id == ref.get("canonical_id"):
            involved.append({"item_id": m.id, "status": m.status,
                             "canonical_id": ref.get("canonical_id"),
                             "merged_ids": ref.get("merged_ids"),
                             "decided_at": m.decided_at.isoformat() if m.decided_at else None})
    return {
        "entity": {"id": ent.id, "uri": ent.uri, "label": ent.label,
                   "class_label": ent.class_label, "status": ent.status,
                   "canonical_id": ent.canonical_id,
                   "label_normalized": normalize_label(ent.label)},
        "provenance": [{"id": p.id, "document_id": p.source_document_id,
                        "chunk_index": p.chunk_index, "evidence_text": p.evidence_text,
                        "char_start": p.char_start, "char_end": p.char_end,
                        "method": p.extraction_method,
                        "created_at": p.created_at.isoformat() if p.created_at else None}
                       for p in provs],
        "merge_history": involved,
    }


def timeline(db: Session, project_id: int, entity_uri: str | None = None,
             time_axis: Literal["valid", "transaction", "both"] = "both") -> dict:
    """双时间轴（04 §9）：valid_from/valid_until（业务时间）+ created_at（事务时间）。

    time_axis ∈ valid|transaction|both；events 按时间升序。
    """
    from app.infrastructure.database import Entity

    q = db.query(Entity).filter(Entity.project_id == project_id)
    if entity_uri:
        q = q.filter(Entity.uri == entity_uri)
    rows = q.limit(2000).all()
    events = []
    for e in rows:
        props = e.props or {}
        if time_axis in ("valid", "both"):
            vf, vu = props.get("valid_from"), props.get("valid_until")
            if vf:
                events.append({"time": str(vf), "axis": "valid", "type": "valid_from",
                               "entity_id": e.id, "uri": e.uri, "label": e.label,
                               "class_label": e.class_label})
            if vu:
                events.append({"time": str(vu), "axis": "valid", "type": "valid_until",
                               "entity_id": e.id, "uri": e.uri, "label": e.label,
                               "class_label": e.class_label})
        if time_axis in ("transaction", "both"):
            if e.created_at:
                events.append({"time": e.created_at.isoformat(), "axis": "transaction",
                               "type": "created", "entity_id": e.id, "uri": e.uri,
                               "label": e.label, "class_label": e.class_label})
    events.sort(key=lambda x: str(x["time"]))
    return {"events": events[:1000], "time_axis": time_axis}


def quality_gate(gd: dict) -> dict:
    """TBox 健康检查（04 §10 发布门禁）：只警告不阻断；返回 {passed, warnings}。"""
    schema = gd.get("schema") or {}
    classes = {c.get("label") for c in schema.get("classes") or [] if c.get("label")}
    warnings: list[str] = []
    if not classes:
        warnings.append("TBox 为空：没有任何类")
    for op_ in schema.get("object_properties") or []:
        for side in ("domain", "range"):
            ref = op_.get(side)
            if ref and classes and ref not in classes:
                warnings.append(f"关系「{op_.get('label')}」的 {side}「{ref}」不在类清单中")
    orphan = sorted({(n.get("data") or {}).get("class_label")
                     for n in gd.get("nodes") or []
                     if (n.get("data") or {}).get("type") in ABOX_TYPES
                     and (n.get("data") or {}).get("class_label") not in classes} - {None})
    if orphan:
        warnings.append(f"实例引用了未定义类: {'、'.join(orphan[:8])}")
    return {"passed": bool(classes), "warnings": warnings}


__all__ = [
    "VersionKind", "VersionDiff", "VersionSnapshot",
    "CLASS_TYPES", "ABOX_TYPES", "compute_checksum", "snapshot_key", "put_snapshot",
    "get_snapshot_json", "graph_stats", "next_version_no", "create_version",
    "load_graph_for", "diff_graphs", "diff_versions", "restore_version",
    "entity_history", "timeline", "quality_gate",
]
