# app/tasks/evolution_tasks.py - QA 失败轨迹定时进化分析（O3-B16，docs/design/09 §2）
# beat 每日扫描：有失败问答且满足触发条件（满 N 条或 7 天未分析）的项目
# → evolution_service.analyze_qa_failures → cq_suggestion/new_class/new_predicate 审核项。
# 单项目失败不影响其他项目（每个 try/except 独立）。

from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.evolution_tasks.daily_evolution_scan")
def daily_evolution_scan() -> dict:
    import logging

    from app.infrastructure.database import Project, SessionLocal
    from app.services.evolution_service import analyze_qa_failures

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    results = {"analyzed": [], "skipped": 0, "failed": []}
    try:
        from app.adapters.provider import build_legacy_llm_config
        from app.infrastructure.llm_client import LLMClient

        for (pid,) in db.query(Project.id).all():
            try:
                llm = None
                try:
                    cfg = build_legacy_llm_config(db, pid)
                    llm = LLMClient(api_key=cfg.get("api_key"),
                                    base_url=cfg.get("base_url"), model=cfg.get("model"))
                except Exception:  # noqa: BLE001 —— 无模型配置退化确定性聚合（llm=None 路径）
                    llm = None
                out = analyze_qa_failures(db, pid, user=None, llm=llm)
                if out.get("status") == "analyzed":
                    results["analyzed"].append(
                        {"project_id": pid, "report_id": out.get("report_id"),
                         "clusters": out.get("clusters")})
                    db.commit()
                else:
                    results["skipped"] += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                results["failed"].append({"project_id": pid, "error": str(e)[:200]})
        logger.info(f"[evolution] 每日扫描完成：analyzed={len(results['analyzed'])} "
                    f"skipped={results['skipped']} failed={len(results['failed'])}")
        return results
    finally:
        db.close()
