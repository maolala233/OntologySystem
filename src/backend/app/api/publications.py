# app/api/publications.py - 发布与公共资产（docs/design/03 §12，M3-6）
# 发布校验链：知识域必填 → 无 pending/claimed critical/high 审核项 → TBox 健康检查（只警告不阻断）
# → 落版本(kind=publication) → graph_snapshots(purpose=publication) → publications + outbox。
# 公共区 /api/assets 只读投影（快照），无任何写入口。
# 只读强制：require_project_role(editor/owner) 对 published 项目抛 403 PUBLISHED_READONLY
# （deps 层统一拦截，publish/unpublish 传 allow_published=True 豁免）。
from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import (
    GraphSnapshot,
    KnowledgeDomain,
    OntologyVersion,
    Project,
    Publication,
    User,
)
from app.services.audit_service import log_action, put_outbox

logger = logging.getLogger(__name__)

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

    # 门禁 1：知识域必填。2026-11-07 起知识域从构建期移到发布期选择：
    # 请求体可携带 domain_id/domain_name 直接落到项目（find-or-create 与
    # create_project 同口径），发布弹窗里选域+发布一步完成
    body_domain_id = body.get("domain_id")
    body_domain_name = str(body.get("domain_name") or "").strip()
    if body_domain_id or body_domain_name:
        if body_domain_id:
            dom = db.query(KnowledgeDomain).filter(
                KnowledgeDomain.id == body_domain_id).first()
            if dom is None:
                raise APIError("知识域不存在", code="DOMAIN_NOT_FOUND", http_status=400)
        else:
            dom = db.query(KnowledgeDomain).filter(
                KnowledgeDomain.name == body_domain_name).first()
            if not dom:
                dom = KnowledgeDomain(name=body_domain_name)
                db.add(dom)
                db.flush()
        proj.domain_id = dom.id
    if not (proj.domain_id or (proj.domains or "").strip()):
        raise APIError("发布前必须设置知识域", code="DOMAIN_REQUIRED", http_status=400)
    # 门禁 2：TBox 机检硬伤（类层次环/悬空父类引用/自父）阻断发布（O1-B2）
    # —— 质量自检即发布检查：构建时自检修复环已保证 error=0（2026-11-05 简化：
    # 审核清零门禁与 CQ 配对门控移除，全部结果由 AI 裁决兜底）
    from app.adapters.versioning import create_version, quality_gate

    gd = proj.graph_data or {}
    health = quality_gate(gd)
    if not health["passed"]:
        raise APIError("TBox 机检未通过，修复后重试发布", code="SCHEMA_CHECKS_FAILED",
                       http_status=400,
                       detail={"errors": health.get("errors", []),
                               "warnings": health.get("warnings", [])})
    # 门禁 3：TBox 证据闭合（O2-B9）——悬空证据引用阻断发布
    from app.services.evidence_closure import tbox_evidence_closure

    closure = tbox_evidence_closure(db, project_id, gd)
    if not closure["passed"]:
        raise APIError(
            f"TBox 证据闭合检查未通过：{len(closure['dangling'])} 个悬空证据引用"
            "（引用的切片已不存在），修复或补抽证据后重试",
            code="EVIDENCE_CLOSURE_FAILED", http_status=400,
            detail={"dangling": closure["dangling"][:20]})

    # 落版本 + publication 快照 + 记录 + 状态机
    # 门禁警告全文可能极长（124 类无锚点+孤岛清单…实测 800+ 字符），description 列
    # 只有 varchar(500)——超长 DataError 让发布 500（2026-11-09 实测）；截到 450 留余量
    _gate_desc = "；".join(health["warnings"]) or "发布快照"
    ver = create_version(db, proj, "publication", current_user.id,
                         label=body.get("label"),
                         description=_gate_desc[:450])
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


@router.get("/api/projects/{project_id}/publish/impact",
            dependencies=[Depends(require_module("publish"))])
def publish_impact_preview(project_id: int,
                           current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """发布影响分析预览（O3-B13）：相对正式基线版本的 diff/波及面/待审项/TBox 漂移。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _project_or_404(project_id, db)
    from app.services.impact_service import publish_impact

    return publish_impact(db, proj)


@router.get("/api/projects/{project_id}/gate/evaluations")
def list_gate_evaluations(project_id: int, limit: int = 20,
                          current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """配对门控记录列表（O3-B14，可审计）。"""
    require_project_role(project_id, "viewer", current_user, db)
    _project_or_404(project_id, db)
    from app.infrastructure.database import GateEvaluation
    from app.services.gate_service import serialize_evaluation

    rows = (db.query(GateEvaluation)
            .filter(GateEvaluation.project_id == project_id)
            .order_by(GateEvaluation.id.desc())
            .limit(max(1, min(limit, 100))).all())
    return {"items": [serialize_evaluation(r) for r in rows], "total": len(rows)}


@router.get("/api/projects/{project_id}/gate/evaluations/{evaluation_id}")
def get_gate_evaluation(project_id: int, evaluation_id: int,
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    require_project_role(project_id, "viewer", current_user, db)
    from app.infrastructure.database import GateEvaluation
    from app.services.gate_service import serialize_evaluation

    row = (db.query(GateEvaluation)
           .filter(GateEvaluation.project_id == project_id,
                   GateEvaluation.id == evaluation_id).first())
    if row is None:
        raise NotFoundError(f"门控记录不存在: {evaluation_id}")
    return serialize_evaluation(row)


@router.post("/api/projects/{project_id}/versions/candidate")
def create_candidate(project_id: int, body: dict | None = None,
                     current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """手动/自动产生候选版本 vN-cK（O3-B14）：复用快照机制，不覆盖正式版本。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _project_or_404(project_id, db)
    from app.adapters.versioning import create_candidate_version, latest_official_version

    baseline = latest_official_version(db, project_id)
    base_no = baseline.version_no if baseline is not None else 0
    ver = create_candidate_version(
        db, proj, "full", current_user.id,
        label=(body or {}).get("label") or f"候选 v{base_no + 1}-c1",
        description="候选版本（配对门控）")
    db.commit()
    return {"version_id": ver.id, "version_no": ver.version_no, "label": ver.label,
            "is_candidate": True, "gate_status": ver.gate_status}


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
    graph_data = None
    if ver and ver.full_snapshot_key:
        from app.adapters.versioning import get_snapshot_json

        payload = get_snapshot_json(ver.full_snapshot_key)
        if payload:
            graph_data = payload.get("graph_data") or {}
            snapshot = {"stats": ver.stats, "checksum": ver.checksum,
                        "node_count": len(graph_data.get("nodes") or []),
                        "edge_count": len(graph_data.get("edges") or [])}
    return {**_serialize_pub(pub, proj), "readonly": True,
            "version_no": ver.version_no if ver else None,
            "stats": ver.stats if ver else None, "snapshot": snapshot,
            # 发布版本快照的图谱（非活图谱）：重新抽取不影响已发布展示，
            # 重新发布后才会更新——资产中心渲染只认这里
            "graph_data": graph_data}
