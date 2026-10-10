# app/api/resolution.py - 实体消解与冲突端点（docs/design/03 §9，M3-5）
# 权限：[M:resolution] 模块码 + [PR:editor]（写）/ [PR:view]（读）。
# run 走 extract 队列 Celery（复用 extraction 的进度/SSE）；merge/split/detect 同步（行表轻操作）。
# 审核队列 API（03 §10）在 M3-6；本文件只暴露消解/冲突面。

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import Project, ReviewItem, User, get_db

router = APIRouter(prefix="/api/projects/{project_id}", tags=["resolution"])


def _load_project_or_404(project_id: int, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


@router.post("/resolution/run", dependencies=[Depends(require_module("resolution"))])
def start_resolution(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发起三层消解（04 §5）：body {scope?: "all"|"class:X", thresholds?, blocking?}。"""
    require_project_role(project_id, "editor", current_user, db)
    _load_project_or_404(project_id, db)
    body = body or {}
    scope = body.get("scope", "all")
    if scope != "all" and not scope.startswith("class:"):
        raise APIError('scope 取值 "all" 或 "class:<类名>"', code="INVALID_SCOPE",
                       http_status=400)
    thresholds = body.get("thresholds")
    if thresholds is not None and not isinstance(thresholds, dict):
        raise APIError("thresholds 必须为对象", code="INVALID_THRESHOLDS", http_status=400)
    blocking = body.get("blocking", "pinyin")
    if blocking not in ("pinyin", "none"):
        raise APIError('blocking 取值 "pinyin"|"none"', code="INVALID_BLOCKING",
                       http_status=400)

    from app.tasks.extract_tasks import dispatch_resolution

    dispatched = dispatch_resolution(project_id, scope, thresholds, blocking)
    return {"task_id": dispatched["task_id"], "mode": dispatched["mode"],
            "scope": scope, "blocking": blocking}


@router.get("/resolution/clusters", dependencies=[Depends(require_module("resolution"))])
def list_clusters(
    project_id: int,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """消解结果：entity_merge 审核项（pending=待审聚类 / approved=已执行合并留痕）。"""
    require_project_role(project_id, "viewer", current_user, db)
    _load_project_or_404(project_id, db)
    q = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "entity_merge",
    )
    if status:
        q = q.filter(ReviewItem.status == status)
    rows = q.order_by(ReviewItem.id.desc()).limit(200).all()
    return {"items": [
        {"id": r.id, "status": r.status, "payload": r.payload, "reason": r.reason,
         "priority": r.priority, "result_ref": r.result_ref,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows]}


@router.post("/resolution/merge", dependencies=[Depends(require_module("resolution"))])
def manual_merge(
    project_id: int,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动合并（03 §9）：{canonical_id, duplicate_ids[], property_strategy?}。"""
    require_project_role(project_id, "editor", current_user, db)
    _load_project_or_404(project_id, db)
    try:
        canonical_id = int(body["canonical_id"])
        duplicate_ids = [int(i) for i in body.get("duplicate_ids") or []]
    except (KeyError, TypeError, ValueError):
        raise APIError("需要 canonical_id(int) 与 duplicate_ids[int[]]",
                       code="INVALID_MERGE_BODY", http_status=400)
    from app.infrastructure.database import Entity

    bad = [i for i in [canonical_id, *duplicate_ids]
           if db.query(Entity).filter(Entity.id == i,
                                      Entity.project_id == project_id).first() is None]
    if bad:
        raise NotFoundError(f"实体不存在或不属于本项目: {bad}")
    from app.adapters.resolution import merge_entities

    res = merge_entities(canonical_id, duplicate_ids,
                         property_strategy=body.get("property_strategy",
                                                    "keep_most_complete"), db=db)
    return res.model_dump()


@router.post("/resolution/split", dependencies=[Depends(require_module("resolution"))])
def manual_split(
    project_id: int,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """拆分误合并（04 §5 可逆）：{canonical_id, split_ids[]}。"""
    require_project_role(project_id, "editor", current_user, db)
    _load_project_or_404(project_id, db)
    try:
        canonical_id = int(body["canonical_id"])
        split_ids = [int(i) for i in body.get("split_ids") or []]
    except (KeyError, TypeError, ValueError):
        raise APIError("需要 canonical_id(int) 与 split_ids[int[]]",
                       code="INVALID_SPLIT_BODY", http_status=400)
    from app.adapters.resolution import split_entities

    ok = split_entities(canonical_id, split_ids, db=db)
    if not ok:
        raise APIError("没有可拆分的成员（须为该 canonical 的 merged 成员）",
                       code="NOTHING_TO_SPLIT", http_status=400)
    return {"canonical_id": canonical_id, "split_ids": split_ids, "split": True}


@router.post("/conflicts/detect", dependencies=[Depends(require_module("resolution"))])
def detect_conflicts_endpoint(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """显式触发冲突检测（04 §6；value/type/relationship 默认，temporal 可选）。"""
    require_project_role(project_id, "editor", current_user, db)
    _load_project_or_404(project_id, db)
    body = body or {}
    allowed = {"value", "type", "relationship", "temporal"}
    types = body.get("types") or ["value", "type", "relationship"]
    if not isinstance(types, list) or any(t not in allowed for t in types):
        raise APIError(f"types 取值限 {sorted(allowed)}", code="INVALID_TYPES",
                       http_status=400)
    from app.tasks.extract_tasks import dispatch_conflict_detection

    dispatched = dispatch_conflict_detection(project_id, types=types)
    return {"task_id": dispatched["task_id"], "mode": dispatched["mode"]}


@router.get("/conflicts", dependencies=[Depends(require_module("resolution"))])
def list_conflicts(
    project_id: int,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """冲突列表 = review_items(item_type='conflict_*')。"""
    require_project_role(project_id, "viewer", current_user, db)
    _load_project_or_404(project_id, db)
    q = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type.in_(["conflict_value", "conflict_type",
                                  "conflict_relationship", "conflict_axiom"]),
    )
    if status:
        q = q.filter(ReviewItem.status == status)
    rows = q.order_by(ReviewItem.id.desc()).limit(200).all()
    return {"items": [
        {"id": r.id, "item_type": r.item_type, "status": r.status, "payload": r.payload,
         "reason": r.reason, "priority": r.priority,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows]}
