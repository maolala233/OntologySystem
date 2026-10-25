# app/services/metrics_store.py - 治理指标存取（S24，docs/15 / COA GovernedMetric 同构）
# 金融复杂操作的口径语义：指标（单笔限额/大额审批线/成功率）= 本体一等公民。
# 单一事实源 = Project.graph_data.schema.metrics（blob，与公理同模式）：
#   {label, definition, expression(口径), unit, applies_to(适用类), severity(block|warn),
#    evidence?(锚点), source_type, certified, decided_by?, decided_at?}
# 语义：指标是"口径的单一权威来源"——QA Tier0 直出、resolve_semantics 可解析、
# precheck_action 对涉及金额/数量的操作返回限额命中。
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.adapters.provenance import normalize_source_type
from app.infrastructure.database import Project

METRIC_SEVERITIES = ("block", "warn")


def list_metrics(proj: Project) -> list[dict]:
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    items = [m for m in ((gd.get("schema") or {}).get("metrics") or [])
             if isinstance(m, dict) and m.get("label")]
    out = []
    for m in items:
        out.append({"label": str(m["label"]), "definition": m.get("definition") or "",
                    "expression": m.get("expression") or "", "unit": m.get("unit") or "",
                    "applies_to": m.get("applies_to") or [],
                    "severity": m.get("severity") or "warn",
                    "source_type": m.get("source_type") or "ai",
                    "certified": bool(m.get("certified")),
                    "evidence": m.get("evidence")})
    return out


def declare_metric(db: Session, proj: Project, payload: dict) -> dict:
    """声明/更新指标（按 label 幂等 upsert）。"""
    from sqlalchemy.orm.attributes import flag_modified

    label = str(payload.get("label") or "").strip()
    if not label:
        return {"skipped": "label 不能为空"}
    severity = str(payload.get("severity") or "warn")
    if severity not in METRIC_SEVERITIES:
        return {"skipped": f"severity 取值限 {METRIC_SEVERITIES}"}
    applies_to = [str(x).strip() for x in (payload.get("applies_to") or []) if str(x).strip()]

    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    metrics = [m for m in (schema.get("metrics") or []) if isinstance(m, dict)]
    entry = {
        "label": label,
        "definition": str(payload.get("definition") or "").strip(),
        "expression": str(payload.get("expression") or "").strip(),
        "unit": str(payload.get("unit") or "").strip(),
        "applies_to": applies_to,
        "severity": severity,
        "source_type": normalize_source_type(payload.get("source_type") or "steward_specified"),
        "certified": bool(payload.get("certified", True)),
        "decided_by": payload.get("decided_by"),
        "decided_at": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
    ev = payload.get("evidence")
    if isinstance(ev, dict) and ev:
        entry["evidence"] = ev
    # upsert by label
    for i, m in enumerate(metrics):
        if str(m.get("label") or "") == label:
            metrics[i] = entry
            break
    else:
        metrics.append(entry)
    schema["metrics"] = metrics
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"metric_declared": label}


def remove_metric(db: Session, proj: Project, label: str) -> dict:
    from sqlalchemy.orm.attributes import flag_modified

    label = str(label or "").strip()
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") or {}
    metrics = [m for m in (schema.get("metrics") or []) if isinstance(m, dict)]
    before = len(metrics)
    schema["metrics"] = [m for m in metrics if str(m.get("label") or "") != label]
    if len(schema["metrics"]) == before:
        return {"skipped": f"指标不存在: {label}"}
    gd["schema"] = schema
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"removed": label}


def certify_metric(db: Session, proj: Project, label: str, certified: bool = True,
                   decided_by: int | None = None) -> dict:
    from sqlalchemy.orm.attributes import flag_modified

    label = str(label or "").strip()
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") or {}
    hit = None
    for m in (schema.get("metrics") or []):
        if isinstance(m, dict) and str(m.get("label") or "") == label:
            hit = m
            break
    if hit is None:
        return {"skipped": f"指标不存在: {label}"}
    hit["certified"] = bool(certified)
    if certified:
        if hit.get("source_type") not in ("steward_edited", "steward_specified"):
            hit["source_type"] = "steward_edited"
        hit["decided_by"] = decided_by
        hit["decided_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    else:
        hit["source_type"] = "ai"
    proj.graph_data = gd
    flag_modified(proj, "graph_data")
    db.flush()
    return {"label": label, "certified": bool(certified),
            "source_type": hit.get("source_type", "ai")}


def find_metrics_for(gd: dict, class_label: str | None = None,
                     keyword: str | None = None) -> list[dict]:
    """按适用类/关键词取指标（precheck 与 Tier0 注入的查询面）。"""
    gd = gd if isinstance(gd, dict) else {}
    out = []
    for m in ((gd.get("schema") or {}).get("metrics") or []):
        if not isinstance(m, dict) or not m.get("label"):
            continue
        if class_label and str(class_label) not in [str(x) for x in (m.get("applies_to") or [])]:
            continue
        if keyword and str(keyword) not in str(m.get("label", "")) + str(m.get("definition", "")):
            continue
        out.append(m)
    return out
