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
                    "confidence": {"type": "number", "description": "0-1 自报置信度，如实评估"},
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
    "range（目标类名）、confidence（0-1 自报置信度，依据充分约 0.9，仅措辞推断约 0.6-0.7，不要全给 1.0），"
    "不要使用 relations 等其他键名。\n"
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
    # SchemaGate 统计（04 §4.3，M3-5）
    discarded_count: int = 0
    withheld_count: int = 0
    promote_count: int = 0
    missing_evidence_count: int = 0
    low_confidence_count: int = 0
    promote_candidates: list[dict] = Field(default_factory=list)
    cache_hits: int = 0


class SchemaExtractionResult(BaseModel):
    """Schema 阶段产物（TBox）。"""

    classes: list[dict] = Field(default_factory=list)   # {label, definition, properties[], parent?, aliases[], pending?}
    object_properties: list[dict] = Field(default_factory=list)  # {label, domain, range}
    datatype_properties: list[dict] = Field(default_factory=list)
    chunks_processed: int = 0
    warnings: list[str] = Field(default_factory=list)
    cache_hits: int = 0
    stitched_relations: int = 0   # 跨切片关系缝合补上的关系数


# ── 缺陷 #9：LLM 输出缓存（model, prompt_hash）→ Redis 8h ──

def _cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    digest = hashlib.sha256(f"{system_prompt}\x00{user_prompt}".encode()).hexdigest()
    return f"llm_cache:{model}:{digest}"


def _redis():
    import redis

    from app.services.env_config_service import redis_url
    return redis.Redis.from_url(redis_url(), socket_connect_timeout=2)


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

def _coerce_confidence(v: Any) -> float | None:
    """LLM 自报置信度 → 0-1 float；缺失/非法返回 None（不伪造，与缺陷 #3 修复一致）。"""
    try:
        if v is None or str(v).strip() == "":
            return None
        return min(max(float(v), 0.0), 1.0)
    except (TypeError, ValueError):
        return None


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
             "range": str(op.get("range") or "").strip(),
             "confidence": _coerce_confidence(op.get("confidence"))}
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
                 chunk_no: int = 0, guidance: str = "") -> str:
    """字段化 prompt（修复缺陷 #2）：结构化字段传入，绝不塞原始 dict。"""
    parts = []
    if guidance:
        parts.append("【抽取引导（用户注入，优先遵守；与文档内容冲突时以文档为准）】\n" + guidance.strip())
    if known_classes:
        parts.append("【已发现类清单】（沿用这些命名，不要造同义新类）\n"
                     + "\n".join(f"- {c}" for c in known_classes))
    parts.append(f"【文档切片 #{chunk_no}】\n{chunk_text}")
    if error_feedback:
        parts.append(f"【上次输出错误，必须修正】\n{error_feedback}")
    return "\n\n".join(parts)


def _chunk_iter(chunks: list) -> list[tuple[int, str, str]]:
    """兼容 str / dict{text,index} / 带 text 属性对象三种切片形态，返回 (idx, text, doc)。"""
    out = []
    for i, c in enumerate(chunks or []):
        if isinstance(c, str):
            text = c
            idx = i
            doc = ""
        elif isinstance(c, dict):
            text = str(c.get("text") or "")
            idx = int(c.get("index", c.get("chunk_index", i)) or i)
            doc = str(c.get("doc") or "")
        else:
            text = str(getattr(c, "text", "") or "")
            idx = int(getattr(c, "chunk_index", i) or i)
            doc = str(getattr(c, "doc", "") or "")
        if text.strip():
            out.append((idx, text, doc))
    return out


# ── 跨切片关系缝合（归并后处理）：切片独立抽取连不上的类间关系，归并后统一补一轮 ──

STITCH_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "description": "文档内容中有证据支持的类间关系",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "关系名，中文动词短语，如'购买'"},
                    "domain": {"type": "string", "description": "源类名，必须严格取自【类清单】"},
                    "range": {"type": "string", "description": "目标类名，必须严格取自【类清单】"},
                    "evidence": {"type": "string", "description": "支持该关系的原文原句（逐字摘录）"},
                    "confidence": {"type": "number", "description": "0-1 自报置信度，如实评估"},
                },
                "required": ["label", "domain", "range"],
            },
        },
    },
    "required": ["relations"],
}

STITCH_SYSTEM_PROMPT = (
    "你是资深知识图谱本体建模专家。文档此前被切成多个片段独立抽取本体，类清单中的类可能分散在"
    "不同片段、彼此之间有依据的关系没有被连上。你的任务是【跨切片关系缝合】：通读给定的文档内容摘录，"
    "找出类清单中尚未连接或连接不足的类之间，文档中有证据支持的关系。\n"
    "规则：\n"
    "1. domain 与 range 必须严格取自【类清单】中的类名，逐字一致，不要发明新类或同义替换。\n"
    "2. 只输出有原文证据支持的关系，evidence 必须从文档内容摘录中逐字摘录原句；没有证据的不要输出。\n"
    "3. 关系名用中文动词短语（2-4 字）；同一对类之间只输出一条最主要的关系。\n"
    "4. 每条关系给出 confidence（0-1 自报置信度，依据充分约 0.9，仅措辞推断约 0.6-0.7，不要全给 1.0）。\n"
    "5. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema，不要输出任何其他文字。"
)

_STITCH_MAX_CHARS_TOTAL = 60000    # 缝合用文档摘录总预算（字符）
_STITCH_MAX_CHARS_PER_SEG = 8000   # 单次缝合调用的摘录上限（小段多次：单段 prompt≈类清单+8k，降低超时概率）
_STITCH_MAX_CALLS = 8              # 缝合调用次数上限
_STITCH_CLASS_DEF_CHARS = 60


def _stitch_user_prompt(class_list_text: str, doc_text: str, error_feedback: str = "") -> str:
    parts = [
        "【类清单】（domain/range 只能取自这里，逐字一致）\n" + class_list_text,
        "【文档内容摘录】\n" + doc_text,
    ]
    if error_feedback:
        parts.append(f"【上次输出错误，必须修正】\n{error_feedback}")
    return "\n\n".join(parts)


def _validate_stitch_payload(payload: Any) -> tuple[Optional[list], str]:
    if not isinstance(payload, dict):
        return None, "输出不是 JSON 对象"
    rels = payload.get("relations")
    if rels is None and isinstance(payload.get("object_properties"), list):
        rels = payload["object_properties"]
    if rels is None and isinstance(payload.get("relationships"), list):
        rels = payload["relationships"]
    if not isinstance(rels, list):
        return None, "缺少 relations 数组"
    for r in rels:
        if not isinstance(r, dict) or not str(r.get("label") or "").strip():
            return None, f"relations 元素缺少非空 label: {r!r}"
        if not str(r.get("domain") or "").strip() or not str(r.get("range") or "").strip():
            return None, f"关系 {r.get('label')} 缺少 domain/range"
    return rels, ""


def _stitch_class_list(classes: list[dict]) -> str:
    # 按类名排序：并行完成顺序不稳定，排序保证缓存键跨次运行一致
    lines = []
    for c in sorted(classes, key=lambda c: str(c.get("label") or "")):
        d = (c.get("definition") or "").strip()
        if len(d) > _STITCH_CLASS_DEF_CHARS:
            d = d[:_STITCH_CLASS_DEF_CHARS] + "…"
        lines.append(f"- {c.get('label')}" + (f"：{d}" if d else ""))
    return "\n".join(lines)


def _stitch_doc_text(prepared: list[tuple[int, str]]) -> str:
    """按切片均摊预算摘录文档内容：每片取头部，总预算受限，覆盖前后所有切片。"""
    if not prepared:
        return ""
    per = max(300, _STITCH_MAX_CHARS_TOTAL // len(prepared))
    parts = []
    used = 0
    for idx, text, _doc in prepared:
        if used >= _STITCH_MAX_CHARS_TOTAL:
            break
        piece = text.strip()[:per]
        parts.append(f"[切片 {idx}] {piece}")
        used += len(piece)
    return "\n".join(parts)


def extract_schema(chunks: list, base_uri: str,
                   parallelism: int = 4,
                   model_override: Optional[dict] = None,
                   known_classes: Optional[list[str]] = None,
                   use_cache: bool = True,
                   llm_call: Optional[Callable] = None,
                   progress_cb: Optional[Callable] = None,
                   stitch_progress_cb: Optional[Callable] = None,
                   cancel_cb: Optional[Callable] = None,
                   guidance: Optional[str] = None) -> SchemaExtractionResult:
    """分块并行抽取 TBox → 归并去重（label_normalized）→ 跨切片关系缝合 → 悬空引用建占位类标记 pending。

    - 并行度 parallelism（修复缺陷 #5，默认 4）
    - (model, prompt_hash) Redis 8h 缓存（修复缺陷 #9，use_cache=False 可关）；
      guidance 注入用户 prompt，随 prompt 哈希进缓存键（改引导自动换缓存）
    - 归并后做跨切片关系缝合：全类清单 + 文档摘录补一轮 LLM 调用，连上分散在
      不同切片的类间关系（只连已知类，需原文证据；stitched_relations 记数）
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

    guidance_text = (guidance or "").strip()

    def one(idx: int, text: str):
        key = _cache_key(model_name, SYSTEM_PROMPT,
                         _user_prompt(text, known, chunk_no=idx, guidance=guidance_text))
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                return idx, cached, True, ""
        feedback = ""
        last_err = "未知错误"
        for _attempt in range(2):  # 首次 + 带错误反馈重试 1 次
            try:
                raw = llm_call(SYSTEM_PROMPT,
                               _user_prompt(text, known, feedback, chunk_no=idx, guidance=guidance_text),
                               SCHEMA_PROMPT_JSON_SCHEMA)
            except Exception as e:  # noqa: BLE001  LLM 连接/超时：单切片失败不入全局
                last_err = f"LLM调用异常: {e}"
                feedback = last_err
                continue
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
        futures = {pool.submit(one, idx, text): idx for idx, text, _doc in prepared}
        doc_by_idx = {idx: doc for idx, _text, doc in prepared}
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
                    # 溯源来源：类首次出现的切片/文档（graph_rows 落 ProvenanceRecord 用）
                    c["_source_chunk_index"] = idx
                    c["_source_doc"] = doc_by_idx.get(idx, "")
                    _merge_class(merged, c)
                for op in payload.get("object_properties") or []:
                    label = str(op.get("label") or "").strip()
                    if label:
                        d = normalize_label(str(op.get("domain") or "").strip())
                        g = normalize_label(str(op.get("range") or "").strip())
                        # 键含 domain/range：同名关系在不同类对之间不互相吞并
                        obj_props.setdefault(f"{normalize_label(label)}|{d}|{g}", {
                            "label": label,
                            "domain": str(op.get("domain") or "").strip(),
                            "range": str(op.get("range") or "").strip(),
                            "confidence": _coerce_confidence(op.get("confidence")),
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

    # ── 跨切片关系缝合：切片独立抽取只连得到同片共现的关系，这里归并后统一补一轮 ──
    # 依据 = 文档内容摘录（evidence 逐字摘录），只连类清单里已有的类，不造新类。
    stitched = 0
    if len(classes) >= 2 and len(prepared) >= 2:
        class_list_text = _stitch_class_list(classes)
        norm2label = {k: v["label"] for k, v in merged.items()}
        existing_rk = {(normalize_label(v["label"]), normalize_label(v["domain"]),
                        normalize_label(v["range"])) for v in obj_props.values()}
        doc_text = _stitch_doc_text(prepared)
        segs = [doc_text[i:i + _STITCH_MAX_CHARS_PER_SEG]
                for i in range(0, len(doc_text), _STITCH_MAX_CHARS_PER_SEG)][:_STITCH_MAX_CALLS]
        for si, seg in enumerate(segs):
            if cancel_cb is not None:
                cancel_cb()
            if stitch_progress_cb is not None:
                stitch_progress_cb(si + 1, len(segs))  # 缝合进度实时上报（否则 UI 在 90% 静默卡住）
            skey = _cache_key(model_name, STITCH_SYSTEM_PROMPT,
                              _stitch_user_prompt(class_list_text, seg))
            rels = _cache_get(skey) if use_cache else None
            if rels is None:
                feedback = ""
                for _attempt in range(2):  # 首次 + 带错误反馈重试 1 次
                    raw = llm_call(STITCH_SYSTEM_PROMPT,
                                   _stitch_user_prompt(class_list_text, seg, feedback),
                                   STITCH_JSON_SCHEMA)
                    rels, feedback = _validate_stitch_payload(raw)
                    if rels is not None:
                        break
                if rels is None:
                    result.warnings.append(f"关系缝合段 #{si} 失败（已重试）: {feedback}")
                    continue
                if use_cache:
                    _cache_put(skey, rels)
            for r in rels:
                label = str(r.get("label") or "").strip()
                d = normalize_label(str(r.get("domain") or "").strip())
                g = normalize_label(str(r.get("range") or "").strip())
                if d == g or d not in norm2label or g not in norm2label:
                    continue  # 缝合只连已知类，不造类不自环
                rk = (normalize_label(label), d, g)
                if rk in existing_rk:
                    continue
                existing_rk.add(rk)
                obj_props[f"{normalize_label(label)}|{d}|{g}"] = {
                    "label": label, "domain": norm2label[d], "range": norm2label[g],
                    "confidence": _coerce_confidence(r.get("confidence")),
                    "evidence": str(r.get("evidence") or "")[:1024]}
                stitched += 1
    result.stitched_relations = stitched

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
        # 首现切片的溯源来源（保留先到者）
        "_source_chunk_index": c.get("_source_chunk_index"),
        "_source_doc": str(c.get("_source_doc") or ""),
    }


# ══ M3-5：Instance 阶段（ABox，04 §4）══

INSTANCE_PROMPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "description": "切片中出现的具体实例",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "实例名，用文中原始称呼"},
                    "class_label": {"type": "string",
                                    "description": "所属类名，必须取自【本体类清单】"},
                    "props": {"type": "object",
                              "description": "该实例的属性键值对：键用【各类属性清单】中所属类的属性名，"
                                             "值用切片原文中的原值；原文没有对应值的属性不要编造；"
                                             "一个属性值都没有时给空对象 {}"},
                    "confidence": {"type": "number",
                                   "description": "0-1 自报置信度：证据明确 0.9+，推断 0.5-0.7"},
                    "evidence": {"type": "string", "description": "支持该实体的原文原句（逐字摘录）"},
                },
                "required": ["label", "class_label", "props"],
            },
        },
        "relations": {
            "type": "array",
            "description": "实例之间的关系",
            "items": {
                "type": "object",
                "properties": {
                    "subject_label": {"type": "string", "description": "主体实例名"},
                    "predicate": {"type": "string", "description": "关系名，必须取自【关系清单】"},
                    "object_label": {"type": "string", "description": "客体实例名"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string", "description": "支持该关系的原文原句"},
                },
                "required": ["subject_label", "predicate", "object_label"],
            },
        },
    },
    "required": ["entities"],
}

INSTANCE_SYSTEM_PROMPT = (
    "你是资深知识图谱构建专家。根据给定的本体（TBox）从文档切片中抽取实例（ABox）与实例间关系。\n"
    "规则：\n"
    "1. class_label 必须严格取自【本体类清单】；predicate 必须严格取自【关系清单】，不要发明新类/新关系。\n"
    "2. 实例名使用文中原始称呼；同一实体的多种称呼都出现时各抽一次，后续消解会合并。\n"
    "3. 每个实例/关系必须给出 evidence：从切片中逐字摘录的原句；没有原文依据的不要抽。\n"
    "4. props：尽可能把实例在文中体现的属性值填入 props——键用【各类属性清单】中所属类的属性名，"
    "值为文中原值（数字/日期保留原样）；文中没有的属性不要编造；一个属性值都没有时 props 给空对象 {}。\n"
    "5. confidence 为 0-1 的自报置信度，如实评估，不要全给 1.0。\n"
    "6. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema，不要输出任何其他文字。"
)


def _normalize_instance_payload(payload: dict) -> dict:
    """LLM 输出键漂移归一（同 _normalize_payload，实例契约版）。"""
    rels = payload.get("relations")
    if rels is None and isinstance(payload.get("relationships"), list):
        rels = payload["relationships"]
    if isinstance(rels, list):
        payload["relations"] = rels
    payload.pop("relationships", None)
    for r_ in payload.get("relations") or []:
        if not isinstance(r_, dict):
            continue
        if r_.get("subject_label") is None and r_.get("subject"):
            r_["subject_label"] = r_["subject"]
        if r_.get("object_label") is None and r_.get("object"):
            r_["object_label"] = r_["object"]
    for e in payload.get("entities") or []:
        if not isinstance(e, dict):
            continue
        if e.get("label") is None and e.get("name"):
            e["label"] = e["name"]
        if e.get("class_label") is None:
            e["class_label"] = e.get("type") or e.get("class") or ""
        pr = e.get("props")
        if pr is None and isinstance(e.get("properties"), dict):
            pr = e["properties"]
        if not isinstance(pr, dict):
            pr = {}
        e["props"] = pr
    return payload


def _validate_instance_payload(payload: Any) -> tuple[Optional[dict], str]:
    if not isinstance(payload, dict):
        return None, "输出不是 JSON 对象"
    payload = _normalize_instance_payload(payload)
    ents = payload.get("entities")
    if ents is None and isinstance(payload.get("relations"), list):
        ents = []  # 只有关系没有实体也算合法形状
    if not isinstance(ents, list):
        return None, "缺少 entities 数组"
    for e in ents:
        if not isinstance(e, dict) or not str(e.get("label") or "").strip():
            return None, f"entities 元素缺少非空 label: {e!r}"
    for r in payload.get("relations") or []:
        if not isinstance(r, dict) or not (str(r.get("predicate") or "").strip()
                                           and str(r.get("subject_label") or "").strip()
                                           and str(r.get("object_label") or "").strip()):
            return None, f"relations 元素缺少 subject/predicate/object: {r!r}"
    return payload, ""


def _instance_user_prompt(chunk_text: str, tbox_summary: dict, known_entities: list[str],
                          prev_tail: str = "", error_feedback: str = "",
                          chunk_no: int = 0) -> str:
    """字段化 TBox 摘要 prompt（04 §4.1：类/属性/关系清单 + 前块上下文）。"""
    parts = []
    classes = tbox_summary.get("classes") or {}
    if classes:
        lines = [f"- {c}" + (f"（别名：{'、'.join(a)}）" if a else "")
                 for c, a in classes.items()]
        parts.append("【本体类清单】（class_label 只能取自这里）\n" + "\n".join(lines))
    cp = tbox_summary.get("class_properties") or {}
    if cp:
        lines = []
        for c, props in cp.items():
            ptxt = "；".join(f"{p.get('name')}（{p.get('data_type', 'string')}）"
                            for p in (props or []) if p.get("name"))
            if ptxt:
                lines.append(f"- {c}：{ptxt}")
        if lines:
            parts.append("【各类属性清单】（实例的 props 键只能取自所属类这里的属性名）\n"
                         + "\n".join(lines))
    ops = tbox_summary.get("object_properties") or {}
    if ops:
        lines = [f"- {p}：{d.get('domain', '')} → {d.get('range', '')}"
                 for p, d in ops.items()]
        parts.append("【关系清单】（predicate 只能取自这里）\n" + "\n".join(lines))
    if known_entities:
        parts.append("【已抽取实体】（重复提及可再抽，消解层会合并）\n"
                     + "、".join(known_entities[-80:]))
    if prev_tail:
        parts.append(f"【前块结尾上下文】\n{prev_tail[-200:]}")
    parts.append(f"【文档切片 #{chunk_no}】\n{chunk_text}")
    if error_feedback:
        parts.append(f"【上次输出错误，必须修正】\n{error_feedback}")
    return "\n\n".join(parts)


def _dedupe_entities(entities: list) -> list:
    """同 chunk 内 (norm label, class) 去重，保留 confidence 最高者（跨 chunk 交给消解层）。"""
    best: dict[tuple, Any] = {}
    order: list[tuple] = []
    for e in entities:
        key = (normalize_label(e.label), normalize_label(e.class_label))
        if key not in best:
            best[key] = e
            order.append(key)
        elif e.confidence > best[key].confidence:
            best[key] = e
    return [best[k] for k in order]


def extract_instances(chunks: list, tbox_summary: dict, base_uri: str,
                      strict_gate: bool = True,
                      promote_policy: Literal["review", "discard", "auto"] = "review",
                      parallelism: int = 4,
                      model_override: Optional[dict] = None,
                      known_entities: Optional[list[str]] = None,
                      use_cache: bool = True,
                      llm_call: Optional[Callable] = None,
                      progress_cb: Optional[Callable] = None,
                      cancel_cb: Optional[Callable] = None) -> ExtractionResult:
    """ABox 抽取（04 §4）：分块并行 → 严格闸门（apply_gate）→ 统计。

    tbox_summary：{"classes": {类名: [别名]}, "object_properties": {谓词: {domain, range}}}
    （与 schema_gate.apply_gate 的 tbox 同形；chunks 定位文本内部自动构建）。
    strict_gate=False 时闸门只做归一不拦违例（调试用）。
    """
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    result = ExtractionResult()
    prepared = _chunk_iter(chunks)
    result.chunks_processed = len(prepared)
    if not prepared:
        return result

    if llm_call is None:
        model_name, llm_call = _default_llm_call(model_override)
    else:
        model_name = str((model_override or {}).get("model_name") or "injected")
    known = list(known_entities or [])
    chunks_by_idx = {idx: text for idx, text, _doc in prepared}

    def one(idx: int, text: str):
        prev_tail = chunks_by_idx.get(idx - 1, "") if idx != prepared[0][0] else ""
        user = _instance_user_prompt(text, tbox_summary, known, prev_tail, chunk_no=idx)
        key = _cache_key(model_name, INSTANCE_SYSTEM_PROMPT, user)
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                return idx, cached, True, ""
        feedback = ""
        last_err = "未知错误"
        for _attempt in range(2):
            try:
                raw = llm_call(INSTANCE_SYSTEM_PROMPT,
                               _instance_user_prompt(text, tbox_summary, known, prev_tail,
                                                     feedback, chunk_no=idx),
                               INSTANCE_PROMPT_JSON_SCHEMA)
            except Exception as e:  # noqa: BLE001  LLM 连接/超时：单切片失败不入全局
                last_err = f"LLM调用异常: {e}"
                feedback = last_err
                continue
            payload, last_err = _validate_instance_payload(raw)
            if payload is not None:
                if use_cache:
                    _cache_put(key, payload)
                return idx, payload, False, ""
            feedback = last_err
        return idx, None, False, last_err

    raw_entities: list[ExtractedEntity] = []
    raw_relations: list[ExtractedRelation] = []
    done = 0
    cache_hits = 0
    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        futures = {pool.submit(one, idx, text): idx for idx, text, _doc in prepared}
        for fut in as_completed(futures):
            if cancel_cb is not None:
                cancel_cb()
            done += 1
            idx, payload, hit, err = fut.result()
            cache_hits += 1 if hit else 0
            if payload is None:
                result.warnings.append(f"chunk #{idx} 实例抽取失败（已重试）: {err}")
            else:
                for e in payload.get("entities") or []:
                    try:
                        conf = float(e.get("confidence") or 0.0)
                    except (TypeError, ValueError):
                        conf = 0.0
                    raw_entities.append(ExtractedEntity(
                        label=str(e.get("label") or "").strip(),
                        class_label=str(e.get("class_label") or "").strip(),
                        props=e.get("props") or {},
                        confidence=min(max(conf, 0.0), 1.0),
                        evidence=str(e.get("evidence") or ""),
                        chunk_index=idx))
                for r in payload.get("relations") or []:
                    try:
                        conf = float(r.get("confidence") or 0.0)
                    except (TypeError, ValueError):
                        conf = 0.0
                    raw_relations.append(ExtractedRelation(
                        subject_label=str(r.get("subject_label") or "").strip(),
                        predicate=str(r.get("predicate") or "").strip(),
                        object_label=str(r.get("object_label") or "").strip(),
                        confidence=min(max(conf, 0.0), 1.0),
                        evidence=str(r.get("evidence") or ""),
                        chunk_index=idx))
            if progress_cb is not None:
                progress_cb(done, len(prepared), cache_hits)

    # 严格闸门（04 §4.1）：类/谓词四级匹配 + domain/range + evidence 定位 + confidence 阈值
    tbox = {"classes": tbox_summary.get("classes") or {},
            "object_properties": tbox_summary.get("object_properties") or {},
            "chunks": chunks_by_idx}
    gate = apply_gate(raw_entities + raw_relations, tbox,
                      PromotePolicy(promote_policy) if isinstance(promote_policy, str)
                      else promote_policy)
    if not strict_gate:
        result.entities = _dedupe_entities([i for i in gate.passed
                                            if isinstance(i, ExtractedEntity)])
        result.relations = [i for i in gate.passed if isinstance(i, ExtractedRelation)]
        return result

    kinds = [v.kind for v in gate.violations]
    result.discarded_count = gate.discarded_count
    result.withheld_count = gate.withheld_count
    result.promote_candidates = gate.promote_candidates
    result.promote_count = len(gate.promote_candidates)
    result.missing_evidence_count = kinds.count("missing_evidence")
    result.low_confidence_count = kinds.count("low_confidence")
    result.entities = _dedupe_entities([i for i in gate.passed
                                        if isinstance(i, ExtractedEntity)])
    result.relations = [i for i in gate.passed if isinstance(i, ExtractedRelation)]
    result.cache_hits = cache_hits
    return result


__all__ = [
    "ExtractedEntity", "ExtractedRelation", "ExtractionResult", "SchemaExtractionResult",
    "extract_schema", "extract_instances", "SCHEMA_PROMPT_JSON_SCHEMA", "SYSTEM_PROMPT",
    "INSTANCE_PROMPT_JSON_SCHEMA", "INSTANCE_SYSTEM_PROMPT",
    "LLM_CACHE_TTL", "_cache_key", "_cache_get", "_cache_put",
]
