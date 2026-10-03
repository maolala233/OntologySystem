# app/adapters/extraction.py - LLM 抽取（docs/design/04 §3/§4）
# M3-4 实装 Schema 阶段（extract_schema）。中文路线：NER/RE 全走 LLM 方法（pattern/spaCy
# 对中文无效，见 _compat.CHINESE_PATCHES）。
# 修复缺陷：#2 结构化字段化 prompt（不塞原始 dict，已发现类清单字段化增量传入）；
#           #5 分块并行 ThreadPoolExecutor parallelism=4；
#           #9 Redis (model, prompt_hash) 8h 缓存，重跑不重复计费。
# LLM 调用经 adapters/provider（ModelPurpose.EXTRACT）+ infrastructure/llm_client，
# 业务侧可注入 llm_call 以便测试与复用。

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.adapters.resolution import normalize_label

# prompt-hash 缓存 TTL（04 §3.1）
LLM_CACHE_TTL = 8 * 3600

# ── 结构化输出约束（与 SchemaExtractionResult 对齐；字段化 prompt 的产物契约）──
SCHEMA_PROMPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "classes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "类名，中文命名，如'理财产品'"},
                    "definition": {"type": "string", "description": "一句话定义，中文"},
                    "parent": {"type": ["string", "null"], "description": "父类名，无则 null"},
                    "properties": {
                        "type": "array",
                        "description": "该类的数据属性",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "属性名，中文，如'风险评级'"},
                                "data_type": {"type": "string",
                                              "enum": ["string", "number", "boolean", "date", "datetime", "array", "object"]},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "data_type"],
                        },
                    },
                },
                "required": ["label"],
            },
        },
        "object_properties": {
            "type": "array",
            "description": "类与类之间的关系（对象属性）",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "关系名，中文动词短语，如'购买'"},
                    "domain": {"type": "string", "description": "源类名"},
                    "range": {"type": "string", "description": "目标类名"},
                },
                "required": ["label", "domain", "range"],
            },
        },
        "datatype_properties": {
            "type": "array",
            "description": "不属于单个类上下文的独立数据属性（可空）",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "data_type": {"type": "string"},
                },
                "required": ["name", "data_type"],
            },
        },
    },
    "required": ["classes"],
}

SYSTEM_PROMPT = (
    "你是资深知识图谱本体建模专家。从给定的文档切片中抽取本体骨架（TBox）："
    "领域类、类的数据属性、类与类之间的关系。\n"
    "规则：\n"
    "1. 全部使用中文命名；类名为名词短语（2-6 字），关系名为动词短语（2-4 字）。\n"
    "2. 只抽取切片文本中有依据的概念，不要臆造；同一概念沿用【已发现类清单】中的既有命名，不要造同义新类。\n"
    "3. 每个类给出简明中文定义；数据属性 data_type 从枚举中选择。\n"
    "4. 类间关系必须写入 object_properties 数组，每个关系含 label（关系名）、domain（源类名）、"
    "range（目标类名）三个字段，不要使用 relations 等其他键名。\n"
    "5. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema，不要输出任何其他文字。"
)


class ExtractedEntity(BaseModel):
    label: str
    class_label: str
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0          # LLM 自报值（修复缺陷 #3：不再伪造）
    evidence: str = ""               # 实体所在原句（修复缺陷 #11）
    chunk_index: int = 0


class ExtractedRelation(BaseModel):
    subject_label: str
    predicate: str                   # 必须命中 TBox 对象属性（SchemaGate 校验）
    object_label: str
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    evidence: str = ""
    chunk_index: int = 0


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    chunks_processed: int = 0
    warnings: list[str] = Field(default_factory=list)


class SchemaExtractionResult(BaseModel):
    """Schema 阶段产物（TBox）。"""

    classes: list[dict] = Field(default_factory=list)   # {label, definition, properties[], parent?, aliases[], pending?}
    object_properties: list[dict] = Field(default_factory=list)  # {label, domain, range}
    datatype_properties: list[dict] = Field(default_factory=list)
    chunks_processed: int = 0
    warnings: list[str] = Field(default_factory=list)
    cache_hits: int = 0


# ── 缺陷 #9：LLM 输出缓存（model, prompt_hash）→ Redis 8h ──

def _cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    digest = hashlib.sha256(f"{system_prompt}\x00{user_prompt}".encode()).hexdigest()
    return f"llm_cache:{model}:{digest}"


def _redis():
    import redis

    from app.core.config import settings

    return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)


def _cache_get(key: str) -> Optional[dict]:
    try:
        raw = _redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 —— 缓存故障不阻断抽取
        return None


def _cache_put(key: str, payload: dict) -> None:
    try:
        _redis().set(key, json.dumps(payload, ensure_ascii=False), ex=LLM_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


# ── 默认 LLM 调用（provider → LLMClient）──

def _default_llm_call(model_override: Optional[dict]):
    """返回 (model_name, call)。call(system, user, json_schema) -> dict | None。"""
    from app.adapters.provider import ModelPurpose, resolve_provider
    from app.infrastructure.database import SessionLocal
    from app.infrastructure.llm_client import LLMClient

    override = model_override if (model_override or {}).get("base_url") else None
    db = SessionLocal()
    try:
        resolved = resolve_provider(ModelPurpose.EXTRACT, db=db, request_override=override)
    finally:
        db.close()
    client = LLMClient(api_key=resolved.api_key, base_url=resolved.base_url,
                       model=resolved.model_name)

    def call(system_prompt: str, user_prompt: str, json_schema: Optional[dict]):
        out = client.call_llm(system_prompt, user_prompt, max_retries=2,
                              stream=False, json_schema=json_schema)
        if isinstance(out, dict) and isinstance(out.get("content"), str):
            # call_llm 返回 {"content": ...} 形态时解析内嵌 JSON
            return _loads_loose(out["content"])
        return out if isinstance(out, dict) else None

    return resolved.model_name, call


def _loads_loose(text: str) -> Optional[dict]:
    """容忍 markdown 代码栅栏的 JSON 解析。"""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


# ── 输出归一 + 本地校验（04 §3.1：失败带错误反馈重试 1 次）──

def _normalize_payload(payload: dict) -> dict:
    """LLM 输出字段名归一：模型常用同义键（relations/data_properties/name），
    统一收敛到 SchemaExtractionResult 契约（object_properties/properties/label）。"""
    ops = payload.get("object_properties")
    if ops is None and isinstance(payload.get("relations"), list):
        ops = payload["relations"]
    if isinstance(ops, list):
        payload["object_properties"] = [
            {"label": str(op.get("label") or op.get("name") or "").strip(),
             "domain": str(op.get("domain") or "").strip(),
             "range": str(op.get("range") or "").strip()}
            for op in ops if isinstance(op, dict)]
    payload.pop("relations", None)
    for c in payload.get("classes") or []:
        if not isinstance(c, dict):
            continue
        if c.get("label") is None and c.get("name"):
            c["label"] = c["name"]
        props = c.get("properties")
        if props is None and isinstance(c.get("data_properties"), list):
            props = c["data_properties"]
        if isinstance(props, list):
            c["properties"] = [
                {"name": str(q.get("name") or q.get("label") or "").strip(),
                 "data_type": str(q.get("data_type") or "string"),
                 "description": str(q.get("description") or "")}
                for q in props if isinstance(q, dict)]
    return payload


def _validate_schema_payload(payload: Any) -> tuple[Optional[dict], str]:
    if not isinstance(payload, dict):
        return None, "输出不是 JSON 对象"
    payload = _normalize_payload(payload)
    if not isinstance(payload.get("classes"), list):
        return None, "缺少 classes 数组"
    for c in payload["classes"]:
        if not isinstance(c, dict) or not str(c.get("label") or "").strip():
            return None, f"classes 元素缺少非空 label: {c!r}"
        props = c.get("properties")
        if props is not None:
            if not isinstance(props, list):
                return None, f"类 {c.get('label')} 的 properties 不是数组"
            for p in props:
                if not isinstance(p, dict) or not str(p.get("name") or "").strip():
                    return None, f"类 {c.get('label')} 存在缺 name 的属性"
    for key in ("object_properties", "datatype_properties"):
        for op in payload.get(key) or []:
            if not isinstance(op, dict):
                return None, f"{key} 元素不是对象"
    return payload, ""


def _user_prompt(chunk_text: str, known_classes: list[str], error_feedback: str = "",
                 chunk_no: int = 0) -> str:
    """字段化 prompt（修复缺陷 #2）：结构化字段传入，绝不塞原始 dict。"""
    parts = []
    if known_classes:
        parts.append("【已发现类清单】（沿用这些命名，不要造同义新类）\n"
                     + "\n".join(f"- {c}" for c in known_classes))
    parts.append(f"【文档切片 #{chunk_no}】\n{chunk_text}")
    if error_feedback:
        parts.append(f"【上次输出错误，必须修正】\n{error_feedback}")
    return "\n\n".join(parts)


def _chunk_iter(chunks: list) -> list[tuple[int, str]]:
    """兼容 str / dict{text,index} / 带 text 属性对象三种切片形态。"""
    out = []
    for i, c in enumerate(chunks or []):
        if isinstance(c, str):
            text = c
            idx = i
        elif isinstance(c, dict):
            text = str(c.get("text") or "")
            idx = int(c.get("index", c.get("chunk_index", i)) or i)
        else:
            text = str(getattr(c, "text", "") or "")
            idx = int(getattr(c, "chunk_index", i) or i)
        if text.strip():
            out.append((idx, text))
    return out


def extract_schema(chunks: list, base_uri: str,
                   parallelism: int = 4,
                   model_override: Optional[dict] = None,
                   known_classes: Optional[list[str]] = None,
                   use_cache: bool = True,
                   llm_call: Optional[Callable] = None,
                   progress_cb: Optional[Callable] = None,
                   cancel_cb: Optional[Callable] = None) -> SchemaExtractionResult:
    """分块并行抽取 TBox → 归并去重（label_normalized）→ 悬空引用建占位类标记 pending。

    - 并行度 parallelism（修复缺陷 #5，默认 4）
    - (model, prompt_hash) Redis 8h 缓存（修复缺陷 #9，use_cache=False 可关）
    - 本地校验失败带错误反馈重试 1 次，仍失败计 warning
    - progress_cb(done, total, cache_hits)；cancel_cb() 抛异常即中止（Celery 取消用）
    llm_call 注入用于测试；生产走 resolve_provider(EXTRACT) + LLMClient。
    """
    result = SchemaExtractionResult()
    prepared = _chunk_iter(chunks)
    result.chunks_processed = len(prepared)
    if not prepared:
        return result

    if llm_call is None:
        model_name, llm_call = _default_llm_call(model_override)
    else:
        model_name = str((model_override or {}).get("model_name") or "injected")
    known: list[str] = list(known_classes or [])

    def one(idx: int, text: str):
        key = _cache_key(model_name, SYSTEM_PROMPT, _user_prompt(text, known, chunk_no=idx))
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                return idx, cached, True, ""
        feedback = ""
        last_err = "未知错误"
        for _attempt in range(2):  # 首次 + 带错误反馈重试 1 次
            raw = llm_call(SYSTEM_PROMPT, _user_prompt(text, known, feedback, chunk_no=idx),
                           SCHEMA_PROMPT_JSON_SCHEMA)
            payload, last_err = _validate_schema_payload(raw)
            if payload is not None:
                if use_cache:
                    _cache_put(key, payload)
                return idx, payload, False, ""
            feedback = last_err
        return idx, None, False, last_err

    merged: dict[str, dict] = {}       # norm(label) → class dict
    obj_props: dict[str, dict] = {}    # norm(label) → {label, domain, range}
    dt_props: dict[str, dict] = {}     # norm(name) → {name, data_type}
    done = 0
    cache_hits = 0

    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        futures = {pool.submit(one, idx, text): idx for idx, text in prepared}
        for fut in as_completed(futures):
            if cancel_cb is not None:
                cancel_cb()  # 取消时抛异常，终结整个抽取
            done += 1
            idx, payload, hit, err = fut.result()
            cache_hits += 1 if hit else 0
            if payload is None:
                result.warnings.append(f"chunk #{idx} 骨架抽取失败（已重试）: {err}")
            else:
                for c in payload.get("classes") or []:
                    _merge_class(merged, c)
                for op in payload.get("object_properties") or []:
                    label = str(op.get("label") or "").strip()
                    if label:
                        obj_props.setdefault(normalize_label(label), {
                            "label": label,
                            "domain": str(op.get("domain") or "").strip(),
                            "range": str(op.get("range") or "").strip(),
                        })
                for dp in payload.get("datatype_properties") or []:
                    name = str(dp.get("name") or "").strip()
                    if name:
                        dt_props.setdefault(normalize_label(name), {
                            "name": name,
                            "data_type": str(dp.get("data_type") or "string"),
                        })
            if progress_cb is not None:
                progress_cb(done, len(prepared), cache_hits)

    classes = list(merged.values())
    # 悬空引用 → 占位类标记 pending（04 §3.1 SchemaGate 归一）
    class_norms = set(merged.keys())
    for op in obj_props.values():
        for side in ("domain", "range"):
            ref = op.get(side) or ""
            if ref and normalize_label(ref) not in class_norms:
                merged[normalize_label(ref)] = {
                    "label": ref, "definition": "", "properties": [],
                    "parent": None, "aliases": [], "pending": True}
                class_norms.add(normalize_label(ref))
                classes.append(merged[normalize_label(ref)])

    result.classes = classes
    result.object_properties = list(obj_props.values())
    result.datatype_properties = list(dt_props.values())
    result.cache_hits = cache_hits
    return result


def _merge_class(merged: dict[str, dict], c: dict) -> None:
    """按 normalize_label 归并同类：properties 按名去重合并，定义/父类取先到者。"""
    label = str(c.get("label") or "").strip()
    if not label:
        return
    key = normalize_label(label)
    props = []
    seen = set()
    for p in c.get("properties") or []:
        name = str((p or {}).get("name") or "").strip()
        if name and normalize_label(name) not in seen:
            seen.add(normalize_label(name))
            props.append({"name": name,
                          "data_type": str((p or {}).get("data_type") or "string"),
                          "description": str((p or {}).get("description") or "")})
    if key in merged:
        tgt = merged[key]
        for p in props:
            if normalize_label(p["name"]) not in {normalize_label(q["name"]) for q in tgt["properties"]}:
                tgt["properties"].append(p)
        if not tgt.get("definition") and c.get("definition"):
            tgt["definition"] = str(c["definition"])
        return
    merged[key] = {
        "label": label,
        "definition": str(c.get("definition") or ""),
        "properties": props,
        "parent": c.get("parent") or None,
        "aliases": [],
    }


def extract_instances(chunks: list, tbox_summary: dict, base_uri: str,
                      strict_gate: bool = True,
                      promote_policy: Literal["review", "discard", "auto"] = "review",
                      parallelism: int = 4,
                      model_override: Optional[dict] = None) -> ExtractionResult:
    """TODO(M3-5): ABox 抽取，输出经 adapters/schema_gate 校验后的实体与关系。"""
    raise NotImplementedError("M3-5 实现（docs/design/04 §4）")


__all__ = [
    "ExtractedEntity", "ExtractedRelation", "ExtractionResult", "SchemaExtractionResult",
    "extract_schema", "extract_instances", "SCHEMA_PROMPT_JSON_SCHEMA", "SYSTEM_PROMPT",
    "LLM_CACHE_TTL", "_cache_key", "_cache_get", "_cache_put",
]
