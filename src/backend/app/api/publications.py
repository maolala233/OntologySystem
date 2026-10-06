# app/api/publications.py - 发布与公共资产（docs/design/03 §12，M3-6）
# 发布校验链：知识域必填 → 无 pending/claimed critical/high 审核项 → TBox 健康检查（只警告不阻断）
# → 落版本(kind=publication) → graph_snapshots(purpose=publication) → publications + outbox。
# 公共区 /api/assets 只读投影（快照），无任何写入口。
# 只读强制：require_project_role(editor/owner) 对 published 项目抛 403 PUBLISHED_READONLY
# （deps 层统一拦截，publish/unpublish 传 allow_published=True 豁免）。
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import (
    GraphSnapshot,
    OntologyVersion,
    Project,
    Publication,
    User,
)
from app.services.audit_service import log_action, put_outbox

router = APIRouter(tags=["publications"])


def _project_or_404(project_id: int, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


def _serialize_pub(p: Publication, proj: Project | None = None) -> dict:
    out = {"id": p.id, "project_id": p.project_id, "version_id": p.version_id,
           "snapshot_id": p.snapshot_id, "status": p.status, "note": p.note,
           "published_by": p.published_by,
           "published_at": p.published_at.isoformat() if p.published_at else None,
           "unpublished_at": p.unpublished_at.isoformat() if p.unpublished_at else None}
    if proj is not None:
        out["project_name"] = proj.name
        out["domain"] = proj.domains
    return out


@router.post("/api/projects/{project_id}/publish",
             dependencies=[Depends(require_module("publish"))])
def publish(project_id: int, body: dict | None = None,
            current_user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    """发布（04 §10 校验链）→ 落版本(kind=publication) + 快照 + publications + 公共区可见。"""
    # allow_published=True：publish 幂等重发豁免只读拦截
    require_project_role(project_id, "owner", current_user, db, allow_published=True)
    proj = _project_or_404(project_id, db)
    body = body or {}

    # 门禁 1：知识域必填
    if not (proj.domain_id or (proj.domains or "").strip()):
        raise APIError("发布前必须设置知识域", code="DOMAIN_REQUIRED", http_status=400)
    # 门禁 2：无 pending/claimed 的 critical/high 审核项
    from app.services.review_service import publish_blockers

    blockers = publish_blockers(db, project_id)
    if blockers:
        raise APIError(f"存在 {len(blockers)} 个未处理的高优先级审核项，先完成审核",
                       code="PENDING_CRITICAL_REVIEWS", http_status=400,
                       detail={"blockers": blockers[:20]})
    # 门禁 3：TBox 健康检查（只警告不阻断，警告写入发布说明）
    from app.adapters.versioning import create_version, quality_gate

    gd = proj.graph_data or {}
    health = quality_gate(gd)

    # 落版本 + publication 快照 + 记录 + 状态机
    ver = create_version(db, proj, "publication", current_user.id,
                         label=body.get("label"),
                         description="；".join(health["warnings"]) or "发布快照")
    if not ver.label:
        ver.label = f"发布 v{ver.version_no}"
    snap = (db.query(GraphSnapshot)
            .filter(GraphSnapshot.project_id == project_id,
                    GraphSnapshot.version_id == ver.id,
                    GraphSnapshot.purpose == "version").first())
    pub = Publication(project_id=project_id, version_id=ver.id,
                      snapshot_id=snap.id if snap else 0,
                      status="published", note=body.get("note"),
                      published_by=current_user.id)
    db.add(pub)
    db.flush()
    proj.status = "published"
    proj.is_published = True  # 兼容期：旧前端读 is_published
    # TTL 重生成（DB 字段；M4：Neo4j 同步改走 outbox——graph_data 变更已由监听器
    # 发 project.graph_rebuilt → worker-graph 消费；publication.created → worker-rdf 写 Oxigraph）
    sync_warnings = []
    if proj.graph_data:
        try:
            from app.api.ontology import generate_ttl_from_graph_data

            proj.ttl_content = generate_ttl_from_graph_data(
                proj.graph_data.get("nodes", []), proj.graph_data.get("edges", []))
        except Exception as exc:  # noqa: BLE001
            sync_warnings.append(f"TTL 重生成失败: {exc}")
    put_outbox(db, "publication", pub.id, "publication.created",
               {"project_id": project_id, "version_id": ver.id,
                "snapshot_id": pub.snapshot_id})
    log_action(db, current_user.id, "publish", "project", project_id,
               {"publication_id": pub.id, "version_no": ver.version_no,
                "warnings": health["warnings"]})
    db.commit()
    # M4：即时派发 outbox 消费（beat 30s 兜底；事件后即时同步降低投影延迟）
    try:
        from app.tasks.graph_tasks import drain_outbox
        from app.tasks.rdf_tasks import drain_rdf_outbox

        drain_outbox.delay()
        drain_rdf_outbox.delay()
    except Exception:  # noqa: BLE001 —— broker 不可达时由 beat 兜底
        pass
    return {"publication_id": pub.id, "version_no": ver.version_no,
            "status": "published", "warnings": health["warnings"] + sync_warnings}


@router.post("/api/projects/{project_id}/unpublish",
             dependencies=[Depends(require_module("publish"))])
def unpublish(project_id: int, body: dict | None = None,
              current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """下线：公共区即刻不可见；快照与版本保留（历史可审计）。"""
    require_project_role(project_id, "owner", current_user, db, allow_published=True)
    _project_or_404(project_id, db)
    pub = (db.query(Publication)
           .filter(Publication.project_id == project_id,
                   Publication.status == "published")
           .order_by(Publication.id.desc()).first())
    if pub is None:
        raise APIError("项目当前未发布", code="NOT_PUBLISHED", http_status=400)
    pub.status = "unpublished"
    pub.unpublished_at = datetime.datetime.utcnow()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj:
        proj.status = "ready"
        proj.is_published = False  # 兼容期：旧前端读 is_published
    log_action(db, current_user.id, "unpublish", "project", project_id,
               {"publication_id": pub.id})
    db.commit()
    return {"publication_id": pub.id, "status": "unpublished"}


@router.get("/api/assets", dependencies=[Depends(require_module("asset_center"))])
def list_assets(current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """公共区：已发布项目列表（按知识域分组；仅读 publications status=published）。"""
    rows = (db.query(Publication, Project)
            .join(Project, Project.id == Publication.project_id)
            .filter(Publication.status == "published")
            .order_by(Publication.published_at.desc()).limit(200).all())
    grouped: dict[str, list] = {}
    for pub, proj in rows:
        domain = (proj.domains or "").strip() or "未分类"
        grouped.setdefault(domain, []).append(_serialize_pub(pub, proj))
    return {"groups": [{"domain": d, "items": items} for d, items in sorted(grouped.items())],
            "total": len(rows)}


@router.get("/api/assets/{project_id}",
            dependencies=[Depends(require_module("asset_center"))])
def asset_detail(project_id: int,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """资产详情 = 快照只读投影（readonly 旗标；前端据此隐藏一切写控件）。"""
    pub = (db.query(Publication)
           .filter(Publication.project_id == project_id,
                   Publication.status == "published")
           .order_by(Publication.id.desc()).first())
    if pub is None:
        raise NotFoundError(f"项目未发布或不存在: {project_id}")
    proj = _project_or_404(project_id, db)
    ver = db.query(OntologyVersion).filter(OntologyVersion.id == pub.version_id).first()
    snapshot = None
    if ver and ver.full_snapshot_key:
        from app.adapters.versioning import get_snapshot_json

        payload = get_snapshot_json(ver.full_snapshot_key)
        if payload:
            snapshot = {"stats": ver.stats, "checksum": ver.checksum,
                        "node_count": len((payload.get("graph_data") or {}).get("nodes") or []),
                        "edge_count": len((payload.get("graph_data") or {}).get("edges") or [])}
    return {**_serialize_pub(pub, proj), "readonly": True,
            "version_no": ver.version_no if ver else None,
            "stats": ver.stats if ver else None, "snapshot": snapshot}
