# app/adapters/rule_llm.py - LLM 规则候选生成器（S9 追加：规则版双通道的 LLM 侧）
# 与公理通道（axiom_llm.py）同哲学：素材组装/prompt 构造/响应解析均为纯函数可测；
# llm 实例由服务层注入。产出与统计挖掘通道（reasoning_service.mine_rule_suggestions）
# 同型的候选 dict（name/if_subject_class/if_predicate/if_object_class/then_predicate/
# support/confidence/source/llm_reason），采纳走既有 POST /reasoning/rules 人工门禁。
# 存在性门禁在本层做：IF 侧（主语类/谓词/宾语类）必须逐字命中素材白名单，
# 引用素材外的名字视为幻觉直接弃单；THEN 侧（结论谓词）是推导出的新关系，允许不在素材中。

from __future__ import annotations

import json
import re
from typing import Any

from app.adapters.resolution import normalize_label

MAX_RULES = 10          # 单轮候选上限（宁缺毋滥，与公理高风险限额同量级）
MAX_THEN_PRED_CHARS = 32

RULE_OUTPUT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "if_subject_class": {"type": "string"},
                    "if_predicate": {"type": "string"},
                    "if_object_class": {"type": "string"},
                    "then_predicate": {"type": "string"},
                    "llm_reason": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["if_subject_class", "if_predicate", "if_object_class",
                             "then_predicate", "llm_reason", "examples"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "你是企业网银本体的推理规则审计专家。规则引擎的形状是："
    "IF 实例属于「主语类」且经「谓词」连接到「宾语类」的实例，THEN 可推断两者之间存在「结论谓词」关系。"
    "请根据给定的本体骨架与实例采样，找出值得声明但尚未声明的产生式规则——"
    "即业务上隐性成立、但从现有关系可以确定性推导的关系。"
    "规则会成为推理机的推导依据，错一条会污染推理结果，宁缺毋滥。\n\n"
    "硬性约束：\n"
    "- 主语类/谓词/宾语类必须从素材中【逐字】引用，禁止创造素材中不存在的名字（会被直接弃单）。\n"
    "- 结论谓词是推导出的【新】关系：用简洁的中文业务词（≤12 字），不得与谓词相同，"
    "且素材中不存在该谓词与该类组合的显式关系时才有价值。\n"
    "- 只推荐业务上稳定成立的规则（几乎每个满足 IF 的实例都应满足 THEN），不要基于个别样本臆测。\n"
    "- 不要推荐与素材关系同义重复的规则（如 持股⇒持股）。\n"
    "- 单轮总计不超过 " + str(MAX_RULES) + " 条；每条必须给 llm_reason（一段话业务依据）"
    "和 examples（引用素材中的具体关系样本，可为空数组但键必须在）。\n"
    "- examples 元素形如 {\"subject\":\"实例名\",\"object\":\"实例名\",\"note\":\"一句话说明\"}。\n\n"
    "【输出格式（最高优先级）】你的全部回复必须且只能是一个 JSON 对象："
    "{\"rules\":[{\"if_subject_class\":\"...\",\"if_predicate\":\"...\","
    "\"if_object_class\":\"...\",\"then_predicate\":\"...\",\"llm_reason\":\"...\",\"examples\":[]}]}。"
    "以 { 开头、以 } 结尾；禁止任何分析文字、解释、markdown 代码围栏或其他内容。"
)


def build_rule_prompt(materials: dict) -> tuple[str, str]:
    """素材 → (system_prompt, user_prompt)。素材 JSON 全量放 user_prompt（紧凑无 indent）。"""
    user_prompt = (
        "【本体骨架与实例素材】\n"
        + json.dumps(materials, ensure_ascii=False)
        + "\n\n请按系统提示中的规则形状与硬性约束，输出 JSON：{\"rules\":[...]}。"
    )
    return _SYSTEM_PROMPT, user_prompt


def parse_rule_suggestions(content: Any, schema: dict | None,
                           relation_predicates: list[str] | None = None,
                           existing_keys: set[tuple] | None = None) -> tuple[list[dict], list[str]]:
    """LLM 响应 → (候选规则列表, 弃单原因列表)。

    IF 侧（主语类/谓词/宾语类）存在性门禁：归一化后必须命中画布 schema 类 /
    object_properties / 实例关系谓词白名单；THEN 侧为推导新关系，仅查非空、长度、
    与 IF 谓词不同；与既有规则（existing_keys）或本轮重复的弃单。
    """
    schema = schema or {}
    classes_by_norm = {normalize_label(str(c.get("label"))): str(c.get("label"))
                       for c in (schema.get("classes") or [])
                       if isinstance(c, dict) and c.get("label")}
    preds_by_norm = {normalize_label(str(op.get("label"))): str(op.get("label"))
                     for op in (schema.get("object_properties") or [])
                     if isinstance(op, dict) and op.get("label")}
    for p in (relation_predicates or []):
        preds_by_norm.setdefault(normalize_label(str(p)), str(p))

    data = _coerce_json(content)
    if not isinstance(data, dict):
        return [], [f"响应不是 JSON 对象: {type(data).__name__}"]
    raw_items = data.get("rules")
    if not isinstance(raw_items, list):
        return [], ["缺少 rules 数组"]

    candidates: list[dict] = []
    rejected: list[str] = []
    seen: set[tuple] = set()
    for i, raw in enumerate(raw_items[:MAX_RULES * 3]):  # 超量部分直接不看
        if not isinstance(raw, dict):
            rejected.append(f"#{i} 非对象")
            continue
        reason = str(raw.get("llm_reason") or "").strip()
        if not reason:
            rejected.append(f"#{i} 缺 llm_reason")
            continue

        s_cls = classes_by_norm.get(normalize_label(str(raw.get("if_subject_class") or "")))
        if s_cls is None:
            rejected.append(f"#{i} 主语类不存在: {raw.get('if_subject_class')}（疑似幻觉）")
            continue
        o_cls = classes_by_norm.get(normalize_label(str(raw.get("if_object_class") or "")))
        if o_cls is None:
            rejected.append(f"#{i} 宾语类不存在: {raw.get('if_object_class')}（疑似幻觉）")
            continue
        pred = preds_by_norm.get(normalize_label(str(raw.get("if_predicate") or "")))
        if pred is None:
            rejected.append(f"#{i} 谓词不存在: {raw.get('if_predicate')}（疑似幻觉）")
            continue
        then_p = str(raw.get("then_predicate") or "").strip()
        if not then_p:
            rejected.append(f"#{i} 缺结论谓词")
            continue
        if len(then_p) > MAX_THEN_PRED_CHARS:
            rejected.append(f"#{i} 结论谓词过长: {then_p}")
            continue
        if normalize_label(then_p) == normalize_label(pred):
            rejected.append(f"#{i} 结论谓词与谓词相同: {then_p}")
            continue
        key = (normalize_label(s_cls), normalize_label(pred),
               normalize_label(o_cls), normalize_label(then_p))
        if key in seen or (existing_keys and key in existing_keys):
            rejected.append(f"#{i} 规则重复")
            continue
        seen.add(key)
        examples = raw.get("examples") if isinstance(raw.get("examples"), list) else []
        candidates.append({
            "name": f"{pred}蕴含{then_p}",
            "if_subject_class": s_cls,
            "if_predicate": pred,
            "if_object_class": o_cls,
            "then_predicate": then_p,
            "support": None,       # LLM 通道无统计支撑数
            "confidence": None,    # 无统计置信度，置信依据在 llm_reason
            "source": "llm",
            "llm_reason": reason,
            "examples": examples[:5],
        })
    return candidates[:MAX_RULES], rejected


def _coerce_json(content: Any) -> Any:
    """content 兼容 str/dict；str 剥代码围栏后 json.loads，失败再宽松提取平衡对象
    （与 axiom_llm._coerce_json 同策略：优先取 "rules" 键所在对象）。"""
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

    key_at = text.rfind('"rules"')
    if key_at > 0:
        obj = _balanced_from(text.rfind("{", 0, key_at))
        if isinstance(obj, dict) and isinstance(obj.get("rules"), list):
            return obj
    start = text.find("{")
    if start < 0:
        return None
    return _balanced_from(start)


__all__ = [
    "MAX_RULES", "RULE_OUTPUT_JSON_SCHEMA", "build_rule_prompt",
    "parse_rule_suggestions",
]
