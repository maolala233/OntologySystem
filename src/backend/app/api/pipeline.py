# app/api/pipeline.py - 一键构建管线端点（新手简化，2026-11）
# POST /run    — 发起管线（幂等：已有进行中返回其 task_id）
# GET  /status — 管线进度聚合（stages 实时计数 + 审核队列统计），?task_id= 可查历史
# POST /cancel — 取消（阶段边界生效）
# 模块权限：documents/schema_build/instance_build/resolution/review 任一即可发起
# （新手项目往往只开基础模块，管线跨多模块，任一持有即可；项目角色仍须 editor）。
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import Project, ReviewItem, User
from app.tasks.pipeline_tasks import (
    PIPELINE_STALE_MESSAGE,
    PIPELINE_STALE_SECONDS,
    build_stage_subs,
    build_sub_steps,
    cancel_scheduled_pipeline,
    display_sub_percent,
    force_finish_cancel_timeout,
    mark_pipeline_cancelling,
    dispatch_pipeline_run,
    pipeline_last_task_id,
    pipeline_running_task_id,
    pipeline_schedule_for,
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
    lean: bool = Query(False),
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
    # 心跳死亡检测：进度键还停在 running/queued 但超过 3 分钟无更新 =
    # worker 已死（重启/掉线）——落终态并清锁，否则"一键构建"被死锁假状态
    # 永久挡住、页面永远转圈（2026-11-05 项目 249 实测；2026-11-16 重启实测
    # 15 分钟阈值太长，改成 3 分钟——心跳每 60s 刷新一次，留 2 次容错）
    # 「停止中」超时兜底：worker 被 SIGKILL/卡在无检查点的调用里，收尾不会执行；
    # 超过阈值强制标已停止并释放锁，否则项目会一直卡在停止中、无法重新构建。
    if prog is not None and prog.get("status") == "cancelling":
        if force_finish_cancel_timeout(project_id, tid, prog):
            prog = None
            tid = None
    if prog is not None and prog.get("status") in ("running", "queued") \
            and int(time.time()) - int(prog.get("updated_at") or 0) > PIPELINE_STALE_SECONDS:
        # 仅当死任务正是锁持有者时才清锁（显式查询更老任务不动当前运行）
        if pipeline_running_task_id(project_id) == tid:
            # 心跳已超时也可能只是 LLM 长调用阻塞了心跳线程，任务本身还在跑：
            # 写取消键让它在下一个检查点停下，避免"锁清了、任务停不下来"
            #（2026-11-18 用户反馈：点停止提示没有进行中的管线，extract 日志仍在跑）
            try:
                from app.tasks.extract_tasks import request_cancel
                request_cancel(tid)
            except Exception:  # noqa: BLE001 —— 取消键失败不阻断清锁
                pass
            try:
                finish_task_progress(tid, "cancelled", PIPELINE_STALE_MESSAGE)
            except Exception:  # noqa: BLE001 —— 标终态失败不阻断清锁
                pass
            release_pipeline_lock(project_id)
        tid = None
        prog = None
    summary = _reviews_summary(db, project_id)
    if prog is None and not task_id:
        # 锁不在（未构建/已释放）→ 回落最近一次构建登记（dispatch 时写，7d）：
        # 失败/完成态在页面刷新后仍可见，不再依赖前端 localStorage 猜测。
        # 心跳死亡的 running 帧不回落（视同无任务，让用户能重新发起）。
        last_tid = pipeline_last_task_id(project_id)
        last_prog = get_task_progress(last_tid) if last_tid else None
        if last_prog and int((last_prog.get("stats") or {}).get("project_id") or -1) == project_id:
            # 停止中：锁已被 worker finally 释放、但状态还停在 cancelling——这里
            # 按取消时间戳做超时兜底，超时强制收尾，避免页面永远卡在停止中。
            if last_prog.get("status") == "cancelling":
                if force_finish_cancel_timeout(project_id, last_tid, last_prog):
                    last_prog = get_task_progress(last_tid)
            stale_running = last_prog is not None and last_prog.get("status") in ("running", "queued") \
                and int(time.time()) - int(last_prog.get("updated_at") or 0) > PIPELINE_STALE_SECONDS
            if last_prog is not None and not stale_running:
                tid, prog = last_tid, last_prog
    if prog is None:
        sched = pipeline_schedule_for(project_id)
        if sched:
            # schedule 带档位/是否重头开始/触发人，前端无需再解析原始 Redis payload
            return {"status": "scheduled", "task_id": None, "stages": [],
                    "scheduled_at": sched.get("fire_at"), "schedule": sched,
                    "reviews": summary}
        return {"status": "idle", "task_id": None, "stages": [], "reviews": summary}
    if int((prog.get("stats") or {}).get("project_id") or -1) != project_id:
        raise NotFoundError(f"任务不存在: {tid}")  # 不泄露他项目任务
    stage = prog.get("stage")
    sub = get_task_progress(f"{tid}:{stage}") if tid and stage else None
    out = {"status": prog.get("status"), "task_id": tid,
           "stage": stage, "percent": prog.get("percent"),
           "message": prog.get("message"),
           "stages": (prog.get("stats") or {}).get("stages") or [],
           "stats": prog.get("stats") or {}, "reviews": summary}
    # 各阶段子步骤回放（方案 A，2026-11-17）：定时构建晚进入、刷新、换浏览器
    # 时，前端没有早期阶段的帧缓存；这里一次性下发，前端可直接渲染。
    out["stage_subs"] = build_stage_subs(tid, out["stages"])
    if sub is not None:
        # 与 SSE 帧同构（2026-11-16）：首屏直接带子阶段与子步骤，断流时详情区也不空白
        out["sub"] = {"stage": stage,
                      "percent": display_sub_percent(stage, sub),
                      "message": sub.get("message"),
                      "status": sub.get("status"),
                      "stats": sub.get("stats") or {},
                      "steps": build_sub_steps(stage, sub)}
    if lean:
        # 轻量状态（2026-11-19）：详情页只用阶段/子进度；stats 里还带着一份 stages
        # 副本（SSE 帧才需要），这里不再随包下发。实测 9.4KB → 6.2KB
        out.pop("stats", None)
    return out


@router.get("/events")
async def pipeline_events(
    project_id: int,
    ticket: str = Query(...),
    last_task: str | None = Query(None),
    request: Request = None,
):
    """SSE 订阅管线进度：运行锁为唯一事实源，idle→running 的跃迁也推送。

    复用 M1 一次性 ticket 鉴权（EventSource 无法携带 Authorization 头）。
    每秒读 Redis 进度键、diff 才推（帧率=变化率，不是轮询风暴）；终态推完
    流保持打开继续等下一次构建（用户坐在页面上点「一键构建」同样免轮询）；
    15s 心跳防代理断流；30min 上限后发 timeout 收流，前端重连换新 ticket。

    async 生成器 + 每秒 is_disconnected 检查（2026-11-08 事故）：客户端关页/
    断连后流必须退出，否则 uvicorn --reload 优雅关闭会被长连接拖住
    "Waiting for connections to close" 卡 9 分钟（旧 sync 生成器收不到取消）。

    会话短作用域（2026-11-10）：db 只用于流前的 ticket/用户/角色校验，流内
    只读 Redis。不走请求级 Depends(get_db)——生成器依赖的清理要等响应结束，
    而本流终态不关+30min 上限+前端换票重连，等于每个开着的项目页永久钉死
    一个池连接；30 连接池被若干标签页占满后所有接口排队 30s 超时（远程
    QueuePool 事故根因）。
    """
    import asyncio
    import json as _json

    import redis as _redis

    from app.core.deps import require_project_role
    from app.infrastructure.database import SessionLocal
    from app.services.env_config_service import redis_url
    from app.tasks.progress import get_task_progress

    client = _redis.Redis.from_url(redis_url(), socket_connect_timeout=2)
    raw = client.get(f"sse:ticket:{ticket}")
    if not raw:
        raise APIError("SSE ticket 无效或已过期", code="TICKET_INVALID", http_status=401)
    client.delete(f"sse:ticket:{ticket}")  # 一次性
    payload = _json.loads(raw)
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == payload.get("user_id")).first()
        if user is None or not user.is_active:
            raise APIError("SSE ticket 用户无效", code="TICKET_INVALID", http_status=401)
        require_project_role(project_id, "viewer", user, db)

    async def _stream():
        started = time.time()
        last_sent = ""
        heartbeat_at = time.time()
        while time.time() - started < 1800:
            if await request.is_disconnected():
                return
            # 兜底顺序（2026-11-19 停止流程反馈）：锁在 = 当前构建；锁释放后
            # 优先取服务端「最近一次发起」的登记，最后才用前端连接时带的
            # last_task——它在建连时就固化了，新一轮构建结束后仍指着上一轮，
            # 会把旧任务的 stages/percent 继续推给前端（阶段时间线整块挪位、
            # 百分比 78% → 42% 来回跳）
            tid = (pipeline_running_task_id(project_id)
                   or pipeline_last_task_id(project_id) or last_task)
            prog = get_task_progress(tid) if tid else None
            # 跨项目防泄露：last_task 属于别的项目时按无任务处理
            if prog is not None and int((prog.get("stats") or {}).get("project_id") or -1) != project_id:
                prog = None
            if prog is None:
                idle_line = _json.dumps({"status": "idle", "task_id": None,
                                         "project_id": project_id}, ensure_ascii=False)
                if idle_line != last_sent:
                    last_sent = idle_line
                    yield f"data: {idle_line}\n\n"
            else:
                # 帧里补上任务 id（2026-11-19）：Redis 原始进度键没有这个字段，而前端
                # 要拿它判断"这一帧是不是本地正在跟踪的任务"（旧任务帧丢弃），也靠它
                # 判断终态帧是否已经补刷过——缺了它，SSE 每次重连都会多打一个 status
                prog = {**prog, "task_id": tid}
                # 注入子阶段帧（2026-11-08）：当前阶段的子任务进度键里有切片级
                # 进度（chunks_done/total、百分比、ETA）——骨架/实例抽几十个切片
                # 时主键消息不变，没有这帧用户看到的就像卡死
                stage = prog.get("stage")
                sub = get_task_progress(f"{tid}:{stage}") if tid and stage else None
                if sub is not None:
                    prog = {**prog, "sub": {"stage": stage,
                                            "percent": display_sub_percent(stage, sub),
                                            "message": sub.get("message"),
                                            "status": sub.get("status"),
                                            "stats": sub.get("stats") or {},
                                            "steps": build_sub_steps(stage, sub)}}
                line = _json.dumps(prog, ensure_ascii=False)
                if line != last_sent:
                    last_sent = line
                    yield f"data: {line}\n\n"
            if time.time() - heartbeat_at > 15:
                heartbeat_at = time.time()
                yield ": heartbeat\n\n"
            await asyncio.sleep(1.0)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    prog = None
    if not tid:
        # 锁被看门狗/启动自检清掉、但 worker 任务仍在跑（"没有进行中的管线"
        # 却停不下来）：回退最近一次构建登记（7d），同样允许取消。
        last_tid = pipeline_last_task_id(project_id)
        last_prog = get_task_progress(last_tid) if last_tid else None
        if (last_prog and last_prog.get("status") in ("running", "queued", "cancelled")
                and int((last_prog.get("stats") or {}).get("project_id") or -1) == project_id):
            tid, prog = last_tid, last_prog
    if not tid:
        raise APIError("没有进行中的管线", code="PIPELINE_NOT_RUNNING", http_status=409)
    if prog is None:
        prog = get_task_progress(tid)
    if prog is not None and prog.get("status") not in ("running", "queued", "cancelled"):
        raise APIError("管线已结束", code="TASK_FINISHED", http_status=409)
    from app.tasks.celery_app import celery_app
    from app.tasks.extract_tasks import request_cancel

    request_cancel(tid)
    # 传导：正在 running 的阶段子任务也写取消键——骨架/实例抽取按切片批次
    # 检查各自的键，不传导则要等整个阶段跑完才停。
    # 阶段子键按 _run_stage_task 的确定性规则 {主id}:{阶段key} 拼，不依赖快照
    # 里的 task_id：那个快照可能停留在子任务入场前（task_id=null），照它遍历
    # 会整段漏写（2026-11-10 项目 272 inline 实测"停止了还在跑"根因①）；
    # parse 阶段的子键 {tid}:parse:{doc_id} 结构不同，仍从快照 task_id 读。
    for s in ((prog or {}).get("stats") or {}).get("stages") or []:
        if s.get("status") != "running":
            continue
        if s.get("key") and s["key"] != "parse":
            request_cancel(f"{tid}:{s['key']}")
        if s.get("task_id"):
            request_cancel(s["task_id"])
    # 强制终结：terminate=False 靠任务在分块间隙自查取消键——但 LLM 调用挂住时
    # 永远走不到检查点（2026-11-05 实测"停止停不下来"）——terminate=True 直接
    # SIGTERM 池子子进程，任务立刻以 Terminated 异常收场（prefork 池自动补进程）
    celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
    for s in ((prog or {}).get("stats") or {}).get("stages") or []:
        if s.get("status") == "running" and s.get("task_id"):
            celery_app.control.revoke(s["task_id"], terminate=True, signal="SIGTERM")
    # 进入「停止中」：保留运行锁，等 worker 在检查点退出或被 revoke；期间
    # dispatch 看到锁/停止中会拒绝重新构建，避免老任务还在写产物时新任务并发。
    # worker 收尾或超时兜底后才落 cancelled 并释放锁。
    mark_pipeline_cancelling(tid, prog)
    return {"task_id": tid, "cancelled": True, "stopping": True}


@router.get("/report")
def get_build_report(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """构建报告（人话版 markdown）：最近一次一键构建的类树/统计/CQ 验证/
    业务规则/采集表规则层/待确认项。纯模板渲染，构建收尾自动更新。"""
    require_project_role(project_id, "viewer", current_user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    report = gd.get("build_report") if isinstance(gd.get("build_report"), dict) else None
    return {"markdown": (report or {}).get("markdown"),
            "generated_at": (report or {}).get("generated_at")}


@router.get("/build/summary")
def build_summary_endpoint(
    project_id: int,
    lean: bool = Query(False),
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

    # 已发布版本号（2026-11-19）：结果卡/发布页要显示「已发布 vN」，前端原本为此
    # 额外拉一次 /versions。这里按与前端一致的判定（kind='publication' 里 version_no
    # 最大的一条）直接带上；未发布项目不查，省掉这次接口调用。
    published_version_no = None
    if proj.is_published:
        from app.infrastructure.database import OntologyVersion
        published_version_no = (db.query(sa_func.max(OntologyVersion.version_no))
                                .filter(OntologyVersion.project_id == project_id,
                                        OntologyVersion.kind == "publication")
                                .scalar())

    summary_out = {
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
        "published_version_no": published_version_no,
    }
    if lean:
        # 轻量汇总（2026-11-19）：详情页只消费四项计数、机检结论与发布状态；
        # 类清单 / 实例清单 / 关系明细 / 公理清单是构建结果页的数据源，
        # 这里置空保持响应形状不变（实测 5.5KB → 0.4KB）
        summary_out["schema"]["classes"] = []
        summary_out["schema"]["class_relations"] = []
        summary_out["instances"] = {"total": summary_out["instances"]["total"],
                                    "by_class": {}, "items": []}
        summary_out["instance_relations"] = []
        summary_out["axioms"]["items"] = []
    return summary_out


def func_count():
    from sqlalchemy import func

    return func.count()
