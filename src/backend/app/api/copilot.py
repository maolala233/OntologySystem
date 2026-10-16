# app/api/copilot.py - Copilot 人机协同 API（O5-B22，docs/design/14 §5 S1）
# POST /projects/{id}/copilot/chat：SSE（stage/token/done/error），支持客户端断开取消；
# GET sessions / GET session detail / DELETE session；GET models（复用 qa 模型口径）。
# 权限挂 resolution 模块（构建域，14 §6 不做清单：不让 Copilot 绕过模块授权）。
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_module
from app.core.exceptions import APIError
from app.infrastructure.database import (
    CopilotMessage,
    CopilotProposal,
    CopilotSession,
    OntologyVersion,
    Project,
    User,
)
from app.services import ontology_copilot
from app.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/copilot", tags=["copilot"])

MAX_MESSAGE_LEN = 2000


def _load_project(db: Session, project_id: int, user: User) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise APIError("项目不存在", code="PROJECT_NOT_FOUND", http_status=404)
    # 可见性对齐平台口径（require_project_role viewer）：owner/超管/成员，已发布项目对登录用户公开只读；
    # 改画布的动作（apply/reject/存档）另走 _require_editor，发布态只读在那里被拦。
    from app.core.deps import require_project_role
    require_project_role(project_id, "viewer", user, db)
    return proj


class CopilotChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)
    session_id: int | None = None
    model_config_id: int | None = None
    # 画布选中态（F12：构建器内选中节点/边随消息携带，"针对选中的东西说话"）
    selected: dict | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _persist_proposal(db: Session, proj: Project, session: CopilotSession,
                      proposal: dict) -> None:
    """B25：把 done 里的提案落 copilot_proposals（pending），base_version 记当前画布版本。"""
    try:
        latest = (db.query(OntologyVersion.version_no)
                  .filter(OntologyVersion.project_id == proj.id)
                  .order_by(OntologyVersion.version_no.desc()).first())
        db.add(CopilotProposal(
            proposal_id=str(proposal.get("proposal_id") or ""),
            project_id=proj.id, session_id=session.id,
            kind=str(proposal.get("kind") or ""),
            targets=proposal.get("targets"),
            diff_preview=proposal.get("diff_preview"),
            impact=proposal.get("impact"),
            llm_reason=proposal.get("llm_reason"),
            evidence=proposal.get("evidence"),
            risk_tier=str(proposal.get("risk_tier") or "low"),
            base_version=int(latest[0]) if latest else 0,
        ))
        proposal["base_version"] = int(latest[0]) if latest else 0
        # ★先 commit 再让 done 事件出流：done 一到前端提案卡就可点应用，若行未提交，
        # apply 在新会话里查不到该行 → 瞬时 404（走查实测复现）。
        db.commit()
    except Exception as exc:  # noqa: BLE001 —— 落库失败不阻断提案返回（apply 时会再校验）
        db.rollback()
        logger.warning(f"[copilot] 提案落库失败：{exc}")


@router.post("/chat", dependencies=[Depends(require_module("resolution"))])
def copilot_chat(project_id: int, body: CopilotChatRequest,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Copilot 对话（SSE）：阶段事件 + 答案流；客户端断开即取消（记录 stopped）。"""
    proj = _load_project(db, project_id, user)
    message = body.message.strip()
    if not message:
        raise APIError("消息不能为空", code="COPILOT_EMPTY_MESSAGE", http_status=400)
    try:
        llm = ontology_copilot.build_llm(db, project_id, body.model_config_id)
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise APIError(f"模型不可用：{exc}", code="COPILOT_MODEL_UNAVAILABLE",
                       http_status=400)

    try:
        session = ontology_copilot.get_or_create_session(
            db, proj, user, body.session_id, body.model_config_id)
    except ValueError as exc:
        raise APIError(str(exc), code="COPILOT_SESSION_NOT_FOUND", http_status=404)
    if session.id is None:
        db.add(session)
        db.commit()
        db.refresh(session)
    history = (db.query(CopilotMessage)
               .filter(CopilotMessage.session_id == session.id)
               .order_by(CopilotMessage.id.asc()).all())
    if not session.title or session.title == "新会话":
        session.title = message[:40]
        db.commit()

    def stream():
        answer = ""
        stages: list[dict] = []
        tool_calls: list = []
        kind = "chat"
        status, latency_ms = "ok", None
        verify_report: dict | None = None
        # B30：回答前先鉴权——只读用户（已发布/无编辑权限）不产编辑提案不澄清
        readonly = not ontology_copilot.can_edit_project(db, proj, user)
        try:
            yield _sse("meta", {"session_id": session.id,
                                "project_id": project_id,
                                "project_name": proj.name})
            for event, payload in ontology_copilot.run_copilot_turn(
                    db, proj, user, message, history=history,
                    selected=body.selected, llm=llm, can_edit=not readonly):
                if event == "stage":
                    stages.append(payload)
                    yield _sse("stage", payload)
                elif event == "tool":
                    # B31/F16：工具调用步骤（start/end + 耗时 + observation）随流外推
                    yield _sse("tool", payload)
                elif event == "token":
                    answer += payload.get("delta") or ""
                    yield _sse("token", payload)
                elif event == "clarify":
                    # B26：澄清事件随流推给前端（F14 渲染选项按钮组）
                    yield _sse("clarify", payload)
                elif event == "verify":
                    # B28：验证报告随流推给前端（F15 渲染三段报告卡）
                    verify_report = payload.get("report")
                    yield _sse("verify", payload)
                elif event == "done":
                    tool_calls = payload.get("tool_calls") or []
                    latency_ms = payload.get("latency_ms")
                    # B25：编辑提案随 done 持久化（copilot_proposals 表，base_version 支撑并发冲突闸）
                    if payload.get("proposal"):
                        kind = "proposal"
                        tool_calls = list(tool_calls) + [{
                            "tool": "generate_proposal",
                            "args": {"message": message[:200]},
                            "observation": {"proposal": payload["proposal"]},
                        }]
                        _persist_proposal(db, proj, session, payload["proposal"])
                    elif (payload.get("clarify")
                          and not payload["clarify"].get("exhausted")):
                        # B26：澄清轮落库 kind=clarify（轮次计数与回放都靠它）
                        kind = "clarify"
                        tool_calls = list(tool_calls) + [{
                            "tool": "generate_clarification",
                            "args": {"message": message[:200]},
                            "observation": {"clarify": payload["clarify"]},
                        }]
                    elif payload.get("verify"):
                        # B28：验证报告落库 kind=verify（report JSON 存 copilot_messages.report）
                        kind = "verify"
                        verify_report = payload.get("verify")
                    yield _sse("done", {**payload, "session_id": session.id})
                elif event == "error":
                    status = "failed"
                    yield _sse("error", payload)
        except Exception as exc:  # noqa: BLE001 —— 客户端断开/生成器异常
            status = "stopped"
            logger.warning(f"[copilot] SSE 中断：{exc}", exc_info=True)
        finally:
            try:
                ontology_copilot.save_turn(db, proj, user, session, message,
                                           answer=answer, tool_calls=tool_calls,
                                           stages=stages, status=status,
                                           latency_ms=latency_ms, kind=kind,
                                           report=verify_report)
                log_action(db, user.id, "copilot.chat", resource_type="project",
                           resource_id=str(project_id),
                           detail={"session_id": session.id, "status": status,
                                   "latency_ms": latency_ms})
                db.commit()
            except Exception as exc:  # noqa: BLE001 —— 落库失败不掩盖原流
                logger.warning(f"[copilot] 会话落库失败：{exc}")
                db.rollback()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/sessions", dependencies=[Depends(require_module("resolution"))])
def list_sessions(project_id: int, limit: int = 50,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """当前用户在本项目的 Copilot 会话列表（标题取首条消息，最近优先）。"""
    _load_project(db, project_id, user)
    rows = (db.query(CopilotSession)
            .filter(CopilotSession.project_id == project_id,
                    CopilotSession.user_id == user.id)
            .order_by(CopilotSession.id.desc())
            .limit(max(1, min(limit, 200))).all())
    return {"items": [
        {"id": r.id, "title": r.title, "message_count": r.message_count,
         "model_config_id": r.model_config_id,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in rows
    ]}


@router.get("/sessions/{session_id}", dependencies=[Depends(require_module("resolution"))])
def session_detail(project_id: int, session_id: int,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """会话回放：全部消息正序（含阶段/工具轨迹）。"""
    _load_project(db, project_id, user)
    sess = (db.query(CopilotSession)
            .filter(CopilotSession.id == session_id,
                    CopilotSession.project_id == project_id).first())
    if sess is None:
        raise APIError("会话不存在", code="COPILOT_SESSION_NOT_FOUND", http_status=404)
    msgs = (db.query(CopilotMessage)
            .filter(CopilotMessage.session_id == session_id)
            .order_by(CopilotMessage.id.asc()).all())
    return {
        "id": sess.id, "title": sess.title,
        "model_config_id": sess.model_config_id,
        "messages": [
            {"id": m.id, "role": m.role, "kind": m.kind, "content": m.content,
             "stages": m.stages or [], "tool_calls": m.tool_calls or [],
             "report": m.report or None,
             "status": m.status, "latency_ms": m.latency_ms,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in msgs
        ],
    }


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_module("resolution"))])
def delete_session(project_id: int, session_id: int,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """删除会话（仅本人）。"""
    _load_project(db, project_id, user)
    deleted = (db.query(CopilotSession)
               .filter(CopilotSession.id == session_id,
                       CopilotSession.project_id == project_id,
                       CopilotSession.user_id == user.id)
               .delete(synchronize_session=False))
    db.commit()
    if not deleted:
        raise APIError("会话不存在", code="COPILOT_SESSION_NOT_FOUND", http_status=404)
    log_action(db, user.id, "copilot.session.delete", resource_type="project",
               resource_id=str(project_id), detail={"session_id": session_id})
    db.commit()
    return {"deleted": True}


@router.get("/models", dependencies=[Depends(require_module("resolution"))])
def copilot_models(project_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """可用 Copilot 模型（口径同问答：启用且 purpose=chat 的全局/本项目配置）。"""
    _load_project(db, project_id, user)
    from app.infrastructure.database import ModelConfig
    rows = (db.query(ModelConfig)
            .filter(ModelConfig.enabled.is_(True),
                    ModelConfig.purpose == "chat",
                    (ModelConfig.scope == "global")
                    | ((ModelConfig.scope == "project")
                       & (ModelConfig.project_id == project_id)))
            .order_by(ModelConfig.scope, ModelConfig.is_default.desc(), ModelConfig.id)
            .all())
    return {"items": [
        {"id": r.id, "name": r.name, "provider": r.provider,
         "model_name": r.model_name, "scope": r.scope,
         "is_default": bool(r.is_default)}
        for r in rows
    ]}


# ---------------------------------------------------------------- 建议卡（O5-B27）

def _suggestion_dict(r: CopilotProposal) -> dict:
    return {"proposal_id": r.proposal_id, "kind": r.kind,
            "targets": r.targets, "llm_reason": r.llm_reason,
            "evidence": r.evidence, "status": r.status, "result": r.result,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None}


@router.get("/suggestions", dependencies=[Depends(require_module("resolution"))])
def list_suggestions(project_id: int, status: str | None = None,
                     limit: int = 50,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """首轮诊断建议卡列表（B27）：构建器顶栏"N 条建议"入口按 pending 计数。"""
    _load_project(db, project_id, user)
    q = db.query(CopilotProposal).filter(
        CopilotProposal.project_id == project_id,
        CopilotProposal.kind == "suggestion")
    if status:
        q = q.filter(CopilotProposal.status == status)
    rows = q.order_by(CopilotProposal.id.desc()).limit(max(1, min(limit, 200))).all()
    pending = (db.query(CopilotProposal)
               .filter(CopilotProposal.project_id == project_id,
                       CopilotProposal.kind == "suggestion",
                       CopilotProposal.status == "pending").count())
    return {"pending_count": pending,
            "items": [_suggestion_dict(r) for r in rows]}


@router.post("/suggestions/{proposal_id}/convert",
             dependencies=[Depends(require_module("resolution"))])
def convert_suggestion(project_id: int, proposal_id: str,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """建议卡"转审核项"（B27）：物化 review_items，走既有裁决流；需 editor。"""
    _load_project(db, project_id, user)
    _require_editor(project_id, user, db)
    row = _proposal_or_404(db, project_id, proposal_id)
    if row.kind != "suggestion":
        raise APIError("该提案不是建议卡", code="COPILOT_PROPOSAL_NOT_SUGGESTION",
                       http_status=400)
    if row.status != "pending":
        raise APIError("建议卡已处理", code="COPILOT_PROPOSAL_ALREADY_DECIDED",
                       http_status=400)
    from app.services.copilot_diagnosis import convert_suggestion_to_review
    item_id = convert_suggestion_to_review(db, project_id, row, user.id)
    log_action(db, user.id, "copilot.suggestion.convert", resource_type="project",
               resource_id=str(project_id),
               detail={"proposal_id": proposal_id, "review_item_id": item_id})
    db.commit()
    return {"converted": True, "review_item_id": item_id}


# ---------------------------------------------------------------- 提案执行（O5-B25）

class ApplyBody(BaseModel):
    confirm: bool = False  # 高风险提案（删类/并类/删实例）须二次确认


def _proposal_or_404(db: Session, project_id: int, proposal_id: str) -> CopilotProposal:
    row = (db.query(CopilotProposal)
           .filter(CopilotProposal.proposal_id == proposal_id,
                   CopilotProposal.project_id == project_id).first())
    if row is None:
        raise APIError("提案不存在", code="COPILOT_PROPOSAL_NOT_FOUND", http_status=404)
    return row


def _require_editor(project_id: int, user: User, db: Session) -> None:
    """执行/忽略提案改画布，须 editor 及以上（与 graph_edit 同口径）。"""
    from app.core.deps import require_project_role
    require_project_role(project_id, "editor", user, db)


@router.get("/proposals", dependencies=[Depends(require_module("resolution"))])
def list_proposals(project_id: int, session_id: int | None = None,
                   limit: int = 50,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """提案列表（F13 会话回放时按 session 拉状态）。"""
    _load_project(db, project_id, user)
    q = db.query(CopilotProposal).filter(CopilotProposal.project_id == project_id)
    if session_id is not None:
        q = q.filter(CopilotProposal.session_id == session_id)
    rows = q.order_by(CopilotProposal.id.desc()).limit(max(1, min(limit, 200))).all()
    return {"items": [
        {"proposal_id": r.proposal_id, "kind": r.kind, "targets": r.targets,
         "diff_preview": r.diff_preview, "impact": r.impact,
         "llm_reason": r.llm_reason, "evidence": r.evidence,
         "risk_tier": r.risk_tier, "status": r.status,
         "base_version": r.base_version, "result": r.result,
         "session_id": r.session_id,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "decided_at": r.decided_at.isoformat() if r.decided_at else None}
        for r in rows
    ]}


@router.post("/proposals/{proposal_id}/apply", dependencies=[Depends(require_module("resolution"))])
def apply_proposal(project_id: int, proposal_id: str, body: ApplyBody | None = None,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """应用提案（B25）：闸门（状态/高风险二次确认/并发冲突/超期）→ 机检自愈 → manual 打版。"""
    proj = _load_project(db, project_id, user)
    _require_editor(project_id, user, db)
    row = _proposal_or_404(db, project_id, proposal_id)
    from app.services.copilot_executor import apply_proposal as run_apply
    result = run_apply(db, proj, user, row,
                       confirm=bool(body.confirm) if body else False)
    if result.get("error") and not result.get("need_confirm") and not result.get("conflict"):
        raise APIError(result["error"], code="COPILOT_PROPOSAL_APPLY_FAILED", http_status=400)
    return result


@router.post("/proposals/{proposal_id}/reject", dependencies=[Depends(require_module("resolution"))])
def reject_proposal(project_id: int, proposal_id: str,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """忽略提案（不产生任何画布副作用，pending→rejected）。"""
    _load_project(db, project_id, user)
    _require_editor(project_id, user, db)
    row = _proposal_or_404(db, project_id, proposal_id)
    from app.services.copilot_executor import reject_proposal as run_reject
    result = run_reject(db, row)
    if not result.get("rejected"):
        raise APIError(result.get("error") or "提案已处理", code="COPILOT_PROPOSAL_APPLY_FAILED",
                       http_status=400)
    log_action(db, user.id, "copilot.proposal.reject", resource_type="project",
               resource_id=str(project_id), detail={"proposal_id": proposal_id})
    db.commit()
    return result


# ───────────────────────── 验证报告（O5-B28/S4） ─────────────────────────

@router.post("/verify/{message_id}/archive",
             dependencies=[Depends(require_module("resolution"))])
def archive_verify_report(project_id: int, message_id: int,
                          body: dict | None = None,
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """验证报告存档到版本（F15 存档按钮）：打 schema 快照版本，description 带报告摘要。"""
    proj = _load_project(db, project_id, user)
    _require_editor(project_id, user, db)
    msg = (db.query(CopilotMessage)
           .filter(CopilotMessage.id == int(message_id),
                   CopilotMessage.project_id == project_id,
                   CopilotMessage.kind == "verify").first())
    if msg is None or not msg.report:
        raise APIError("验证报告不存在", code="VERIFY_REPORT_NOT_FOUND", http_status=404)
    if (msg.report or {}).get("archived_version_no"):
        raise APIError(f"该报告已存档为 v{msg.report['archived_version_no']}",
                       code="VERIFY_ALREADY_ARCHIVED", http_status=400)

    from app.adapters.versioning import create_version
    from app.services.copilot_verification import summarize_report

    label = str((body or {}).get("label") or "AI 验证报告")[:64]
    description = summarize_report(msg.report)[:500]
    ver = create_version(db, proj, "schema", user.id, label=label,
                         description=description)
    msg.report = {**msg.report, "archived_version_no": ver.version_no}
    log_action(db, user.id, "copilot.verify.archive", resource_type="project",
               resource_id=str(project_id),
               detail={"message_id": msg.id, "version_no": ver.version_no,
                       "overall": (msg.report or {}).get("overall")})
    db.commit()
    return {"archived": True, "version_no": ver.version_no}


# ───────────────────────── 会话审计与操作追溯（O5-B29/S4） ─────────────────────────

@router.get("/audit", dependencies=[Depends(require_module("resolution"))])
def copilot_audit(project_id: int, limit: int = 200,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """AI 协同操作历史（B29）：本项目 copilot.* 审计流水，actor/会话/提案/diff 摘要。"""
    _load_project(db, project_id, user)
    from app.infrastructure.database import AuditLog

    rows = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "project",
                    AuditLog.resource_id == str(project_id),
                    AuditLog.action.like("copilot.%"))
            .order_by(AuditLog.id.desc())
            .limit(max(1, min(limit, 500))).all())
    user_ids = {r.user_id for r in rows if r.user_id}
    users = {}
    if user_ids:
        from app.infrastructure.database import User as _User
        users = {u.id: u.username for u in
                 db.query(_User).filter(_User.id.in_(user_ids)).all()}
    return {"items": [
        {"id": r.id, "action": r.action,
         "actor_id": r.user_id, "actor": users.get(r.user_id),
         "detail": r.detail or {}, "created_at":
             r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}
