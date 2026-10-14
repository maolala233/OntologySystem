# app/services/impact_service.py - 发布影响分析 + TBox 漂移监控（O3-B13，docs/design/09 §2）
# 发布前 diff 影响面（相对正式基线版本：改动的类/关系/实例 + 波及面 + 待审项），
# 替代"点了发布才报错"；抽取后 TBox 未覆盖的新实体类型 → 漂移候选物化为审核项。
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.adapters.schema_gate import match_class
from app.adapters.versioning import diff_graphs
from app.infrastructure.database import Project, ReviewItem

logger = logging.getLogger(__name__)

DRIFT_REVIEW_LIMIT = 20  # 单次物化漂移候选上限（防大项目刷屏）


def _baseline_graph(db: Session, project: Project) -> tuple[int | None, dict]:
    """正式基线 = 最近一个非候选版本（发布/自动打版/手动/回滚）；无则空图。"""
    from app.adapters.versioning import load_graph_for
    from app.infrastructure.database import OntologyVersion

    ver = (db.query(OntologyVersion)
           .filter(OntologyVersion.project_id == project.id,
                   OntologyVersion.is_candidate.is_(False))
           .order_by(OntologyVersion.version_no.desc()).first())
    if ver is None:
        return None, {}
    try:
        return ver.id, load_graph_for(db, project, ver) or {}
    except Exception as exc:  # noqa: BLE001 —— 基线快照缺失时按空基线给出全量影响
        logger.warning(f"[impact] 基线快照读取失败 project={project.id} v{ver.version_no}: {exc}")
        return ver.id, {}


def _schema_class_labels(schema: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in (schema.get("classes") or []):
        if isinstance(c, dict) and c.get("label"):
            out[str(c["label"])] = list(c.get("aliases") or [])
    return out


def detect_drift(db: Session, project_id: int, gd: dict) -> list[dict]:
    """TBox 漂移检测：ABox 实例的 class_label 无法命中 schema 类（四级匹配）→ 聚合。

    已有 pending 的 new_class 审核项（闸门已物化）的同名 label 不重复报。
    """
    schema = (gd.get("schema") or {}) if isinstance(gd, dict) else {}
    tbox = _schema_class_labels(schema)
    from app.services.graph_rows import CLASS_TYPES

    counts: dict[str, dict] = {}
    for n in gd.get("nodes") or []:
        d = n.get("data") or {}
        if str(d.get("type") or "") not in CLASS_TYPES:
            label = str(d.get("class_label") or "").strip()
            if not label or match_class(label, tbox) is not None:
                continue
            entry = counts.setdefault(label, {"label": label, "instance_count": 0,
                                              "samples": []})
            entry["instance_count"] += 1
            if len(entry["samples"]) < 3:
                entry["samples"].append({
                    "node_label": str(d.get("label") or ""),
                    "quote": str(d.get("source_quote") or "")[:200],
                    "source_document": str(d.get("source_document") or ""),
                })
    if not counts:
        return []
    # 排除闸门已物化待审的同名 label（避免双报）
    pending = (db.query(ReviewItem.payload)
               .filter(ReviewItem.project_id == project_id,
                       ReviewItem.item_type == "new_class",
                       ReviewItem.status.in_(("pending", "claimed"))).all())
    covered: set[str] = set()
    for (payload,) in pending:
        if isinstance(payload, dict):
            covered.add(str(payload.get("label") or "").strip().lower())
    return [e for lbl, e in sorted(counts.items(), key=lambda kv: -kv[1]["instance_count"])
            if lbl.strip().lower() not in covered]


def materialize_drift_reviews(db: Session, project_id: int, gd: dict,
                              limit: int = DRIFT_REVIEW_LIMIT) -> list[int]:
    """漂移候选 → review_items(new_class, payload.source='drift')；批准复用既有 TBox 回写。"""
    drift = detect_drift(db, project_id, gd)[:limit]
    ids: list[int] = []
    for entry in drift:
        item = ReviewItem(
            project_id=project_id,
            item_type="new_class",
            payload={"label": entry["label"], "source": "drift",
                     "instance_count": entry["instance_count"],
                     "samples": entry["samples"],
                     "definition": ""},
            reason=f"TBox 漂移：{entry['instance_count']} 个实例的类型「{entry['label']}」未被骨架覆盖",
            priority="medium",
            status="pending",
        )
        db.add(item)
        db.flush()
        ids.append(item.id)
    return ids


def publish_impact(db: Session, project: Project, gd: dict | None = None) -> dict:
    """发布影响面（O3-B13）：相对正式基线版本的四级 diff + 波及实例/关系 + 待审项 + 漂移。"""
    current = gd if gd is not None else (project.graph_data or {})
    baseline_id, baseline = _baseline_graph(db, project)
    diff = diff_graphs(baseline, current)

    changed_classes: set[str] = set()
    ch = diff.get("classes") or {}
    # diff_graphs 口径：added/removed 是标签字符串列表，modified 是 {label,...} 字典
    changed_classes |= set(ch.get("added") or [])
    changed_classes |= set(ch.get("removed") or [])
    changed_classes |= {m.get("label") for m in (ch.get("modified") or []) if isinstance(m, dict)}
    changed_classes.discard(None)
    chp = diff.get("properties") or {}
    changed_props = set(chp.get("added") or []) | set(chp.get("removed") or [])
    changed_props |= {m.get("label") for m in (chp.get("modified") or []) if isinstance(m, dict)}
    changed_props.discard(None)

    # 波及面：改动类的实例数 + 改动谓词的边数（当前工作图口径）
    from app.services.graph_rows import CLASS_TYPES

    affected_instances = 0
    for n in current.get("nodes") or []:
        d = n.get("data") or {}
        if str(d.get("type") or "") not in CLASS_TYPES:
            if str(d.get("class_label") or "") in changed_classes:
                affected_instances += 1
    affected_relations = 0
    for e in current.get("edges") or []:
        d = e.get("data") or {}
        if str(d.get("relation") or "") in changed_props:
            affected_relations += 1

    pending_reviews = (db.query(ReviewItem)
                       .filter(ReviewItem.project_id == project.id,
                               ReviewItem.status.in_(("pending", "claimed"))).count())
    drift = detect_drift(db, project.id, current)

    def _count(level: str) -> dict:
        ch = diff.get(level) or {}
        return {"added": len(ch.get("added") or []),
                "removed": len(ch.get("removed") or []),
                "modified": len(ch.get("modified") or [])}

    return {
        "baseline_version_id": baseline_id,
        "has_baseline": baseline_id is not None,
        "diff": {lvl: _count(lvl) for lvl in ("classes", "properties", "instances", "relations")},
        "affected": {
            "instances": affected_instances,
            "relations": affected_relations,
            "pending_reviews": pending_reviews,
        },
        "changed_classes": sorted(changed_classes)[:20],
        "changed_properties": sorted(changed_props)[:20],
        "drift": drift,
    }


__all__ = ["publish_impact", "detect_drift", "materialize_drift_reviews", "DRIFT_REVIEW_LIMIT"]
