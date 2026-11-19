# app/services/index_reconcile.py - 索引表与图谱实例对账（2026-11-19）
# 背景：接口索引表有 152 行，但 LLM/结构展开漏了其中 1 行（C250405N），
# 导致图谱 class_label='前端接口' 只有 151 个实例。索引表是确定性清单，
# 这里按 chunk 里的 markdown 表补建缺失实例（幂等，已有同 label+class 跳过）。
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import update

from app.adapters.extraction import normalize_label
from app.adapters.structure_expand import parse_md_tables
from app.core.security import sha256_hex
from app.infrastructure.database import (
    DocumentChunk, Entity, Project, ProvenanceRecord, Relation, UploadedDocument,
)
from app.services.class_membership import add_membership, membership_ids

INDEX_TITLE = "普惠金融前端接口清单"
DEFAULT_CLASS = "前端接口"


def _is_code(v: Any) -> bool:
    s = str(v or "").strip()
    return len(s) >= 5 and s[0].upper() == "C" and any(ch.isdigit() for ch in s)


def scan_frontend_index(db: Session, project_id: int) -> list[dict]:
    """从项目切片回溯「普惠金融前端接口清单」表格行（按 code 去重）。"""
    rows: dict[str, dict] = {}
    chunks = (
        db.query(DocumentChunk)
        .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
        .filter(UploadedDocument.project_id == project_id,
                UploadedDocument.deleted_at.is_(None))
        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        .all()
    )
    for c in chunks:
        text = c.text or ""
        for header, table_rows in parse_md_tables(text):
            htxt = " ".join(str(x) for x in header)
            # 标题可能被切片单独放在上一块（> caption），表格块本身没有标题；
            # 以「接口号 + 登记人」表头识别前端接口索引，不依赖 caption 同块。
            if "接口号" not in htxt or "登记人" not in htxt:
                continue
            for r in table_rows:
                if len(r) < 4 or not _is_code(r[0]):
                    continue
                code = str(r[0]).strip()
                if code in rows:
                    continue
                rows[code] = {
                    "code": code,
                    "description": str(r[1] or "").strip(),
                    "detail": str(r[2] or "").strip(),
                    "owner": str(r[3] or "").strip(),
                    "document_id": c.document_id,
                    "chunk_index": c.chunk_index,
                    "char_start": text.find(code),
                }
    return list(rows.values())


def reconcile_frontend_index(db: Session, project_id: int, apply: bool = True) -> dict:
    """补建索引表缺失的前端接口实例；返回 rows/existing/missing/created。"""
    rows = scan_frontend_index(db, project_id)
    cls = (db.query(Entity)
           .filter(Entity.project_id == project_id,
                   Entity.is_class_node.is_(True),
                   Entity.label == DEFAULT_CLASS).first())
    existing = {
        e.label for e in db.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.is_class_node.is_(False),
            Entity.status != "merged",
            Entity.id.in_(membership_ids(db, project_id, DEFAULT_CLASS) or [-1])).all()
    }
    missing = [r for r in rows if r["code"] not in existing]
    created: list[str] = []
    linked: list[str] = []
    if apply and cls is not None:
        for r in missing:
            code = r["code"]
            # 同码已在别的类下（C250405N 已是 BP接口）：补一条 instance_of
            # 多类成员边，不再造重复实例。
            any_ent = (db.query(Entity)
                       .filter(Entity.project_id == project_id,
                               Entity.is_class_node.is_(False),
                               Entity.label == code,
                               Entity.status != "merged")
                       .first())
            if any_ent is not None:
                # 同码实例可能由抽取先建成空属性（C2504081 实测登记人缺失）；
                # 对账时用索引行补齐属性，再补多类成员边。
                props = dict(any_ent.props or {})
                for k, v in (("接口号", code), ("接口描述", r["description"]),
                             ("登记人", r["owner"])):
                    if v and not str(props.get(k) or "").strip():
                        props[k] = v
                any_ent.props = props
                if add_membership(db, project_id, any_ent.id, DEFAULT_CLASS):
                    linked.append(code)
                    _add_graph_data_membership(db, project_id, any_ent, cls)
                continue
            seed = f"{project_id}|{code}|{DEFAULT_CLASS}|{r['document_id']}"
            uri = (f"urn:onto:{project_id}:entity/inst_"
                   f"{sha256_hex(seed)[:16]}")
            ent = Entity(
                project_id=project_id, uri=uri, label=code,
                label_normalized=normalize_label(code), class_label=DEFAULT_CLASS,
                aliases=[], props={"接口号": code, "接口描述": r["description"],
                                   "登记人": r["owner"]},
                confidence=0.95, status="auto", is_class_node=False,
            )
            db.add(ent)
            db.flush()
            db.add(Relation(project_id=project_id, subject_id=ent.id,
                            predicate="instance_of", object_id=cls.id,
                            confidence=1.0, status="auto", is_class_edge=False))
            _add_graph_data_membership(db, project_id, ent, cls)
            evidence = f"{code} | {r['description']} | {r['detail']} | {r['owner']}"
            cs = r["char_start"] if (r["char_start"] or -1) >= 0 else 0
            db.add(ProvenanceRecord(
                project_id=project_id, target_type="entity", target_id=ent.id,
                source_document_id=r["document_id"], chunk_index=r["chunk_index"],
                evidence_text=evidence[:1024], char_start=cs,
                char_end=cs + len(code) if cs is not None else None,
                extraction_method="table_index",
                checksum=sha256_hex(f"{r['document_id']}|{cs}|{evidence}"),
            ))
            created.append(code)
        db.commit()
    return {"rows": len(rows), "existing": len(existing),
            "missing": [r["code"] for r in missing], "created": created,
            "linked": linked,
            "class_node": cls.id if cls else None, "applied": bool(apply and cls)}


def _add_graph_data_membership(db: Session, project_id: int,
                               ent: Entity, cls: Entity) -> None:
    """把多类 instance_of 边也写进 graph_data（直接 UPDATE，避免触发全量行同步）。"""
    db.expire_all()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None or not isinstance(proj.graph_data, dict):
        return
    gd = dict(proj.graph_data)
    nodes = gd.get("nodes") or []
    edges = gd.get("edges") or []
    src = str(ent.uri or "").rsplit("/", 1)[-1]
    dst = str(cls.uri or "").rsplit("/", 1)[-1]
    if not src or not dst:
        return
    # 从 0 构建时缺失实例只有行表记录，画布 blob 里还没有节点；这里补节点，
    # 否则图谱/图谱页看不到对账补出来的接口。
    node = next((n for n in nodes if str(n.get("id")) == src), None)
    if node is None:
        nodes.append({
            "id": src,
            "data": {
                "type": "owl:NamedIndividual",
                "label": ent.label,
                "confidence": float(ent.confidence or 0.95),
                "properties": ent.props or {},
                "class_label": ent.class_label,
            },
            "type": "default",
            "style": {"border": "2px solid #10b981", "padding": "10px",
                      "background": "#f8fafc", "borderRadius": "8px"},
            "position": {"x": 80, "y": 80},
        })
        gd["nodes"] = nodes
    else:
        nd = node.setdefault("data", {})
        nd["label"] = ent.label
        nd["class_label"] = ent.class_label
        nd["properties"] = {**(nd.get("properties") or {}), **(ent.props or {})}
    if any(str(e.get("source")) == src and str(e.get("target")) == dst
           and str(e.get("label")) in ("rdf:type", "instance_of") for e in edges):
        db.execute(update(Project).where(Project.id == project_id).values(graph_data=gd))
        return
    edges.append({
        "id": f"etype_multi_{ent.id}_{cls.id}",
        "data": {"type": "Type", "label": "rdf:type", "relation": "instance_of"},
        "type": "smoothstep", "label": "rdf:type", "source": src, "target": dst,
    })
    gd["edges"] = edges
    db.execute(update(Project).where(Project.id == project_id).values(graph_data=gd))


__all__ = ["scan_frontend_index", "reconcile_frontend_index"]
