# app/api/reviews.py - 审核队列（docs/design/03 §10，M3-6）
# 权限：[M:review] + 项目角色（view 读 / editor 裁决）。
# 跨项目队列 /api/reviews 自动过滤为用户有 view 权限的项目（审核工作台数据源）。
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import Project, ProjectMember, ReviewItem, User

router = APIRouter(prefix="/api", tags=["reviews"])

ITEM_TYPES = ("entity_merge", "new_class", "new_predicate", "low_confidence_entity",
              "low_confidence_relation", "conflict_value", "conflict_type",
              "conflict_relationship", "missing_evidence", "conflict_axiom",
              "axiom_suggestion", "schema_match_suggestion", "cq_suggestion")


def _project_name_map(db: Session) -> dict[int, str | None]:
    return dict(db.query(Project.id, Project.name).all())


def _reviewable_projects(db: Session, viewable_ids: list[int] | None) -> list[dict]:
    """可读范围内实际存在审核项的项目清单（项目筛选项数据源；不受状态/类型筛选影响保持稳定）。

    join Project 排除已删除项目的孤立审核项（项目行不存在的不进下拉）。
    """
    q = (db.query(ReviewItem.project_id)
           .join(Project, ReviewItem.project_id == Project.id).distinct())
    if viewable_ids is not None:
        q = q.filter(ReviewItem.project_id.in_(viewable_ids or [-1]))
    names = _project_name_map(db)
    return [{"id": pid, "name": names.get(pid)} for pid in sorted(r[0] for r in q.all())]


def _serialize(r: ReviewItem, detail: bool = False, project_names: dict[int, str | None] | None = None) -> dict:
    out = {"id": r.id, "project_id": r.project_id, "item_type": r.item_type,
           "status": r.status, "priority": r.priority, "reason": r.reason,
           "payload": r.payload, "suggested_action": r.suggested_action,
           "assigned_to": r.assigned_to, "decided_by": r.decided_by,
           "decided_at": r.decided_at.isoformat() if r.decided_at else None,
           "result_ref": r.result_ref,
           "created_at": r.created_at.isoformat() if r.created_at else None}
    if project_names is not None:
        out["project_name"] = project_names.get(r.project_id)
    if detail:
        out["payload"] = r.payload  # 详情含完整上下文（候选/证据/冲突双方/建议动作）
    return out


def _viewable_project_ids(db: Session, user: User) -> list[int] | None:
    """用户可 view 的项目 id 集合；仅超级管理员返回 None 表示不过滤（R11）。"""
    from app.core.deps import is_super_admin
    if is_super_admin(user):
        return None
    ids = {p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()}
    ids |= {m.project_id for m in db.query(ProjectMember.project_id).filter(
        ProjectMember.user_id == user.id).all()}
    return sorted(ids)


def _get_item_or_404(db: Session, review_id: int) -> ReviewItem:
    item = db.query(ReviewItem).filter(ReviewItem.id == review_id).first()
    if item is None:
        raise NotFoundError(f"审核项不存在: {review_id}")
    return item


def _guard_project_view(project_id: int, user: User, db: Session) -> None:
    require_project_role(project_id, "viewer", user, db)


@router.get("/projects/{project_id}/reviews",
            dependencies=[Depends(require_module("review"))])
def list_project_reviews(project_id: int,
                         status: str | None = Query(None),
                         type: str | None = Query(None),
                         priority: str | None = Query(None),
                         limit: int = Query(200, ge=1, le=2000),
                         offset: int = Query(0, ge=0),
                         current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """项目内审核队列（03 §10）。limit/offset 分页，total 为真实总数（不随 limit 截断）。"""
    require_project_role(project_id, "viewer", current_user, db)
    q = db.query(ReviewItem).filter(ReviewItem.project_id == project_id)
    if status:
        q = q.filter(ReviewItem.status.in_([s for s in status.split(",") if s]))
    if type:
        types = [t for t in type.split(",") if t]
        for t in types:
            if t not in ITEM_TYPES:
                raise APIError(f"type 取值限 {ITEM_TYPES}", code="INVALID_TYPE",
                               http_status=400)
        q = q.filter(ReviewItem.item_type.in_(types))
    if priority:
        q = q.filter(ReviewItem.priority == priority)
    total = q.count()
    rows = q.order_by(ReviewItem.priority.desc(), ReviewItem.id).offset(offset).limit(limit).all()
    names = _project_name_map(db)
    return {"items": [_serialize(r, project_names=names) for r in rows],
            "total": total, "limit": limit, "offset": offset}


@router.get("/reviews", dependencies=[Depends(require_module("review"))])
def list_all_reviews(status: str | None = Query(None),
                     type: str | None = Query(None),
                     project_id: str | None = Query(None),
                     limit: int = Query(200, ge=1, le=2000),
                     offset: int = Query(0, ge=0),
                     current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """跨项目审核队列（审核工作台首页）：自动过滤为用户可 view 的项目。

    status/type/project_id 均支持逗号分隔多值（审核工作台多选过滤）。
    limit/offset 分页，total 为真实总数（不随 limit 截断）。
    响应携带 projects（可读范围内有审核项的项目清单，供项目筛选下拉）。
    join Project：已删除项目的孤立审核项不再出现在工作台（items/total/projects 一致排除）。
    """
    ids = _viewable_project_ids(db, current_user)
    projects = _reviewable_projects(db, ids)
    q = db.query(ReviewItem).join(Project, ReviewItem.project_id == Project.id)
    if ids is not None:
        q = q.filter(ReviewItem.project_id.in_(ids or [-1]))
    if status:
        q = q.filter(ReviewItem.status.in_([s for s in status.split(",") if s]))
    if type:
        types = [t for t in type.split(",") if t]
        for t in types:
            if t not in ITEM_TYPES:
                raise APIError(f"type 取值限 {ITEM_TYPES}", code="INVALID_TYPE",
                               http_status=400)
        q = q.filter(ReviewItem.item_type.in_(types))
    if project_id:
        try:
            filter_ids = [int(x) for x in project_id.split(",") if x.strip()]
        except ValueError:
            raise APIError("project_id 取值限逗号分隔的项目 ID", code="INVALID_PROJECT_FILTER",
                           http_status=400)
        q = q.filter(ReviewItem.project_id.in_(filter_ids))
    total = q.count()
    rows = q.order_by(ReviewItem.priority.desc(), ReviewItem.id).offset(offset).limit(limit).all()
    names = _project_name_map(db)
    return {"items": [_serialize(r, project_names=names) for r in rows],
            "total": total, "limit": limit, "offset": offset, "projects": projects}


@router.get("/reviews/{review_id}", dependencies=[Depends(require_module("review"))])
def review_detail(review_id: int,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """详情：payload 完整上下文（候选项、证据原文、冲突双方、建议动作）。"""
    item = _get_item_or_404(db, review_id)
    _guard_project_view(item.project_id, current_user, db)
    return _serialize(item, detail=True, project_names=_project_name_map(db))


@router.post("/reviews/{review_id}/claim", dependencies=[Depends(require_module("review"))])
def claim_review(review_id: int,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """认领 pending→claimed（防重复裁决）。"""
    item = _get_item_or_404(db, review_id)
    require_project_role(item.project_id, "editor", current_user, db)
    from app.services.review_service import ReviewStateError, claim_item

    try:
        claim_item(item, current_user, db)
    except ReviewStateError as e:
        raise APIError(str(e), code=e.code, http_status=409)
    return {"id": item.id, "status": item.status, "assigned_to": item.assigned_to}


@router.post("/reviews/{review_id}/decide", dependencies=[Depends(require_module("review"))])
async def decide_review(review_id: int, body: dict, request: Request,
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """裁决：{action: approve|reject|edit, edited_payload?, note?}；落地对应业务 + 审计。"""
    item = _get_item_or_404(db, review_id)
    require_project_role(item.project_id, "editor", current_user, db)
    from app.services.review_service import ReviewStateError, decide_item

    action = body.get("action")
    try:
        return decide_item(item, current_user, action,
                           edited_payload=body.get("edited_payload"),
                           note=body.get("note"), db=db,
                           ip=request.client.host if request.client else None)
    except ReviewStateError as e:
        raise APIError(str(e), code=e.code, http_status=409)


@router.post("/projects/{project_id}/reviews/batch-decide",
             dependencies=[Depends(require_module("review"))])
def batch_decide_reviews(project_id: int, body: dict,
                         current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """批量裁决（04 §7.2）：同类型同动作 ≤50；entity_merge 仅 approve 且 similarity≥0.75。"""
    require_project_role(project_id, "editor", current_user, db)
    from app.services.review_service import ReviewStateError, batch_decide

    ids = body.get("ids") or []
    try:
        return batch_decide(project_id, [int(i) for i in ids], body.get("action", ""),
                            current_user, db, note=body.get("note"))
    except ReviewStateError as e:
        raise APIError(str(e), code=e.code, http_status=400)
