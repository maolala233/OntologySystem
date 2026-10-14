# app/api/ontology_imports.py - 标准本体导入与对齐（O3-B17，docs/design/09 §2）
# 导入 OWL/TTL 本体作为项目基础：可浏览（类/属性清单）可引用（匹配建议）；
# schema matching（JW + 向量）→ schema_match_suggestion 审核项，批准写别名 + 映射留痕。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import OntologyImport, Project, User
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/projects", tags=["ontology-imports"])


def _proj_or_404(db: Session, project_id: int) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


def _import_or_404(db: Session, project_id: int, import_id: int) -> OntologyImport:
    row = db.query(OntologyImport).filter(
        OntologyImport.id == import_id,
        OntologyImport.project_id == project_id).first()
    if row is None:
        raise NotFoundError(f"导入本体不存在: {import_id}")
    return row


@router.post("/{project_id}/ontology-imports")
def create_import(project_id: int, body: dict,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """导入本体（JSON body）：{"filename", "ttl_content"}（前端读文件后传文本，避免 multipart 依赖）。"""
    require_project_role(project_id, "editor", user, db)
    _proj_or_404(db, project_id)
    filename = str((body or {}).get("filename") or "").strip()
    ttl = str((body or {}).get("ttl_content") or "").strip()
    if not filename or not ttl:
        raise APIError("filename 与 ttl_content 必填", code="IMPORT_CONTENT_REQUIRED",
                       http_status=400)
    if len(ttl) > 4_000_000:
        raise APIError("本体文件过大（>4MB）", code="IMPORT_TOO_LARGE", http_status=400)

    from app.api.ontology import extract_schema_from_ttl

    try:
        schema_dict = extract_schema_from_ttl(ttl)
    except Exception as exc:  # noqa: BLE001
        raise APIError(f"本体解析失败：{exc}", code="IMPORT_PARSE_FAILED", http_status=400)
    classes = [c for c in (schema_dict.get("classes") or []) if isinstance(c, dict) and c.get("label")]
    props = [p for p in (schema_dict.get("object_properties") or [])
             if isinstance(p, dict) and p.get("label")]
    if not classes:
        raise APIError("本体中没有可识别的类（owl:Class / rdfs:subClassOf）",
                       code="IMPORT_NO_CLASSES", http_status=400)

    storage_key = None
    try:  # 快照正文入 MinIO（解析失败容错：无快照仍可导入，仅匹配时重新解析不可用）
        from app.infrastructure.minio_client import get_minio_client
        from app.core.config import settings

        key = f"ontology-imports/{project_id}/{__import__('uuid').uuid4().hex}.json"
        import json as _json

        get_minio_client().put_bytes(
            settings.MINIO_BUCKET_PARSED, key,
            _json.dumps({"classes": classes, "object_properties": props},
                        ensure_ascii=False).encode("utf-8"),
            content_type="application/json")
        storage_key = key
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(f"[ontology-import] 快照存储失败（不阻断）：{exc}")

    row = OntologyImport(project_id=project_id, filename=filename[:255],
                         storage_key=storage_key, format="ttl",
                         class_count=len(classes), property_count=len(props),
                         created_by=user.id)
    db.add(row)
    db.commit()
    log_action(db, user.id, "ontology_import.create", "project", str(project_id),
               {"import_id": row.id, "classes": len(classes)})
    db.commit()
    return {"id": row.id, "filename": row.filename, "class_count": row.class_count,
            "property_count": row.property_count, "storage_key": row.storage_key}


@router.get("/{project_id}/ontology-imports")
def list_imports(project_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    require_project_role(project_id, "viewer", user, db)
    _proj_or_404(db, project_id)
    rows = (db.query(OntologyImport).filter(OntologyImport.project_id == project_id)
            .order_by(OntologyImport.id.desc()).limit(100).all())
    return {"items": [{"id": r.id, "filename": r.filename, "format": r.format,
                       "class_count": r.class_count, "property_count": r.property_count,
                       "mapped_count": len(r.mappings or []),
                       "created_at": r.created_at.isoformat() if r.created_at else None}
                      for r in rows], "total": len(rows)}


@router.get("/{project_id}/ontology-imports/{import_id}")
def import_detail(project_id: int, import_id: int,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """导入本体浏览：类/属性清单（B17 验收"可浏览"）。"""
    require_project_role(project_id, "viewer", user, db)
    row = _import_or_404(db, project_id, import_id)
    classes: list[dict] = []
    props: list[dict] = []
    if row.storage_key:
        from app.adapters.versioning import get_snapshot_json

        payload = get_snapshot_json(row.storage_key) or {}
        classes = payload.get("classes") or []
        props = payload.get("object_properties") or []
    return {"id": row.id, "filename": row.filename, "class_count": row.class_count,
            "property_count": row.property_count, "mappings": row.mappings or [],
            "classes": [{"label": c.get("label"), "definition": c.get("definition"),
                         "parent": c.get("parent")} for c in classes[:500]],
            "object_properties": [{"label": p.get("label"), "domain": p.get("domain"),
                                   "range": p.get("range")} for p in props[:500]]}


@router.delete("/{project_id}/ontology-imports/{import_id}")
def delete_import(project_id: int, import_id: int,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    require_project_role(project_id, "editor", user, db)
    row = _import_or_404(db, project_id, import_id)
    db.delete(row)
    db.commit()
    return {"deleted": import_id}


@router.post("/{project_id}/ontology-imports/{import_id}/match")
def match_import(project_id: int, import_id: int,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """跑 schema matching：建议物化为审核项（带相似度证据，B17 验收）。"""
    require_project_role(project_id, "editor", user, db)
    proj = _proj_or_404(db, project_id)
    row = _import_or_404(db, project_id, import_id)
    from app.services.ontology_match import (materialize_match_suggestions,
                                             match_suggestions)

    embeddings_fn = None
    try:  # 向量通道可选：embedding 服务不可用时退化为纯 JW
        from app.services.resolution_service import _embeddings_fn

        embeddings_fn = _embeddings_fn(project_id)
    except Exception:  # noqa: BLE001
        embeddings_fn = None
    suggestions = match_suggestions(db, project_id, row,
                                    (proj.graph_data or {}).get("schema") or {},
                                    embeddings_fn=embeddings_fn)
    ids = materialize_match_suggestions(db, project_id, row, suggestions)
    db.commit()
    log_action(db, user.id, "ontology_import.match", "project", str(project_id),
               {"import_id": import_id, "suggestions": len(ids)})
    db.commit()
    return {"import_id": import_id, "suggestions": len(ids), "review_item_ids": ids,
            "items": suggestions}
