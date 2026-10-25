# app/services/publish_gate.py - 发布门禁报告采集器（S17，docs/15 S17-1/S17-3）
# 把 publish() 内联的门禁抽成单一事实源：同一份检查同时服务
# ① 发布端点（阻断式，保留原错误码语义）② 只读预览端点（FE3 发布向导 Step1）。
# S17-1 幂等信号也在此采集：与最近一次发布的内容 checksum 比对（EvoOntology
# publish 语义：同内容重试安全返回既有发布，不重复建版本/不重跑 CQ 评估）。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.versioning import compute_checksum, latest_official_version, quality_gate
from app.infrastructure.database import (
    CompetencyQuestion,
    OntologyVersion,
    Project,
    Publication,
)


def collect_gate_report(db: Session, proj: Project, gd: dict) -> dict:
    """采集发布门禁报告（只读，不落库不发事件）。

    返回 {passed, checks, warnings, content_changed, checksum, last_publication}。
    check = {code, name, level(block|warn|info), passed, detail}；
    passed=False 仅由 block 级失败决定；warnings 汇总各检查的非阻断告警。
    CQ 配对评估（LLM judge 成本高）只在正式发布时执行，此处仅报告触发条件。
    """
    gd = gd if isinstance(gd, dict) else {}
    checks: list[dict] = []

    # 门禁 1：知识域必填
    has_domain = bool(proj.domain_id or (proj.domains or "").strip())
    checks.append({"code": "DOMAIN_REQUIRED", "name": "知识域已设置",
                   "level": "block", "passed": has_domain,
                   "detail": None if has_domain else "发布前必须设置知识域"})

    # 门禁 2：无 pending/claimed 的 critical/high 审核项
    from app.services.review_service import publish_blockers

    blockers = publish_blockers(db, proj.id)
    checks.append({"code": "PENDING_CRITICAL_REVIEWS", "name": "无未处理的高优审核项",
                   "level": "block", "passed": not blockers,
                   "detail": {"blockers": blockers[:20]} if blockers else None})

    # 门禁 3：TBox 机检（类层次环/悬空父类引用/自父）
    health = quality_gate(gd)
    checks.append({"code": "SCHEMA_CHECKS_FAILED", "name": "TBox 机检（层次环/悬空父类/自父）",
                   "level": "block", "passed": bool(health["passed"]),
                   "detail": {"errors": health.get("errors", []),
                              "warnings": health.get("warnings", [])}})

    # 门禁 4：TBox 证据闭合（O2-B9；S16-Step2 扩展到公理锚点）
    from app.services.evidence_closure import tbox_evidence_closure

    closure = tbox_evidence_closure(db, proj.id, gd)
    checks.append({"code": "EVIDENCE_CLOSURE_FAILED", "name": "TBox 证据闭合",
                   "level": "block", "passed": bool(closure["passed"]),
                   "detail": {"dangling": closure["dangling"][:20],
                              "missing_count": len(closure["missing"])}})

    # 门禁 5（info）：CQ 配对评估触发条件（O3-B14）
    cq_count = (db.query(CompetencyQuestion)
                .filter(CompetencyQuestion.project_id == proj.id,
                        CompetencyQuestion.enabled.is_(True)).count())
    baseline = latest_official_version(db, proj.id)
    checks.append({"code": "CQ_PAIRWISE_GATE", "name": "CQ 配对评估门控",
                   "level": "info", "passed": True,
                   "detail": {"cq_count": cq_count,
                              "will_run_at_publish": cq_count > 0 and baseline is not None,
                              "baseline_version_no": baseline.version_no if baseline else None}})

    # S17-1 幂等信号：与最近一次发布的内容 checksum 比对
    checksum = compute_checksum(gd)
    last = (db.query(Publication)
            .filter(Publication.project_id == proj.id,
                    Publication.status == "published")
            .order_by(Publication.id.desc()).first())
    last_info = None
    last_checksum = None
    if last is not None:
        ver = db.query(OntologyVersion).filter(OntologyVersion.id == last.version_id).first()
        last_checksum = ver.checksum if ver else None
        last_info = {"publication_id": last.id,
                     "version_no": ver.version_no if ver else None}
    content_changed = last_checksum is None or last_checksum != checksum

    blocks = [c for c in checks if c["level"] == "block" and not c["passed"]]
    warnings: list[str] = []
    for c in checks:
        d = c.get("detail")
        if isinstance(d, dict):
            warnings.extend(str(w) for w in d.get("warnings") or [])
            if c["code"] == "EVIDENCE_CLOSURE_FAILED" and d.get("missing_count"):
                warnings.append(f"证据闭合：{d['missing_count']} 个对象无证据锚点（不阻断，建议补齐）")

    return {"passed": not blocks, "checks": checks, "warnings": warnings,
            "content_changed": content_changed, "checksum": checksum,
            "last_publication": last_info}
