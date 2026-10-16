# app/services/copilot_diagnosis.py - 首轮诊断 + 建议卡物化（O5-B27，docs/design/14 §5 S3）
# 职责：抽取/重抽完成回调追加内置诊断 prompt（记分卡 + 结构统计 + CQ 评测 + 失败轨迹），
# Top ≤5 建议卡落 copilot_proposals(kind=suggestion)；
# "转审核项"复用既有裁决流（review_service 八类建议批准链）。
# 不做运行时主动推送（14 §5 B27 验收口径）；线程异步，不阻塞抽取完成耗时。
from __future__ import annotations

import json
import logging
import threading
import uuid

from sqlalchemy.orm import Session

from app.infrastructure.database import CopilotProposal, Project

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5

# 转审核项允许的 review_kind（对齐 review_items 枚举与 review_service 批准链）
REVIEW_KINDS = ("new_class", "new_predicate", "cq_suggestion", "axiom_suggestion")

_DIAGNOSIS_SYSTEM = (
    "你是企业本体构建平台的首轮诊断器。抽取刚完成，请基于给定画像给出最值得人工跟进的改进建议，"
    "最多 5 条，按重要性排序。每条输出："
    '{"title":"一句话标题（人话、面向非本体专业用户）",'
    '"reason":"为什么要处理（引用具体数字/名称）",'
    '"evidence":["支撑证据（数字、类名、失败问题等，来自画像）"],'
    '"priority":"high|medium|low",'
    '"review_kind":"new_class|new_predicate|cq_suggestion|axiom_suggestion|none",'
    '"label":"建议新增的类/关系名（review_kind 适用时）",'
    '"suggested_cq":"建议补充的测评问题（review_kind=cq_suggestion 时）"}'
    "只输出 JSON：{\"suggestions\":[...]}。没有值得建议的就给空数组。"
    "禁止编造画像里没有的数字或名称。"
)


def _diagnosis_context(db: Session, project_id: int) -> dict:
    """诊断画像：记分卡 + 结构统计 + 最近 CQ 评测 + 最近失败轨迹（B16）。"""
    from app.services.copilot_tools import get_schema_stats

    proj = db.query(Project).filter(Project.id == project_id).first()
    card = {}
    try:
        from app.services.copilot_tools import get_scorecard
        card = get_scorecard(db, proj, {}) if proj is not None else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-diagnosis] 记分卡读取失败：{exc}")
    stats = {}
    try:
        stats = get_schema_stats(db, proj, {}) if proj is not None else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-diagnosis] 结构统计读取失败：{exc}")

    cq = None
    try:
        from app.infrastructure.database import CqEvaluation
        row = (db.query(CqEvaluation)
               .filter(CqEvaluation.project_id == project_id)
               .order_by(CqEvaluation.id.desc()).first())
        if row is not None:
            cq = {"trigger": row.trigger, "mode": row.mode,
                  "total_cq": int(row.total_cq or 0),
                  "answerable_count": int(row.answerable_count or 0),
                  "coverage_rate": float(row.coverage_rate or 0),
                  "failed": [r.get("question") for r in (row.results or [])
                             if isinstance(r, dict) and not r.get("answerable")][:5]}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-diagnosis] CQ 评测读取失败：{exc}")

    evolution = None
    try:
        from app.infrastructure.database import EvolutionReport
        row = (db.query(EvolutionReport)
               .filter(EvolutionReport.project_id == project_id)
               .order_by(EvolutionReport.id.desc()).first())
        if row is not None and row.clusters:
            evolution = {"failure_count": int(row.failure_count or 0),
                         "clusters": [
                             {k: c.get(k) for k in ("theme", "category", "count")}
                             for c in row.clusters[:5] if isinstance(c, dict)]}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-diagnosis] 失败轨迹读取失败：{exc}")

    return {"scorecard": card, "schema_stats": stats,
            "cq_evaluation": cq, "failure_trajectories": evolution}


def run_first_diagnosis(project_id: int, trigger: str = "schema_extract") -> int:
    """跑一轮首轮诊断并把建议卡落 copilot_proposals(kind=suggestion, pending)。

    返回新建建议条数；无可用 LLM / 模型失败 / 画像为空时静默返回 0（诊断永不阻断抽取）。
    自开 DB 会话（在后台线程中执行）。
    """
    from app.infrastructure.database import SessionLocal
    from app.services.ontology_copilot import build_llm

    db = SessionLocal()
    created = 0
    try:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj is None:
            return 0
        try:
            llm = build_llm(db, project_id, None)
        except Exception as exc:  # noqa: BLE001 —— 无可用模型：跳过诊断
            logger.info(f"[copilot-diagnosis] 项目 {project_id} 无可用模型，跳过诊断：{exc}")
            return 0
        context = _diagnosis_context(db, project_id)
        if not context["schema_stats"] and not context["scorecard"]:
            return 0
        from app.services.copilot_proposals import _extract_json
        resp = llm.call_llm(
            system_prompt=_DIAGNOSIS_SYSTEM,
            user_prompt=("【项目画像】\n" + json.dumps(context, ensure_ascii=False)[:6000]
                         + f"\n【触发时机】{trigger}"),
            stream=False, json_schema={
                "name": "diagnosis",
                "schema": {"type": "object", "properties": {
                    "suggestions": {"type": "array"}}}})
        data = _extract_json(resp) or {}
        items = data.get("suggestions") if isinstance(data.get("suggestions"), list) else []

        pending_titles = {str(r.targets.get("title") or "")
                          for r in db.query(CopilotProposal)
                          .filter(CopilotProposal.project_id == project_id,
                                  CopilotProposal.kind == "suggestion",
                                  CopilotProposal.status == "pending").all()
                          if isinstance(r.targets, dict)}
        for raw in items[:MAX_SUGGESTIONS]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            reason = str(raw.get("reason") or "").strip()
            if not title or not reason or title in pending_titles:
                continue
            db.add(CopilotProposal(
                proposal_id=f"cp_{uuid.uuid4().hex[:10]}",
                project_id=project_id, session_id=None,
                kind="suggestion",
                targets={"title": title[:120],
                         "priority": str(raw.get("priority") or "medium")[:8],
                         "review_kind": str(raw.get("review_kind") or "none")[:32],
                         "label": (str(raw.get("label")).strip()[:64]
                                   if raw.get("label") else None),
                         "suggested_cq": (str(raw.get("suggested_cq")).strip()[:500]
                                          if raw.get("suggested_cq") else None),
                         "trigger": trigger},
                llm_reason=reason[:1000],
                evidence=[str(x)[:300] for x in (raw.get("evidence") or [])
                          if str(x).strip()][:6],
                risk_tier="low",
            ))
            pending_titles.add(title)
            created += 1
        db.commit()
        return created
    except Exception as exc:  # noqa: BLE001 —— 诊断失败只告警
        logger.warning(f"[copilot-diagnosis] 项目 {project_id} 诊断失败：{exc}", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


def schedule_first_diagnosis(project_id: int, trigger: str = "schema_extract") -> None:
    """抽取完成回调入口：后台线程跑诊断（≤30s 增量在后台消化，不阻塞任务返回）。"""
    threading.Thread(
        target=run_first_diagnosis,
        args=(project_id, trigger),
        name=f"copilot-diagnosis-{project_id}", daemon=True).start()


def convert_suggestion_to_review(db: Session, project_id: int, row: CopilotProposal,
                                 user_id: int) -> int:
    """建议卡"转审核项"：按 review_kind 物化 review_items（复用既有裁决流）。

    兜底：review_kind 无效/缺 label 时落 cq_suggestion（标题即待测评问题，
    批准后转正为 CQ 资产，无破坏性）。成功后建议卡置 applied 并回链审核项 id。
    """
    from app.infrastructure.database import ReviewItem

    t = row.targets if isinstance(row.targets, dict) else {}
    kind = str(t.get("review_kind") or "")
    title = str(t.get("title") or "诊断建议")
    label = str(t.get("label") or "").strip()
    cq = str(t.get("suggested_cq") or "").strip()
    if kind == "new_class" and label:
        item_type, payload = "new_class", {
            "label": label, "source": "copilot_diagnosis",
            "definition": (row.llm_reason or "")[:200]}
    elif kind == "new_predicate" and label:
        item_type, payload = "new_predicate", {
            "label": label, "source": "copilot_diagnosis"}
    elif kind == "axiom_suggestion" and t.get("axiom_payload"):
        item_type, payload = "axiom_suggestion", {
            **t["axiom_payload"], "source": "copilot_diagnosis"}
    else:
        item_type = "cq_suggestion"
        payload = {"suggested_cq": (cq or title)[:2000],
                   "category": "copilot_diagnosis", "theme": title[:200],
                   "suggestion_id": row.proposal_id}
    priority = t.get("priority") if t.get("priority") in ("high", "medium", "low") else "medium"
    item = ReviewItem(project_id=project_id, item_type=item_type, payload=payload,
                      reason=f"AI 首轮诊断建议：{title}"[:255],
                      priority=priority, status="pending")
    db.add(item)
    db.flush()
    row.status = "applied"
    row.result = {"converted_review_item_id": item.id, "converted_by": user_id}
    return item.id


__all__ = ["run_first_diagnosis", "schedule_first_diagnosis",
           "convert_suggestion_to_review", "MAX_SUGGESTIONS", "REVIEW_KINDS"]
