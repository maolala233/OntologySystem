# app/services/evolution_service.py - QA 失败轨迹驱动的进化建议（O3-B16，docs/design/09 §2）
# 触发：满 N 条失败问答（min_failures）或距上次分析超 window_days 天（EvoOntology 语义）。
# 聚合失败问题 → LLM 归因三分类（语义缺失/工具能力/结构问题）→
# 物化为 cq_suggestion 审核项（批准回写 CQ 列表）与 new_class/new_predicate 建议项。
from __future__ import annotations

import datetime
import json
import logging
import re

from sqlalchemy.orm import Session

from app.infrastructure.database import EvolutionReport, Project, QaHistory, ReviewItem

logger = logging.getLogger(__name__)

DEFAULT_MIN_FAILURES = 5
DEFAULT_WINDOW_DAYS = 7
MAX_CLUSTERS = 6

_ANALYSIS_SYSTEM = (
    "你是本体平台的进化分析器。给你一批失败的问答（检索不到引用或生成失败），"
    "把问题按主题聚类并归因，只输出 JSON。归因三分类：\n"
    "- semantic_gap：本体缺类/关系/实例，覆盖不了问题（给 suggested_cq/suggested_class/suggested_relations）\n"
    "- tool_limitation：本体覆盖了但检索/问答工具能力不足（改问法/改检索，不动物体）\n"
    "- structural_issue：层级/命名/结构混乱导致检索错乱\n"
    "cluster 数量按主题自然分，最多 6 个。"
)


def _cluster_key(question: str) -> str:
    """轻量聚类键：问题里长度≥2 的 CJK 段排序拼接（同主题问法共享词项）。"""
    segs = sorted(set(re.findall(r"[\u4e00-\u9fff]{2,}", question or "")))
    return "|".join(segs[:4]) or (question or "")[:30]


def _failure_stats(db: Session, project_id: int, window_days: int) -> dict:
    since = datetime.datetime.utcnow() - datetime.timedelta(days=window_days)
    rows = (db.query(QaHistory)
            .filter(QaHistory.project_id == project_id,
                    QaHistory.status == "failed",
                    QaHistory.created_at >= since)
            .order_by(QaHistory.id.desc())
            .limit(200).all())
    return {"count": len(rows), "rows": rows, "since": since}


def analyze_qa_failures(db: Session, project_id: int, user=None,
                        llm=None, min_failures: int = DEFAULT_MIN_FAILURES,
                        window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """分析失败问答并物化建议。返回 {"status": "skipped"|"analyzed", ...}。"""
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"status": "skipped", "reason": "project_not_found"}
    stats = _failure_stats(db, project_id, window_days)
    # 触发条件：满 N 条失败 或 本窗口从未分析过且有 ≥1 条失败
    last = (db.query(EvolutionReport)
            .filter(EvolutionReport.project_id == project_id)
            .order_by(EvolutionReport.id.desc()).first())
    enough = stats["count"] >= min_failures
    stale = last is None or (datetime.datetime.utcnow() - last.created_at
                             if last.created_at else datetime.timedelta.max) \
        >= datetime.timedelta(days=window_days)
    if not enough and not (stale and stats["count"] > 0):
        return {"status": "skipped", "reason": "not_enough_failures",
                "failure_count": stats["count"], "min_failures": min_failures}
    if stats["count"] == 0:
        return {"status": "skipped", "reason": "no_failures"}

    # 聚合：按轻量主题键分组，取每组代表问题
    clusters: dict[str, list[str]] = {}
    for r in stats["rows"]:
        clusters.setdefault(_cluster_key(r.question), []).append(r.question)
    grouped = sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:MAX_CLUSTERS]
    payload = [{"theme_key": k, "questions": qs[:5], "count": len(qs)}
               for k, qs in grouped]

    analysis: list[dict] = []
    if llm is not None:
        try:
            resp = llm.call_llm(
                system_prompt=_ANALYSIS_SYSTEM,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                stream=False,
                json_schema={"name": "evolution_analysis", "schema": {
                    "type": "object",
                    "properties": {"clusters": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string"},
                            "category": {"type": "string",
                                         "enum": ["semantic_gap", "tool_limitation",
                                                  "structural_issue"]},
                            "questions": {"type": "array", "items": {"type": "string"}},
                            "suggested_cq": {"type": "string"},
                            "suggested_class": {"type": "string"},
                            "suggested_relations": {"type": "array",
                                                    "items": {"type": "string"}},
                        },
                        "required": ["theme", "category", "questions"],
                    }}},
                    "required": ["clusters"]}})
            text = resp.get("content") if isinstance(resp, dict) else resp
            parsed = text if isinstance(text, dict) else json.loads(str(text))
            analysis = list((parsed or {}).get("clusters") or [])[:MAX_CLUSTERS]
        except Exception as exc:  # noqa: BLE001 —— LLM 失败降级为主题聚合（无归因）
            logger.warning(f"[evolution] LLM 归因失败，降级主题聚合：{exc}")
    if not analysis:  # 降级：主题聚合即建议（每簇一条 CQ 候选）
        analysis = [{"theme": (k or "失败问题")[:40], "category": "semantic_gap",
                     "questions": qs[:3], "suggested_cq": qs[0] if qs else ""}
                    for k, qs in grouped]

    created_items: list[int] = []
    for cl in analysis:
        category = str(cl.get("category") or "semantic_gap")
        questions = [str(q) for q in (cl.get("questions") or []) if q][:5]
        cq = str(cl.get("suggested_cq") or "").strip()
        if cq:
            item = ReviewItem(
                project_id=project_id,
                item_type="cq_suggestion",
                payload={"suggested_cq": cq[:2000],
                         "category": category,
                         "theme": str(cl.get("theme") or "")[:200],
                         "evidence_questions": questions},
                reason=f"失败问答进化建议（{category}）：主题「{cl.get('theme') or ''}」",
                priority="medium", status="pending")
            db.add(item)
            db.flush()
            created_items.append(item.id)
        cls_label = str(cl.get("suggested_class") or "").strip()
        if category == "semantic_gap" and cls_label:
            item = ReviewItem(
                project_id=project_id,
                item_type="new_class",
                payload={"label": cls_label[:64], "source": "evolution",
                         "definition": f"进化建议：失败问答主题「{cl.get('theme') or ''}」缺失该类",
                         "instance_count": 0},
                reason=f"失败问答建议补充类（{category}）",
                priority="medium", status="pending")
            db.add(item)
            db.flush()
            created_items.append(item.id)
        for rel in (cl.get("suggested_relations") or [])[:5]:
            rel_text = str(rel).strip()
            if not rel_text:
                continue
            item = ReviewItem(
                project_id=project_id,
                item_type="new_predicate",
                payload={"label": rel_text[:64], "source": "evolution",
                         "domain": "", "range": ""},
                reason=f"失败问答建议补充关系（{category}）：{rel_text[:60]}",
                priority="low", status="pending")
            db.add(item)
            db.flush()
            created_items.append(item.id)

    report = EvolutionReport(
        project_id=project_id,
        failure_count=stats["count"],
        window_days=window_days,
        clusters=analysis,
        review_item_ids=created_items,
        created_by=user.id if user is not None else None,
    )
    db.add(report)
    db.flush()
    return {"status": "analyzed", "report_id": report.id,
            "failure_count": stats["count"], "clusters": analysis,
            "review_items_created": len(created_items)}


def serialize_report(r: EvolutionReport) -> dict:
    return {"id": r.id, "project_id": r.project_id,
            "failure_count": r.failure_count, "window_days": r.window_days,
            "clusters": r.clusters or [], "review_item_ids": r.review_item_ids or [],
            "created_at": r.created_at.isoformat() if r.created_at else None}


__all__ = ["analyze_qa_failures", "serialize_report", "_failure_stats",
           "DEFAULT_MIN_FAILURES", "DEFAULT_WINDOW_DAYS"]
