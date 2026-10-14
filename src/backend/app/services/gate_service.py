# app/services/gate_service.py - 发布门控配对评估（O3-B14，docs/design/09 §2）
# 重抽/修改产生候选版本 vN-cK（复用版本快照机制，不覆盖正式版本）；
# 发布前跑 CQ 配对评估：有 GT 走绝对评分，无 GT 走 LLM Judge 匿名 A/B
# （随机标 A/B 消位置偏差）；接受条件 = 零硬伤 + 非平局胜场严格更多。
# 门控记录（评分/verdict）持久化 gate_evaluations 可审计；幂等重试安全（每次评估新行）。
from __future__ import annotations

import json
import logging
import random

from sqlalchemy.orm import Session

from app.adapters.versioning import load_graph_for
from app.infrastructure.database import CompetencyQuestion, GateEvaluation, OntologyVersion
from app.services.cq_service import _question_terms

logger = logging.getLogger(__name__)

# GT 绝对评分阈值：term 覆盖率 ≥ 0.5 视为"该版本能回答此 CQ"
GT_ANSWERABLE_SCORE = 0.5
# 胜/平/负分差阈值
WIN_MARGIN = 0.1
# GT 评分剔除的疑问框架字（含这些字的 2-gram 是问法噪音，不是本体内容词）
_GT_FRAME_CHARS = "哪些什么是否如何怎么怎样多少请问吗呢吧啊了的是有没有介绍说明"

_JUDGE_SYSTEM = (
    "你是本体版本评估裁判。给你一个问题与两个版本（A/B）各自的本体摘要下的答案，"
    "判断哪个答案更能回答问题。只输出 JSON。"
)


def profile_text(gd: dict) -> str:
    """图快照的确定性文本摘要（配对评估的公共输入）：类/属性/实例三层。"""
    schema = (gd.get("schema") or {}) if isinstance(gd, dict) else {}
    lines: list[str] = []
    for c in schema.get("classes") or []:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        seg = f"类：{c['label']}"
        if c.get("parent"):
            seg += f"（父类：{c['parent']}）"
        if c.get("definition"):
            seg += f"：{str(c['definition'])[:120]}"
        lines.append(seg)
    for p in schema.get("object_properties") or []:
        if not isinstance(p, dict) or not p.get("label"):
            continue
        lines.append(f"关系：{p.get('domain') or '?'} -{p['label']}-> {p.get('range') or '?'}")
    for n in gd.get("nodes") or []:
        d = n.get("data") or {}
        if str(d.get("type") or "") in ("owl:NamedIndividual", "Instance"):
            lines.append(f"实例：{d.get('label')}（类型：{d.get('class_label') or '未分类'}）")
    return "\n".join(lines)


def gt_score(profile: str, question: str, criteria: str | None) -> float:
    """GT 绝对评分（确定性代理，无 LLM）：问题+GT 要点的词项在版本摘要中的覆盖率。

    词项复用 _question_terms（CJK 段+2-gram），但剔除含疑问框架字的 2-gram
    （哪些/什么/了 等问法噪音），否则绝对阈值 0.5 永远够不到。
    """
    terms = set(_question_terms(question)) | set(_question_terms(criteria or ""))
    terms = {t for t in terms
             if t.isascii() or not any(c in _GT_FRAME_CHARS for c in t)}
    if not terms:
        return 0.0
    hit = sum(1 for t in terms if t in profile)
    return round(hit / len(terms), 3)


def _llm_json(llm, system: str, user: str) -> dict | None:
    try:
        resp = llm.call_llm(system_prompt=system, user_prompt=user, stream=False,
                            json_schema={"name": "verdict",
                                         "schema": {"type": "object",
                                                    "properties": {
                                                        "winner": {"type": "string",
                                                                   "enum": ["A", "B", "tie"]},
                                                        "reason": {"type": "string"},
                                                        "candidate_has_errors": {"type": "boolean"}},
                                                    "required": ["winner", "reason"]}})
        text = resp.get("content") if isinstance(resp, dict) else resp
        if isinstance(text, dict):
            return text
        return json.loads(str(text))
    except Exception as exc:  # noqa: BLE001 —— 单条裁决失败按平局计，不伪造结论
        logger.warning(f"[gate] judge 失败：{exc}")
        return None


def run_gate_evaluation(db: Session, project, baseline_ver: OntologyVersion | None,
                        candidate_gd: dict, mode: str,
                        unacceptable_regressions: bool = False,
                        created_by: int | None = None,
                        judge_llm=None) -> GateEvaluation:
    """跑配对评估并落 gate_evaluations（O3-B14）。verdict: pass|reject。"""
    cqs = (db.query(CompetencyQuestion)
           .filter(CompetencyQuestion.project_id == project.id,
                   CompetencyQuestion.enabled.is_(True))
           .order_by(CompetencyQuestion.sort_order.asc()).all())
    baseline_gd = {}
    if baseline_ver is not None:
        try:
            baseline_gd = load_graph_for(db, project, baseline_ver) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[gate] 基线快照读取失败：{exc}")

    cand_profile = profile_text(candidate_gd)
    base_profile = profile_text(baseline_gd)

    wins = ties = losses = hard = 0
    results: list[dict] = []
    if mode == "gt":
        for cq in cqs:
            s_base = gt_score(base_profile, cq.question, cq.answer_criteria)
            s_cand = gt_score(cand_profile, cq.question, cq.answer_criteria)
            delta = round(s_cand - s_base, 3)
            is_hard = s_base >= GT_ANSWERABLE_SCORE > s_cand
            if is_hard:
                hard += 1
            if delta >= WIN_MARGIN:
                wins += 1
                verdict = "win"
            elif delta <= -WIN_MARGIN:
                losses += 1
                verdict = "loss"
            else:
                ties += 1
                verdict = "tie"
            results.append({"cq_id": cq.id, "question": cq.question,
                            "baseline_score": s_base, "candidate_score": s_cand,
                            "delta": delta, "verdict": verdict,
                            "hard_failure": is_hard})
    else:  # llm_judge：匿名 A/B（随机标 A/B 消位置偏差）
        if judge_llm is None:
            raise ValueError("llm_judge 模式需要 judge_llm")
        for cq in cqs:
            ans_base = _answer_from_profile(judge_llm, base_profile, cq.question)
            ans_cand = _answer_from_profile(judge_llm, cand_profile, cq.question)
            cand_is_a = random.Random(f"{cq.id}-{cq.question}").random() < 0.5
            a_text, b_text = ((ans_cand, ans_base) if cand_is_a else (ans_base, ans_cand))
            v = _llm_json(judge_llm, _JUDGE_SYSTEM,
                          f"【问题】{cq.question}\n\n【答案 A】\n{a_text}\n\n【答案 B】\n{b_text}\n\n"
                          "哪个答案更准确地回答了问题（信息更完整且无编造）？")
            winner_raw = (v or {}).get("winner") or "tie"
            cand_won = (winner_raw == "A") == cand_is_a and winner_raw != "tie"
            cand_err = bool((v or {}).get("candidate_has_errors"))
            is_hard = cand_err and winner_raw != "tie" and not cand_won
            if is_hard:
                hard += 1
            if cand_won:
                wins += 1
                verdict = "win"
            elif winner_raw != "tie":
                losses += 1
                verdict = "loss"
            else:
                ties += 1
                verdict = "tie"
            results.append({"cq_id": cq.id, "question": cq.question,
                            "verdict": verdict, "hard_failure": is_hard,
                            "judge_reason": (v or {}).get("reason", ""),
                            "hard": is_hard})

    passed = hard == 0 and wins > losses and (
        losses == 0 if unacceptable_regressions else True)
    row = GateEvaluation(
        project_id=project.id,
        mode=mode if mode in ("gt", "llm_judge") else "gt",
        baseline_version_id=baseline_ver.id if baseline_ver is not None else None,
        wins=wins, ties=ties, losses=losses, hard_failures=hard,
        unacceptable_regressions=bool(unacceptable_regressions),
        verdict="pass" if passed else "reject",
        results=results,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def _answer_from_profile(llm, profile: str, question: str) -> str:
    try:
        resp = llm.call_llm_text(
            system_prompt="只基于给定的本体摘要回答问题，摘要中没有的信息明确说没有。简洁回答。",
            user_prompt=f"【本体摘要】\n{profile[:6000]}\n\n【问题】{question}",
            stream=False, timeout=120.0)
        return (resp.get("content") if isinstance(resp, dict) else str(resp)) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[gate] 摘要问答失败：{exc}")
        return "（生成失败）"


def serialize_evaluation(row: GateEvaluation) -> dict:
    return {"id": row.id, "project_id": row.project_id, "mode": row.mode,
            "baseline_version_id": row.baseline_version_id,
            "candidate_version_id": row.candidate_version_id,
            "wins": row.wins, "ties": row.ties, "losses": row.losses,
            "hard_failures": row.hard_failures,
            "unacceptable_regressions": bool(row.unacceptable_regressions),
            "verdict": row.verdict, "results": row.results or [],
            "created_at": row.created_at.isoformat() if row.created_at else None}


__all__ = ["run_gate_evaluation", "profile_text", "gt_score", "serialize_evaluation",
           "GT_ANSWERABLE_SCORE", "WIN_MARGIN"]
