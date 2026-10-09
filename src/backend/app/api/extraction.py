# app/api/extraction.py - 抽取端点（docs/design/03 §8，M3-4）
# 权限：[M:schema_build] 模块码 + [PR:editor] 项目角色（core/deps）。
# 本期只上 Schema 阶段（POST /schema + 任务查询/SSE/取消）；instances 端点 M3-5。
# SSE 复用 M3-2 parse-events 的 ticket 模式（EventSource 无法携带 Authorization 头）。
# 旧端点 /extract-schema[-from-documents] 保留不动，收编映射在 M3-7 执行（03 §18）。

import json as _json
import time

import redis as _redis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, require_module, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import DocumentChunk, Project, UploadedDocument, User, get_db

from app.tasks.extract_tasks import request_cancel
from app.tasks.progress import get_task_progress

router = APIRouter(prefix="/api/projects/{project_id}/extraction", tags=["extraction"])

_TASK_TTL_MAX_STREAM = 600  # SSE 上限 10 分钟（与 parse-events 一致）


def _check_task_of_project(task_id: str, project_id: int) -> dict:
    """任务进度存在且属于本项目（progress stats.project_id 对账），防跨项目探测。"""
    prog = get_task_progress(task_id)
    if prog is None:
        raise NotFoundError(f"任务不存在或已过期: {task_id}")
    if int((prog.get("stats") or {}).get("project_id") or -1) != project_id:
        raise NotFoundError(f"任务不存在: {task_id}")  # 不泄露他项目任务存在性
    return prog


def _resolve_extract_config_id(db: Session, current_user: User, body: dict,
                               project_id: int) -> int | None:
    """解析本次抽取使用的 model_configs id（R14 用户个人选择）。

    优先级：请求显式 model_config_id → 用户个人选择（user_model_picks, extract）。
    校验行存在/启用/对用户可见（global 或本项目 project 级）；不可用时回落
    None（默认解析：项目默认 → 全局默认），不阻塞抽取任务。
    """
    from app.infrastructure.database import ModelConfig, UserModelPick

    config_id = body.get("model_config_id")
    if not isinstance(config_id, int):
        pick = (db.query(UserModelPick)
                .filter(UserModelPick.user_id == current_user.id,
                        UserModelPick.purpose == "extract")
                .first())
        config_id = pick.config_id if pick else None
    if config_id is None:
        return None

    row = db.query(ModelConfig).filter(ModelConfig.id == config_id,
                                       ModelConfig.enabled.is_(True)).first()
    if row is None:
        return None
    if row.scope == "project" and row.project_id != project_id:
        return None  # 他项目的 project 级配置不可用（个人选择仅限 global/本项目）
    return config_id


@router.post("/schema", dependencies=[Depends(require_module("schema_build"))])
def start_schema_extraction(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发起 Schema 阶段抽取（03 §8）：读项目已解析切片 → extract 队列 → 画布骨架。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")

    body = body or {}
    document_ids = body.get("document_ids") or None
    parallelism = body.get("parallelism", 4)
    guidance = body.get("guidance")
    if not isinstance(parallelism, int) or not 1 <= parallelism <= 8:
        raise APIError("parallelism 取值 1-8", code="INVALID_PARALLELISM", http_status=400)
    if document_ids is not None and (
            not isinstance(document_ids, list) or not all(isinstance(i, int) for i in document_ids)):
        raise APIError("document_ids 必须为整数数组", code="INVALID_DOCUMENT_IDS", http_status=400)
    if guidance is not None:
        if not isinstance(guidance, str) or not guidance.strip():
            guidance = None
        elif len(guidance) > 2000:
            raise APIError("guidance 引导内容不能超过 2000 字", code="GUIDANCE_TOO_LONG", http_status=400)
        else:
            guidance = guidance.strip()

    q = (db.query(DocumentChunk)
         .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
         .filter(UploadedDocument.project_id == project_id,
                 UploadedDocument.deleted_at.is_(None)))
    if document_ids:
        q = q.filter(UploadedDocument.id.in_(document_ids))
    chunks_total = q.count()
    if chunks_total == 0:
        raise APIError("项目没有已解析切片，请先在文档 Tab 完成解析",
                       code="NO_CHUNKS", http_status=400)

    from app.tasks.extract_tasks import dispatch_schema_extraction

    model_config_id = _resolve_extract_config_id(db, current_user, body, project_id)
    dispatched = dispatch_schema_extraction(
        project_id, document_ids, parallelism,
        guidance=guidance, model_config_id=model_config_id)
    return {"task_id": dispatched["task_id"], "mode": dispatched["mode"],
            "chunks_total": chunks_total, "parallelism": parallelism}


@router.post("/instances", dependencies=[Depends(require_module("instance_build"))])
def start_instance_extraction(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发起 Instance 阶段抽取（03 §8）：需先有骨架（schema 键）；extract 队列。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    schema = ((proj.graph_data or {}).get("schema") or {})

    body = body or {}
    document_ids = body.get("document_ids") or None
    parallelism = body.get("parallelism", 4)
    if not isinstance(parallelism, int) or not 1 <= parallelism <= 8:
        raise APIError("parallelism 取值 1-8", code="INVALID_PARALLELISM", http_status=400)
    strict_gate = bool(body.get("strict_gate", True))
    promote_policy = body.get("promote_policy", "review")
    if promote_policy not in ("review", "discard", "auto"):
        raise APIError("promote_policy 取值 review|discard|auto",
                       code="INVALID_PROMOTE_POLICY", http_status=400)
    if document_ids is not None and (
            not isinstance(document_ids, list) or not all(isinstance(i, int) for i in document_ids)):
        raise APIError("document_ids 必须为整数数组", code="INVALID_DOCUMENT_IDS", http_status=400)

    # 框架复用（04 §9）：缺省用当前画布骨架；schema_version_no 指定历史框架版本快照
    schema_version_no = body.get("schema_version_no")
    if schema_version_no is not None:
        if not isinstance(schema_version_no, int):
            raise APIError("schema_version_no 必须为整数", code="INVALID_SCHEMA_VERSION",
                           http_status=400)
        from app.infrastructure.database import OntologyVersion
        ver = (db.query(OntologyVersion)
               .filter(OntologyVersion.project_id == project_id,
                       OntologyVersion.version_no == schema_version_no)
               .first())
        if ver is None:
            raise APIError(f"框架版本不存在: v{schema_version_no}",
                           code="SCHEMA_VERSION_NOT_FOUND", http_status=404)
        if ver.kind not in ("schema", "rollback", "full"):
            raise APIError(f"版本 v{schema_version_no} 类型({ver.kind})不可作为框架",
                           code="INVALID_SCHEMA_VERSION_KIND", http_status=400)
    elif not schema.get("classes"):
        raise APIError("项目还没有骨架，请先完成骨架抽取（或指定框架版本）",
                       code="NO_SCHEMA", http_status=400)

    q = (db.query(DocumentChunk)
         .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
         .filter(UploadedDocument.project_id == project_id,
                 UploadedDocument.deleted_at.is_(None)))
    if document_ids:
        q = q.filter(UploadedDocument.id.in_(document_ids))
    chunks_total = q.count()
    if chunks_total == 0:
        raise APIError("项目没有已解析切片，请先在文档 Tab 完成解析",
                       code="NO_CHUNKS", http_status=400)

    from app.tasks.extract_tasks import dispatch_instance_extraction

    model_config_id = _resolve_extract_config_id(db, current_user, body, project_id)
    dispatched = dispatch_instance_extraction(
        project_id, document_ids, strict_gate, promote_policy, parallelism,
        schema_version_no=schema_version_no, model_config_id=model_config_id)
    return {"task_id": dispatched["task_id"], "mode": dispatched["mode"],
            "chunks_total": chunks_total, "parallelism": parallelism,
            "strict_gate": strict_gate, "promote_policy": promote_policy,
            "schema_version_no": schema_version_no}


def _user_has_any_module(db: Session, user: User, codes: tuple[str, ...]) -> bool:
    if user.role == "admin":
        return True
    from app.core.deps import _user_module_context
    from app.core.permissions import resolve_user_modules

    grants, default_on, all_codes = _user_module_context(db, user)
    got = resolve_user_modules("user", grants, default_on, all_codes)
    return any(c in got for c in codes)


@router.get("/tasks/{task_id}")
def get_extraction_task(
    project_id: int,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询抽取任务进度（progress:{task_id}，24h TTL）。schema_build/instance_build 皆可查。"""
    if not _user_has_any_module(db, current_user, ("schema_build", "instance_build")):
        raise APIError("未开通模块: schema_build/instance_build",
                       code="MODULE_NOT_GRANTED", http_status=403)
    return _check_task_of_project(task_id, project_id)


@router.get("/tasks/{task_id}/events")
async def extraction_events(
    project_id: int,
    task_id: str,
    ticket: str = Query(...),
    db: Session = Depends(get_db),
):
    """SSE 订阅抽取进度。鉴权靠一次性 ticket（M1 auth/sse-ticket，60s），补模块码校验。"""
    from app.services.env_config_service import redis_url
    client = _redis.Redis.from_url(redis_url(), socket_connect_timeout=2)
    raw = client.get(f"sse:ticket:{ticket}")
    if not raw:
        raise APIError("SSE ticket 无效或已过期", code="TICKET_INVALID", http_status=401)
    client.delete(f"sse:ticket:{ticket}")  # 一次性
    payload = _json.loads(raw)
    user = db.query(User).filter(User.id == payload.get("user_id")).first()
    if user is None or not user.is_active:
        raise APIError("SSE ticket 用户无效", code="TICKET_INVALID", http_status=401)
    if not _user_has_any_module(db, user, ("schema_build", "instance_build")):
        raise APIError("未开通模块: schema_build/instance_build",
                       code="MODULE_NOT_GRANTED", http_status=403)

    # 进度尚未写入（任务在队列中/worker 刚启动）时允许建流，归属校验在流内首次读到进度时补做；
    # 直接 404 会让前端 SSE 建连即断、兜底轮询也拿不到任务，进度条卡死
    prog0 = get_task_progress(task_id)
    if prog0 is not None:
        if int((prog0.get("stats") or {}).get("project_id") or -1) != project_id:
            raise NotFoundError(f"任务不存在: {task_id}")  # 不泄露他项目任务存在性

    def _stream():
        started = time.time()
        last_sent = ""
        heartbeat_at = time.time()
        ownership_checked = prog0 is not None
        while time.time() - started < _TASK_TTL_MAX_STREAM:
            prog = get_task_progress(task_id)
            if prog is None:
                if last_sent:
                    yield "event: gone\ndata: {}\n\n"
                    return
                # 任务尚未写入进度（排队中）：继续等，不发事件
            else:
                if not ownership_checked:
                    ownership_checked = True
                    if int((prog.get("stats") or {}).get("project_id") or -1) != project_id:
                        yield "event: gone\ndata: {}\n\n"
                        return
                line = _json.dumps(prog, ensure_ascii=False)
                if line != last_sent:
                    last_sent = line
                    yield f"data: {line}\n\n"
                if prog.get("status") in ("completed", "failed", "cancelled"):
                    return
            if time.time() - heartbeat_at > 15:
                heartbeat_at = time.time()
                yield ": heartbeat\n\n"
            time.sleep(1.0)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(require_module("schema_build"))])
def cancel_extraction_task(
    project_id: int,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消抽取任务：写取消标记（worker 在 chunk 间隙检查）+ 软 revoke。"""
    require_project_role(project_id, "editor", current_user, db)
    _check_task_of_project(task_id, project_id)
    if get_task_progress(task_id).get("status") not in ("running", "queued"):
        raise APIError("任务已结束，无法取消", code="TASK_FINISHED", http_status=409)
    request_cancel(task_id)
    from app.tasks.celery_app import celery_app

    celery_app.control.revoke(task_id, terminate=False)
    return {"task_id": task_id, "cancelled": True}
