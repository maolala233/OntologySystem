# app/api/versions.py - 版本与时间轴（docs/design/03 §11，M3-6）
# 权限：读 [PR:view]；打版/回滚 [M:version_control] + [PR:editor|owner]。
# 快照下载走 MinIO 预签名 URL（API 不代理字节流，M3-1 同款）。
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import OntologyVersion, Project, User
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/projects/{project_id}", tags=["versions"])


def _snapshot_bucket() -> str:
    from app.core.config import settings

    return settings.MINIO_BUCKET_PARSED


def _project_or_404(project_id: int, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


def _version_or_404(db: Session, project_id: int, vid: int) -> OntologyVersion:
    # vid 兼容 version_no 与行 id（时间轴/前端一般持 version_no）
    ver = db.query(OntologyVersion).filter(
        OntologyVersion.project_id == project_id,
        (OntologyVersion.id == vid) | (OntologyVersion.version_no == vid)).first()
    if ver is None:
        raise NotFoundError(f"版本不存在: {vid}")
    return ver


def _serialize(v: OntologyVersion) -> dict:
    return {"id": v.id, "version_no": v.version_no, "label": v.label, "kind": v.kind,
            "stats": v.stats, "checksum": v.checksum,
            "parent_version_id": v.parent_version_id,
            "created_by": v.created_by, "description": v.description,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "is_current": False}


@router.get("/versions")
def list_versions(project_id: int,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """版本列表（时间轴主数据）。"""
    require_project_role(project_id, "viewer", current_user, db)
    _project_or_404(project_id, db)
    rows = (db.query(OntologyVersion)
            .filter(OntologyVersion.project_id == project_id)
            .order_by(OntologyVersion.version_no.desc()).limit(200).all())
    items = [_serialize(v) for v in rows]
    cur = {p.current_version_id for p in [db.query(Project)
                                          .filter(Project.id == project_id).first()] or []}
    for it in items:
        it["is_current"] = it["id"] in cur
    return {"items": items, "total": len(items),
            "current_version_id": next(iter(cur), None)}


@router.post("/versions", dependencies=[Depends(require_module("version_control"))])
def create_version_endpoint(project_id: int, body: dict | None = None,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """手动打版本：{label?, description?, kind?}（自动版本在抽取完成/发布/回滚前产生）。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _project_or_404(project_id, db)
    body = body or {}
    kind = body.get("kind", "schema")
    if kind not in ("schema", "full"):
        raise APIError("手动打版 kind 取值 schema|full（publication/rollback 由系统产生）",
                       code="INVALID_KIND", http_status=400)
    from app.adapters.versioning import create_version

    ver = create_version(db, proj, kind, current_user.id,
                         label=body.get("label"), description=body.get("description"))
    log_action(db, current_user.id, "version.create", "project", project_id,
               {"version_no": ver.version_no, "kind": kind})
    db.commit()
    return _serialize(ver)


@router.get("/versions/{a}/diff/{b}")
def diff_versions_endpoint(project_id: int, a: int, b: int,
                           current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """diff：类/属性/实例/关系四级 added/removed/modified。"""
    require_project_role(project_id, "viewer", current_user, db)
    proj = _project_or_404(project_id, db)
    va, vb = _version_or_404(db, project_id, a), _version_or_404(db, project_id, b)
    from app.adapters.versioning import diff_versions

    try:
        diff = diff_versions(db, proj, va, vb)
    except ValueError as e:
        raise APIError(str(e), code="SNAPSHOT_MISSING", http_status=404)
    return {"a": va.version_no, "b": vb.version_no, **diff}


@router.get("/versions/{vid}/snapshot")
def snapshot_url(project_id: int, vid: int,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """下载快照（MinIO 预签名 URL，1h TTL）。"""
    require_project_role(project_id, "viewer", current_user, db)
    _project_or_404(project_id, db)
    ver = _version_or_404(db, project_id, vid)
    if not ver.full_snapshot_key:
        raise APIError("该版本无快照文件", code="SNAPSHOT_MISSING", http_status=404)
    from app.infrastructure.minio_client import get_minio_client

    try:
        url = get_minio_client().get_presigned_url(
            _snapshot_bucket(), ver.full_snapshot_key)
    except Exception as e:  # noqa: BLE001
        raise APIError(f"快照预签名失败: {e}", code="SNAPSHOT_UNAVAILABLE",
                       http_status=503)
    return {"version_no": ver.version_no, "url": url, "checksum": ver.checksum,
            "expires_in": 3600}


@router.post("/versions/{vid}/restore", dependencies=[Depends(require_module("version_control"))])
def restore_version_endpoint(project_id: int, vid: int, body: dict | None = None,
                             current_user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    """回滚：先自动落 pre_rollback 快照，再恢复 + 新版本（kind=rollback）。仅 owner。"""
    require_project_role(project_id, "owner", current_user, db)
    proj = _project_or_404(project_id, db)
    ver = _version_or_404(db, project_id, vid)
    from app.adapters.versioning import restore_version

    try:
        res = restore_version(db, proj, ver, current_user.id)
    except ValueError as e:
        raise APIError(str(e), code="SNAPSHOT_MISSING", http_status=404)
    log_action(db, current_user.id, "rollback", "project", project_id,
               {"restored_from": res["restored_from"],
                "new_version_no": res["new_version_no"]})
    db.commit()
    return res


@router.get("/timeline")
def timeline_endpoint(project_id: int,
                      entity_uri: str | None = Query(None),
                      time_axis: str = Query("both"),
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """双时间轴（04 §9）：valid_from/valid_until + created_at；vis-timeline 数据源。"""
    require_project_role(project_id, "viewer", current_user, db)
    _project_or_404(project_id, db)
    if time_axis not in ("valid", "transaction", "both"):
        raise APIError('time_axis 取值 valid|transaction|both', code="INVALID_AXIS",
                       http_status=400)
    from app.adapters.versioning import timeline

    return timeline(db, project_id, entity_uri=entity_uri, time_axis=time_axis)


@router.get("/entities/{entity_id}/history")
def entity_history_endpoint(project_id: int, entity_id: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """单实体变更史（get_node_history 语义）：行表状态 + 溯源 + 合并留痕。"""
    require_project_role(project_id, "viewer", current_user, db)
    from app.adapters.versioning import entity_history

    try:
        return entity_history(db, project_id, entity_id)
    except ValueError as e:
        raise NotFoundError(str(e))
