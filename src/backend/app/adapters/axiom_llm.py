# app/adapters/axiom_llm.py - LLM 公理候选生成器（O4-B18）
# 素材组装 / prompt 构造 / 响应解析均为纯函数可测；llm 实例由服务层注入
# （call_llm 支持 json_schema=...，方舟模型不支持时 llm_client 自动降级 json_object）。
# 产出统一 evidence 结构（kind/targets/evidence/risk_tier/source/summary），
# 与统计通道（axiom_suggestions.py）同型——审批/去重/前端零分叉。
# 存在性校验（targets 必须命中画布已有 label）在本层做：LLM 被要求逐字引用素材，
# 引用了素材外的名字即视为畸形响应直接弃单；B19 闸门在此之上叠加冲突/影子/owlrl。

from __future__ import annotations

import json
import re
from typing import Any

from app.adapters.resolution import normalize_label

# 8 种公理类型 → 风险分级 / 目标字段（docs/design/13 §3.3）
KIND_META: dict[str, dict] = {
    "functional":         {"risk": "low",  "target": "predicate"},   # + predicate
    "inverse_of":         {"risk": "low",  "target": "predicates"},  # + [p, q]
    "disjoint_with":      {"risk": "low",  "target": "classes"},     # + [a, b]
    "domain_fix":         {"risk": "low",  "target": "predicate"},   # + value（新 domain）
    "range_fix":          {"risk": "low",  "target": "predicate"},   # + value（新 range）
    "subclass_of":        {"risk": "high", "target": "classes"},     # + [子类, 父类]
    "equivalent_classes": {"risk": "high", "target": "classes"},
    "cardinality":        {"risk": "high", "target": "predicate"},   # + min_card/max_card
}

LOW_RISK_LIMIT = 20    # 每轮低风险候选上限/kind（对齐统计通道 MAX_SUGGESTIONS_PER_KIND）
HIGH_RISK_LIMIT = 10   # 高风险减半（防低质候选刷爆审核队列）

SAMPLES_PER_CLASS = 4    # 每类实例采样数
RELATION_SAMPLES = 6     # 每关系样本对数
MAX_CLASSES_IN_MATERIAL = 150
MAX_PROPS_IN_MATERIAL = 150
MAX_MATERIAL_CHARS = 12000  # 素材 JSON 字符预算，超长逐级降采样
# prompt 用紧凑序列化（无 indent）+ 空值省略：早年 indent=1 把 24k 预算膨胀成
# 48k prompt，glm flash 300s 跑不完（O4 走查实测超时）；12k 预算可容纳约
# 197 全量类 + ~200 关系属性标签（723 实测 618 属性），prompt ≈13k 实测 3 分钟内完成。

_KINDS = list(KIND_META)


# ── 素材组装（B18.1）──

def build_materials(schema: dict | None, ents: list[dict],
                    rels: list[dict]) -> dict:
    """画布 schema + 实例采样摘要 → LLM 素材。已声明公理显式随行防重复荐。"""
    schema = schema or {}
    # 空值省略：空 parent_classes/disjoint_with/functional:false/inverse_of:""
    # 纯占体积（O4 走查实测：省略后全量 197 类/120 关系标签装得进预算）
    classes = [{"label": str(c.get("label") or ""),
                **({"parent_classes": list(c.get("parent_classes") or [])}
                   if c.get("parent_classes") else {}),
                **({"disjoint_with": list(c.get("disjoint_with") or [])}
                   if c.get("disjoint_with") else {})}
               for c in (schema.get("classes") or [])
               if isinstance(c, dict) and c.get("label")]
    object_properties = []
    for op in (schema.get("object_properties") or []):
        if not (isinstance(op, dict) and op.get("label")):
            continue
        entry = {"label": str(op.get("label") or "")}
        if op.get("domain"):
            entry["domain"] = str(op.get("domain"))
        if op.get("range"):
            entry["range"] = str(op.get("range"))
        if op.get("functional"):
            entry["functional"] = True
        if op.get("inverse_of"):
            entry["inverse_of"] = str(op.get("inverse_of"))
        object_properties.append(entry)

    # 实例按类采样（merged 已在服务层过滤）
    inst_by_class: dict[str, list[str]] = {}
    seen_labels: set[str] = set()
    for e in ents:
        cls = str(e.get("class_label") or "").strip()
        lbl = str(e.get("label") or "").strip()
        if not cls or not lbl or lbl in seen_labels:
            continue
        seen_labels.add(lbl)
        bucket = inst_by_class.setdefault(cls, [])
        if len(bucket) < SAMPLES_PER_CLASS:
            bucket.append(lbl)
    class_instances = [{"class": cls, "count": count,
                        "samples": inst_by_class.get(cls, [])}
                       for cls, count in _class_counts(ents).items()]

    # 关系按谓词采样
    pairs_by_pred: dict[str, list[dict]] = {}
    pred_counts: dict[str, int] = {}
    for r in rels:
        p = str(r.get("predicate") or "").strip()
        s = str(r.get("subject_label") or "").strip()
        o = str(r.get("object_label") or "").strip()
        if not p or not s or not o:
            continue
        pred_counts[p] = pred_counts.get(p, 0) + 1
        bucket = pairs_by_pred.setdefault(p, [])
        if len(bucket) < RELATION_SAMPLES:
            bucket.append({"subject": s, "object": o})
    relation_samples = [{"predicate": p, "count": pred_counts[p],
                         "samples": pairs_by_pred[p]} for p in sorted(pred_counts)]

    materials = {
        "classes": classes[:MAX_CLASSES_IN_MATERIAL],
        "object_properties": object_properties[:MAX_PROPS_IN_MATERIAL],
        "class_instances": class_instances,
        "relation_samples": relation_samples,
    }
    return _trim_to_budget(materials)


def _class_counts(ents: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in ents:
        cls = str(e.get("class_label") or "").strip()
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def _trim_to_budget(materials: dict) -> dict:
    """超字符预算时逐级降采样。类/关系标签（存在性校验的白名单）最后才裁：
    样本减半 → 样本清空 → 条目减半 → 类/属性清单减半。"""
    def _size(m: dict) -> int:
        # 与 build_prompt 同口径（紧凑无 indent），否则预算失真
        return len(json.dumps(m, ensure_ascii=False))

    def _halve_samples(key: str) -> bool:
        trimmed = False
        for entry in materials.get(key, []):
            if len(entry.get("samples") or []) > 1:  # 长度 1 时砍不动，返回 False 防死循环
                entry["samples"] = entry["samples"][:max(1, len(entry["samples"]) // 2)]
                trimmed = True
        return trimmed

    def _drop_samples(key: str) -> bool:
        trimmed = False
        for entry in materials.get(key, []):
            if entry.get("samples"):
                entry["samples"] = []
                trimmed = True
        return trimmed

    def _halve_entries(key: str) -> bool:
        rows = materials.get(key) or []
        if len(rows) > 1:
            materials[key] = rows[:max(1, len(rows) // 2)]
            return True
        return False

    def _compact_entries(key: str) -> bool:
        # 只留标签键（class/predicate），丢 count 等修饰——标签是存在性校验
        # 的白名单，条目数不减、信息密度翻倍（O4 走查实测：197 类/120 关系
        # 全标签紧凑后 ≈4k 字符，无需再砍条目）
        trimmed = False
        for entry in materials.get(key, []):
            for k in list(entry):
                if k not in ("class", "predicate", "samples"):
                    entry.pop(k)
                    trimmed = True
        return trimmed

    stages = [
        lambda: _halve_samples("relation_samples"),
        lambda: _halve_samples("class_instances"),
        lambda: _drop_samples("relation_samples"),
        lambda: _drop_samples("class_instances"),
        lambda: _compact_entries("relation_samples"),
        lambda: _compact_entries("class_instances"),
        lambda: _halve_entries("class_instances"),
        lambda: _halve_entries("relation_samples"),
    ]

    while _size(materials) > MAX_MATERIAL_CHARS:
        for stage in stages:
            if stage():
                break
        else:
            materials["classes"] = materials["classes"][:max(1, len(materials["classes"]) // 2)]
            materials["object_properties"] = \
                materials["object_properties"][:max(1, len(materials["object_properties"]) // 2)]
            if len(materials["classes"]) <= 1 and len(materials["object_properties"]) <= 1:
                break
    return materials


# ── prompt 构造（B18.2）──

AXIOM_OUTPUT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": _KINDS},
                    "predicate": {"type": "string"},
                    "predicates": {"type": "array", "items": {"type": "string"}},
                    "classes": {"type": "array", "items": {"type": "string"}},
                    "value": {"type": "string"},
                    "min_card": {"type": "integer"},
                    "max_card": {"type": "integer"},
                    "llm_reason": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["kind", "predicate", "predicates", "classes",
                             "value", "min_card", "max_card", "llm_reason", "examples"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

_KIND_RULES = """八种公理类型与判定标准：
1. functional：某关系在所有主体上均为单客体 → {"kind":"functional","predicate":"关系名"}
2. inverse_of：两个关系方向相反且成对出现 → {"kind":"inverse_of","predicates":["关系A","关系B"]}
3. disjoint_with：同父兄弟类在业务上不可能有共同实例 → {"kind":"disjoint_with","classes":["类A","类B"]}
4. domain_fix：某关系的 domain 声明与实例矛盾（主体实为别的类）→ {"kind":"domain_fix","predicate":"关系名","value":"正确的类名"}
5. range_fix：某关系的 range 声明与实例矛盾（客体实为别的类）→ {"kind":"range_fix","predicate":"关系名","value":"正确的类名"}
6. subclass_of：类 A 明显是类 B 的下位概念但未声明继承 → {"kind":"subclass_of","classes":["子类A","父类B"]}
7. equivalent_classes：两个类名指同一业务概念（非 merely 相似）→ {"kind":"equivalent_classes","classes":["类A","类B"]}
8. cardinality：某关系有明确业务基数约束 → {"kind":"cardinality","predicate":"关系名","min_card":N,"max_card":N}（min/max 至少给一个）"""

_PROMPT_CONSTRAINTS = """硬性约束：
- 类名/关系名必须从素材中【逐字】引用，禁止创造素材中不存在的名字（会被直接弃单）。
- 素材中已声明的公理（parent_classes/disjoint_with/functional/inverse_of/domain/range）不要重复推荐。
- 只推荐高置信度候选，宁缺毋滥；单轮总计不超过 30 条。
- 每条必须给 llm_reason（一段话说明业务依据）和 examples（引用素材中的具体实例/关系样本，可为空数组但键必须在）。
- examples 元素形如 {"entity":"实例名","note":"一句话说明"}。

【输出格式（最高优先级）】你的全部回复必须且只能是一个 JSON 对象：{"suggestions":[...]}。
以 { 开头、以 } 结尾；禁止任何分析文字、解释、markdown 代码围栏或其他内容。"""

_SYSTEM_PROMPT = (
    "你是企业网银本体（OWL）的公理审计专家。根据给定的本体骨架与实例采样，"
    "找出值得声明但尚未声明的公理（约束性事实）。公理是强约束，错一条会污染整个推理，"
    "因此宁缺毋滥。\n\n" + _KIND_RULES + "\n\n" + _PROMPT_CONSTRAINTS
    + "\n\n只输出 JSON 对象：{\"suggestions\":[...]}，不要输出其他文字。"
)


def build_prompt(materials: dict) -> tuple[str, str]:
    """素材 → (system_prompt, user_prompt)。素材 JSON 全量放 user_prompt。"""
    user_prompt = (
        "【本体骨架与实例素材】\n"
        + json.dumps(materials, ensure_ascii=False)  # 紧凑：预算按此口径计量，勿加 indent
        + "\n\n请按系统提示中的八种类型与硬性约束，输出 JSON：{\"suggestions\":[...]}。"
    )
    return _SYSTEM_PROMPT, user_prompt


# ── 响应解析（B18.2）──

def parse_axiom_suggestions(content: Any, schema: dict | None) -> tuple[list[dict], list[str]]:
    """LLM 响应 → (统一结构候选列表, 弃单原因列表)。

    结构非法 / 缺 llm_reason / 引用素材外名字（幻觉）/ 已声明 / 超限额 一律弃单；
    targets 解析为画布原始 label（大小写与画布一致，审批按 label 直查）。
    """
    schema = schema or {}
    classes_by_norm, props_by_norm = _label_indexes(schema)

    data = _coerce_json(content)
    if not isinstance(data, dict):
        return [], [f"响应不是 JSON 对象: {type(data).__name__}"]
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list):
        return [], ["缺少 suggestions 数组"]

    candidates: list[dict] = []
    rejected: list[str] = []
    seen_keys: set[tuple] = set()
    limits = {k: (HIGH_RISK_LIMIT if meta["risk"] == "high" else LOW_RISK_LIMIT)
              for k, meta in KIND_META.items()}
    counts: dict[str, int] = {}

    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            rejected.append(f"#{i} 非对象")
            continue
        kind = str(raw.get("kind") or "").strip()
        meta = KIND_META.get(kind)
        if meta is None:
            rejected.append(f"#{i} 未知 kind: {kind}")
            continue
        reason = str(raw.get("llm_reason") or "").strip()
        if not reason:
            rejected.append(f"#{i} {kind} 缺 llm_reason")
            continue

        parsed = _resolve_targets(raw, kind, meta["target"], classes_by_norm, props_by_norm)
        if isinstance(parsed, str):
            rejected.append(f"#{i} {kind}: {parsed}")
            continue

        # subclass_of 的 [子类, 父类] 顺序即语义，key 保序；其余类对/谓词对无序化
        ordered = (kind == "subclass_of")
        norm_key = (kind, tuple(normalize_label(t) for t in parsed["_targets"])
                    if ordered else
                    tuple(sorted(normalize_label(t) for t in parsed["_targets"])))
        if norm_key in seen_keys:
            rejected.append(f"#{i} {kind} 重复候选")
            continue
        if counts.get(kind, 0) >= limits[kind]:
            rejected.append(f"#{i} {kind} 超限额 {limits[kind]}")
            continue

        seen_keys.add(norm_key)
        counts[kind] = counts.get(kind, 0) + 1
        candidates.append(_to_candidate(raw, kind, parsed, reason))
    return candidates, rejected


def _label_indexes(schema: dict) -> tuple[dict, dict]:
    classes_by_norm: dict[str, str] = {}
    for c in (schema.get("classes") or []):
        if isinstance(c, dict) and c.get("label"):
            classes_by_norm[normalize_label(str(c["label"]))] = str(c["label"])
    props_by_norm: dict[str, str] = {}
    for op in (schema.get("object_properties") or []):
        if isinstance(op, dict) and op.get("label"):
            props_by_norm[normalize_label(str(op["label"]))] = str(op["label"])
    return classes_by_norm, props_by_norm


def _resolve_targets(raw: dict, kind: str, target_key: str,
                     classes_by_norm: dict, props_by_norm: dict) -> dict | str:
    """按 kind 校验目标并解析为画布原始 label；不合法返回原因字符串。"""
    def _resolve_one(name: str, table: dict, what: str) -> tuple[str | None, str | None]:
        canon = table.get(normalize_label(str(name or "")))
        if canon is None:
            return None, f"引用了不存在的{what}: {name}（疑似幻觉）"
        return canon, None

    def _fail(result: tuple) -> bool:
        return result[0] is None

    if target_key == "predicate":
        pred, err = _resolve_one(raw.get("predicate"), props_by_norm, "关系名")
        if err:
            return err
        out = {"predicate": pred, "_targets": [pred]}
        if kind in ("domain_fix", "range_fix"):
            value, err = _resolve_one(raw.get("value"), classes_by_norm, "类名")
            if err:
                return err
            if kind == "domain_fix" and raw.get("domain") == value:
                return "domain 已是该值"
            if kind == "range_fix" and raw.get("range") == value:
                return "range 已是该值"
            out["value"] = value
        if kind == "cardinality":
            mn, mx = raw.get("min_card"), raw.get("max_card")
            mn = int(mn) if isinstance(mn, (int, float)) and mn >= 0 else None
            mx = int(mx) if isinstance(mx, (int, float)) and mx >= 0 else None
            if mn is None and mx is None:
                return "cardinality 缺 min_card/max_card"
            if mn is not None and mx is not None and mn > mx:
                return f"min_card({mn}) > max_card({mx})"
            out["min_card"], out["max_card"] = mn, mx
        return out

    pair_raw = raw.get(target_key) or []
    if not isinstance(pair_raw, list) or len(pair_raw) != 2:
        return f"{target_key} 必须是两个元素的数组"
    table = props_by_norm if target_key == "predicates" else classes_by_norm
    what = "关系名" if target_key == "predicates" else "类名"
    a, err = _resolve_one(pair_raw[0], table, what)
    if err:
        return err
    b, err = _resolve_one(pair_raw[1], table, what)
    if err:
        return err
    if normalize_label(a) == normalize_label(b):
        return f"{target_key} 两元素相同: {a}"
    return {target_key: [a, b], "_targets": [a, b]}


def _to_candidate(raw: dict, kind: str, parsed: dict, reason: str) -> dict:
    meta = KIND_META[kind]
    evidence = {"llm_reason": reason,
                "examples": raw.get("examples") if isinstance(raw.get("examples"), list) else []}
    cand: dict[str, Any] = {"kind": kind,
                            "evidence": evidence,
                            "risk_tier": meta["risk"],
                            "source": "llm"}
    for key in ("predicate", "predicates", "classes", "value", "min_card", "max_card"):
        if key in parsed and parsed[key] is not None:
            cand[key] = parsed[key]
    if kind == "subclass_of":
        cand["classes"] = parsed["classes"]  # [子类, 父类] 有序，去重时按序比对
    cand["summary"] = _summary(kind, cand)
    return cand


def _summary(kind: str, cand: dict) -> str:
    if kind == "functional":
        return f"AI 建议：关系「{cand['predicate']}」声明为函数性（每个主体至多一个客体）"
    if kind == "inverse_of":
        p, q = cand["predicates"]
        return f"AI 建议：关系「{p}」与「{q}」声明为互逆"
    if kind == "disjoint_with":
        a, b = cand["classes"]
        return f"AI 建议：类「{a}」与「{b}」声明互斥（disjointWith）"
    if kind == "domain_fix":
        return f"AI 建议：关系「{cand['predicate']}」的定义域修正为「{cand['value']}」"
    if kind == "range_fix":
        return f"AI 建议：关系「{cand['predicate']}」的值域修正为「{cand['value']}」"
    if kind == "subclass_of":
        child, parent = cand["classes"]
        return f"AI 建议：类「{child}」声明为「{parent}」的子类"
    if kind == "equivalent_classes":
        a, b = cand["classes"]
        return f"AI 建议：类「{a}」与「{b}」声明等价（同一概念）"
    if kind == "cardinality":
        parts = []
        if cand.get("min_card") is not None:
            parts.append(f"至少 {cand['min_card']}")
        if cand.get("max_card") is not None:
            parts.append(f"至多 {cand['max_card']}")
        return f"AI 建议：关系「{cand['predicate']}」声明基数约束（{'、'.join(parts)}）"
    return "AI 公理建议"


def _coerce_json(content: Any) -> Any:
    """content 兼容 str/dict；str 剥代码围栏后 json.loads，失败再宽松提取平衡对象。

    模型在 json_object 降级模式下可能先输出大段分析文字再给 JSON（O4 走查实测），
    所以定位策略优先取「"suggestions" 键所在的对象」，其次才是首个平衡对象。
    """
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass

    def _balanced_from(start: int) -> Any:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:  # noqa: BLE001
                        return None
        return None

    # 首选：包含 "suggestions" 键的对象（从该键往左找最近的 '{'）
    key_at = text.rfind('"suggestions"')
    if key_at > 0:
        obj = _balanced_from(text.rfind("{", 0, key_at))
        if isinstance(obj, dict) and isinstance(obj.get("suggestions"), list):
            return obj
    start = text.find("{")
    if start < 0:
        return None
    return _balanced_from(start)


__all__ = [
    "KIND_META", "LOW_RISK_LIMIT", "HIGH_RISK_LIMIT",
    "AXIOM_OUTPUT_JSON_SCHEMA", "build_materials", "build_prompt",
    "parse_axiom_suggestions",
]
