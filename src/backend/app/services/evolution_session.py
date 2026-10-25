# app/services/evolution_session.py - 进化运行状态机（S22，docs/15 / EvoOntology EvolutionSession 同构）
# 状态机：running --reject 循环--> running；--pass--> accepted；--预算/中断--> incomplete。
# 职责边界：本模块编排「假设→候选→配对评估→结论」并留痕；实际发布仍走 /publish
# （其门禁会复评并落版本，S21 checkpoint 随发布推进）——确定性门禁重算不因路径而变。
# 反停滞：budget_exhausted 之外的 incomplete 原因需已积累 ≥2 次 reject。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.versioning import (
    create_candidate_version,
    latest_official_version,
)
from app.core.exceptions import APIError
from app.infrastructure.database import (
    CompetencyQuestion,
    EvolutionRoundRow,
    EvolutionRun,
    Project,
)
from app.services import gate_service

INCOMPLETE_REASONS = ("budget_exhausted", "user_interrupted", "missing_data",
                      "unreliable_evaluation", "external_block")
# 反停滞：这些"判断类"原因需要先积累足够 reject（EvoOntology min_rejects_before_incomplete）
STRICT_REASONS = ("missing_data", "unreliable_evaluation", "external_block")
MIN_REJECTS_BEFORE_INCOMPLETE = 2
DEFAULT_BUDGET = 8


def _run_or_404(db: Session, project_id: int, run_id: int) -> EvolutionRun:
    run = (db.query(EvolutionRun)
           .filter(EvolutionRun.id == run_id,
                   EvolutionRun.project_id == project_id).first())
    if run is None:
        raise APIError(f"进化运行不存在: {run_id}", code="EVOLUTION_RUN_NOT_FOUND",
                       http_status=404)
    return run


def _reject_count(db: Session, run_id: int) -> int:
    return (db.query(EvolutionRoundRow)
            .filter(EvolutionRoundRow.run_id == run_id,
                    EvolutionRoundRow.verdict == "reject").count())


def start_run(db: Session, proj: Project, user_id: int | None,
              hypothesis: str | None = None, budget_rounds: int | None = None) -> EvolutionRun:
    """开新运行：单项目同时只允许一个 running（EvoOntology 语义——resume 而非并行）。"""
    running = (db.query(EvolutionRun)
               .filter(EvolutionRun.project_id == proj.id,
                       EvolutionRun.status == "running").first())
    if running is not None:
        raise APIError(f"已有进行中的进化运行 #{running.id}，请先完成（resume 语义）",
                       code="EVOLUTION_RUN_RUNNING", http_status=409)
    baseline = latest_official_version(db, proj.id)
    run = EvolutionRun(
        project_id=proj.id,
        status="running",
        hypothesis=hypothesis,
        budget_rounds=max(1, min(int(budget_rounds or DEFAULT_BUDGET), 20)),
        parent_version_id=baseline.id if baseline else None,
        created_by=user_id,
    )
    db.add(run)
    db.flush()
    return run


def diagnose(db: Session, proj: Project, run: EvolutionRun, user_id: int | None = None,
             llm=None) -> dict:
    """诊断步（B16 失败挖掘 + 触发器快照）：为假设设计提供素材，不改状态。"""
    from app.services import evolution_service, trigger

    report = evolution_service.analyze_qa_failures(db, proj.id, user=user_id, llm=llm)
    trig = trigger.check(db, proj.id)
    run.diagnosis = {"evolution_report": report, "trigger": trig}
    db.flush()
    return {"run_id": run.id, "diagnosis": run.diagnosis}


def evaluate_round(db: Session, proj: Project, run: EvolutionRun, user_id: int | None,
                   hypothesis: str | None = None, mode: str | None = None,
                   unacceptable_regressions: bool = False, judge_llm=None) -> dict:
    """一轮：候选（当前画布）→ 配对评估（B14 确定性重算）→ 结论留痕。

    pass → run=accepted（随后走 /publish 复评落版本）；reject → 留痕继续；
    预算耗尽（round 达 budget 且仍未 pass）→ 自动 incomplete(budget_exhausted)。
    """
    if run.status != "running":
        raise APIError(f"运行 #{run.id} 已终态（{run.status}），不可再评估",
                       code="EVOLUTION_RUN_TERMINAL", http_status=400)

    cq_count = (db.query(CompetencyQuestion)
                .filter(CompetencyQuestion.project_id == proj.id,
                        CompetencyQuestion.enabled.is_(True)).count())
    baseline = latest_official_version(db, proj.id)
    if cq_count == 0 or baseline is None:
        raise APIError("配对评估需要：启用中的 CQ 且存在正式基线版本（先在「评测问题」配置）",
                       code="GATE_PRECONDITION_MISSING", http_status=400)

    if mode is None:
        mode = ("gt" if db.query(CompetencyQuestion)
                .filter(CompetencyQuestion.project_id == proj.id,
                        CompetencyQuestion.enabled.is_(True),
                        CompetencyQuestion.answer_criteria.isnot(None)).first()
                else "llm_judge")

    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    candidate = create_candidate_version(
        db, proj, "full", user_id or 0,
        label=f"进化候选 #{run.id}-r{run.round + 1}",
        description=f"进化运行 #{run.id} 第 {run.round + 1} 轮候选")
    db.flush()
    evaluation = gate_service.run_gate_evaluation(
        db, proj, baseline, gd, mode,
        unacceptable_regressions=unacceptable_regressions,
        created_by=user_id, judge_llm=judge_llm)
    evaluation.candidate_version_id = candidate.id
    db.flush()

    run.round += 1
    verdict = evaluation.verdict
    round_row = EvolutionRoundRow(
        run_id=run.id, round_no=run.round,
        hypothesis=hypothesis or run.hypothesis,
        candidate_version_id=candidate.id,
        gate_evaluation_id=evaluation.id, mode=mode, verdict=verdict,
        wins=evaluation.wins, ties=evaluation.ties, losses=evaluation.losses,
        hard_failures=evaluation.hard_failures,
        notes=(evaluation.results or [{}])[0].get("question") if evaluation.results else None,
    )
    db.add(round_row)

    if verdict == "pass":
        run.status = "accepted"
        result = {"run_id": run.id, "status": "accepted", "round": run.round,
                  "verdict": "pass", "candidate_version_id": candidate.id,
                  "gate_evaluation_id": evaluation.id,
                  "next": "调用 /publish 完成发布（复评落版本，checkpoint 随发布推进）"}
    else:
        result = {"run_id": run.id, "status": run.status, "round": run.round,
                  "verdict": "reject", "wins": evaluation.wins, "ties": evaluation.ties,
                  "losses": evaluation.losses, "hard_failures": evaluation.hard_failures,
                  "next": "修订假设后再评估；checkpoint 不推进，同一批轨迹可重试"}
    # 预算纪律：轮数达上限仍未 pass → 自动 incomplete
    if run.status == "running" and run.round >= run.budget_rounds:
        run.status = "incomplete"
        run.incomplete_reason = "budget_exhausted"
        result["status"] = "incomplete"
        result["incomplete_reason"] = "budget_exhausted"
        result["next"] = f"预算 {run.budget_rounds} 轮耗尽仍未通过：本轮不发布，可开新运行"
    db.flush()
    return result


def mark_incomplete(db: Session, proj: Project, run: EvolutionRun, reason: str,
                    user_id: int | None = None) -> dict:
    """中断运行：判断类原因需 ≥2 次 reject（反停滞）——预算/用户中断例外。"""
    if run.status != "running":
        raise APIError(f"运行 #{run.id} 已终态（{run.status}）",
                       code="EVOLUTION_RUN_TERMINAL", http_status=400)
    if reason not in INCOMPLETE_REASONS:
        raise APIError(f"incomplete 原因限 {INCOMPLETE_REASONS}",
                       code="EVOLUTION_INCOMPLETE_REASON_INVALID", http_status=400)
    if reason in STRICT_REASONS and _reject_count(db, run.id) < MIN_REJECTS_BEFORE_INCOMPLETE:
        raise APIError(
            f"原因 {reason} 属判断类：需先积累 {MIN_REJECTS_BEFORE_INCOMPLETE} 次 reject"
            "（设计下一个候选再评估，而不是放弃）",
            code="EVOLUTION_ANTI_STAGNATION", http_status=400)
    run.status = "incomplete"
    run.incomplete_reason = reason
    db.flush()
    return {"run_id": run.id, "status": "incomplete", "incomplete_reason": reason}


def serialize_run(db: Session, run: EvolutionRun, with_rounds: bool = True) -> dict:
    rounds = (db.query(EvolutionRoundRow)
              .filter(EvolutionRoundRow.run_id == run.id)
              .order_by(EvolutionRoundRow.round_no.asc()).all())
    out = {
        "id": run.id, "status": run.status, "hypothesis": run.hypothesis,
        "budget_rounds": run.budget_rounds, "round": run.round,
        "parent_version_id": run.parent_version_id,
        "diagnosis": run.diagnosis, "incomplete_reason": run.incomplete_reason,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if with_rounds:
        out["rounds"] = [{
            "round_no": r.round_no, "hypothesis": r.hypothesis,
            "candidate_version_id": r.candidate_version_id,
            "gate_evaluation_id": r.gate_evaluation_id, "mode": r.mode,
            "verdict": r.verdict, "wins": r.wins, "ties": r.ties,
            "losses": r.losses, "hard_failures": r.hard_failures,
            "notes": r.notes,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rounds]
    return out
