# app/services/copilot_verification.py - 对话式验证报告（O5-B28，docs/design/14 §5 S4）
#
# verify 意图触发的三件套：
#   ① CQ 评测（复用 B12 评测器 evaluate_cqs）；
#   ② 一致性（owlrl 推理 error + SHACL 违例汇总）；
#   ③ 抽样溯源核验（随机 N 个实例/关系，get_evidence 拉 chunk 原文，LLM 判定
#      支撑/部分支撑/无支撑）。
# 报告 JSON 落 copilot_messages.report（kind=verify），支持与上一版对比与存档到版本。
from __future__ import annotations

import logging
import random

from sqlalchemy.orm import Session

from app.core.logging import logger
from app.infrastructure.database import (
    CopilotMessage,
    Entity,
    Project,
    Relation,
)

SAMPLE_N_DEFAULT = 5
SAMPLE_N_MAX = 10

_VERDICT_SYSTEM = (
    "你是本体质量审核员。根据给定的证据原文判断一条本体事实是否有文档依据。"
    '只输出 JSON：{"verdict":"supported|partial|unsupported","reason":"≤60字"}。'
    "verdict 口径：supported=证据原句直接支撑该事实；partial=证据相关但不完整/仅间接；"
    "unsupported=证据无关或为空。禁止编造。"
)


def _schema_of(project: Project) -> dict:
    return ((project.graph_data or {}).get("schema") or {})


# ── ① CQ 评测 ──────────────────────────────────────────────

def _cq_section(db: Session, project_id: int, retrieve_fn=None) -> dict:
    from app.services.cq_service import evaluate_cqs, serialize_evaluation

    try:
        ev = evaluate_cqs(db, project_id, trigger="copilot_verify",
                          retrieve_fn=retrieve_fn)
        if ev is None:
            return {"total": 0, "note": "项目未配置评测问题（CQ），跳过本段"}
        s = serialize_evaluation(ev)
        failed = [r for r in (s.get("results") or []) if not r.get("answerable")]
        return {"total": s.get("total", len(s.get("results") or [])),
                "answerable": s.get("answerable"),
                "coverage_rate": s.get("coverage_rate"),
                "failed": [
                    {"question": r["question"], "source_count": r["source_count"]}
                    for r in failed[:5]],
                "evaluation_id": ev.id}
    except Exception as exc:  # noqa: BLE001 —— 单段失败不阻断整份报告
        logger.warning(f"[copilot-verify] CQ 评测失败：{exc}")
        return {"error": f"CQ 评测失败：{str(exc)[:120]}"}


# ── ② 一致性（owlrl 错误 + SHACL 违例） ────────────────────

def _consistency_section(db: Session, project: Project) -> dict:
    section: dict = {}

    # owlrl：跑一次最新推理取 error 项（结果落 reasoning_results，latest-only）
    try:
        from app.services.reasoning_service import run_reasoning
        res = run_reasoning(db, project, profile="owlrl")
        items = res.get("items") or []
        errors = [it for it in items
                  if str(it.get("predicate_label") or "").lower() in ("error", "错误")]
        section["owlrl_errors"] = len(errors)
        section["error_samples"] = [
            {"subject": it.get("subject_label"), "object": it.get("object_label")}
            for it in errors[:5]]
        section["inferred_count"] = max(0, len(items) - len(errors))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-verify] owlrl 推理失败：{exc}")
        section["owlrl_errors"] = 1
        section["error_samples"] = [{"message": f"owlrl 推理失败：{str(exc)[:120]}"}]

    # SHACL：违例统计（记分卡 F7 同口径）
    try:
        from app.adapters.shacl_checks import run_shacl_summary
        ents = (db.query(Entity).filter(Entity.project_id == project.id).all())
        rels = (db.query(Relation).filter(Relation.project_id == project.id).all())
        ents_d = [{"id": e.id, "label": e.label, "class_label": e.class_label,
                   "props": e.props or {}} for e in ents]
        rels_d = [{"subject_id": r.subject_id, "predicate": r.predicate,
                   "object_id": r.object_id} for r in rels]
        summary = run_shacl_summary(ents_d, rels_d, _schema_of(project))
        section["shacl_violations"] = summary.get("violations", 0)
        section["shacl_by_kind"] = summary.get("by_kind", {})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-verify] SHACL 失败：{exc}")
        section["shacl_violations"] = None
        section["shacl_error"] = str(exc)[:120]
    return section


# ── ③ 抽样溯源核验 ─────────────────────────────────────────

def _sample_pool(db: Session, project_id: int) -> list[dict]:
    """抽样池：实体与关系各占一半（不足时互相补位），随机取样。"""
    ents = (db.query(Entity.id, Entity.label)
            .filter(Entity.project_id == project_id).all())
    rels = (db.query(Relation.id, Relation.predicate,
                     Relation.subject_id, Relation.object_id)
            .filter(Relation.project_id == project_id).all())
    pool: list[dict] = []
    ent_ids = {e.id: e.label for e in ents}
    for r in rels:
        pool.append({"type": "relation", "id": r.id,
                     "target": f"{ent_ids.get(r.subject_id, r.subject_id)}"
                               f" -[{r.predicate}]-> "
                               f"{ent_ids.get(r.object_id, r.object_id)}",
                     "label": r.predicate})
    for e in ents:
        pool.append({"type": "entity", "id": e.id, "target": e.label, "label": e.label})
    random.shuffle(pool)
    return pool


def _judge_sample(llm, item: dict, evidence_texts: list[str]) -> dict:
    """LLM 判定单条样例的证据支撑度；无 LLM 或无证据时给确定性结论。"""
    if not evidence_texts:
        return {"verdict": "unsupported",
                "reason": "无证据锚点（provenance/schema evidence 均为空）"}
    if llm is None:
        return {"verdict": "partial",
                "reason": "存在证据锚点（未配置模型，无法自动判定支撑度）"}
    try:
        prompt = (
            f"事实：{item['target']}\n\n证据原文：\n" +
            "\n---\n".join(evidence_texts[:3]))
        resp = llm.call_llm(system_prompt=_VERDICT_SYSTEM, user_prompt=prompt,
                            stream=False, json_schema={
                                "name": "verdict",
                                "schema": {"type": "object", "properties": {
                                    "verdict": {"type": "string",
                                                "enum": ["supported", "partial",
                                                         "unsupported"]},
                                    "reason": {"type": "string"}}}})
        from app.services.copilot_proposals import _extract_json
        data = _extract_json(resp) or {}
        verdict = str(data.get("verdict") or "").strip()
        if verdict not in ("supported", "partial", "unsupported"):
            verdict = "partial"
        return {"verdict": verdict, "reason": str(data.get("reason") or "")[:120]}
    except Exception as exc:  # noqa: BLE001 —— 判定失败降级为 partial（有证据）
        logger.warning(f"[copilot-verify] LLM 判定失败：{exc}")
        return {"verdict": "partial", "reason": f"自动判定失败，人工复核：{str(exc)[:80]}"}


def _evidence_texts(db: Session, project: Project, item: dict, limit: int = 3) -> list[str]:
    """样例的证据原文：实体走 get_evidence；关系直查 ProvenanceRecord（target_type=relation）。"""
    from app.services.copilot_tools import get_evidence

    if item["type"] == "entity":
        ev = get_evidence(db, project, {"entity_id": item["id"],
                                        "label": item["label"], "limit": limit})
        return [a.get("evidence") or "" for a in (ev.get("evidence") or [])
                if a.get("evidence")]

    from app.infrastructure.database import DocumentChunk, ProvenanceRecord, UploadedDocument
    doc_ids = {d.id for d in db.query(UploadedDocument.id)
               .filter(UploadedDocument.project_id == project.id,
                       UploadedDocument.deleted_at.is_(None)).all()}
    provs = (db.query(ProvenanceRecord)
             .filter(ProvenanceRecord.project_id == project.id,
                     ProvenanceRecord.target_type == "relation",
                     ProvenanceRecord.target_id == item["id"])
             .order_by(ProvenanceRecord.id).limit(limit).all())
    texts = []
    for p in provs:
        t = p.evidence_text or ""
        if p.source_document_id in doc_ids and p.chunk_index is not None:
            chunk = (db.query(DocumentChunk)
                     .filter(DocumentChunk.document_id == p.source_document_id,
                             DocumentChunk.chunk_index == int(p.chunk_index)).first())
            if chunk is not None and not t:
                t = chunk.text[:500]
        if t:
            texts.append(t[:600])
    return texts


def _sample_section(db: Session, project: Project, llm, sample_n: int) -> dict:
    pool = _sample_pool(db, project.id)
    if not pool:
        return {"sampled": 0, "samples": [], "note": "画布为空，跳过抽样"}
    picked = pool[:max(1, min(sample_n, SAMPLE_N_MAX))]
    samples: list[dict] = []
    for item in picked:
        texts = _evidence_texts(db, project, item)
        judgement = _judge_sample(llm, item, texts)
        samples.append({
            "type": item["type"], "target": item["target"][:120],
            "verdict": judgement["verdict"], "reason": judgement["reason"],
            "evidence_count": len(texts),
            "quote": texts[0][:200] if texts else None})
    supported = sum(1 for s in samples if s["verdict"] == "supported")
    unsupported = sum(1 for s in samples if s["verdict"] == "unsupported")
    return {"sampled": len(samples), "supported": supported,
            "partial": sum(1 for s in samples if s["verdict"] == "partial"),
            "unsupported": unsupported,
            "support_rate": round(supported / len(samples), 3) if samples else None,
            "samples": samples}


# ── 汇总 / 对比 / 摘要 ─────────────────────────────────────

def run_verification(db: Session, project: Project, llm,
                     sample_n: int = SAMPLE_N_DEFAULT,
                     retrieve_fn=None) -> dict:
    """验证三件套一次跑齐，返回报告 JSON（写入方负责持久化）。"""
    cq = _cq_section(db, project.id, retrieve_fn=retrieve_fn)
    consistency = _consistency_section(db, project)
    sample = _sample_section(db, project, llm, sample_n)

    # 总体结论：SHACL/一致性硬伤 > 0 或抽样无支撑率高 → warn/fail
    hard = ((consistency.get("owlrl_errors") or 0)
            + (consistency.get("shacl_violations") or 0))
    if hard > 0 or (sample.get("unsupported") or 0) > (sample.get("sampled") or 1) / 2:
        overall = "warn" if hard <= 2 else "fail"
    else:
        overall = "pass"
    return {"overall": overall, "cq": cq, "consistency": consistency,
            "sample": sample}


def latest_verify_report(db: Session, project_id: int) -> dict | None:
    """本项目最近一份验证报告（不含 comparison，避免链式膨胀）。"""
    row = (db.query(CopilotMessage)
           .filter(CopilotMessage.project_id == project_id,
                   CopilotMessage.kind == "verify",
                   CopilotMessage.report.isnot(None))
           .order_by(CopilotMessage.id.desc()).first())
    if row is None:
        return None
    rep = dict(row.report or {})
    rep.pop("comparison", None)
    return rep


def compare_reports(current: dict, previous: dict) -> dict:
    """与上一版验证报告对比：指出本版多了什么问题/解决了什么。"""
    out: dict = {}

    def _num(d: dict, *path):
        v = d
        for p in path:
            v = (v or {}).get(p) if isinstance(v, dict) else None
        return v if isinstance(v, (int, float)) else None

    deltas = {}
    for key, path in [("coverage_rate", ("cq", "coverage_rate")),
                      ("answerable", ("cq", "answerable")),
                      ("owlrl_errors", ("consistency", "owlrl_errors")),
                      ("shacl_violations", ("consistency", "shacl_violations")),
                      ("unsupported_samples", ("sample", "unsupported"))]:
        cur, prev = _num(current, *path), _num(previous, *path)
        if cur is not None and prev is not None and cur != prev:
            deltas[key] = {"previous": prev, "current": cur, "delta": cur - prev}
    out["metrics"] = deltas

    # CQ：本版新失败 / 上版失败本版通过
    cur_failed = {f.get("question") for f in ((current.get("cq") or {}).get("failed") or [])}
    prev_failed = {f.get("question") for f in ((previous.get("cq") or {}).get("failed") or [])}
    out["newly_failed_cqs"] = sorted(cur_failed - prev_failed)[:5]
    out["resolved_cqs"] = sorted(prev_failed - cur_failed)[:5]
    return out


def summarize_report(report: dict) -> str:
    """报告的人话摘要（作为会话 answer 文本）。"""
    cq = report.get("cq") or {}
    cons = report.get("consistency") or {}
    sample = report.get("sample") or {}
    overall_text = {"pass": "✅ 通过", "warn": "⚠️ 有警告", "fail": "❌ 未通过"}.get(
        report.get("overall"), report.get("overall", ""))
    lines = [f"验证完成，总体结论：{overall_text}。"]

    if cq.get("total"):
        lines.append(f"① CQ 评测：{cq.get('answerable', 0)}/{cq.get('total')} 条可回答，"
                     f"证据覆盖率 {cq.get('coverage_rate')}；"
                     + (f"未通过示例：{cq['failed'][0]['question']}" if cq.get("failed")
                        else "全部通过。"))
    else:
        lines.append(f"① CQ 评测：{cq.get('note') or cq.get('error') or '未执行'}")

    owlrl_n = cons.get("owlrl_errors")
    shacl_n = cons.get("shacl_violations")
    lines.append(f"② 一致性：owlrl 错误 {owlrl_n if owlrl_n is not None else '未知'} 项，"
                 f"SHACL 违例 {shacl_n if shacl_n is not None else '未知'} 条"
                 + (f"（{cons.get('shacl_error')}）" if cons.get("shacl_error") else "。"))

    if sample.get("sampled"):
        lines.append(f"③ 抽样溯源：核验 {sample.get('sampled')} 条，"
                     f"证据支撑 {sample.get('supported')} / 部分 {sample.get('partial')} / "
                     f"无支撑 {sample.get('unsupported')}。")
    else:
        lines.append(f"③ 抽样溯源：{sample.get('note') or '未执行'}")
    return "\n".join(lines)


__all__ = ["run_verification", "latest_verify_report", "compare_reports",
           "summarize_report", "SAMPLE_N_DEFAULT", "SAMPLE_N_MAX"]
