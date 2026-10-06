# app/adapters/conflicts.py - 冲突检测与解决（docs/design/04 §6，M3-5）
# 平台实现（Semantica ConflictDetector 语义，行表数据面）：
#   VALUE        同(label_norm,class)多实体同属性不同值（消解后残留=真冲突）
#   TYPE         同(label_norm)多类型
#   RELATIONSHIP 同(subject,predicate)多客体，且谓词在 TBox 声明为单值（one-to-one/many-to-one）
#   TEMPORAL     平台自建：valid_from/valid_until 区间重叠且属性值不一致
#   LOGICAL      SHACL 校验（M3-6 接 adapters/exporting，本期返回空）
# 纯函数可测；物化为 review_items(item_type='conflict_*') 由服务层（tasks/resolution）完成。

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.adapters.resolution import normalize_label

# 单值谓词判定的默认基数（object_properties[pred].cardinality）
_SINGLE_VALUE_CARDINALITY = {"one-to-one", "many-to-one"}


class ConflictType(str, Enum):
    VALUE = "value"
    TYPE = "type"
    RELATIONSHIP = "relationship"
    TEMPORAL = "temporal"    # 平台自建：valid_from/valid_until 区间重叠
    LOGICAL = "logical"      # 平台自建：SHACL 校验失败项转审核


class ResolutionStrategy(str, Enum):
    VOTING = "voting"
    CREDIBILITY_WEIGHTED = "credibility_weighted"  # 默认
    MOST_RECENT = "most_recent"
    FIRST_SEEN = "first_seen"
    HIGHEST_CONFIDENCE = "highest_confidence"
    MANUAL_REVIEW = "manual_review"                # critical 强制
    EXPERT_REVIEW = "expert_review"


class ConflictRecord(BaseModel):
    conflict_type: ConflictType
    severity: str  # low/medium/high/critical
    entity_uri: str
    property_name: str | None = None
    conflicting_values: list = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    recommended_action: ResolutionStrategy = ResolutionStrategy.CREDIBILITY_WEIGHTED
    guide: dict = Field(default_factory=dict)  # InvestigationGuideGenerator 输出


def _as_ent(e: Any) -> dict:
    if isinstance(e, dict):
        return {"id": e.get("id"), "uri": str(e.get("uri") or ""),
                "label": str(e.get("label") or ""), "class_label": str(e.get("class_label") or ""),
                "props": e.get("props") or {}, "confidence": e.get("confidence") or 0.0,
                "status": e.get("status") or "auto"}
    return {"id": getattr(e, "id", None), "uri": str(getattr(e, "uri", "") or ""),
            "label": str(getattr(e, "label", "") or ""),
            "class_label": str(getattr(e, "class_label", "") or ""),
            "props": getattr(e, "props", None) or {},
            "confidence": float(getattr(e, "confidence", None) or 0.0),
            "status": str(getattr(e, "status", "") or "auto")}


def _as_rel(r: Any) -> dict:
    if isinstance(r, dict):
        return {"subject_id": r.get("subject_id"), "predicate": str(r.get("predicate") or ""),
                "object_id": r.get("object_id"),
                "subject_label": str(r.get("subject_label") or ""),
                "object_label": str(r.get("object_label") or "")}
    return {"subject_id": getattr(r, "subject_id", None),
            "predicate": str(getattr(r, "predicate", "") or ""),
            "object_id": getattr(r, "object_id", None),
            "subject_label": str(getattr(r, "subject_label", "") or ""),
            "object_label": str(getattr(r, "object_label", "") or "")}


def _guide(steps: list[str]) -> dict:
    return {"steps": steps, "generated_by": "InvestigationGuideGenerator(m3-5)"}


def detect_value_conflicts(ents: list[dict]) -> list[ConflictRecord]:
    """同(label_norm, class)的多实体：同名属性值不一致 → VALUE 冲突。"""
    groups: dict[tuple, list[dict]] = {}
    for e in ents:
        groups.setdefault((normalize_label(e["label"]), normalize_label(e["class_label"])),
                          []).append(e)
    records: list[ConflictRecord] = []
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        # 属性键 → [(value, entity)]
        prop_values: dict[str, list[tuple[str, dict]]] = {}
        for m in members:
            for k, v in (m["props"] or {}).items():
                if v in (None, ""):
                    continue
                prop_values.setdefault(str(k), []).append((str(v), m))
        for prop, pairs in prop_values.items():
            distinct = {v for v, _ in pairs}
            if len(distinct) < 2:
                continue
            records.append(ConflictRecord(
                conflict_type=ConflictType.VALUE,
                severity="high",
                entity_uri=";".join(m["uri"] for m in members if m["uri"]),
                property_name=prop,
                conflicting_values=sorted(distinct),
                sources=[{"uri": m["uri"], "label": m["label"],
                          "confidence": m.get("confidence", 0.0)}
                         for _, m in pairs],
                recommended_action=ResolutionStrategy.CREDIBILITY_WEIGHTED,
                guide=_guide([
                    f"核对属性「{prop}」的 {len(distinct)} 个取值的原始出处（溯源记录）",
                    "按来源可信度加权取值；无法裁定则人工指定权威值",
                    "裁定后在画布修订实体属性，另一值移入别名/备注",
                ]),
            ))
    return records


def detect_type_conflicts(ents: list[dict]) -> list[ConflictRecord]:
    """同 label_norm 映射到不同 class_label → TYPE 冲突（含 SchemaGate 后残留）。"""
    groups: dict[str, list[dict]] = {}
    for e in ents:
        groups.setdefault(normalize_label(e["label"]), []).append(e)
    records: list[ConflictRecord] = []
    for _norm, members in groups.items():
        types = sorted({m["class_label"] for m in members if m["class_label"]})
        if len(types) < 2:
            continue
        records.append(ConflictRecord(
            conflict_type=ConflictType.TYPE,
            severity="medium",
            entity_uri=";".join(m["uri"] for m in members if m["uri"]),
            property_name="class_label",
            conflicting_values=types,
            sources=[{"uri": m["uri"], "label": m["label"], "class": m["class_label"]}
                     for m in members],
            recommended_action=ResolutionStrategy.MANUAL_REVIEW,
            guide=_guide([
                f"实体「{members[0]['label']}」被归入 {len(types)} 个类型：{'、'.join(types)}",
                "判断是否为 TBox 层级缺失（可建父类归一）或抽取错误",
                "裁定后修订画布类归属并重跑消解",
            ]),
        ))
    return records


def detect_relationship_conflicts(rels: list[dict], ents_by_id: dict[int, dict],
                                  object_properties: dict | None = None) -> list[ConflictRecord]:
    """同(subject,predicate)多客体且谓词声明为单值 → RELATIONSHIP 冲突。

    object_properties: {谓词: {"domain":…, "range":…, "cardinality":…}}（画布/TBox 提供）；
    未声明基数的谓词不判（避免多对多关系误报）。
    """
    object_properties = object_properties or {}
    groups: dict[tuple, list[dict]] = {}
    for r in rels:
        groups.setdefault((r["subject_id"], normalize_label(r["predicate"])), []).append(r)
    records: list[ConflictRecord] = []
    for (s_id, pred), members in groups.items():
        if not s_id or len(members) < 2:
            continue
        prop_def = object_properties.get(members[0]["predicate"]) or {}
        if prop_def.get("cardinality") not in _SINGLE_VALUE_CARDINALITY:
            continue
        distinct = sorted({r["object_label"] for r in members if r["object_label"]})
        if len(distinct) < 2:
            continue
        subject = ents_by_id.get(s_id, {})
        records.append(ConflictRecord(
            conflict_type=ConflictType.RELATIONSHIP,
            severity="medium",
            entity_uri=str(subject.get("uri") or s_id),
            property_name=members[0]["predicate"],
            conflicting_values=distinct,
            sources=[{"object": r["object_label"], "predicate": r["predicate"]}
                     for r in members],
            recommended_action=ResolutionStrategy.MANUAL_REVIEW,
            guide=_guide([
                f"「{subject.get('label', s_id)}」的单值关系「{members[0]['predicate']}」"
                f"指向 {len(distinct)} 个不同客体：{'、'.join(distinct)}",
                "核对各关系的证据原句，确认唯一客体或修订基数",
            ]),
        ))
    return records


def _parse_date(v: str):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m", "%Y年%m月", "%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def detect_temporal_conflicts(ents: list[dict]) -> list[ConflictRecord]:
    """TEMPORAL（自建，04 §6）：同(label_norm,class)实体带有效区间，区间重叠且属性值不一致。"""
    ents = [_as_ent(e) for e in ents]  # 直调时统一形状（confidence/props 缺省）
    out: list[ConflictRecord] = []
    groups: dict[tuple, list[dict]] = {}
    for e in ents:
        groups.setdefault((normalize_label(e["label"]), normalize_label(e["class_label"])),
                          []).append(e)
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        intervals = []
        for m in members:
            vf = _parse_date(str((m["props"] or {}).get("valid_from") or ""))
            vu = _parse_date(str((m["props"] or {}).get("valid_until") or ""))
            if vf:
                intervals.append((vf, vu or date.max, m))
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                a, b = intervals[i], intervals[j]
                overlap = a[0] <= b[1] and b[0] <= a[1]
                props_diff = any(
                    (a[2]["props"] or {}).get(k) not in (None, "")
                    and (b[2]["props"] or {}).get(k) not in (None, "")
                    and (a[2]["props"] or {}).get(k) != (b[2]["props"] or {}).get(k)
                    for k in set(a[2]["props"] or {}) & set(b[2]["props"] or {})
                    - {"valid_from", "valid_until"})
                if overlap and props_diff:
                    out.append(ConflictRecord(
                        conflict_type=ConflictType.TEMPORAL,
                        severity="high",
                        entity_uri=";".join(m["uri"] for m in members if m["uri"]),
                        property_name="valid_from/valid_until",
                        conflicting_values=[f"{m['label']}:[{m['props'].get('valid_from')}~"
                                            f"{m['props'].get('valid_until') or '至今'}]"
                                            for m in members],
                        sources=[{"uri": m["uri"], "props": m["props"]} for m in members],
                        recommended_action=ResolutionStrategy.MOST_RECENT,
                        guide=_guide([
                            "两个版本的有效期区间重叠且属性值不一致",
                            "确认新版本生效时间，将旧版本属性移入历史版本/别名",
                        ]),
                    ))
    return out


def detect_conflicts(entities: list, relations: list,
                     types: tuple[ConflictType, ...] = (
                         ConflictType.VALUE, ConflictType.TYPE, ConflictType.RELATIONSHIP),
                     source_credibility: dict[str, float] | None = None,
                     object_properties: dict | None = None) -> list[ConflictRecord]:
    """冲突检测主入口（04 §6：消解之后运行，消解减少假冲突）。

    entities/relations：行表行或 dict；object_properties 供 RELATIONSHIP 单值判定。
    critical 严重度强制 MANUAL_REVIEW（当前实现 VALUE/TEMPORAL=high，未到 critical）。
    """
    ents = [_as_ent(e) for e in (entities or [])]
    ents = [e for e in ents if e["label"] and e["status"] != "merged"]
    rels = [_as_rel(r) for r in (relations or [])]
    ents_by_id = {e["id"]: e for e in ents if e["id"] is not None}
    records: list[ConflictRecord] = []
    if ConflictType.VALUE in types:
        records.extend(detect_value_conflicts(ents))
    if ConflictType.TYPE in types:
        records.extend(detect_type_conflicts(ents))
    if ConflictType.RELATIONSHIP in types:
        records.extend(detect_relationship_conflicts(rels, ents_by_id, object_properties))
    if ConflictType.TEMPORAL in types:
        records.extend(detect_temporal_conflicts(ents))
    # critical 强制人工（当前无 critical 判定源；预留策略钩子）
    for rec in records:
        if rec.severity == "critical":
            rec.recommended_action = ResolutionStrategy.MANUAL_REVIEW
    return records


__all__ = ["ConflictType", "ResolutionStrategy", "ConflictRecord", "detect_conflicts",
           "detect_value_conflicts", "detect_type_conflicts", "detect_relationship_conflicts",
           "detect_temporal_conflicts"]
