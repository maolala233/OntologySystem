# app/api/qa.py - 本体问答（M5，docs/design/03 §15 / 07 §4）
# POST /qa：SSE 流式（token/sources/done/error）+ 非流式聚合；[M:qa] + [Pub+]。
# GET /qa/history、GET /qa/sources/{ref_id}：历史与引用溯源详情（文档预签名链接）。
# R8：对话分组（列表/详情/删除）+ 问答模型选择（purpose∈{chat,extract} 且启用）。
# 检索后端 = adapters/retrieval（向量强制 project_id 表达式 + 行表图扩展 + 溯源引用）。
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.provider import build_legacy_llm_config
from app.adapters.retrieval import QAResult, RetrievalQuery, iter_answer, query_with_reasoning
from app.core.config import settings
from app.core.deps import get_current_user, get_db, require_module
from app.core.exceptions import APIError, ProjectForbiddenError
from app.core.permissions import has_project_role
from app.infrastructure.database import (
    ModelConfig,
    Project,
    ProjectMember,
    QaHistory,
    UploadedDocument,
    User,
)
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.minio_client import get_minio_client
from app.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["qa"])

# 03 §15 07 §3.3：单响应限制
MAX_QUESTION_LEN = 2000
MAX_HISTORY = 50


def _ensure_qa_access(db: Session, project_id: int, user: User) -> Project:
    """[M:qa] + [Pub+]：已发布项目对全部开通 qa 模块的用户开放；未发布需项目成员。
    R11：仅超级管理员（username='admin'）跨租户直通，普通管理员按成员规则。"""
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise APIError("项目不存在", code="PROJECT_NOT_FOUND", http_status=404)
    from app.core.deps import is_super_admin
    if is_super_admin(user) or proj.owner_id == user.id:
        return proj
    if proj.status == "published":
        return proj
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        .first()
    )
    if not has_project_role(member.role if member else None, "viewer"):
        raise ProjectForbiddenError("无该项目访问权限")
    return proj


def _build_llm(db: Session, project_id: int,
               model_config_id: int | None = None) -> LLMClient:
    """按指定模型配置构造客户端；未指定时回落项目/全局默认（purpose=extract 兜底链）。"""
    if model_config_id is not None:
        row = db.query(ModelConfig).filter(ModelConfig.id == model_config_id).first()
        # 只允许选用：已启用 + chat/extract 用途 + 全局或本项目的配置
        if (row is None or not row.enabled
                or row.purpose not in ("chat", "extract")
                or (row.scope == "project" and row.project_id != project_id)):
            raise APIError("所选问答模型不可用", code="QA_MODEL_UNAVAILABLE", http_status=400)
        from app.core.security import decrypt_str
        api_key = decrypt_str(bytes(row.api_key_encrypted)) if row.api_key_encrypted else ""
        return LLMClient(api_key=api_key, base_url=row.base_url, model=row.model_name)
    cfg = build_legacy_llm_config(db, project_id)
    return LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                     model=cfg.get("model"))


def _sources_out(result: QAResult) -> list[dict]:
    return [s.model_dump() for s in result.sources]


class QaRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LEN)
    top_k: int = 12
    use_graph: bool = True
    use_inferred: bool = True  # 注入语义推理引用（来源标注区分，不与事实混排）
    stream: bool = True
    knowledge_domain: str | None = None  # 可选知识域过滤（表达式内强制 project_id）
    conversation_id: str | None = None  # 对话分组（R8）：同会话多轮归属一个 uuid
    model_config_id: int | None = None  # 问答模型选择（R8）：缺省用项目/全局默认


@router.post("/{project_id}/qa", dependencies=[Depends(require_module("qa"))])
def ask_question(
    project_id: int,
    body: QaRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """本体问答（03 §15）：stream=true → SSE（token/sources/done），否则 JSON 聚合。"""
    proj = _ensure_qa_access(db, project_id, user)
    question = body.question.strip()
    if not question:
        raise APIError("问题不能为空", code="QA_EMPTY_QUESTION", http_status=400)
    # 对话归属（R8）：客户端传 uuid 则沿用，否则新建；格式校验防脏数据
    conversation_id = body.conversation_id or str(uuid.uuid4())
    if len(conversation_id) > 36 or not all(
            c.isalnum() or c == "-" for c in conversation_id):
        raise APIError("conversation_id 格式非法", code="QA_BAD_CONVERSATION", http_status=400)

    query = RetrievalQuery(
        project_id=project_id,
        question=question,
        top_k=max(1, min(body.top_k, 30)),
        use_graph=body.use_graph,
        use_inferred=body.use_inferred,
        knowledge_domain=body.knowledge_domain,
    )
    llm = _build_llm(db, project_id, body.model_config_id)

    if not body.stream:
        t0 = time.time()
        result = query_with_reasoning(query, db=db, llm=llm)
        hist = _save_history(db, project_id, user.id, question, result,
                             conversation_id=conversation_id)
        log_action(db, user.id, "qa.ask", resource_type="project", resource_id=str(project_id),
                   detail={"latency_ms": result.latency_ms, "sources": len(result.sources),
                           "conversation_id": conversation_id})
        db.commit()  # log_action 同事务写入，请求会话 close 不 commit，须显式落盘
        return {
            "answer": result.answer,
            "sources": _sources_out(result),
            "confidence": result.confidence,
            "reasoning_path": result.reasoning_path,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "history_id": hist.id,
            "conversation_id": conversation_id,
        }

    return StreamingResponse(
        _sse_stream(db, project_id, user, question, query, llm, conversation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _save_history(db: Session, project_id: int, user_id: int | None,
                  question: str, result: QAResult,
                  conversation_id: str | None = None) -> QaHistory:
    hist = QaHistory(
        project_id=project_id,
        user_id=user_id,
        conversation_id=conversation_id,
        question=question[:2000],
        answer=result.answer,
        sources=_sources_out(result),
        model=result.model,
        latency_ms=result.latency_ms,
        confidence=result.confidence,
    )
    db.add(hist)
    db.commit()
    db.refresh(hist)
    return hist


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_stream(db: Session, project_id: int, user: User, question: str,
                query: RetrievalQuery, llm: LLMClient, conversation_id: str):
    """SSE 生成器（07 §4）：event: token（增量）→ event: done（sources/confidence/耗时）。"""
    answer_parts: list[str] = []
    result: QAResult | None = None
    t0 = time.time()
    try:
        for kind, payload in iter_answer(query, db=db, llm=llm):
            if kind == "token":
                answer_parts.append(payload)
                yield _sse("token", {"delta": payload})
            elif kind == "meta":
                yield _sse("meta", payload)
            elif kind == "error":
                yield _sse("error", {"message": str(payload)})
            elif kind == "done":
                result = payload
        if result is None:  # 生成器异常退出且未产生 done
            raise RuntimeError("流式生成未完成")
        result.answer = "".join(answer_parts) or result.answer
        hist = _save_history(db, project_id, user.id, question, result,
                             conversation_id=conversation_id)
        log_action(db, user.id, "qa.ask", resource_type="project", resource_id=str(project_id),
                   detail={"stream": True, "latency_ms": result.latency_ms,
                           "sources": len(result.sources),
                           "conversation_id": conversation_id})
        db.commit()  # log_action 同事务写入，SSE 生成器结束会话 close 不 commit
        yield _sse("sources", {"sources": _sources_out(result)})
        yield _sse("done", {
            "answer": result.answer,
            "confidence": result.confidence,
            "reasoning_path": result.reasoning_path,
            "model": result.model,
            "latency_ms": int((time.time() - t0) * 1000),
            "history_id": hist.id,
            "conversation_id": conversation_id,
        })
    except Exception as e:  # SSE 已发出 200，只能以 error 事件收尾
        logger.error(f"[qa] SSE 流失败：{e}")
        yield _sse("error", {"message": f"问答服务异常：{e}"})


@router.get("/{project_id}/qa/history")
def qa_history(
    project_id: int,
    limit: int = 20,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """问答历史（03 §15）：问题/答案/引用/模型/耗时，倒序；可按对话过滤。"""
    _ensure_qa_access(db, project_id, user)
    q = db.query(QaHistory).filter(QaHistory.project_id == project_id)
    if conversation_id:
        q = q.filter(QaHistory.conversation_id == conversation_id)
    rows = q.order_by(QaHistory.id.desc()).limit(max(1, min(limit, MAX_HISTORY))).all()
    return {
        "items": [
            {
                "id": r.id,
                "question": r.question,
                "answer": r.answer,
                "sources": r.sources or [],
                "model": r.model,
                "latency_ms": r.latency_ms,
                "confidence": float(r.confidence) if r.confidence is not None else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/{project_id}/qa/models")
def qa_models(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """可用问答模型（R8）：启用且 purpose=chat 的全局/本项目配置；不回落 env。"""
    _ensure_qa_access(db, project_id, user)
    rows = (
        db.query(ModelConfig)
        .filter(ModelConfig.enabled.is_(True),
                ModelConfig.purpose == "chat",
                (ModelConfig.scope == "global")
                | ((ModelConfig.scope == "project") & (ModelConfig.project_id == project_id)))
        .order_by(ModelConfig.scope, ModelConfig.is_default.desc(), ModelConfig.id)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "provider": r.provider,
                "model_name": r.model_name,
                "scope": r.scope,
                "is_default": bool(r.is_default),
            }
            for r in rows
        ]
    }


@router.get("/{project_id}/qa/conversations")
def qa_conversations(
    project_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """对话列表（R8）：当前用户在本项目的会话分组，标题取首问，按最近时间倒序。"""
    _ensure_qa_access(db, project_id, user)
    rows = (
        db.query(QaHistory)
        .filter(QaHistory.project_id == project_id,
                QaHistory.user_id == user.id,
                QaHistory.conversation_id.isnot(None))
        .order_by(QaHistory.id.desc())
        .limit(2000)
        .all()
    )
    convs: dict[str, dict] = {}
    for r in rows:
        c = convs.get(r.conversation_id)
        if c is None:
            convs[r.conversation_id] = {
                "id": r.conversation_id,
                "title": (r.question or "").strip()[:40] or "未命名对话",
                "message_count": 1,
                "first_time": r.created_at.isoformat() if r.created_at else None,
                "last_time": r.created_at.isoformat() if r.created_at else None,
                "last_answer": (r.answer or "")[:80],
            }
        else:
            c["message_count"] += 1
            if r.created_at:
                c["last_time"] = max(c["last_time"] or "", r.created_at.isoformat())
    items = sorted(convs.values(), key=lambda c: c["last_time"] or "", reverse=True)
    return {"items": items[:max(1, min(limit, 200))]}


@router.get("/{project_id}/qa/conversations/{conversation_id}")
def qa_conversation_detail(
    project_id: int,
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """对话详情（R8）：该会话全部轮次正序（问题/答案/引用/模型/耗时）。"""
    _ensure_qa_access(db, project_id, user)
    rows = (
        db.query(QaHistory)
        .filter(QaHistory.project_id == project_id,
                QaHistory.user_id == user.id,
                QaHistory.conversation_id == conversation_id)
        .order_by(QaHistory.id.asc())
        .all()
    )
    if not rows:
        raise APIError("对话不存在", code="QA_CONVERSATION_NOT_FOUND", http_status=404)
    return {
        "id": conversation_id,
        "title": (rows[0].question or "").strip()[:40] or "未命名对话",
        "messages": [
            {
                "id": r.id,
                "question": r.question,
                "answer": r.answer,
                "sources": r.sources or [],
                "model": r.model,
                "latency_ms": r.latency_ms,
                "confidence": float(r.confidence) if r.confidence is not None else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.delete("/{project_id}/qa/conversations/{conversation_id}")
def qa_conversation_delete(
    project_id: int,
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """删除对话（R8）：仅删自己的会话轮次。"""
    _ensure_qa_access(db, project_id, user)
    deleted = (
        db.query(QaHistory)
        .filter(QaHistory.project_id == project_id,
                QaHistory.user_id == user.id,
                QaHistory.conversation_id == conversation_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    if not deleted:
        raise APIError("对话不存在", code="QA_CONVERSATION_NOT_FOUND", http_status=404)
    log_action(db, user.id, "qa.conversation.delete", resource_type="project",
               resource_id=str(project_id), detail={"conversation_id": conversation_id})
    db.commit()
    return {"deleted": deleted}


@router.get("/{project_id}/qa/sources/{ref_id}")
def qa_source_detail(
    project_id: int,
    ref_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_module("qa")),
):
    """引用溯源详情（03 §15）：问题/答案的引用集合 + 文档预签名链接（点击定位原文）。"""
    _ensure_qa_access(db, project_id, user)
    hist = (
        db.query(QaHistory)
        .filter(QaHistory.id == ref_id, QaHistory.project_id == project_id)
        .first()
    )
    if hist is None:
        raise APIError("问答记录不存在", code="QA_HISTORY_NOT_FOUND", http_status=404)

    doc_ids = {s.get("source_document_id") for s in (hist.sources or [])
               if s.get("source_document_id")}
    doc_urls: dict[int, str] = {}
    doc_names: dict[int, str] = {}
    for did in doc_ids:
        d = db.query(UploadedDocument).filter(UploadedDocument.id == did).first()
        if d is not None and d.deleted_at is None:
            doc_names[did] = d.filename
            try:
                doc_urls[did] = get_minio_client().get_presigned_url(
                    settings.MINIO_BUCKET_UPLOADS, d.storage_key)
            except Exception as e:  # 预签名失败不阻断溯源内容返回
                logger.warning(f"[qa] 预签名失败 doc={did}: {e}")

    sources = []
    for s in (hist.sources or []):
        item = dict(s)
        did = s.get("source_document_id")
        if did and did in doc_names:
            item["doc_name"] = doc_names[did]
            item["doc_url"] = doc_urls.get(did)
        sources.append(item)
    return {
        "id": hist.id,
        "question": hist.question,
        "answer": hist.answer,
        "sources": sources,
        "created_at": hist.created_at.isoformat() if hist.created_at else None,
    }
