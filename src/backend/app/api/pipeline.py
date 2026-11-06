# app/api/pipeline.py - 一键构建管线端点（新手简化，2026-11）
# POST /run    — 发起管线（幂等：已有进行中返回其 task_id）
# GET  /status — 管线进度聚合（stages 实时计数 + 审核队列统计），?task_id= 可查历史
# POST /cancel — 取消（阶段边界生效）
# 模块权限：documents/schema_build/instance_build/resolution/review 任一即可发起
# （新手项目往往只开基础模块，管线跨多模块，任一持有即可；项目角色仍须 editor）。
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import Project, ReviewItem, User
from app.tasks.pipeline_tasks import (
    cancel_scheduled_pipeline,
    dispatch_pipeline_run,
    pipeline_running_task_id,
    pipeline_scheduled_at,
    release_pipeline_lock,
)
from app.tasks.progress import finish_task_progress, get_task_progress

router = APIRouter(prefix="/api/projects/{project_id}/pipeline", tags=["pipeline"])


def _require_any_module(db: Session, user: User, codes: tuple[str, ...]) -> None:
    if user.role == "admin":
        return
    from app.core.deps import _user_module_context
    from app.core.permissions import resolve_user_modules

    grants, default_on, all_codes = _user_module_context(db, user)
    got = resolve_user_modules("user", grants, default_on, all_codes)
    if not any(c in got for c in codes):
        raise APIError("未开通构建相关模块", code="MODULE_NOT_GRANTED", http_status=403)


def _reviews_summary(db: Session, project_id: int) -> dict:
    """审核队列统计：需人裁决（critical/high pending/claimed）+ AI 已自动处理数。"""
    from sqlalchemy import func as sa_func

    rows = (db.query(ReviewItem.status, ReviewItem.priority, sa_func.count())
            .filter(ReviewItem.project_id == project_id)
            .group_by(ReviewItem.status, ReviewItem.priority).all())
    pending_by_priority: dict[str, int] = {}
    need_human = 0
    for status, priority, cnt in rows:
        if status in ("pending", "claimed") and priority in ("critical", "high"):
            need_human += int(cnt)
        if status == "pending":
            pending_by_priority[priority] = pending_by_priority.get(priority, 0) + int(cnt)
    auto_handled = 0
    auto_rows = (db.query(ReviewItem)
                 .filter(ReviewItem.project_id == project_id,
                         ReviewItem.status.in_(("approved", "edited")))
                 .all())
    for r in auto_rows:
        if (r.result_ref or {}).get("auto_rule"):
            auto_handled += 1
    return {"pending_by_priority": pending_by_priority,
            "pending_total": sum(pending_by_priority.values()),
            "need_human": need_human, "auto_handled": auto_handled}


@router.post("/run")
def run_pipeline_endpoint(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发起一键构建管线（上传→解析→抽取→消解→冲突→公理→自动裁决 一条龙）。"""
    _require_any_module(db, current_user,
                        ("documents", "schema_build", "instance_build",
                         "resolution", "review"))
    require_project_role(project_id, "editor", current_user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    body = body or {}
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    from app.tasks.pipeline_tasks import DEPTH_PROFILES

    depth = str(options.get("depth") or "standard")
    if depth not in DEPTH_PROFILES:
        raise APIError("depth 取值限 fast|standard|deep", code="INVALID_DEPTH",
                       http_status=400)
    if options.get("restart") is not None:
        options["restart"] = bool(options["restart"])
    # 0 文档直接拒绝：空管线除白烧一次 AI 调用（公理扫描等）外无任何产出，
    # 还会把项目置成「构建中」误导用户（2026-11-06 项目 3784 实测）
    from sqlalchemy import func as sa_func
    from app.infrastructure.database import UploadedDocument
    doc_count = (db.query(sa_func.count(UploadedDocument.id))
                 .filter(UploadedDocument.project_id == project_id,
                         UploadedDocument.deleted_at.is_(None)).scalar() or 0)
    if doc_count == 0:
        raise APIError("请先上传文档再开始构建", code="NO_DOCUMENTS", http_status=400)
    dispatched = dispatch_pipeline_run(project_id,
                                       trigger_user_id=current_user.id,
                                       options=options)
    return dispatched


@router.get("/status")
def pipeline_status_endpoint(
    project_id: int,
    task_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """管线进度聚合：stages 实时计数（不展示预估时间）+ 审核统计。

    task_id 缺省时读项目当前管线锁；锁过期（6h）后可显式传 task_id 查 24h 内历史。
    """
    require_project_role(project_id, "viewer", current_user, db)
    tid = task_id or pipeline_running_task_id(project_id)
    prog = get_task_progress(tid) if tid else None
    if tid and prog is None:
        # 锁残留但进度键已丢（>24h）：清锁恢复可再次发起
        if not task_id:
            release_pipeline_lock(project_id)
            tid = None
    # 心跳死亡检测：进度键还停在 running/queued 但超过 15 分钟无更新 =
    # worker 已死（重启/掉线）——清锁返回 idle，否则"一键构建"被死锁假状态
    # 永久挡住、页面永远转圈（2026-11-05 项目 249 实测）
    if prog is not None and prog.get("status") in ("running", "queued") \
            and int(time.time()) - int(prog.get("updated_at") or 0) > 900:
        # 仅当死任务正是锁持有者时才清锁（显式查询更老任务不动当前运行）
        if pipeline_running_task_id(project_id) == tid:
            release_pipeline_lock(project_id)
        tid = None
        prog = None
    summary = _reviews_summary(db, project_id)
    if prog is None:
        sched = pipeline_scheduled_at(project_id)
        if sched:
            return {"status": "scheduled", "task_id": None, "stages": [],
                    "scheduled_at": sched, "reviews": summary}
        return {"status": "idle", "task_id": None, "stages": [], "reviews": summary}
    if int((prog.get("stats") or {}).get("project_id") or -1) != project_id:
        raise NotFoundError(f"任务不存在: {tid}")  # 不泄露他项目任务
    return {"status": prog.get("status"), "task_id": tid,
            "stage": prog.get("stage"), "percent": prog.get("percent"),
            "message": prog.get("message"), "stages": (prog.get("stats") or {}).get("stages") or [],
            "stats": prog.get("stats") or {}, "reviews": summary}


@router.post("/cancel")
def cancel_pipeline_endpoint(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消管线：取消键同时写给管线与运行中的子任务（子任务在切片间隙检查自己的键，
    当前这次 LLM 调用返回后即中断；阶段产物只在末尾落库，中断不留半成品，
    重新构建会重跑被中断的阶段）。"""
    require_project_role(project_id, "editor", current_user, db)
    # 定时构建（未开跑）取消：清登记即可
    if cancel_scheduled_pipeline(project_id):
        return {"cancelled": True, "kind": "scheduled"}
    tid = pipeline_running_task_id(project_id)
    if not tid:
        raise APIError("没有进行中的管线", code="PIPELINE_NOT_RUNNING", http_status=409)
    prog = get_task_progress(tid)
    if prog is not None and prog.get("status") not in ("running", "queued"):
        raise APIError("管线已结束", code="TASK_FINISHED", http_status=409)
    from app.tasks.celery_app import celery_app
    from app.tasks.extract_tasks import request_cancel

    request_cancel(tid)
    # 传导：正在 running 的阶段子任务也写取消键——骨架/实例抽取按切片批次
    # 检查各自的键，不传导则要等整个阶段跑完才停
    for s in (prog.get("stats") or {}).get("stages") or []:
        if s.get("status") == "running" and s.get("task_id"):
            request_cancel(s["task_id"])
    # 强制终结：terminate=False 靠任务在分块间隙自查取消键——但 LLM 调用挂住时
    # 永远走不到检查点（2026-11-05 实测"停止停不下来"）——terminate=True 直接
    # SIGTERM 池子子进程，任务立刻以 Terminated 异常收场（prefork 池自动补进程）
    celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
    for s in (prog.get("stats") or {}).get("stages") or []:
        if s.get("status") == "running" and s.get("task_id"):
            celery_app.control.revoke(s["task_id"], terminate=True, signal="SIGTERM")
    # 进度键立即落"已停止"终态 + 清管线锁：前端下一次轮询立刻翻到停止态
    #（主按钮变「重头开始」），不等 worker 收尸
    finish_task_progress(tid, "cancelled", "已停止（用户取消）",
                         stats=prog.get("stats"))
    release_pipeline_lock(project_id)
    return {"task_id": tid, "cancelled": True}


@router.get("/build/summary")
def build_summary_endpoint(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """构建结果页数据源：骨架/实例/公理/AI 自动处理四区计数与清单 + 机检结论。

    轻量聚合（全部读 graph_data 与既有行表/审核表，零 LLM 调用）。
    """
    require_project_role(project_id, "viewer", current_user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    from sqlalchemy import func as sa_func

    from app.adapters.schema_checks import run_schema_checks
    from app.infrastructure.database import Entity, Relation
    from app.services.axiom_store import list_axioms

    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
    classes = [c for c in schema.get("classes") or [] if isinstance(c, dict)]
    ops = [o for o in schema.get("object_properties") or [] if isinstance(o, dict)]
    dprops = [d for d in schema.get("datatype_properties") or [] if isinstance(d, dict)]

    inst_total = db.query(func_count()).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status != "merged").scalar() or 0
    rel_total = db.query(func_count()).filter(
        Relation.project_id == project_id).scalar() or 0
    # 按类分组的实例计数（结果页分组预览）
    by_class_rows = (db.query(Entity.class_label, sa_func.count())
                     .filter(Entity.project_id == project_id,
                             Entity.is_class_node.is_(False),
                             Entity.status != "merged")
                     .group_by(Entity.class_label).all())
    by_class = {lbl: int(c) for lbl, c in by_class_rows}

    axioms = list_axioms(proj)
    reviews = _reviews_summary(db, project_id)

    # ── 结果总览明细（2026-11 交互改版）：类属性/类间关系/实例清单/实例关系 ──
    # 类属性 = schema 类目 properties + 挂类的 datatype_properties；类间关系 = ops
    inst_rows = (db.query(Entity)
                 .filter(Entity.project_id == project_id,
                         Entity.is_class_node.is_(False),
                         Entity.status.notin_(("rejected", "merged")))
                 .order_by(Entity.class_label, Entity.id)
                 .limit(500).all())
    rel_rows = (db.query(Relation)
                .filter(Relation.project_id == project_id,
                        Relation.status.notin_(("rejected",)))
                .limit(500).all())
    ent_index = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == project_id).all()}
    class_names = {str(c.get("label") or "") for c in classes}
    class_relations = [
        {"predicate": str(o.get("label") or ""),
         "domain": str(o.get("domain") or "") or None,
         "range": str(o.get("range") or "") or None,
         "functional": bool(o.get("functional")),
         "transitive": bool(o.get("transitive")),
         "symmetric": bool(o.get("symmetric")),
         "inverse_of": str(o.get("inverse_of") or "") or None}
        for o in ops if (o.get("domain") in class_names and o.get("range") in class_names)
    ][:100]

    checks = run_schema_checks(schema)
    # 最近一次管线任务信息（质量自检轮次等）
    tid = pipeline_running_task_id(project_id)
    prog = get_task_progress(tid) if tid else None
    last_pipeline = None
    if prog and int((prog.get("stats") or {}).get("project_id") or -1) == project_id:
        st = prog.get("stats") or {}
        last_pipeline = {"task_id": tid, "status": prog.get("status"),
                         "message": prog.get("message"), "depth": st.get("depth"),
                         "selfcheck": st.get("selfcheck"), "stages": st.get("stages")}

    return {
        "schema": {
            "classes_total": len(classes),
            "object_properties_total": len(ops),
            "datatype_properties_total": len(dprops),
            "classes": [{"label": c.get("label"),
                         "definition": (c.get("definition") or "")[:200],
                         "aliases": c.get("aliases") or [],
                         "parents": c.get("parent_classes") or [],
                         "properties": [
                             {"name": str(p.get("name") or ""),
                              "data_type": str(p.get("data_type") or "string"),
                              "description": str(p.get("description") or "")[:120]}
                             for p in (c.get("properties") or [])
                             if isinstance(p, dict) and p.get("name")][:30]}
                        for c in classes[:200]],
            "class_relations": class_relations,
        },
        "instances": {"total": int(inst_total), "by_class": by_class,
                      "items": [{"id": e.id, "label": e.label,
                                 "class_label": e.class_label,
                                 "props": e.props or {}}
                                for e in inst_rows]},
        "instance_relations": [{"subject_id": r.subject_id, "object_id": r.object_id,
                                "predicate": r.predicate,
                                "subject_label": (ent_index.get(r.subject_id).label
                                                  if ent_index.get(r.subject_id) else ""),
                                "object_label": (ent_index.get(r.object_id).label
                                                 if ent_index.get(r.object_id) else "")}
                               for r in rel_rows],
        "relations_total": int(rel_total),
        "axioms": {"total": len(axioms), "items": axioms[:100]},
        "reviews": reviews,
        "checks": checks.summary(),
        "pipeline": last_pipeline,
        "is_published": bool(proj.is_published),
    }


def func_count():
    from sqlalchemy import func

    return func.count()
