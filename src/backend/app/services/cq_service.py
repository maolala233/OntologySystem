# app/services/cq_service.py - Competency Question 资产化（O3-B12，docs/design/09 §2）
# ①抽取前：CQ 注入 guidance 约束抽取范围（压噪声、省 token）；
# ②抽取后：CQ 自动转评测集（检索式跑通率/证据覆盖率，确定性无 LLM）并回写 cq_evaluations。
from __future__ import annotations

import datetime
import logging

from sqlalchemy.orm import Session

from app.adapters.retrieval import RetrievalQuery, retrieve as default_retrieve
from app.infrastructure.database import CompetencyQuestion, CqEvaluation

logger = logging.getLogger(__name__)

# 单条 CQ 判定"可回答"所需的最低引用数（检索式口径，无 LLM）
ANSWERABLE_MIN_SOURCES = 1


def list_cqs(db: Session, project_id: int, enabled_only: bool = False) -> list[CompetencyQuestion]:
    q = (db.query(CompetencyQuestion)
         .filter(CompetencyQuestion.project_id == project_id))
    if enabled_only:
        q = q.filter(CompetencyQuestion.enabled.is_(True))
    return q.order_by(CompetencyQuestion.sort_order.asc(),
                      CompetencyQuestion.id.asc()).all()


def create_cqs(db: Session, project_id: int, items: list[dict],
               created_by: int | None, source: str = "manual") -> list[CompetencyQuestion]:
    """批量建 CQ（单条也走这里）。question 必填去重（项目内归一化后重复的跳过）。"""
    base = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.project_id == project_id).all()
    existing = {str(c.question or "").strip().lower() for c in base}
    out: list[CompetencyQuestion] = []
    next_sort = max((c.sort_order or 0) for c in base) + 1 if base else 1
    for item in items:
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        key = question.lower()
        if key in existing:
            continue
        existing.add(key)
        row = CompetencyQuestion(
            project_id=project_id,
            question=question[:2000],
            answer_criteria=(str(item.get("answer_criteria") or "").strip() or None),
            source=source if source in ("manual", "evolution") else "manual",
            sort_order=int(item.get("sort_order") or next_sort),
            enabled=bool(item.get("enabled", True)),
            created_by=created_by,
        )
        next_sort += 1
        db.add(row)
        out.append(row)
    db.flush()
    return out


def update_cq(db: Session, project_id: int, cq_id: int, patch: dict) -> CompetencyQuestion | None:
    row = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.id == cq_id,
        CompetencyQuestion.project_id == project_id).first()
    if row is None:
        return None
    if "question" in patch and str(patch["question"] or "").strip():
        row.question = str(patch["question"]).strip()[:2000]
    if "answer_criteria" in patch:
        row.answer_criteria = (str(patch["answer_criteria"] or "").strip() or None)
    if "enabled" in patch:
        row.enabled = bool(patch["enabled"])
    if "sort_order" in patch and isinstance(patch["sort_order"], int):
        row.sort_order = patch["sort_order"]
    row.updated_at = datetime.datetime.utcnow()
    db.flush()
    return row


def delete_cq(db: Session, project_id: int, cq_id: int) -> bool:
    row = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.id == cq_id,
        CompetencyQuestion.project_id == project_id).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def reorder_cqs(db: Session, project_id: int, ordered_ids: list[int]) -> int:
    rows = {c.id: c for c in db.query(CompetencyQuestion).filter(
        CompetencyQuestion.project_id == project_id).all()}
    n = 0
    for i, cid in enumerate(ordered_ids, start=1):
        row = rows.get(int(cid))
        if row is not None:
            row.sort_order = i
            n += 1
    db.flush()
    return n


def guidance_from_cqs(db: Session, project_id: int,
                      base_guidance: str | None) -> str | None:
    """抽取 guidance 注入（O3-B12①）：项目有启用 CQ 时，拼装 CQ 引导段。

    引导语义：优先抽取与 CQ 主体/关系相关的类与实例，抑制与问题无关的泛化类型。
    """
    cqs = list_cqs(db, project_id, enabled_only=True)
    if not cqs:
        return base_guidance
    lines = [f"{i}. {c.question}" for i, c in enumerate(cqs[:20], start=1)]
    cq_block = ("【Competency Questions 引导】本项目的本体要能回答以下能力问题，"
                "抽取时优先覆盖这些问题涉及的实体类型、关系与属性，"
                "与问题无关的泛化名词不要建类：\n" + "\n".join(lines))
    if base_guidance:
        return f"{base_guidance}\n\n{cq_block}"
    return cq_block


def _question_terms(question: str) -> list[str]:
    """查询词项：长度≥2 的连续 CJK 段与 ASCII 词，去虚词。"""
    import re
    stopwords = {"什么", "哪些", "如何", "怎么", "是否", "多少", "哪些个", "请问", "的", "了", "吗"}
    tokens: list[str] = []
    for seg in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", question or ""):
        if seg.isascii():
            if len(seg) >= 2:
                tokens.append(seg.lower())
            continue
        # 中文：整段 + 2-gram（无分词器，2-gram 提命中鲁棒性）
        if seg not in stopwords:
            tokens.append(seg)
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in stopwords:
                tokens.append(bg)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def evaluate_cqs(db: Session, project_id: int, trigger: str = "manual",
                 retrieve_fn=None, created_by: int | None = None,
                 baseline_version_id: int | None = None,
                 candidate_version_id: int | None = None) -> CqEvaluation | None:
    """CQ 评测（O3-B12②）：检索式确定性口径。

    answerable = 检索返回 ≥ANSWERABLE_MIN_SOURCES 条引用；
    coverage   = 该 CQ 引用中"真实文档/图谱证据"（有 source_document_id 或 graph_edge
                 带 quote）的占比；overall coverage_rate = 各 CQ coverage 均值。
    retrieve_fn 可注入（测试/离线评估），缺省用 adapters.retrieval.retrieve。
    """
    cqs = list_cqs(db, project_id, enabled_only=True)
    if not cqs:
        return None
    _retrieve = retrieve_fn or default_retrieve
    results: list[dict] = []
    for cq in cqs:
        try:
            sources = _retrieve(RetrievalQuery(
                project_id=project_id, question=cq.question,
                top_k=8, use_graph=True, use_inferred=False), db=db) or []
        except Exception as exc:  # noqa: BLE001 —— 单条失败不影响整份报告
            logger.warning(f"[cq-eval] project={project_id} cq={cq.id} 检索失败：{exc}")
            sources = []
        n = len(sources)
        evidence = 0
        for s in sources:
            doc = getattr(s, "source_document_id", None)
            quote = getattr(s, "quote", None)
            if doc or (quote and str(quote).strip()):
                evidence += 1
        coverage = round(evidence / n, 3) if n else 0.0
        results.append({
            "cq_id": cq.id,
            "question": cq.question,
            "answerable": n >= ANSWERABLE_MIN_SOURCES,
            "source_count": n,
            "coverage": coverage,
        })
    answerable = sum(1 for r in results if r["answerable"])
    rate = round(sum(r["coverage"] for r in results) / len(results), 3) if results else 0.0
    row = CqEvaluation(
        project_id=project_id,
        trigger=trigger if trigger in ("manual", "auto_post_extract", "publish_gate") else "manual",
        mode="retrieval",
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        total_cq=len(results),
        answerable_count=answerable,
        coverage_rate=rate,
        results=results,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def serialize_cq(cq: CompetencyQuestion) -> dict:
    return {"id": cq.id, "project_id": cq.project_id, "question": cq.question,
            "answer_criteria": cq.answer_criteria, "source": cq.source,
            "sort_order": cq.sort_order, "enabled": bool(cq.enabled),
            "created_at": cq.created_at.isoformat() if cq.created_at else None}


def serialize_evaluation(ev: CqEvaluation) -> dict:
    return {"id": ev.id, "project_id": ev.project_id, "trigger": ev.trigger,
            "mode": ev.mode, "total_cq": ev.total_cq,
            "answerable_count": ev.answerable_count,
            "coverage_rate": float(ev.coverage_rate or 0),
            "results": ev.results or [],
            "created_at": ev.created_at.isoformat() if ev.created_at else None}


__all__ = ["list_cqs", "create_cqs", "update_cq", "delete_cq", "reorder_cqs",
           "guidance_from_cqs", "evaluate_cqs", "serialize_cq", "serialize_evaluation",
           "ANSWERABLE_MIN_SOURCES"]
