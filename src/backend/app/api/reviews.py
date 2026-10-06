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

ITEM_TYPES = ("entity_merge", "new_class", "low_confidence_entity",
              "low_confidence_relation", "conflict_value", "conflict_type",
              "conflict_relationship", "missing_evidence")


def _serialize(r: ReviewItem, detail: bool = False) -> dict:
    out = {"id": r.id, "project_id": r.project_id, "item_type": r.item_type,
           "status": r.status, "priority": r.priority, "reason": r.reason,
           "payload": r.payload, "suggested_action": r.suggested_action,
           "assigned_to": r.assigned_to, "decided_by": r.decided_by,
           "decided_at": r.decided_at.isoformat() if r.decided_at else None,
           "result_ref": r.result_ref,
           "created_at": r.created_at.isoformat() if r.created_at else None}
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
                         current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """项目内审核队列（03 §10）。"""
    require_project_role(project_id, "viewer", current_user, db)
    q = db.query(ReviewItem).filter(ReviewItem.project_id == project_id)
    if status:
        q = q.filter(ReviewItem.status == status)
    if type:
        if type not in ITEM_TYPES:
            raise APIError(f"type 取值限 {ITEM_TYPES}", code="INVALID_TYPE",
                           http_status=400)
        q = q.filter(ReviewItem.item_type == type)
    if priority:
        q = q.filter(ReviewItem.priority == priority)
    rows = q.order_by(ReviewItem.priority.desc(), ReviewItem.id).limit(200).all()
    return {"items": [_serialize(r) for r in rows], "total": len(rows)}


@router.get("/reviews", dependencies=[Depends(require_module("review"))])
def list_all_reviews(status: str | None = Query(None),
                     type: str | None = Query(None),
                     current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """跨项目审核队列（审核工作台首页）：自动过滤为用户可 view 的项目。

    status/type 均支持逗号分隔多值（审核工作台多选过滤）。
    """
    q = db.query(ReviewItem)
    ids = _viewable_project_ids(db, current_user)
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
    rows = q.order_by(ReviewItem.priority.desc(), ReviewItem.id).limit(200).all()
    return {"items": [_serialize(r) for r in rows], "total": len(rows)}


@router.get("/reviews/{review_id}", dependencies=[Depends(require_module("review"))])
def review_detail(review_id: int,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """详情：payload 完整上下文（候选项、证据原文、冲突双方、建议动作）。"""
    item = _get_item_or_404(db, review_id)
    _guard_project_view(item.project_id, current_user, db)
    return _serialize(item, detail=True)


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
