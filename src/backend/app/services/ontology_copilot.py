# app/services/ontology_copilot.py - Copilot 会话编排器（O5-B22，docs/design/14 §5 S1）
# 职责：①上下文组装（TBox 摘要 + 记分卡核心 + 会话窗口，token 预算）；
# ②tool-loop 编排（只读工具，分析类 ≤2 轮）；③SSE 阶段事件产出。
# S1 只做只读分析；编辑提案（S2）/澄清（S3）在本文件后续扩展。
from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.database import CopilotMessage, CopilotSession, Project, User
from app.services.copilot_tools import build_toolbox

logger = logging.getLogger(__name__)

# token 预算（14 §3.3）：TBox 摘要超预算按实例数降序 + 最近变更优先截断
CONTEXT_CHAR_BUDGET = 6000
HISTORY_WINDOW = 6  # 会话窗口最近 6 轮
MAX_ROUNDS = 3      # tool-loop 总轮数：≤2 轮工具调用 + 1 轮产出 final（14 §3.2）

SYSTEM_PROMPT = (
    "你是企业本体构建平台的「本体协同助手」（Copilot），帮用户分析、质检、迭代本体。"
    "回答要求：①基于工具返回的真实数据，引用具体数字与名称；②结构化（小标题/列表）；"
    "③人话、面向非本体专业用户；④只读分析，不承诺直接修改数据——涉及编辑时说明"
    "「可以先给出编辑提案，由你确认后执行」；⑤不确定就建议调用哪个工具进一步查证。"
)

# S5 反馈③：只读用户（已发布项目/无编辑权限）不得承诺修改——回答前先看权限
_READONLY_SYSTEM_SUFFIX = (
    "\n\n【用户权限提示】当前用户对该项目只有只读权限（项目已发布或无编辑权限）。"
    "你在回答中绝对不能承诺或描述任何编辑/删除/新增操作，也不要反问用户想改什么；"
    "用户提出修改诉求时，明确说明只读原因并给出正路："
    "①负责人/超管可点「取消发布并调整」按钮，先下线再让我改；②或由项目负责人在画布手动修改。"
    "分析、问答、验证不受影响。"
)

# 只读用户提出编辑诉求时的确定性回复（不走 LLM，杜绝"可以帮你删"类越权承诺）。
# 有取消发布权（负责人/超管）：给一键"取消发布并调整"通道——前端按钮，点击=用户确认
_READONLY_EDIT_NOTICE_OFFER = (
    "这个项目当前处于**已发布（只读）**状态，我不能直接生成编辑或删除操作。\n\n"
    "你是有权限的负责人——点下方「取消发布并调整」按钮，我会先**取消发布**"
    "（版本历史保留，之后可随时重新发布），然后立即按你的要求生成编辑提案。\n\n"
    "不想下线项目的话，也可以直接在画布上手动修改；分析、问答、验证不受影响，随时可以问我。"
)

# 无取消发布权的只读用户：维持原指引（找负责人）
_READONLY_EDIT_NOTICE = (
    "这个项目当前处于只读状态（已发布），我不能生成编辑或删除操作。两种改法：\n\n"
    "1. 项目负责人在「版本时间轴」先**取消发布**，然后让我来改；\n"
    "2. 联系项目负责人在画布上手动修改。\n\n"
    "项目的分析、问答、验证不受影响，随时可以问我。"
)


def can_edit_project(db: Session, project: Project, user: User) -> bool:
    """当前用户对项目是否有编辑权（B30）：owner/超管/成员 editor+，且项目未发布。"""
    from app.core.deps import is_super_admin
    if project.status == "published" or project.is_published:
        return False
    if is_super_admin(user) or project.owner_id == user.id:
        return True
    from app.core.permissions import has_project_role
    from app.infrastructure.database import ProjectMember
    member = (db.query(ProjectMember)
              .filter(ProjectMember.project_id == project.id,
                      ProjectMember.user_id == user.id).first())
    return has_project_role(member.role if member else None, "editor")


def can_unpublish_project(project: Project, user: User) -> bool:
    """当前用户是否有取消发布权（口径对齐 POST /unpublish：owner 或超管）。"""
    from app.core.deps import is_super_admin
    return is_super_admin(user) or project.owner_id == user.id


def _truncate_dict(obj: Any, budget: int) -> Any:
    """token 预算兜底：整体序列化超预算时对清单类字段做截断。"""
    text = json.dumps(obj, ensure_ascii=False)
    if len(text) <= budget:
        return obj
    if isinstance(obj, dict):
        out = {}
        used = 0
        for k, v in obj.items():
            piece = json.dumps({k: v}, ensure_ascii=False)
            if used + len(piece) > budget and out:
                out["_truncated"] = True
                break
            out[k] = v
            used += len(piece)
        return out
    return text[:budget]


def assemble_context(db: Session, project: Project, history: list[CopilotMessage] | None = None,
                     selected: dict | None = None) -> str:
    """组装用户 prompt：画布上下文 + 会话窗口 + 选中态（14 §3.3）。"""
    from app.services.copilot_tools import get_schema_stats, get_scorecard

    stats = get_schema_stats(db, project, {})
    card = get_scorecard(db, project, {})
    parts = [
        "【画布上下文（自动注入，勿重复罗列全部清单）】",
        json.dumps(_truncate_dict(stats, CONTEXT_CHAR_BUDGET // 2), ensure_ascii=False),
        "【记分卡核心指标】",
        json.dumps(card, ensure_ascii=False),
    ]
    if history:
        window = history[-HISTORY_WINDOW:]
        lines = [f"{'用户' if m.role == 'user' else '助手'}：{(m.content or '')[:300]}"
                 for m in window]
        parts.append("【最近对话】\n" + "\n".join(lines))
    if selected:
        sel = _truncate_dict(selected, 1500)
        parts.append("【用户当前画布选中态】（用户在说这些元素）\n"
                     + json.dumps(sel, ensure_ascii=False))
    return "\n".join(parts)


def _sse_chunks(text: str, size: int = 120) -> list[str]:
    """最终答案切段推送（tool-loop 非流式，SSE 侧按块产出保持流式契约）。"""
    return [text[i:i + size] for i in range(0, len(text), size)] if text else []


def _sse_chunks(text: str, size: int = 120) -> list[str]:
    """最终答案切段推送（tool-loop 非流式，SSE 侧按块产出保持流式契约）。"""
    return [text[i:i + size] for i in range(0, len(text), size)] if text else []


_CHITCHAT_SYSTEM = (
    "你是企业本体构建平台的「本体协同助手」。用户在寒暄或询问你本身："
    "用一两句人话简短回应，介绍你能分析本体、生成编辑提案、协助验证；"
    "不要罗列指标，不要调用工具。")


def run_copilot_turn(db: Session, project: Project, user: User,
                     message: str, history: list[CopilotMessage] | None = None,
                     selected: dict | None = None,
                     llm=None,
                     can_edit: bool = True) -> Generator[tuple[str, dict], None, None]:
    """执行一轮 Copilot 对话，产出 (event, payload) 序列。

    事件：stage（阶段提示）/ tool（工具调用步骤，B31/F16）/ token（答案增量）/
    proposal（编辑提案）/ done / error。
    意图路由（B24）：edit → 单次生成提案（不进工具循环）；chitchat → 简短寒暄；
    analysis/verify/clarify_continue → 只读工具循环。落库由调用方（API 层）负责。
    can_edit=False（已发布/无编辑权限，B30）：编辑诉求直接给只读指引，不产提案不澄清；
    分析回答的提示词附权限声明，杜绝"可以帮你改"式越权承诺。
    """
    from app.services.copilot_proposals import (
        build_clarify, classify_intent, clarify_context, generate_proposal,
    )

    t0 = time.time()
    if llm is None:
        raise ValueError("llm client required")

    yield "stage", {"stage": "intent", "detail": "识别意图…"}
    intent_info = classify_intent(message, history=history, llm=llm)
    intent = intent_info["intent"]

    # B26 澄清后继续：把用户补充并回原指令，再按合成消息重新路由；
    # 回复若直接带具体编辑动词（分类为 edit），合成消息作为提案上下文一并带上
    effective = message
    combined = clarify_context(message, history)
    if combined and (intent == "clarify_continue" or intent == "edit"):
        effective = combined
        if intent == "clarify_continue":
            intent_info = classify_intent(effective, history=history, llm=llm)
            intent = intent_info["intent"]

    # B30：只读用户提出编辑诉求（含澄清链补充后仍是 edit）→ 确定性只读指引。
    # S6：有取消发布权的用户（负责人/超管）给一键"取消发布并调整"（unpublish_offer，
    # 前端按钮点击=用户确认，自动取消发布并重新发起原诉求）；无权限者维持找负责人指引。
    if intent == "edit" and not can_edit:
        unpublishable = can_unpublish_project(project, user)
        answer = _READONLY_EDIT_NOTICE_OFFER if unpublishable else _READONLY_EDIT_NOTICE
        for chunk in _sse_chunks(answer):
            yield "token", {"delta": chunk}
        done: dict = {"answer": answer, "tool_calls": [],
                      "latency_ms": int((time.time() - t0) * 1000),
                      "rounds": 0, "intent": intent, "proposal": None,
                      "readonly": True}
        if unpublishable:
            done["unpublish_offer"] = True
        yield "done", done
        return

    def _clarify_branch(trigger: str, errors: list[str] | None = None):
        """B26：进入澄清态——LLM 产问题+≤3 选项（含"其他"），SSE 推 clarify 事件。"""
        clarify = build_clarify(db, project, effective, history, llm,
                                trigger=trigger, errors=errors)
        if clarify.get("exhausted"):
            answer = clarify["question"]
            for chunk in _sse_chunks(answer):
                yield "token", {"delta": chunk}
            yield "done", {"answer": answer, "tool_calls": [],
                           "latency_ms": int((time.time() - t0) * 1000),
                           "rounds": 0, "intent": intent, "proposal": None,
                           "clarify": clarify}
            return
        yield "clarify", clarify
        answer = clarify["question"]
        for chunk in _sse_chunks(answer):
            yield "token", {"delta": chunk}
        yield "done", {"answer": answer, "tool_calls": [],
                       "latency_ms": int((time.time() - t0) * 1000),
                       "rounds": 0, "intent": intent, "proposal": None,
                       "clarify": clarify}

    if intent == "edit":
        # B26：模糊指令（"整理一下账户"）不赌提案生成，直接选项化澄清
        if intent_info.get("vague"):
            yield "stage", {"stage": "clarify", "detail": "需要澄清你的意图…"}
            yield from _clarify_branch(trigger="vague")
            return
        yield "stage", {"stage": "proposal", "detail": "生成编辑提案…"}
        result = generate_proposal(db, project, effective, selected, llm)
        if result.get("ok"):
            proposal = result["proposal"]
            answer = (f"已生成「{proposal['kind']}」编辑提案：{proposal['llm_reason']}\n\n"
                      "请在小窗的提案卡中核对影响面后确认应用"
                      + ("（高风险操作将要求二次确认）" if proposal["risk_tier"] == "high" else "")
                      + "。")
            for chunk in _sse_chunks(answer):
                yield "token", {"delta": chunk}
            latency_ms = int((time.time() - t0) * 1000)
            yield "done", {"answer": answer, "tool_calls": [],
                           "latency_ms": latency_ms, "rounds": 0,
                           "intent": intent, "proposal": proposal}
        else:
            # 幻觉 target 打回重生成仍失败 → 选项化澄清（B26 升级自纯文本提示）
            yield "stage", {"stage": "clarify", "detail": "需要澄清你的意图…"}
            yield from _clarify_branch(trigger=result.get("kind") or "hallucination",
                                       errors=result.get("missing"))
        return

    if intent == "verify":
        # B28：验证三件套（CQ 评测 / 一致性 / 抽样溯源），报告随 verify 事件+done 落库
        yield "stage", {"stage": "verify",
                        "detail": "跑验证三件套（CQ 评测 / 一致性 / 抽样溯源）…"}
        from app.services.copilot_verification import (
            SAMPLE_N_DEFAULT, compare_reports, latest_verify_report,
            run_verification, summarize_report,
        )
        yield "tool", {"phase": "start", "tool": "run_verification",
                       "args": {"sample_n": SAMPLE_N_DEFAULT}}
        t_verify = time.time()
        try:
            report = run_verification(db, project, llm, sample_n=SAMPLE_N_DEFAULT)
        except Exception as exc:  # noqa: BLE001 —— 三件套失败给可解释错误
            yield "tool", {"phase": "end", "tool": "run_verification",
                           "duration_ms": int((time.time() - t_verify) * 1000),
                           "observation": {"error": str(exc)[:300]}}
            yield "error", {"message": f"验证执行失败：{exc}"}
            return
        yield "tool", {"phase": "end", "tool": "run_verification",
                       "duration_ms": int((time.time() - t_verify) * 1000),
                       "observation": {"overall": report.get("overall"),
                                       "cq_total": (report.get("cq") or {}).get("total"),
                                       "shacl_violations":
                                           (report.get("consistency") or {}).get(
                                               "shacl_violations")}}
        prev = latest_verify_report(db, project.id)
        if prev:
            try:
                report["comparison"] = compare_reports(report, prev)
            except Exception:  # noqa: BLE001 —— 对比失败不阻断报告
                pass
        answer = summarize_report(report)
        for chunk in _sse_chunks(answer):
            yield "token", {"delta": chunk}
        yield "verify", {"report": report, "answer": answer}
        yield "done", {"answer": answer,
                       "tool_calls": [{
                           "tool": "run_verification",
                           "args": {"sample_n": SAMPLE_N_DEFAULT},
                           "observation": {"overall": report.get("overall"),
                                           "cq": (report.get("cq") or {}).get("total"),
                                           "shacl_violations":
                                               (report.get("consistency") or {}).get(
                                                   "shacl_violations")}}],
                       "latency_ms": int((time.time() - t0) * 1000),
                       "rounds": 0, "intent": "verify", "proposal": None,
                       "verify": report}
        return

    if intent == "chitchat":
        yield "stage", {"stage": "answer", "detail": "组织回答…"}
        try:
            resp = llm.call_llm(system_prompt=_CHITCHAT_SYSTEM, user_prompt=message,
                                stream=False)
            text = resp.get("content") if isinstance(resp, dict) else resp
            answer = str(text or "").strip() or "你好！我可以帮你分析本体、生成编辑提案或跑验证。"
        except Exception as exc:  # noqa: BLE001
            yield "error", {"message": f"模型输出失败：{exc}"}
            return
        for chunk in _sse_chunks(answer):
            yield "token", {"delta": chunk}
        yield "done", {"answer": answer, "tool_calls": [],
                       "latency_ms": int((time.time() - t0) * 1000), "rounds": 0,
                       "intent": intent}
        return

    # analysis / clarify_continue → 只读工具循环（B31：工具步骤以 ("tool", payload) 实时外推）
    yield "stage", {"stage": "context", "detail": "整理画布上下文…"}
    context = assemble_context(db, project, history=history, selected=selected)
    tools = build_toolbox(db, project)
    user_prompt = f"{context}\n\n【用户消息】\n{message}"
    # B30：只读用户回答前的权限声明（防"可以帮你改"式承诺）
    sys_prompt = SYSTEM_PROMPT if can_edit else SYSTEM_PROMPT + _READONLY_SYSTEM_SUFFIX

    from app.services.tool_loop import iter_tool_loop
    yield "stage", {"stage": "tool", "detail": "分析中（检索本体数据）…"}
    result = yield from iter_tool_loop(llm, sys_prompt, user_prompt, tools,
                                       max_rounds=MAX_ROUNDS)
    answer = result.get("answer") or ""
    tool_calls = result.get("tool_calls") or []
    if result.get("error"):
        # 模型/解析失败：给可解释的错误（不装成功）
        yield "stage", {"stage": "answer", "detail": "生成回答…"}
        err = result["error"]
        if err == "max_rounds_exhausted":
            answer = ("连续多轮检索仍未收敛出结论，建议换个更具体的问题（例如指定类名或实例名）。"
                      if not answer else answer)
        elif not answer:
            yield "error", {"message": f"模型输出失败：{err}"}
            return

    yield "stage", {"stage": "answer", "detail": "生成回答…"}
    for chunk in _sse_chunks(answer):
        yield "token", {"delta": chunk}

    latency_ms = int((time.time() - t0) * 1000)
    yield "done", {
        "answer": answer,
        "tool_calls": tool_calls,
        "latency_ms": latency_ms,
        "rounds": result.get("rounds", 0),
        "intent": intent,
    }


def save_turn(db: Session, project: Project, user: User, session: CopilotSession,
              message: str, answer: str = "", tool_calls: list | None = None,
              stages: list | None = None, status: str = "ok",
              latency_ms: int | None = None,
              kind: str = "chat",
              report: dict | None = None) -> tuple[CopilotSession, CopilotMessage]:
    """落一轮对话（user + assistant 两条消息，SSE 中断也记 stopped/failed）。

    report：kind=verify 的验证报告 JSON（O5-B28，copilot_messages.report 列）。
    """
    um = CopilotMessage(session_id=session.id, project_id=project.id, role="user",
                        kind="chat", content=message[:4000], status="ok")
    db.add(um)
    am = CopilotMessage(session_id=session.id, project_id=project.id, role="assistant",
                        kind=kind, content=(answer or "")[:8000],
                        stages=stages or None, tool_calls=tool_calls or None,
                        status=status, latency_ms=latency_ms, report=report)
    db.add(am)
    session.message_count = (session.message_count or 0) + 2
    session.updated_at = None  # 触发 ON UPDATE
    db.commit()
    db.refresh(am)
    return session, am


def get_or_create_session(db: Session, project: Project, user: User,
                          session_id: int | None, model_config_id: int | None) -> CopilotSession:
    if session_id:
        row = db.query(CopilotSession).filter(
            CopilotSession.id == int(session_id),
            CopilotSession.project_id == project.id).first()
        if row is None:
            raise ValueError("会话不存在")
        return row
    return CopilotSession(project_id=project.id, user_id=user.id,
                          title="新会话", model_config_id=model_config_id)


def build_llm(db: Session, project_id: int,
              model_config_id: int | None = None):
    """与 qa._build_llm 同约定：指定配置须可用（chat/extract、全局或本项目）。

    未指定时优先取第一个启用的 chat 模型（小窗默认开箱即用），再回落 legacy 默认链。
    """
    from app.api.qa import _build_llm
    if model_config_id is None:
        from app.infrastructure.database import ModelConfig
        rows = db.query(ModelConfig).filter(
            ModelConfig.enabled == True,  # noqa: E712
            ModelConfig.purpose == "chat").order_by(ModelConfig.id.asc()).all()
        usable = [r for r in rows if r.scope != "project" or r.project_id == project_id]
        if usable:
            model_config_id = usable[0].id
    return _build_llm(db, project_id, model_config_id)


__all__ = ["assemble_context", "run_copilot_turn", "save_turn",
           "get_or_create_session", "build_llm",
           "CONTEXT_CHAR_BUDGET", "HISTORY_WINDOW", "MAX_ROUNDS"]
