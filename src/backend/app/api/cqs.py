# app/api/cqs.py - Competency Questions CRUD + 评测报告（O3-B12/F8，docs/design/09 §2）
# 项目级 CQ 列表：增删改排序、来源标记（manual/evolution）、评测结果视图。
# 评测 = 检索式跑通率/证据覆盖率（确定性）；抽取完成后由 extract_tasks 自动触发。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import CqEvaluation, User
from app.services import cq_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/projects", tags=["competency-questions"])


def _proj_or_404(db: Session, project_id: int):
    from app.infrastructure.database import Project

    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


@router.get("/{project_id}/cqs")
def list_cqs(project_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    require_project_role(project_id, "viewer", user, db)
    _proj_or_404(db, project_id)
    rows = cq_service.list_cqs(db, project_id)
    return {"items": [cq_service.serialize_cq(c) for c in rows],
            "total": len(rows)}


@router.post("/{project_id}/cqs")
def create_cqs(project_id: int, body: dict,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """批量创建：body={"items":[{"question","answer_criteria"?},...]}（单条也走 items）。"""
    require_project_role(project_id, "editor", user, db)
    _proj_or_404(db, project_id)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise APIError("items 必须为非空数组", code="CQ_ITEMS_REQUIRED", http_status=400)
    if len(items) > 100:
        raise APIError("单次最多 100 条", code="CQ_ITEMS_TOO_MANY", http_status=400)
    for it in items:
        if not isinstance(it, dict) or not str(it.get("question") or "").strip():
            raise APIError("每条 CQ 必须有非空 question", code="CQ_QUESTION_REQUIRED",
                           http_status=400)
        if len(str(it["question"])) > 2000:
            raise APIError("question 不能超过 2000 字", code="CQ_QUESTION_TOO_LONG",
                           http_status=400)
    rows = cq_service.create_cqs(db, project_id, items, created_by=user.id)
    db.commit()
    log_action(db, user.id, "cq.create", "project", str(project_id),
               {"count": len(rows)})
    db.commit()
    return {"created": len(rows),
            "items": [cq_service.serialize_cq(c) for c in rows]}


@router.put("/{project_id}/cqs/reorder")
def reorder_cqs(project_id: int, body: dict,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    require_project_role(project_id, "editor", user, db)
    _proj_or_404(db, project_id)
    ids = body.get("ordered_ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise APIError("ordered_ids 必须为整数数组", code="CQ_ORDER_INVALID", http_status=400)
    n = cq_service.reorder_cqs(db, project_id, ids)
    db.commit()
    return {"reordered": n}


@router.put("/{project_id}/cqs/{cq_id}")
def update_cq(project_id: int, cq_id: int, body: dict,
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    require_project_role(project_id, "editor", user, db)
    _proj_or_404(db, project_id)
    row = cq_service.update_cq(db, project_id, cq_id, body or {})
    if row is None:
        raise NotFoundError(f"CQ 不存在: {cq_id}")
    db.commit()
    return cq_service.serialize_cq(row)


@router.delete("/{project_id}/cqs/{cq_id}")
def delete_cq(project_id: int, cq_id: int,
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    require_project_role(project_id, "editor", user, db)
    _proj_or_404(db, project_id)
    ok = cq_service.delete_cq(db, project_id, cq_id)
    if not ok:
        raise NotFoundError(f"CQ 不存在: {cq_id}")
    db.commit()
    return {"deleted": cq_id}


@router.post("/{project_id}/cqs/evaluate")
def evaluate_cqs(project_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """手动触发 CQ 评测（检索式跑通率/证据覆盖率），生成评测报告行。"""
    require_project_role(project_id, "viewer", user, db)
    _proj_or_404(db, project_id)
    ev = cq_service.evaluate_cqs(db, project_id, trigger="manual", created_by=user.id)
    if ev is None:
        raise APIError("项目没有启用的 CQ，先在 CQ 管理中添加",
                       code="CQ_EMPTY", http_status=400)
    db.commit()
    log_action(db, user.id, "cq.evaluate", "project", str(project_id),
               {"evaluation_id": ev.id, "answerable": ev.answerable_count})
    db.commit()
    return cq_service.serialize_evaluation(ev)


@router.get("/{project_id}/cqs/evaluations")
def list_evaluations(project_id: int, limit: int = 20,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    require_project_role(project_id, "viewer", user, db)
    _proj_or_404(db, project_id)
    rows = (db.query(CqEvaluation)
            .filter(CqEvaluation.project_id == project_id)
            .order_by(CqEvaluation.id.desc())
            .limit(max(1, min(limit, 100))).all())
    return {"items": [cq_service.serialize_evaluation(e) for e in rows],
            "total": len(rows)}


@router.get("/{project_id}/cqs/evaluations/{evaluation_id}")
def get_evaluation(project_id: int, evaluation_id: int,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    require_project_role(project_id, "viewer", user, db)
    row = (db.query(CqEvaluation)
           .filter(CqEvaluation.project_id == project_id,
                   CqEvaluation.id == evaluation_id).first())
    if row is None:
        raise NotFoundError(f"评测报告不存在: {evaluation_id}")
    return cq_service.serialize_evaluation(row)
