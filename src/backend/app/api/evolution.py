# app/api/evolution.py - 失败问答进化建议（O3-B16/F8，docs/design/09 §2）
# 失败问题聚合报告可查；建议项（cq_suggestion/new_class/new_predicate）进审核流，
# cq_suggestion 批准后回写 CQ 列表（source=evolution）。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import NotFoundError
from app.infrastructure.database import EvolutionReport, Project, QaHistory, User
from app.services import evolution_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/projects", tags=["evolution"])


@router.get("/{project_id}/evolution/failures")
def list_failures(project_id: int, limit: int = 50,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """失败问答轨迹列表（B16 数据源可查）。"""
    require_project_role(project_id, "viewer", user, db)
    rows = (db.query(QaHistory)
            .filter(QaHistory.project_id == project_id, QaHistory.status == "failed")
            .order_by(QaHistory.id.desc())
            .limit(max(1, min(limit, 200))).all())
    return {"items": [{"id": r.id, "question": r.question, "error": r.error,
                       "repaired_rounds": r.repaired_rounds,
                       "created_at": r.created_at.isoformat() if r.created_at else None}
                      for r in rows], "total": len(rows)}


@router.post("/{project_id}/evolution/analyze")
def analyze(project_id: int, body: dict | None = None,
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """手动触发进化分析（beat 每日任务也调同一服务；触发参数可仿 EvoOntology 调整）。"""
    require_project_role(project_id, "editor", user, db)
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    body = body or {}
    from app.adapters.provider import build_legacy_llm_config
    from app.infrastructure.llm_client import LLMClient

    llm = None
    try:  # 未配置模型时退化为确定性聚合（llm=None 路径），分析不因模型缺失而不可用
        cfg = build_legacy_llm_config(db, project_id)
        llm = LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                        model=cfg.get("model"))
    except Exception:  # noqa: BLE001
        llm = None
    result = evolution_service.analyze_qa_failures(
        db, project_id, user=user, llm=llm,
        min_failures=int(body.get("min_failures") or evolution_service.DEFAULT_MIN_FAILURES),
        window_days=int(body.get("window_days") or evolution_service.DEFAULT_WINDOW_DAYS))
    db.commit()
    log_action(db, user.id, "evolution.analyze", "project", str(project_id),
               {"status": result.get("status"),
                "review_items": result.get("review_items_created", 0)})
    db.commit()
    return result


@router.get("/{project_id}/evolution/report")
def latest_report(project_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    require_project_role(project_id, "viewer", user, db)
    row = (db.query(EvolutionReport)
           .filter(EvolutionReport.project_id == project_id)
           .order_by(EvolutionReport.id.desc()).first())
    if row is None:
        return {"report": None}
    return {"report": evolution_service.serialize_report(row)}


# ─────────────── S21（docs/15）：轨迹聚合 + 进化触发器 ───────────────

@router.get("/{project_id}/evolution/trajectories")
def list_trajectories(project_id: int, limit: int = 50,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """进化素材轨迹（EvoOntology 形状聚合视图）：qa + copilot 可观察行为，
    不记思维链；结果字段截断。只读，viewer 可查。"""
    require_project_role(project_id, "viewer", user, db)
    from app.services.trajectory import list_trajectories as _list

    return {"items": _list(db, project_id, limit=limit)}


@router.get("/{project_id}/evolution/trigger/check")
def trigger_check(project_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """进化触发器检查（S21-2）：新轨迹 ≥N 条或距 checkpoint ≥M 天 → due。

    幂等；due 只代表「该提醒进化了」，不自动启动任何进化动作。
    """
    require_project_role(project_id, "viewer", user, db)
    from app.services import trigger

    result = trigger.check(db, project_id)
    db.commit()  # due 翻转持久化
    return result


@router.post("/{project_id}/evolution/trigger/advance")
def trigger_advance(project_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """手动推进 checkpoint（编辑意图：本批轨迹看过/暂不进化）。发布成功会自动推进。

    allow_published=True：checkpoint 推进的正常时机恰在发布之后（项目已只读），
    与 publish 的幂等重发豁免同理。
    """
    require_project_role(project_id, "editor", user, db, allow_published=True)
    from app.services import trigger

    result = trigger.advance_checkpoint(db, project_id)
    db.commit()
    log_action(db, user.id, "evolution.trigger_advance", "project", str(project_id), result)
    db.commit()
    return result
