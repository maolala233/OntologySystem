# app/adapters/conflicts.py - 冲突检测与解决（docs/design/04 §6，M3-5）
# 平台实现（Semantica ConflictDetector 语义，行表数据面）：
#   VALUE        同(label_norm,class)多实体同属性不同值（消解后残留=真冲突）
#   TYPE         同(label_norm)多类型
#   RELATIONSHIP 同(subject,predicate)多客体，且谓词在 TBox 声明为单值（one-to-one/many-to-one）
#   TEMPORAL     平台自建：valid_from/valid_until 区间重叠且属性值不一致
#   LOGICAL      SHACL 校验（M3-6 接 adapters/exporting，本期返回空）
#   AXIOM        平台自建（公理 2 期）：disjointWith 违例 / 函数性多值 / 基数超限不足
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
    AXIOM = "axiom"          # 平台自建：TBox 公理违例（互斥/函数性/基数）


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


def _class_closure(class_label: str, classes_by_label: dict, classes_by_id: dict) -> set[str]:
    """实体类型的祖先闭包（parent_classes 混存 id/label，两者都能解析），含自身。

    闭包统一收敛为 class label：id 解析到类后追加其 label，供与
    disjoint_with / cardinality_restrictions 里存 label 的公理比对。
    """
    seen: set[str] = set()
    stack = [class_label]
    while stack:
        cur = stack.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        info = classes_by_label.get(cur) or classes_by_id.get(cur)
        if info:
            if info.get("label"):
                stack.append(info["label"])
            stack.extend(info.get("parent_classes") or [])
    return seen


def detect_axiom_conflicts(ents: list[dict], rels: list[dict], ents_by_id: dict,
                           schema: dict | None = None,
                           entity_index: dict | None = None) -> list[ConflictRecord]:
    """AXIOM（公理 2 期）：画布/TBox 声明的公理在 ABox 实例上的违例检测。

    schema：项目 graph_data.schema，需含
      - classes[].{label, id, parent_classes, disjoint_with}
      - object_properties[].{label, functional, cardinality_restrictions:[{class,min,max}]}
    entity_index：{实体id: {uri,label,class_label}} 全量索引（含类节点/已合并），
      供关系主体解析；缺省回退 ents_by_id（仅活跃实体）。
    检查三类：
      1. disjointWith：实体类型闭包同时命中互斥类对 → 违例
      2. functional：函数性谓词同主体指向 ≥2 个不同客体 → 违例
      3. 基数：谓词在主体所属类上声明 min/max，实例客体数越界 → 超限/不足
    """
    schema = schema or {}
    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    classes_by_label = {c.get("label"): c for c in classes if c.get("label")}
    classes_by_id = {c.get("id"): c for c in classes if c.get("id")}
    obj_props = {op.get("label"): op for op in (schema.get("object_properties") or [])
                 if isinstance(op, dict) and op.get("label")}
    records: list[ConflictRecord] = []

    # ── 1) disjointWith ──
    disjoint_pairs = set()
    for c in classes:
        for peer in (c.get("disjoint_with") or []):
            if peer and peer != c.get("label"):
                disjoint_pairs.add(frozenset((c["label"], peer)))
    if disjoint_pairs:
        for e in ents:
            if not e["class_label"]:
                continue
            closure = _class_closure(e["class_label"], classes_by_label, classes_by_id)
            for pair in disjoint_pairs:
                if pair <= closure:
                    a, b = sorted(pair)
                    records.append(ConflictRecord(
                        conflict_type=ConflictType.AXIOM,
                        severity="high",
                        entity_uri=e["uri"],
                        property_name="owl:disjointWith",
                        conflicting_values=sorted(pair),
                        sources=[{"uri": e["uri"], "label": e["label"],
                                  "class": e["class_label"]}],
                        recommended_action=ResolutionStrategy.MANUAL_REVIEW,
                        guide=_guide([
                            f"实体「{e['label']}」的类型「{e['class_label']}」同时落在互斥类"
                            f"「{a}」与「{b}」的层级内，违反 disjointWith 公理",
                            "确认实体真实归属并修正类标注；若互斥声明有误，在画布节点公理中调整",
                        ]),
                    ))

    # ── 2) 函数性 / 3) 基数：按 (subject, predicate) 聚合关系 ──
    func_preds = {p for p, op in obj_props.items() if op.get("functional")}
    card_preds: dict[str, list[dict]] = {}
    for p, op in obj_props.items():
        restrs = [r for r in (op.get("cardinality_restrictions") or [])
                  if isinstance(r, dict) and (r.get("min") is not None or r.get("max") is not None)]
        if restrs:
            card_preds[p] = restrs
    if not func_preds and not card_preds:
        return records

    rel_groups: dict[tuple, list[dict]] = {}
    for r in rels:
        if r["subject_id"] is not None:
            rel_groups.setdefault((r["subject_id"], normalize_label(r["predicate"])),
                                  []).append(r)

    for (s_id, _pred_norm), members in rel_groups.items():
        pred = members[0]["predicate"]
        prop_def = obj_props.get(pred) or {}
        subject = (entity_index or {}).get(s_id) or ents_by_id.get(s_id, {})
        subj_label = subject.get("label") or members[0].get("subject_label") or str(s_id)
        distinct_objs = sorted({r["object_label"] for r in members if r["object_label"]})
        if not distinct_objs:
            continue

        # 函数性：同主体多客体
        if pred in func_preds and len(distinct_objs) >= 2:
            records.append(ConflictRecord(
                conflict_type=ConflictType.AXIOM,
                severity="high",
                entity_uri=str(subject.get("uri") or s_id),
                property_name=pred,
                conflicting_values=distinct_objs,
                sources=[{"object": o, "predicate": pred} for o in distinct_objs],
                recommended_action=ResolutionStrategy.MANUAL_REVIEW,
                guide=_guide([
                    f"关系「{pred}」声明为函数性（每个主体至多一个客体），"
                    f"但「{subj_label}」指向 {len(distinct_objs)} 个客体：{'、'.join(distinct_objs)}",
                    "核对各关系证据原句，保留正确客体；若关系本就多值，请在画布边公理中取消函数性",
                ]),
            ))

        # 基数：主体类型闭包命中声明类
        restrs = card_preds.get(pred)
        if not restrs:
            continue
        closure = (_class_closure(subject.get("class_label") or "", classes_by_label,
                                  classes_by_id)
                   if subject else set())
        applicable = next((r for r in restrs if r.get("class") in closure), None)
        if applicable is None:
            continue
        count = len(distinct_objs)
        rmin, rmax = applicable.get("min"), applicable.get("max")
        if rmax is not None and count > int(rmax):
            records.append(ConflictRecord(
                conflict_type=ConflictType.AXIOM,
                severity="medium",
                entity_uri=str(subject.get("uri") or s_id),
                property_name=pred,
                conflicting_values=distinct_objs,
                sources=[{"object": o, "predicate": pred} for o in distinct_objs],
                recommended_action=ResolutionStrategy.MANUAL_REVIEW,
                guide=_guide([
                    f"「{subj_label}」的关系「{pred}」有 {count} 个客体，"
                    f"超过类「{applicable['class']}」上声明的最大基数 {int(rmax)}",
                    "确认多余客体的证据来源，删除错误关系或将边基数上限调大",
                ]),
            ))
        elif rmin is not None and count < int(rmin):
            records.append(ConflictRecord(
                conflict_type=ConflictType.AXIOM,
                severity="low",
                entity_uri=str(subject.get("uri") or s_id),
                property_name=pred,
                conflicting_values=distinct_objs,
                sources=[{"object": o, "predicate": pred} for o in distinct_objs],
                recommended_action=ResolutionStrategy.MANUAL_REVIEW,
                guide=_guide([
                    f"「{subj_label}」的关系「{pred}」只有 {count} 个客体，"
                    f"少于类「{applicable['class']}」上声明的最小基数 {int(rmin)}",
                    "可能为抽取遗漏：核对原文补齐关系，或确认后调低最小基数",
                ]),
            ))
    return records


def detect_conflicts(entities: list, relations: list,
                     types: tuple[ConflictType, ...] = (
                         ConflictType.VALUE, ConflictType.TYPE, ConflictType.RELATIONSHIP),
                     source_credibility: dict[str, float] | None = None,
                     object_properties: dict | None = None,
                     schema: dict | None = None,
                     entity_index: dict | None = None) -> list[ConflictRecord]:
    """冲突检测主入口（04 §6：消解之后运行，消解减少假冲突）。

    entities/relations：行表行或 dict；object_properties 供 RELATIONSHIP 单值判定；
    schema（graph_data.schema）供 AXIOM 公理校验。
    critical 严重度强制 MANUAL_REVIEW。
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
    if ConflictType.AXIOM in types:
        records.extend(detect_axiom_conflicts(ents, rels, ents_by_id, schema,
                                              entity_index=entity_index))
    # critical 强制人工（当前无 critical 判定源；预留策略钩子）
    for rec in records:
        if rec.severity == "critical":
            rec.recommended_action = ResolutionStrategy.MANUAL_REVIEW
    return records


__all__ = ["ConflictType", "ResolutionStrategy", "ConflictRecord", "detect_conflicts",
           "detect_value_conflicts", "detect_type_conflicts", "detect_relationship_conflicts",
           "detect_temporal_conflicts", "detect_axiom_conflicts"]
