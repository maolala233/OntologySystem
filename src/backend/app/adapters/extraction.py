# app/adapters/extraction.py - LLM 抽取（docs/design/04 §3/§4）
# M3-4 实装 Schema 阶段（extract_schema）。中文路线：NER/RE 全走 LLM 方法（pattern/spaCy
# 对中文无效，见 _compat.CHINESE_PATCHES）。
# 修复缺陷：#2 结构化字段化 prompt（不塞原始 dict，已发现类清单字段化增量传入）；
#           #5 分块并行 ThreadPoolExecutor（线程预算统一由 LLM_MAX_CONCURRENCY 控制）；
#           #9 Redis (model, prompt_hash) 8h 缓存，重跑不重复计费。
# LLM 调用经 adapters/provider（ModelPurpose.EXTRACT）+ infrastructure/llm_client，
# 业务侧可注入 llm_call 以便测试与复用。

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.adapters.resolution import normalize_label
from app.core.config import settings
from app.infrastructure.llm_client import LLMEndpointUnreachableError
from app.services.pipeline_profiler import (
    profile_metric,
    profile_metric_accumulate,
    submit_with_context,
)

# prompt-hash 缓存 TTL（04 §3.1）
# 单次 LLM 调用硬上限：本地小模型慢推理/云端拥堵时防无限等待；
# 也是"取消"的最坏生效延迟——进行中的调用最多再跑这么多秒
LLM_CALL_TIMEOUT_SECONDS = 180
LLM_CACHE_TTL = 7 * 24 * 3600

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
                                "enum_values": {"type": "array",
                                                "description": "文中明确列出的枚举取值（原值逐字），无则省略"},
                                "required": {"type": "boolean",
                                             "description": "文中明确写明必填才为 true"},
                                "min_occurs": {"type": "integer",
                                               "description": "文中明确写明的最少数量（如'至少提交1张影像'），无则省略"},
                                "max_occurs": {"type": "integer",
                                               "description": "文中明确写明的最多数量（如'最多30条'），无则省略"},
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
        "business_rules": {
            "type": "array",
            "description": "切片中的业务规则/约束/流程要求（阈值、枚举取值、必填、"
                           "校验条件、操作先后顺序、权限划分），每条必须给原文出处；"
                           "没有则留空数组",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string",
                                  "description": "规则内容，一句话中文"},
                    "kind": {"type": "string",
                             "enum": ["约束", "校验", "流程", "权限"]},
                    "evidence": {"type": "string",
                                 "description": "原文逐字摘录"},
                },
                "required": ["statement", "evidence"],
            },
        },
    },
    "required": ["classes"],
}

SYSTEM_PROMPT = (
    "你是资深知识图谱本体建模专家，方法论遵循「事实-事理-行动」一体化模型："
    "类覆盖事实层（业务对象/概念），关系覆盖事理层（依据、约束、归属）与行动层"
    "（触发、流转、执行）。从给定的文档切片中抽取本体骨架（TBox）："
    "领域类、类的数据属性、类与类之间的关系。\n"
    "规则：\n"
    "1. 全部使用中文命名；类名为名词短语（2-6 字），关系名为动词短语（2-4 字）。\n"
    "2. 类必须是【业务事物的分类】（名词概念，如「贷款合同」「保证金账户」）。"
    "动作/操作（经办、查询、审核、制单）、界面控件（页面、菜单、按钮、列表）、"
    "状态与属性值不是类——动作体现为关系，控件与属性值体现为数据属性或实例属性，"
    "一律不要建类。注意：系统、接口、协议、业务规则、业务字段、单据等业务对象"
    "是合法的类（它们是文档数据行/配置行的主体），不要把「接口/系统/规则」"
    "误解为界面元素而漏抽。\n"
    "3. 层次优先：只要切片文本支持上下位关系（如「流动资金贷款合同」⊑「贷款合同」⊑「合同」），"
    "必须给 parent；更具体的类永远挂到更通用的类下，不要让类孤立。\n"
    "4. 只抽取切片文本中有依据的概念，不要臆造；同一概念沿用【已发现类清单】中的既有命名，不要造同义新类。\n"
    "5. 每个类给出简明中文定义；数据属性 data_type 从枚举中选择。"
    "属性在文中明确写了枚举取值（如产品类型 0/1）、必填、数量上限（如'最多30条'）时，"
    "逐字写入 enum_values/required/min_occurs/max_occurs——约束只登记文中确实写了的，"
    "不要脑补。\n"
    "6. 类间关系必须写入 object_properties 数组，每个关系含 label（关系名）、domain（源类名）、"
    "range（目标类名）、confidence（0-1 自报置信度，依据充分约 0.9，仅措辞推断约 0.6-0.7，不要全给 1.0），"
    "不要使用 relations 等其他键名。\n"
    "7. 同步盘点切片中的业务规则与约束（业务阈值、枚举取值、必填/校验、操作流程"
    "顺序、权限划分），逐条写入 business_rules 数组，evidence 必须逐字摘录原文，"
    "不确定的口径不要臆造数值——只登记「文中确实写了」的规则。\n"
    "8. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema，不要输出任何其他文字。"
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
    # O2 增强（默认 0/空，向后兼容）
    coref_stats: dict = Field(default_factory=dict)       # B10 指代消解 L0 统计
    pattern_filled_count: int = 0                         # B11 pattern 直抽补全数
    pattern_calibrated_count: int = 0                     # B11 pattern 校准降置信数
    # O3-B15：闸门违例明细（{kind,label,detail}，定向修复 pick_worst_chunks 消费）
    gate_violations: list[dict] = Field(default_factory=list)


class SchemaExtractionResult(BaseModel):
    """Schema 阶段产物（TBox）。"""

    classes: list[dict] = Field(default_factory=list)   # {label, definition, properties[], parent?, aliases[], pending?}
    object_properties: list[dict] = Field(default_factory=list)  # {label, domain, range}
    datatype_properties: list[dict] = Field(default_factory=list)
    chunks_processed: int = 0
    warnings: list[str] = Field(default_factory=list)
    cache_hits: int = 0
    stitched_relations: int = 0   # 跨切片关系缝合补上的关系数
    strict_filtered: dict = Field(default_factory=dict)  # 严格白名单过滤计数 {classes, relations, properties}
    normalized_concepts: int = 0  # 语义归一化并入别名的类数（相似概念聚成概念，确定性零 LLM）
    table_inferred_relations: int = 0  # 边清单型表格推断出的关系数（确定性零 LLM）
    consumed_parents: int = 0     # 自报 parent 落成 parent_classes 的类数（确定性零 LLM）
    pruned_classes: int = 0       # 骨架组装剔除/并掉的伪类数（动作/页面词等）
    assembled_parents: int = 0    # 骨架组装为孤岛/缺层类补挂的父类数
    business_rules: list[dict] = Field(default_factory=list)
    # 语义采集表·规则层：[{statement, kind?, evidence, _source_chunk_index}]（文档证据锚定）
    stitch_segments: int = 0      # 缝合实际执行的段数（大文档覆盖可见性）
    structure_maps: list[dict] = Field(default_factory=list)
    # 结构映射（表格/条款 → 行主体类 + 列→关系/属性），实例阶段确定性展开用
    structure_map_calls: int = 0  # 实际发起 LLM 决策的签名数（缓存命中不计）


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

def _default_llm_call(model_override: Optional[dict],
                      model_config_id: Optional[int] = None):
    """返回 (model_name, call)。call(system, user, json_schema) -> dict | None。"""
    from app.adapters.provider import ModelPurpose, resolve_provider
    from app.infrastructure.database import SessionLocal
    from app.infrastructure.llm_client import LLMClient

    override = model_override if (model_override or {}).get("base_url") else None
    db = SessionLocal()
    try:
        resolved = resolve_provider(ModelPurpose.EXTRACT, db=db,
                                    request_override=override, config_id=model_config_id)
    finally:
        db.close()
    # disable_think 从抽取行 params 真正接线（原先只接到 VL 路径）：关思考直接砍掉
    # 长思考延迟——缝合等长 prompt 调用不再顶穿 330s 硬闸（ce2f 等四连死根因）
    client = LLMClient(api_key=resolved.api_key, base_url=resolved.base_url,
                       model=resolved.model_name,
                       think_mode="disabled"
                       if (resolved.params or {}).get("disable_think") else None,
                       streaming_enabled=(resolved.params or {}).get("streaming_enabled"))
    # 远程服务提速/降载：单次调用超时与重试次数可由 extract 行 params 覆盖，
    # 不配置时保持原行为（LLM_CALL_TIMEOUT_SECONDS / max_retries=2）；硬闸 =
    # timeout+30s，总预算≈(timeout+30)×retries，注意别顶穿 celery 软超时。
    llm_timeout = float((resolved.params or {}).get("llm_timeout") or LLM_CALL_TIMEOUT_SECONDS)
    max_retries = int((resolved.params or {}).get("max_retries") or 2)

    def call(system_prompt: str, user_prompt: str, json_schema: Optional[dict]):
        # stream 不传：跟随模型行 streaming_enabled 配置（客户端级缺省）
        out = client.call_llm(system_prompt, user_prompt, max_retries=max_retries,
                              json_schema=json_schema,
                              timeout=llm_timeout)
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
                 "description": str(q.get("description") or ""),
                 **({"enum_values": [str(v).strip() for v in q["enum_values"] if str(v).strip()]}
                    if isinstance(q.get("enum_values"), list) and q["enum_values"] else {}),
                 **({"required": bool(q["required"])} if q.get("required") is not None else {}),
                 **({"min_occurs": int(q["min_occurs"])}
                    if isinstance(q.get("min_occurs"), (int, float)) else {}),
                 **({"max_occurs": int(q["max_occurs"])}
                    if isinstance(q.get("max_occurs"), (int, float)) else {})}
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
                 chunk_no: int = 0, guidance: str = "", strict: bool = False,
                 table_header: str = "") -> str:
    """字段化 prompt（修复缺陷 #2）：结构化字段传入，绝不塞原始 dict。

    strict=True（上传配置模板的严格白名单模式）时引导头改为强约束措辞：
    模板即唯一抽取范围，不再保留"与文档内容冲突时以文档为准"的发散口子。
    """
    parts = []
    if guidance:
        if strict:
            parts.append("【抽取范围（严格白名单，最高优先级）】\n"
                         "只能抽取下列配置中列出的主体类、属性与关系；文档中出现但配置未列出的概念"
                         "一律不抽取、不扩展、不归纳、不造同义新类。本白名单与任何其他指示冲突时以本白名单为准。\n"
                         + guidance.strip())
        else:
            parts.append("【抽取引导（用户注入，优先遵守；与文档内容冲突时以文档为准）】\n" + guidance.strip())
    if known_classes:
        parts.append("【已发现类清单】（沿用这些命名，不要造同义新类）\n"
                     + "\n".join(f"- {c}" for c in known_classes))
    if table_header:
        parts.append(
            "【表格切片上下文】本切片是 markdown 表格的一部分，行可能不带表头。"
            "表头（列名）如下：\n" + table_header +
            "\n判读规则：列名是实体的【属性】，数据行是实体的【实例取值】——"
            "不要把列名或取值当作类；类名从表头与业务语义归纳（一行记录通常对应一个主体实例）。")
    parts.append(f"【文档切片 #{chunk_no}】\n{chunk_text}")
    if error_feedback:
        parts.append(f"【上次输出错误，必须修正】\n{error_feedback}")
    return "\n\n".join(parts)


def _chunk_iter(chunks: list) -> list[tuple[int, str, str, str]]:
    """兼容 str / dict{text,index} / 带 text 属性对象三种切片形态。

    返回 (idx, text, doc, table_header)：table_header 来自切片 meta
    （chunking 表格感知切片），供抽取 prompt 注入列语义——表格按行界切片
    后表头不随行走，丢失列名的切片会让模型从取值里瞎归纳（过度抽取根因）。
    """
    out = []
    for i, c in enumerate(chunks or []):
        if isinstance(c, str):
            text, idx, doc = c, i, ""
            meta = {}
        elif isinstance(c, dict):
            text = str(c.get("text") or "")
            idx = int(c.get("index", c.get("chunk_index", i)) or i)
            doc = str(c.get("doc") or "")
            meta = c.get("meta") if isinstance(c.get("meta"), dict) else {}
        else:
            text = str(getattr(c, "text", "") or "")
            idx = int(getattr(c, "chunk_index", i) or i)
            doc = str(getattr(c, "doc", "") or "")
            meta = getattr(c, "meta", None)
            meta = meta if isinstance(meta, dict) else {}
        if text.strip():
            out.append((idx, text, doc, str(meta.get("table_header") or "")))
    return out


def _llm_max_workers() -> int:
    """LLM 并行阶段唯一的线程预算：与全局模型调用并发闸同源。"""
    return max(1, int(getattr(settings, "LLM_MAX_CONCURRENCY", 16) or 16))


def _run_sliding_window(pool, items, submit_fn, done_fn, *,
                        max_workers: int, request_interval: float = 0.0,
                        cancel_cb: Optional[Callable] = None) -> None:
    """滑动窗口调度：始终保持 max_workers 个任务在途，完成一个补一个。

    旧实现是“每批 N 个，等整批结束再提交下一批”：批内单次调用
    50–150 秒的长尾会把其它空闲线程一起拖住（实测 4 路平均只有 2.4 路在跑）。
    这里不提高并发上限，只去掉批屏障——某个切片完成立即补下一个，直到任务耗尽。

    结果顺序仍由调用方按切片 idx 归并（本函数不改变确定性）。
    """
    from concurrent.futures import FIRST_COMPLETED, wait

    task_iter = iter(items)
    inflight: dict = {}
    max_workers = max(1, max_workers)

    def submit_next() -> bool:
        if cancel_cb is not None:
            cancel_cb()  # 取消后不再提交新任务
        try:
            item = next(task_iter)
        except StopIteration:
            return False
        if inflight and request_interval > 0:
            time.sleep(request_interval)  # params.request_interval：提交间隔限速
        fut = submit_fn(item)
        inflight[fut] = item
        return True

    for _ in range(max_workers):
        if not submit_next():
            break

    while inflight:
        done_futs, _ = wait(list(inflight.keys()), return_when=FIRST_COMPLETED)
        for fut in done_futs:
            if cancel_cb is not None:
                cancel_cb()  # 取消时抛异常，终结整个抽取
            item = inflight.pop(fut)
            done_fn(item, fut.result())
            submit_next()


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
_STITCH_MAX_CALLS = 8              # 缝合调用次数基础上限（小文档）
_STITCH_CLASS_DEF_CHARS = 60
# 大文档缝合覆盖（2026-11-09 根修：原固定 8 段只覆盖前 6 万字符，长文档后半的关系永远缝不上）
_STITCH_CALLS_PER_CHUNKS = 25      # 每 25 个切片增加一次缝合调用
_STITCH_MAX_CALLS_CAP = 24         # 缝合调用总上限（并行执行，墙钟成本可控）
_STITCH_TARGET_CHARS = 2500        # 缺口定向段：单切片摘录长度


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
        aliases = "、".join(str(a) for a in (c.get("aliases") or []))
        parents = "、".join(str(p) for p in (c.get("parent_classes") or []))
        notes = (f"（别名：{aliases}）" if aliases else "") \
            + (f"（父类：{parents}）" if parents else "")
        lines.append(f"- {c.get('label')}{notes}" + (f"：{d}" if d else ""))
    return "\n".join(lines)


def _stitch_segments(prepared: list[tuple[int, str, str, str]],
                     merged: dict[str, dict],
                     obj_props: dict[str, dict],
                     max_calls: int) -> list[str]:
    """缝合素材分段（缺口驱动 + 顺序兜底）。

    原实现只做顺序摘录（文档头部起 8 段）——大文档后半部分的关系永远缝不上。
    现在：①计算类连接度，把零连接/低连接（度≤1）类的首现切片摘录做成【定向段】
    （缺失关系最可能藏在那里）；②剩余预算给顺序段兜底全局。
    返回段列表（顺序确定性），调用方逐段（并行）缝合。
    """
    segs: list[str] = []
    # ── ① 定向段：低连接类的首现切片 ──
    deg: dict[str, int] = {}
    for op in obj_props.values():
        for side in ("domain", "range"):
            k = normalize_label(str(op.get(side) or ""))
            if k:
                deg[k] = deg.get(k, 0) + 1
    low_keys = {k for k, v in merged.items() if deg.get(k, 0) <= 1}
    target_chunks = sorted({v.get("_source_chunk_index") for k, v in merged.items()
                            if k in low_keys and isinstance(v.get("_source_chunk_index"), int)})
    text_by_idx = {idx: text for idx, text, _d, _h in prepared}
    buf = ""
    for idx in target_chunks:
        piece = f"[切片 {idx}] " + str(text_by_idx.get(idx, ""))[:_STITCH_TARGET_CHARS]
        if len(buf) + len(piece) > _STITCH_MAX_CHARS_PER_SEG and buf:
            segs.append(buf)
            buf = ""
            if len(segs) >= max_calls:
                return segs
        buf = (buf + "\n\n" + piece) if buf else piece
    if buf and len(segs) < max_calls:
        segs.append(buf)
    # ── ② 顺序段兜底（剩余预算）──
    remain = max_calls - len(segs)
    if remain > 0:
        doc_text = _stitch_doc_text(prepared)
        seq = [doc_text[i:i + _STITCH_MAX_CHARS_PER_SEG]
               for i in range(0, len(doc_text), _STITCH_MAX_CHARS_PER_SEG)][:remain]
        segs.extend(seq)
    return segs


def _stitch_doc_text(prepared: list[tuple[int, str]]) -> str:
    """按切片均摊预算摘录文档内容：每片取头部，总预算受限，覆盖前后所有切片。"""
    if not prepared:
        return ""
    per = max(300, _STITCH_MAX_CHARS_TOTAL // len(prepared))
    parts = []
    used = 0
    for idx, text, _doc, _h in prepared:
        if used >= _STITCH_MAX_CHARS_TOTAL:
            break
        piece = text.strip()[:per]
        parts.append(f"[切片 {idx}] {piece}")
        used += len(piece)
    return "\n".join(parts)


# ── 严格白名单模式（用户上传主体配置模板）：prompt 强约束 + 归并结果硬过滤双保险 ──

STRICT_GUIDANCE_HEADER = (
    "严格白名单模式：只允许抽取下列配置中列出的主体类、属性与关系；"
    "文档中出现但配置未列出的概念一律不抽取、不扩展、不归纳、不造同义类。"
)

def parse_strict_config(strict_config: Optional[list[dict]]) -> Optional[dict]:
    """把前端上传的主体配置行解析为白名单结构。

    行形状 {class: str, properties: str(逗号分隔)|空, relations: str(逗号分隔)|空}。
    语义：class 列表=类白名单（必含）；某类的 properties 非空=该类属性白名单，
    空=该类属性不设限；relations 全部为空=关系不设限，否则=全局关系谓词白名单。
    无有效类行返回 None（调用方按非严格模式处理）。
    """
    class_norms: set[str] = set()
    props_by_class: dict[str, set[str]] = {}
    rel_norms: set[str] = set()
    has_rel_spec = False
    for row in strict_config or []:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("class") or "").strip()
        if not cls:
            continue
        key = normalize_label(cls)
        class_norms.add(key)
        props = str(row.get("properties") or "").strip()
        if props:
            props_by_class[key] = {normalize_label(p.strip())
                                   for p in _split_config_list(props) if p.strip()}
        rels = str(row.get("relations") or "").strip()
        if rels:
            has_rel_spec = True
            rel_norms.update(normalize_label(r.strip())
                             for r in _split_config_list(rels) if r.strip())
    if not class_norms:
        return None
    return {"class_norms": class_norms,
            "props_by_class": props_by_class,
            "rel_norms": rel_norms if has_rel_spec else None}


def _split_config_list(s: str) -> list[str]:
    """配置行里的列表字段：逗号/中文逗号/顿号/分号/竖线都接受。"""
    import re as _re
    return [p for p in _re.split(r"[,，、;；|]", s or "") if p.strip()]


def apply_strict_filter(merged: dict[str, dict], obj_props: dict[str, dict],
                        dt_props: dict[str, dict], wl: dict) -> dict:
    """按白名单就地过滤归并产物，返回过滤计数（prompt 层拦不住的在这里兜底）。"""
    filtered = {"classes": 0, "relations": 0, "properties": 0}
    class_norms: set[str] = wl["class_norms"]
    props_by_class: dict[str, set[str]] = wl["props_by_class"]
    rel_norms: Optional[set[str]] = wl["rel_norms"]

    for key in [k for k in merged if k not in class_norms]:
        merged.pop(key)
        filtered["classes"] += 1

    kept_prop_norms: set[str] = set()
    for key, c in merged.items():
        spec = props_by_class.get(key)
        if not spec:
            for p in c.get("properties") or []:
                kept_prop_norms.add(normalize_label(str(p.get("name") or "")))
            continue
        props = c.get("properties") or []
        kept = [p for p in props if normalize_label(str(p.get("name") or "")) in spec]
        filtered["properties"] += len(props) - len(kept)
        c["properties"] = kept
        for p in kept:
            kept_prop_norms.add(normalize_label(str(p.get("name") or "")))

    for key in [k for k in obj_props]:
        op = obj_props[key]
        d = normalize_label(str(op.get("domain") or ""))
        g = normalize_label(str(op.get("range") or ""))
        if d not in class_norms or g not in class_norms or \
                (rel_norms is not None and normalize_label(str(op.get("label") or "")) not in rel_norms):
            obj_props.pop(key)
            filtered["relations"] += 1

    if props_by_class:  # 任一类设了属性白名单才过滤 schema 级 datatype_properties
        for key in [k for k in dt_props]:
            name = str(dt_props[key].get("name") or "")
            if normalize_label(name) not in kept_prop_norms:
                dt_props.pop(key)
                filtered["properties"] += 1
    return filtered


def extract_schema(chunks: list, base_uri: str,
                   request_interval: float = 0.0,
                   model_override: Optional[dict] = None,
                   model_config_id: Optional[int] = None,
                   known_classes: Optional[list[str]] = None,
                   use_cache: bool = True,
                   llm_call: Optional[Callable] = None,
                   progress_cb: Optional[Callable] = None,
                   stitch_progress_cb: Optional[Callable] = None,
                   cancel_cb: Optional[Callable] = None,
                   guidance: Optional[str] = None,
                   strict_config: Optional[list[dict]] = None) -> SchemaExtractionResult:
    """分块并行抽取 TBox → 归并去重（label_normalized）→ 跨切片关系缝合 → 悬空引用建占位类标记 pending。

    - 并行线程预算统一取 settings.LLM_MAX_CONCURRENCY（与全局模型并发闸同源）
    - (model, prompt_hash) Redis 8h 缓存（修复缺陷 #9，use_cache=False 可关）；
      guidance 注入用户 prompt，随 prompt 哈希进缓存键（改引导自动换缓存）
    - strict_config（主体配置模板行）：严格白名单模式——prompt 头改为强约束措辞，
      归并与缝合后按白名单硬过滤（超出的类/属性/关系一律丢弃），防大模型抽取发散
    - 归并后做跨切片关系缝合：全类清单 + 文档摘录补一轮 LLM 调用，连上分散在
      不同切片的类间关系（只连已知类，需原文证据；stitched_relations 记数）
    - 本地校验失败带错误反馈重试 1 次，仍失败计 warning
    - progress_cb(done, total, cache_hits)；cancel_cb() 抛异常即中止（Celery 取消用）
    model_config_id：显式指定 model_configs 行（用户个人选择，API 层已校验可见性）；
    llm_call 注入用于测试；生产走 resolve_provider(EXTRACT) + LLMClient。
    """
    result = SchemaExtractionResult()
    _phases: dict = {}
    prepared = _chunk_iter(chunks)
    result.chunks_processed = len(prepared)
    if not prepared:
        return result

    if llm_call is None:
        model_name, llm_call = _default_llm_call(model_override, model_config_id)
    else:
        model_name = str((model_override or {}).get("model_name") or "injected")
    known: list[str] = list(known_classes or [])

    wl = parse_strict_config(strict_config)
    guidance_text = (guidance or "").strip()
    if wl is not None and guidance_text:
        guidance_text = STRICT_GUIDANCE_HEADER + "\n" + guidance_text

    def one(idx: int, text: str, table_header: str = ""):
        key = _cache_key(model_name, SYSTEM_PROMPT,
                         _user_prompt(text, known, chunk_no=idx, guidance=guidance_text,
                                      strict=wl is not None, table_header=table_header))
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                return idx, cached, True, ""
        feedback = ""
        last_err = "未知错误"
        for _attempt in range(2):  # 首次 + 带错误反馈重试 1 次
            if cancel_cb is not None:
                cancel_cb()  # 取消后不再发起新的调用（在途那次返回后立即中断）
            try:
                raw = llm_call(SYSTEM_PROMPT,
                               _user_prompt(text, known, feedback, chunk_no=idx,
                                            guidance=guidance_text, strict=wl is not None,
                                            table_header=table_header),
                               SCHEMA_PROMPT_JSON_SCHEMA)
            except LLMEndpointUnreachableError:
                raise  # 端点不可达（熔断）：中止整段抽取，快速失败给人话报错
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
    biz_rules: dict[str, dict] = {}    # norm(statement) → 采集表规则行
    done = 0
    cache_hits = 0

    _t_parallel = time.perf_counter()
    with ThreadPoolExecutor(max_workers=_llm_max_workers()) as pool:
        doc_by_idx = {idx: doc for idx, _text, doc, _h in prepared}
        # ★ 确定性关键①：先按切片序收齐全部结果，再按【固定切片顺序】归并——
        #   不用 as_completed 的完成序（线程调度随机）：完成序会打乱 merged 插入序
        #   → 类清单顺序变 → 缝合 prompt 变 → 缓存 miss + 缝合结果漂移
        #   （2026-11-05 确定性实测：缓存 8/8 命中仍出现 27→29 关系漂移的根因）
        # 滑动窗口：始终 _llm_max_workers() 个在途、完成一个补一个（2026-11-12）。
        # 旧版“每批等最慢的”会让 4 路只有 2.4 路有效；窗口调度只去掉批屏障，
        # 不提高并发上限，取消语义不变（每完成一个都检查 cancel_cb）。
        results_by_idx: dict[int, tuple] = {}

        def _on_chunk_done(item, payload_result):
            nonlocal done, cache_hits
            idx, payload, hit, err = payload_result
            done += 1
            results_by_idx[idx] = (idx, payload, hit, err)
            cache_hits += 1 if hit else 0
            if payload is None:
                result.warnings.append(f"chunk #{idx} 骨架抽取失败（已重试）: {err}")
            if progress_cb is not None:
                progress_cb(done, len(prepared), cache_hits)

        _run_sliding_window(
            pool, prepared,
            lambda item: submit_with_context(pool, one, item[0], item[1], item[3]),
            _on_chunk_done,
            max_workers=_llm_max_workers(), request_interval=request_interval,
            cancel_cb=cancel_cb)

        _phases["parallel_extract_seconds"] = round(
            time.perf_counter() - _t_parallel, 6)
        _t_merge = time.perf_counter()
        for idx in sorted(results_by_idx):
            idx, payload, hit, err = results_by_idx[idx]
            if payload is None:
                continue
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
                    if d and g and d == g:
                        continue  # 自环（两端同类）：抽取噪声，丢弃
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
            # 语义采集表·规则层归并：按规范语句去重（同规则多片出现只留一条）
            for br in payload.get("business_rules") or []:
                if not isinstance(br, dict):
                    continue
                stmt = str(br.get("statement") or "").strip()[:300]
                if not stmt:
                    continue
                biz_rules.setdefault(normalize_label(stmt), {
                    "statement": stmt,
                    "kind": str(br.get("kind") or "约束"),
                    "evidence": str(br.get("evidence") or "")[:500],
                    "_source_chunk_index": idx,
                })

    # ★ 确定性关键②：类清单按（出现切片数降序, 规范名）排序——下游 prompt
    #   （缝合/实例抽取/公理扫描）与产物顺序稳定，LLM 缓存键才可复现
    classes = sorted(merged.values(),
                     key=lambda v: (-v.get("_count", 1), normalize_label(v["label"])))

    # ── 严格白名单硬过滤：prompt 约束拦不住的发散产出在这里兜底丢弃（缝合前过滤，
    #    缝合的类清单/关系谓词同样受限）──
    if wl is not None:
        result.strict_filtered = apply_strict_filter(merged, obj_props, dt_props, wl)

    _phases["merge_seconds"] = round(time.perf_counter() - _t_merge, 6)
    _t_assemble = time.perf_counter()
    # ── 语义归一化：相似概念聚成概念（别名降级），保证骨架不产同义类 ──
    result.normalized_concepts = _semantic_normalize(merged, obj_props)
    # ── 边清单型表格推断关系（列取值互指 → 列即关系），补关系清单空白 ──
    result.table_inferred_relations = _infer_table_edge_relations(prepared, obj_props)
    classes = sorted(merged.values(),
                     key=lambda v: (-v.get("_count", 1), normalize_label(v["label"])))

    # ── 骨架组装（孤岛根修）：自报 parent 落成层次 + LLM 裁决伪类/孤岛类 ──
    # 顺序刻意放在缝合之前：伪类剔除后缝合面对的类清单更干净，不会白连已删类；
    # 严格白名单模式跳过组装（白名单即唯一范围，不许删也不许挂）
    result.consumed_parents = _consume_parents(merged)
    if wl is None:
        try:
            result.pruned_classes, result.assembled_parents = _assemble_taxonomy(
                merged, obj_props, llm_call, model_name,
                use_cache=use_cache, cancel_cb=cancel_cb, prepared=prepared)
        except Exception as e:  # noqa: BLE001 —— 组装是增强不是闸门，失败退回原形态
            result.warnings.append(f"骨架组装跳过: {e}")
    _phases["assemble_seconds"] = round(time.perf_counter() - _t_assemble, 6)
    _t_structure = time.perf_counter()
    classes = sorted(merged.values(),
                     key=lambda v: (-v.get("_count", 1), normalize_label(v["label"])))

    # ── 结构映射（外部 get_* 登记表模式的前置决策）：表格表头/条款编号 →
    #    行主体类 + 列→关系/属性。建模期定一次，实例阶段逐行确定性应用（零 LLM）。
    #    映射新声明的谓词正式进 TBox（关系契约先行）。失败/无结构信号静默跳过。
    from app.adapters.structure_expand import generate_structure_maps

    try:
        maps, map_calls = generate_structure_maps(
            prepared, [c["label"] for c in classes], obj_props, llm_call,
            model_name, use_cache=use_cache, cancel_cb=cancel_cb)
        result.structure_maps = maps
        result.structure_map_calls = map_calls
        # 映射新增的关系声明 → TBox（列即关系的正式入约）
        for m_ in maps:
            for col in m_.get("columns") or []:
                if col.get("kind") != "relation":
                    continue
                pred, dom, rng = col.get("predicate"), m_["subject_class"], col.get("target_class")
                if not (pred and dom and rng):
                    continue
                rk = f"{normalize_label(pred)}|{normalize_label(dom)}|{normalize_label(rng)}"
                if rk not in obj_props and normalize_label(pred) not in {
                        normalize_label(str(o.get("label") or "")) for o in obj_props.values()}:
                    obj_props[rk] = {"label": pred, "domain": dom, "range": rng,
                                     "confidence": 0.9,
                                     "evidence": "结构映射：列「" + col.get("column") + "」为引用列"}
    except Exception as e:  # noqa: BLE001 —— 映射是增强，失败退回纯 LLM 形态
        result.warnings.append(f"结构映射跳过: {e}")
    _phases["structure_maps_seconds"] = round(time.perf_counter() - _t_structure, 6)
    _t_stitch = time.perf_counter()
    classes = sorted(merged.values(),
                     key=lambda v: (-v.get("_count", 1), normalize_label(v["label"])))

    # ── 跨切片关系缝合：切片独立抽取只连得到同片共现的关系，这里归并后统一补一轮 ──
    # 依据 = 文档内容摘录（evidence 逐字摘录），只连类清单里已有的类，不造新类。
    stitched = 0
    if len(classes) >= 2 and len(prepared) >= 2:
        class_list_text = _stitch_class_list(classes)
        norm2label = {k: v["label"] for k, v in merged.items()}
        for _v in merged.values():  # 别名也解析到规范名（缝合/归并后谓词端点不丢）
            for _a in _v.get("aliases") or []:
                norm2label.setdefault(normalize_label(_a), _v["label"])
        existing_rk = {(normalize_label(v["label"]), normalize_label(v["domain"]),
                        normalize_label(v["range"])) for v in obj_props.values()}
        # 调用上限随切片数扩展（基础 8 + 每 25 切片加一，封顶 24）；
        # 素材分段缺口驱动（低连接类首现切片优先）+ 顺序兜底
        max_calls = min(_STITCH_MAX_CALLS_CAP,
                        _STITCH_MAX_CALLS + len(prepared) // _STITCH_CALLS_PER_CHUNKS)
        segs = _stitch_segments(prepared, merged, obj_props, max_calls)

        def _stitch_one(si: int, seg: str):
            """单段缝合（线程内执行）：缓存查询 + 重试一次。返回 (si, rels|None, err)。"""
            skey = _cache_key(model_name, STITCH_SYSTEM_PROMPT,
                              _stitch_user_prompt(class_list_text, seg))
            rels = _cache_get(skey) if use_cache else None
            if rels is None:
                feedback = ""
                for _attempt in range(2):  # 首次 + 带错误反馈重试 1 次
                    if cancel_cb is not None:
                        cancel_cb()  # 取消后不再发起新的缝合调用（在途那次返回后中断）
                    try:
                        raw = llm_call(STITCH_SYSTEM_PROMPT,
                                       _stitch_user_prompt(class_list_text, seg, feedback),
                                       STITCH_JSON_SCHEMA)
                    except LLMEndpointUnreachableError:
                        raise  # 端点不可达：中止整段缝合（快速失败）
                    except Exception as _e:  # noqa: BLE001
                        rels, feedback = None, f"LLM调用异常: {_e}"
                        continue
                    rels, feedback = _validate_stitch_payload(raw)
                    if rels is not None:
                        break
                if rels is None:
                    return si, None, feedback
                if use_cache:
                    _cache_put(skey, rels)
            return si, rels, ""

        # 并行执行（段间独立），结果按段序应用——并发只加速 IO，不破坏确定性
        seg_results: dict[int, tuple] = {}
        done_si = 0
        with ThreadPoolExecutor(max_workers=_llm_max_workers()) as spool:
            futures = {submit_with_context(spool, _stitch_one, si, seg): si
                       for si, seg in enumerate(segs)}
            for fut in as_completed(futures):
                if cancel_cb is not None:
                    cancel_cb()
                si, rels, err = fut.result()
                seg_results[si] = (si, rels, err)
                done_si += 1
                if stitch_progress_cb is not None:
                    stitch_progress_cb(done_si, len(segs))
        for si in sorted(seg_results):
            si, rels, err = seg_results[si]
            if rels is None:
                result.warnings.append(f"关系缝合段 #{si} 失败（已重试）: {err}")
                continue
            for r in rels:
                label = str(r.get("label") or "").strip()
                d = normalize_label(str(r.get("domain") or "").strip())
                g = normalize_label(str(r.get("range") or "").strip())
                if d == g or d not in norm2label or g not in norm2label:
                    continue  # 缝合只连已知类，不造类不自环
                if wl is not None and wl["rel_norms"] is not None \
                        and normalize_label(label) not in wl["rel_norms"]:
                    continue  # 严格模式：缝合关系谓词同样必须在白名单内
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
    result.stitch_segments = len(segs) if len(classes) >= 2 and len(prepared) >= 2 else 0
    _phases["stitch_seconds"] = round(time.perf_counter() - _t_stitch, 6)

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
    # 采集表·规则层：按语句排序（确定性），上限 200 条
    result.business_rules = [biz_rules[k] for k in sorted(biz_rules)][:200]
    # 终产物同样规范化排序（写进 schema/graph_data 的顺序稳定 → QA 检索/导出/比对全链确定）
    result.object_properties = sorted(obj_props.values(),
                                      key=lambda v: normalize_label(v["label"]))
    result.datatype_properties = sorted(dt_props.values(),
                                        key=lambda v: normalize_label(v["name"]))
    result.cache_hits = cache_hits
    _phases["cache_hits"] = cache_hits
    _phases["chunks"] = len(prepared)
    profile_metric("schema_phases", _phases)
    return result


# ── 语义归一化：相似概念聚成一个类（semantica class_inferrer 思路，确定性零 LLM）──
# 背景：并行切片抽取对同一概念常给出略异名称（"日志中心"/"日志中心系统"），
# 仅按 normalize_label 精确归并会留下同义类，骨架重复且后续实例分类漂移。
# 防误并口径（消解事故教训）：短名（<4 字）不参与；仅「高相似（≥0.84）」或
# 「泛化尾词包含」（短名 + 服务/系统/平台/中心/模块/应用/类）两种情形；
# 谓词归并阈值更严（≥0.90，不含包含规则）。

_GENERIC_TAILS = ("服务", "系统", "平台", "中心", "模块", "应用", "类")
_CLASS_SIM_THRESHOLD = 0.87
_PRED_SIM_THRESHOLD = 0.90


def _same_concept(a: str, b: str) -> bool:
    """类名级同义判定（纯函数）。"""
    import difflib

    if min(len(a), len(b)) < 4:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() >= _CLASS_SIM_THRESHOLD:
        return True
    for short, long_ in ((a, b), (b, a)):
        if len(short) >= 4 and long_.startswith(short) and long_[len(short):] in _GENERIC_TAILS:
            return True
    return False


def _same_predicate(a: str, b: str) -> bool:
    import difflib

    if min(len(a), len(b)) < 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= _PRED_SIM_THRESHOLD


def _semantic_normalize(merged: dict[str, dict], obj_props: dict[str, dict]) -> int:
    """相似概念聚成一类：簇内取出现切片数最多者为规范名，其余降别名
    （properties/definition 并入）；谓词 domain/range 端点重映射后重建键去重。
    返回并入别名的类数。确定性：排序后按序比较，同输入同结果。"""
    keys = sorted(merged.keys())  # 排序保证聚类顺序确定
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(keys):
        la = merged[a]["label"]
        for b in keys[i + 1:]:
            if _same_concept(la, merged[b]["label"]):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    clusters: dict[str, list[str]] = {}
    for k in keys:
        clusters.setdefault(find(k), []).append(k)

    merged_n = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        # 规范名：出现切片数多者优先（文档中更核心），其次更长（更具体）
        members.sort(key=lambda k: (-(merged[k].get("_count") or 0),
                                    -len(merged[k]["label"]), k))
        canon = members[0]
        for k in members[1:]:
            src = merged.pop(k)
            tgt = merged[canon]
            for alias in [src["label"]] + list(src.get("aliases") or []):
                if alias and alias != tgt["label"] and alias not in tgt["aliases"]:
                    tgt["aliases"].append(alias)
            for prop in src.get("properties") or []:
                nm = normalize_label(prop.get("name") or "")
                if nm and nm not in {normalize_label(q.get("name") or "")
                                     for q in tgt["properties"]}:
                    tgt["properties"].append(prop)
            if not tgt.get("definition") and src.get("definition"):
                tgt["definition"] = src["definition"]
            merged_n += 1

    if merged_n:
        # 别名 → 规范名 映射，谓词端点重映射 + 键重建去重
        alias2canon: dict[str, str] = {}
        for c in merged.values():
            for a in c.get("aliases") or []:
                alias2canon.setdefault(normalize_label(a), c["label"])
        rebuilt: dict[str, dict] = {}
        for op in obj_props.values():
            d = alias2canon.get(normalize_label(op.get("domain") or ""), op.get("domain") or "")
            g = alias2canon.get(normalize_label(op.get("range") or ""), op.get("range") or "")
            op["domain"], op["range"] = d, g
            if normalize_label(d) == normalize_label(g):
                continue  # 端点归并后自环（原两端本是同义类）→ 丢弃
            rk = (f"{normalize_label(op['label'])}|{normalize_label(d)}|{normalize_label(g)}")
            rebuilt.setdefault(rk, op)
        obj_props.clear()
        obj_props.update(rebuilt)
    return merged_n


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def _infer_table_edge_relations(prepared: list, obj_props: dict[str, dict]) -> int:
    """边清单型表格 → 关系推断（semantica 数据驱动思路，确定性零 LLM）。

    判据：某列的取值 ≥50% 出现在其他列的取值集合里（值本身也是实体）→
    该列是一条关系，列名（剥掉「系统/名称」尾词）作谓词，如
    「系统名称|下游系统」→ 下游（系统→系统）。没有这一步，清单型文档
    抽不出关系声明，实例侧"禁止发明关系"守卫会把上下游链路整体挡掉。
    """
    import re as _re

    def _cells(text: str) -> tuple[list[str], list[list[str]]]:
        header: list[str] | None = None
        rows: list[list[str]] = []
        for line in text.split("\n"):
            s = line.strip()
            if not (s.startswith("|") and s.endswith("|")):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells or all(not c for c in cells):
                continue
            if _re.fullmatch(r"[\-: ]+", "".join(cells)):
                continue  # 分隔行
            if header is None:
                header = cells
            else:
                rows.append(cells)
        return (header or []), rows

    col_values: dict[str, set[str]] = {}   # 列名 → 取值集合（跨表块聚合）
    col_order: list[str] = []
    subject_cols: set[str] = set()         # 每表首列 = 行主体列，不作关系候选
    for _idx, text, _doc, _hdr in prepared:
        header, rows = _cells(text)
        if not header or len(header) < 2 or not rows:
            continue
        if str(header[0]).strip():
            subject_cols.add(str(header[0]).strip())
        for ci, name in enumerate(header):
            name = str(name).strip()
            if not name or name in col_values:
                continue
            col_order.append(name)
            col_values[name] = set()
        for row in rows:
            for ci, name in enumerate(header):
                if ci >= len(row):
                    continue
                name = str(header[ci]).strip()
                if not name:
                    continue
                for v in _re.split(r"[;；,，、/]", str(row[ci] or "")):
                    v = v.strip()
                    if len(v) >= 2:
                        col_values[name].add(v)

    added = 0
    for name in col_order:
        if name in subject_cols:
            continue  # 行主体列不是关系
        # 列名本身是纯编码（产品号"0103"、接口号"201A"等）→ 它是数据列不是关系：
        # 产品矩阵表的"表头"其实是数据行，列名全变成编码谓词污染关系清单
        # （项目 265 实测：16 条 "0103"→"0129" 编码谓词进了 schema）
        if not _has_cjk(name):
            continue
        values = col_values.get(name) or set()
        if len(values) < 2:
            continue
        others: set[str] = set()
        for other_name, vals in col_values.items():
            if other_name != name:
                others |= vals
        if not others:
            continue
        hit = sum(1 for v in values if v in others)
        if hit / len(values) < 0.5:
            continue  # 取值自成一体（属性列），不是实体引用列
        label = str(name)
        for suffix in ("系统", "名称"):
            if len(label) > 2 and label.endswith(suffix):
                label = label[:-len(suffix)]
                break
        key = f"{normalize_label(label)}||"
        if key not in obj_props and normalize_label(label) not in {
                normalize_label(str(o.get("label") or "")) for o in obj_props.values()}:
            obj_props[key] = {"label": label, "domain": None, "range": None,
                              "confidence": 0.8,
                              "evidence": f"表格列「{name}」取值与其他列互指（边清单型）"}
            added += 1
    return added


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
            entry = {"name": name,
                     "data_type": str((p or {}).get("data_type") or "string"),
                     "description": str((p or {}).get("description") or "")}
            # 约束字段（方案三约束通道）：原文写明的枚举/必填/基数随属性透传
            for k in ("enum_values", "required", "min_occurs", "max_occurs"):
                if (p or {}).get(k) not in (None, [], {}):
                    entry[k] = p[k]
            props.append(entry)
    if key in merged:
        tgt = merged[key]
        tgt["_count"] = tgt.get("_count", 1) + 1  # 出现切片数（语义归一化的规范名选取依据）
        for p in props:
            hit = next((q for q in tgt["properties"]
                        if normalize_label(q["name"]) == normalize_label(p["name"])), None)
            if hit is None:
                tgt["properties"].append(p)
                continue
            # 约束并集：枚举取值合并去重、必填取或、基数取宽（min 取小 max 取大）
            if p.get("enum_values"):
                merged_enum = list(hit.get("enum_values") or [])
                for v in p["enum_values"]:
                    if v not in merged_enum:
                        merged_enum.append(v)
                hit["enum_values"] = merged_enum
            if p.get("required"):
                hit["required"] = True
            if isinstance(p.get("min_occurs"), int):
                hit["min_occurs"] = min(hit.get("min_occurs", p["min_occurs"]), p["min_occurs"])
            if isinstance(p.get("max_occurs"), int):
                hit["max_occurs"] = max(hit.get("max_occurs", p["max_occurs"]), p["max_occurs"])
        if not tgt.get("definition") and c.get("definition"):
            tgt["definition"] = str(c["definition"])
        return
    merged[key] = {
        "label": label,
        "definition": str(c.get("definition") or ""),
        "properties": props,
        "parent": c.get("parent") or None,
        "aliases": [],
        "_count": 1,
        # 首现切片的溯源来源（保留先到者）
        "_source_chunk_index": c.get("_source_chunk_index"),
        "_source_doc": str(c.get("_source_doc") or ""),
    }


# ── 骨架组装（孤岛根修，2026-11-08）：自报 parent 落层次 + LLM 裁决伪类/孤岛 ──
# 背景：切片 prompt 一直要求每个类自报 parent，但归并后无人消费——层次声明全部
# 蒸发，骨架只剩孤立标签（项目 265 实测：79 类仅 12 条 subclass_of 且全部来自
# 公理扫描事后补，9 类零连接）。外部方法论（29 句话 skill）对照：单根层次深度 3、
# 全图单连通分量。修复分两层：
#   ① _consume_parents：契约字段落成 parent_classes，确定性零 LLM；
#   ② _assemble_taxonomy：一次预算内 LLM 调用裁决伪类（动作/页面/菜单词被误抽成
#      类）与孤岛类（挂父类或并入通用类），对齐 pipeline_selfcheck 的最小动作口径。

ASSEMBLE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "description": "骨架组装动作集",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["set_parent", "merge_alias", "remove_class"]},
                    "label": {"type": "string",
                              "description": "目标类名，必须逐字取自【类清单】"},
                    "into": {"type": "string",
                             "description": "merge_alias：并入的已有类名"},
                    "parent": {"type": "string",
                               "description": "set_parent：父类名，必须逐字取自【类清单】"},
                    "reason": {"type": "string", "description": "一句话依据"},
                },
                "required": ["action", "label"],
            },
        },
    },
    "required": ["actions"],
}

ASSEMBLE_SYSTEM_PROMPT = (
    "你是资深本体建模专家。下面是一份由业务文档切片独立抽取后归并的类清单"
    "（含定义、当前父类、关系连接数），以及文档摘录（表格列名与内容抽样）。"
    "常见三类问题需要你裁决：\n"
    "1. 伪类：动词/操作/界面控件词被误抽成类（如「经办」「查询」「审核」「二级菜单」"
    "「页面」「进度查询」）——它们是对事物的动作或界面元素，不是业务事物的分类；\n"
    "2. 孤岛类：连接数为 0 且无父类的类，需要挂到有业务依据的父类下，"
    "或并入更通用的已有类；\n"
    "3. 层次缺失：明显存在上下位关系的类没挂上（如「流动资金贷款合同」⊑「贷款合同」）。\n"
    "规则：\n"
    "1. 动作只限 set_parent（给类指定父类）、merge_alias（把类并入更通用的已有类作别名）、"
    "remove_class（删除完全无业务意义的伪类）；禁止发明新类；\n"
    "2. 【数据行主体类必须保留】若文档摘录显示某个表格列/清单的取值是具体个体"
    "（系统名、接口编码、规则条目、产品编号、字段名），对应该列语义的类（如交互系统、"
    "接口、业务规则、业务字段、产品）是数据行的主体，必须保留——它们是文档事实层的"
    "锚点，删掉会导致整份文档抽不出实例；只有取值全是属性值/状态词的列名才可删；\n"
    "3. label/parent/into 必须逐字取自类清单；\n"
    "4. 每条动作给出业务依据（reason），拿不准就不要动——宁少勿多；\n"
    "5. 顶层若缺领域根类概念，可把相互孤立的类归到清单中已有的最通用类下，不要造新根。\n"
    "只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema。"
)

_ASSEMBLY_MAX_CLASSES = 150
_ASSEMBLY_MAX_CHARS = 12000   # 类清单部分预算（对齐 pipeline_selfcheck 素材预算口径）
_ASSEMBLY_EXCERPT_CHARS = 8000  # 文档摘录部分预算（列名+内容抽样）
_ASSEMBLY_MAX_ACTIONS = 60
_ASSEMBLY_DEF_CHARS = 40


def _consume_parents(merged: dict[str, dict]) -> int:
    """把每类自报的 parent 契约字段落成 parent_classes（确定性零 LLM）。

    父类能对上（含别名解析）→ parent_classes；对不上 → 建 pending 占位类
    （与关系悬空引用同口径，schema_checks 的 dangling_parent_ref 才不会报错）；
    自父 → 丢弃。同步保留 parent 键（versioning 快照 diff 读它）。
    """
    if not merged:
        return 0
    norm2label: dict[str, str] = {}
    for v in merged.values():
        norm2label.setdefault(normalize_label(v["label"]), v["label"])
        for a in v.get("aliases") or []:
            norm2label.setdefault(normalize_label(a), v["label"])
    linked = 0
    for key in sorted(merged.keys()):
        c = merged[key]
        parent_raw = str(c.get("parent") or "").strip()
        c.pop("parent", None)
        if not parent_raw:
            continue
        pnorm = normalize_label(parent_raw)
        target = norm2label.get(pnorm)
        if target is not None and target == c["label"]:
            continue  # 自父（或经别名解析回自己）：丢弃
        if target is None:
            # 父类引用悬空 → 占位类 pending，后续缝合/实例阶段仍可给它连关系
            merged[pnorm] = {"label": parent_raw, "definition": "", "properties": [],
                             "aliases": [], "pending": True}
            norm2label[pnorm] = parent_raw
            target = parent_raw
        pc = c.setdefault("parent_classes", [])
        if target not in pc:
            pc.append(target)
        c["parent"] = target  # 与 parent_classes 同源（versioning diff 兼容）
        linked += 1
    return linked


def _class_degree(label: str, obj_props: dict[str, dict]) -> int:
    nl = normalize_label(label)
    deg = 0
    for op in obj_props.values():
        if normalize_label(str(op.get("domain") or "")) == nl \
                or normalize_label(str(op.get("range") or "")) == nl:
            deg += 1
    return deg


def _assemble_doc_excerpt(prepared: Optional[list]) -> str:
    """组装用文档摘录：全部表格列名 + 每片头部抽样（总预算内）。

    组装 LLM 必须能看到"列的取值是什么"才能区分两类列：取值是具体个体的
    （交互系统列=COFP/NCRM… → 该列语义是类，必须保留）vs 取值是属性值的
    （二级菜单列=贷款申请/合同签订 → 列名只是属性维度，可降级）。
    没有这段上下文，组装会把数据行主体类当伪类清掉（2026-11-08 项目 265
    实测：BP接口/交互系统/前端业务规则被删，实例抽取 31 切片全空）。
    """
    if not prepared:
        return ""
    headers: list[str] = []
    seen_h: set[str] = set()
    total = 0
    for _idx, _text, _doc, hdr in prepared:
        h = str(hdr or "").strip()
        if h and h not in seen_h:
            seen_h.add(h)
            headers.append(h)
            total += len(h)
    parts = []
    per = max(200, _ASSEMBLY_EXCERPT_CHARS // max(len(prepared), 1))
    for idx, text, _doc, _hdr in prepared:
        if total >= _ASSEMBLY_EXCERPT_CHARS:
            break
        piece = text.strip()[:per]
        parts.append(f"[切片 {idx}] {piece}")
        total += len(piece)
    out = ""
    if headers:
        out += "【表格列名】\n" + "\n".join(f"- {h}" for h in headers[:40]) + "\n\n"
    out += "【文档内容抽样】\n" + "\n".join(parts)
    return out


def _assemble_material(merged: dict[str, dict], obj_props: dict[str, dict],
                       prepared: Optional[list] = None) -> str:
    """组装素材：类清单（定义/父类/连接数）+ 文档摘录，预算内截断（确定性）。"""
    rows = []
    chars = 0
    for key in sorted(merged.keys(), key=lambda k: normalize_label(merged[k]["label"])):
        c = merged[key]
        label = c["label"]
        d = str(c.get("definition") or "").strip()
        if len(d) > _ASSEMBLY_DEF_CHARS:
            d = d[:_ASSEMBLY_DEF_CHARS] + "…"
        parents = "、".join(c.get("parent_classes") or [])
        entry = {"label": label}
        if d:
            entry["定义"] = d
        if parents:
            entry["父类"] = parents
        entry["连接数"] = _class_degree(label, obj_props)
        if c.get("pending"):
            entry["占位"] = True
        line = str(entry)
        if chars + len(line) > _ASSEMBLY_MAX_CHARS or len(rows) >= _ASSEMBLY_MAX_CLASSES:
            rows.append({"note": "（素材截断）"})
            break
        rows.append(entry)
        chars += len(line)
    material = "【类清单】\n" + "\n".join(str(r) for r in rows)
    excerpt = _assemble_doc_excerpt(prepared)
    if excerpt:
        material += "\n\n" + excerpt
    return material


def _ancestor_has(label: str, start: str, merged: dict[str, dict],
                  depth: int = 32) -> bool:
    """沿 parent_classes 向上走，判断 start 的祖先链是否含 label（防 set_parent 成环）。"""
    by_label = {v["label"]: v for v in merged.values()}
    cur = start
    seen = {cur}
    while depth > 0:
        depth -= 1
        node = by_label.get(cur)
        if node is None:
            return False
        for p in node.get("parent_classes") or []:
            if p == label:
                return True
            if p not in seen:
                seen.add(p)
                cur = p
                break
        else:
            return False
    return False


def _apply_assembly_action(merged: dict[str, dict], obj_props: dict[str, dict],
                           act: dict) -> Optional[str]:
    """校验并应用单条组装动作（返回人话描述；非法返回 None）。

    类定位按规范名解析（生产 merged 的 key 即 normalize_label(label)；
    兼容 key≠label 的场景按 label 值回查）。"""
    action = str(act.get("action") or "")
    label = str(act.get("label") or "").strip()
    if not label:
        return None
    by_label = {normalize_label(v["label"]): k for k, v in merged.items()}
    lnorm = normalize_label(label)
    lkey = by_label.get(lnorm)
    if lkey is None:
        return None

    def _drop_class(key: str) -> None:
        dead = merged.pop(key)
        dead_label = dead["label"]
        dnorm = normalize_label(dead_label)
        for rk in [rk for rk, op in obj_props.items()
                   if normalize_label(str(op.get("domain") or "")) == dnorm
                   or normalize_label(str(op.get("range") or "")) == dnorm]:
            obj_props.pop(rk, None)

    def _remap_endpoint(op: dict, field: str, frm: str, to: str) -> None:
        if normalize_label(str(op.get(field) or "")) == normalize_label(frm):
            op[field] = to

    if action == "remove_class":
        _drop_class(lkey)
        return f"删除伪类「{label}」"
    if action == "merge_alias":
        into = str(act.get("into") or "").strip()
        ikey = by_label.get(normalize_label(into))
        if not into or ikey is None or ikey == lkey:
            return None
        src = merged.pop(lkey)
        tgt = merged[ikey]
        for alias in [src["label"]] + list(src.get("aliases") or []):
            if alias and alias != tgt["label"] and alias not in tgt["aliases"]:
                tgt["aliases"].append(alias)
        for prop in src.get("properties") or []:
            nm = normalize_label(str(prop.get("name") or ""))
            if nm and nm not in {normalize_label(str(q.get("name") or ""))
                                 for q in tgt["properties"]}:
                tgt["properties"].append(prop)
        if not tgt.get("definition") and src.get("definition"):
            tgt["definition"] = src["definition"]
        for op in obj_props.values():
            _remap_endpoint(op, "domain", src["label"], tgt["label"])
            _remap_endpoint(op, "range", src["label"], tgt["label"])
        # 端点并入后自环的关系一并清掉
        for rk in [rk for rk, op in obj_props.items()
                   if normalize_label(str(op.get("domain") or ""))
                   == normalize_label(str(op.get("range") or ""))
                   and op.get("domain")]:
            obj_props.pop(rk, None)
        return f"把「{label}」并入「{into}」作别名"
    if action == "set_parent":
        parent = str(act.get("parent") or "").strip()
        pkey = by_label.get(normalize_label(parent))
        if not parent or pkey is None or pkey == lkey:
            return None
        c = merged[lkey]
        if parent in (c.get("parent_classes") or []):
            return None
        if _ancestor_has(label, parent, merged):
            return None  # 会成环：parent 的祖先链已含 label
        c.setdefault("parent_classes", []).append(parent)
        c["parent"] = parent
        return f"「{label}」挂到「{parent}」下"
    return None


def _assemble_taxonomy(merged: dict[str, dict], obj_props: dict[str, dict],
                       llm_call: Callable, model_name: str,
                       use_cache: bool = True,
                       cancel_cb: Optional[Callable] = None,
                       prepared: Optional[list] = None) -> tuple[int, int]:
    """骨架组装 LLM 裁决（单次调用，素材预算内）。返回 (剔除类数, 补挂父类数)。

    伪类裁并/孤岛挂接后，缝合阶段在更干净的类清单上连关系；调用失败/超预算
    不抛错（返回 0），骨架退化回无组装形态——组装是增强，不是闸门。
    """
    if len(merged) < 3:
        return 0, 0
    material = _assemble_material(merged, obj_props, prepared=prepared)
    key = _cache_key(model_name, ASSEMBLE_SYSTEM_PROMPT, material)
    cached = _cache_get(key) if use_cache else None
    actions = cached.get("actions") if isinstance(cached, dict) else cached
    if actions is not None and not isinstance(actions, list):
        actions = None
    if actions is None:
        feedback = ""
        for _attempt in range(2):
            if cancel_cb is not None:
                cancel_cb()
            try:
                raw = llm_call(ASSEMBLE_SYSTEM_PROMPT, material
                               + (f"\n\n【上次输出错误，必须修正】\n{feedback}"
                                  if feedback else ""),
                               ASSEMBLE_JSON_SCHEMA)
            except Exception:  # noqa: BLE001
                raw = None
            ok = isinstance(raw, dict) and isinstance(raw.get("actions"), list)
            if ok:
                actions = raw["actions"]
                break
            feedback = "输出必须是 {\"actions\": [...]} 且 action 取值合法"
        if actions is None:
            return 0, 0
        if use_cache:
            _cache_put(key, {"actions": actions})
    pruned = 0
    attached = 0
    for act in actions[:_ASSEMBLY_MAX_ACTIONS]:
        if not isinstance(act, dict):
            continue
        desc = _apply_assembly_action(merged, obj_props, act)
        if not desc:
            continue
        a = str(act.get("action") or "")
        if a in ("remove_class", "merge_alias"):
            pruned += 1
        elif a == "set_parent":
            attached += 1
    return pruned, attached


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
    "输出顶层结构固定为：{\"entities\": [...], \"relations\": [...]}——实体数组键名必须是 entities，"
    "实体元素必须含 label（实例名）与 class_label（类名）字段。\n"
    "规则：\n"
    "1. class_label 必须严格取自【本体类清单】；predicate 必须严格取自【关系清单】，不要发明新类/新关系。\n"
    "2. 实例必须是文中具体的、可与其他个体区分开的对象（具体名称、编号、单据、组织）——"
    "类本身、类的子类型、泛指的一类事物不是实例：例如类「结算账户」下的「基本存款账户」是子类概念，"
    "不能抽成实例；实例的 label 也不得与任何类名相同。"
    "若切片只是制度/定义/规则（没有任何具体个体），entities 返回空数组 []，这是正确答案，"
    "不要为凑数把类或子类降格成实例。"
    "同一实体的多种称呼都出现时各抽一次，后续消解会合并。\n"
    "3. 每个实例/关系必须给出 evidence：从切片中逐字摘录的原句；没有原文依据的不要抽。\n"
    "4. props：尽可能把实例在文中体现的属性值填入 props——键用【各类属性清单】中所属类的属性名，"
    "值为文中原值（数字/日期保留原样）；文中没有的属性不要编造；一个属性值都没有时 props 给空对象 {}。\n"
    "5. confidence 为 0-1 的自报置信度，如实评估，不要全给 1.0。\n"
    "6. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema，不要输出任何其他文字。"
)


def _prop_label(p: dict) -> str:
    """属性 prompt 标签：枚举/必填约束可见（约束通道下探到实例抽取）。"""
    txt = f"{p.get('name')}（{p.get('data_type', 'string')}"
    if p.get("enum_values"):
        txt += "，枚举：" + "、".join(str(v) for v in p["enum_values"][:8])
    if p.get("required"):
        txt += "，必填"
    mn, mx = p.get("min_occurs"), p.get("max_occurs")
    if isinstance(mn, int) or isinstance(mx, int):
        lo = mn if isinstance(mn, int) else 0
        hi = mx if isinstance(mx, int) else "n"
        txt += f"，数量 {lo}..{hi}"
    return txt + "）"


def _normalize_instance_payload(payload: dict) -> dict:
    """LLM 输出键漂移归一（同 _normalize_payload，实例契约版）。"""
    ents = payload.get("entities")
    if ents is None and isinstance(payload.get("instances"), list):
        # json_object 降级场景下模型常见顶层键漂移（instances/nodes）
        payload["entities"] = payload["instances"]
    payload.pop("instances", None)
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
    if ents is None:
        # 防静默丢实体：模型把实体放在未知键下（如 instances）时，不能当作 0 实体放行，
        # 要让重试反馈把键名纠正回来
        suspects = [k for k, v in payload.items()
                    if k not in ("entities", "relations") and isinstance(v, list)
                    and v and isinstance(v[0], dict)]
        if suspects:
            return None, f"缺少 entities 数组（实体疑似放在未知键 {suspects[0]} 下）"
        ents = []
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
                          chunk_no: int = 0, table_header: str = "") -> str:
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
            ptxt = "；".join(_prop_label(p) for p in (props or []) if p.get("name"))
            if ptxt:
                lines.append(f"- {c}：{ptxt}")
        if lines:
            parts.append("【各类属性清单】（实例的 props 键只能取自所属类这里的属性名）\n"
                         + "\n".join(lines))
    sp = tbox_summary.get("suggested_properties") or []
    if sp:
        parts.append("【可用属性建议】\n" + "、".join(sp)
                     + "\n（所属类未声明属性时，props 键优先取这里的名称；"
                       "都不合适时用文中原词作属性名，不要漏掉文中的细项数值）")
    ops = tbox_summary.get("object_properties") or {}
    if ops:
        lines = [f"- {p}：{d.get('domain', '')} → {d.get('range', '')}"
                 for p, d in ops.items()]
        parts.append("【关系清单】（predicate 只能取自这里）\n" + "\n".join(lines))
    else:
        # 关系清单为空（如纯清单型文档）：明令禁止发明关系，否则模型用通用动词
        # 硬连实体，闸门逐条拦截后刷爆审核队列
        parts.append("【关系清单】当前本体未声明任何实例间关系——禁止输出 relations 数组"
                     "（留空 []），也不要发明新关系；只抽取实例和它的属性。")
    if known_entities:
        parts.append("【已抽取实体】（重复提及可再抽，消解层会合并）\n"
                     + "、".join(known_entities[-80:]))
    if prev_tail:
        parts.append(f"【前块结尾上下文】\n{prev_tail[-200:]}")
    if table_header:
        parts.append(
            "【表格切片上下文】本切片是 markdown 表格的一部分，行可能不带表头。"
            "表头（列名）如下：\n" + table_header +
            "\n判读规则：列名是实体的【属性】，数据行是实体的【实例取值】——"
            "不要把列名或取值当作类；类名从表头与业务语义归纳（一行记录通常对应一个主体实例）。")
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
                      request_interval: float = 0.0,
                      model_override: Optional[dict] = None,
                      model_config_id: Optional[int] = None,
                      known_entities: Optional[list[str]] = None,
                      use_cache: bool = True,
                      llm_call: Optional[Callable] = None,
                      progress_cb: Optional[Callable] = None,
                      cancel_cb: Optional[Callable] = None,
                      coref_enabled: bool = False,
                      pattern_enabled: bool = False) -> ExtractionResult:
    """ABox 抽取（04 §4）：分块并行 → 严格闸门（apply_gate）→ 统计。

    tbox_summary：{"classes": {类名: [别名]}, "object_properties": {谓词: {domain, range}}}
    （与 schema_gate.apply_gate 的 tbox 同形；chunks 定位文本内部自动构建）。
    strict_gate=False 时闸门只做归一不拦违例（调试用）。
    model_config_id：显式指定 model_configs 行（用户个人选择，API 层已校验可见性）。
    coref_enabled：O2-B10 指代消解 L0（闸门前按 chunk 合并代词/简称提及，可关闭）。
    pattern_enabled：O2-B11 pattern 层（结构化属性直抽补全 + LLM 值校准，可关闭）。
    """
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    result = ExtractionResult()
    _phases: dict = {}
    prepared = _chunk_iter(chunks)
    result.chunks_processed = len(prepared)
    if not prepared:
        return result

    if llm_call is None:
        model_name, llm_call = _default_llm_call(model_override, model_config_id)
    else:
        model_name = str((model_override or {}).get("model_name") or "injected")
    known = list(known_entities or [])
    chunks_by_idx = {idx: text for idx, text, _doc, _h in prepared}
    hdr_by_idx = {idx: hdr for idx, _text, _doc, hdr in prepared}

    def one(idx: int, text: str):
        prev_tail = chunks_by_idx.get(idx - 1, "") if idx != prepared[0][0] else ""
        user = _instance_user_prompt(text, tbox_summary, known, prev_tail, chunk_no=idx,
                                     table_header=hdr_by_idx.get(idx, ""))
        key = _cache_key(model_name, INSTANCE_SYSTEM_PROMPT, user)
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                return idx, cached, True, ""
        feedback = ""
        last_err = "未知错误"
        for _attempt in range(2):
            if cancel_cb is not None:
                cancel_cb()  # 取消即时生效：重试间隙不再发起新调用
            try:
                raw = llm_call(INSTANCE_SYSTEM_PROMPT,
                               _instance_user_prompt(text, tbox_summary, known, prev_tail,
                                                     feedback, chunk_no=idx,
                                                     table_header=hdr_by_idx.get(idx, "")),
                               INSTANCE_PROMPT_JSON_SCHEMA)
            except LLMEndpointUnreachableError:
                raise  # 端点不可达（熔断）：中止整段抽取，快速失败给人话报错
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
    _t_parallel = time.perf_counter()
    with ThreadPoolExecutor(max_workers=_llm_max_workers()) as pool:
        # 滑动窗口调度（2026-11-12）：不再整批等待，完成一个补一个。
        def _on_chunk_done(item, payload_result):
            nonlocal done, cache_hits
            idx, payload, hit, err = payload_result
            done += 1
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

        _run_sliding_window(
            pool, prepared,
            lambda item: submit_with_context(pool, one, item[0], item[1]),
            _on_chunk_done,
            max_workers=_llm_max_workers(), request_interval=request_interval,
            cancel_cb=cancel_cb)
    _phases["parallel_extract_seconds"] = round(
        time.perf_counter() - _t_parallel, 6)

    # O2-B10 指代消解 L0：闸门前按 chunk 合并代词/简称提及（失败静默，不影响主抽取）
    _t_coref = time.perf_counter()
    if coref_enabled and raw_entities:
        from app.adapters.coref import apply_coref, run_coref_ll0

        mapping, cstats = run_coref_ll0(
            chunks, raw_entities, raw_relations, llm_call, cancel_cb=cancel_cb,
            use_cache=use_cache,
            cache_key_fn=lambda _m, s, u: _cache_key(model_name, s, u),
            cache_get=_cache_get, cache_put=_cache_put)
        raw_entities, raw_relations, _relabeled = apply_coref(
            raw_entities, raw_relations, mapping)
        result.coref_stats = cstats
    _phases["coref_seconds"] = round(time.perf_counter() - _t_coref, 6)

    # O2-B11 pattern 层：结构化属性直抽补全 + LLM 值校准（毫秒级正则，零 LLM 成本）
    _t_pattern = time.perf_counter()
    if pattern_enabled and raw_entities:
        from app.adapters.pattern_extract import fill_and_calibrate

        raw_entities, p_filled, p_cal = fill_and_calibrate(
            raw_entities, chunks, tbox_summary)
        result.pattern_filled_count = p_filled
        result.pattern_calibrated_count = p_cal
    _phases["pattern_seconds"] = round(time.perf_counter() - _t_pattern, 6)

    # 严格闸门（04 §4.1）：类/谓词四级匹配 + domain/range + evidence 定位 + confidence 阈值
    _t_gate = time.perf_counter()
    tbox = {"classes": tbox_summary.get("classes") or {},
            "object_properties": tbox_summary.get("object_properties") or {},
            "chunks": chunks_by_idx}
    gate = apply_gate(raw_entities + raw_relations, tbox,
                      PromotePolicy(promote_policy) if isinstance(promote_policy, str)
                      else promote_policy)
    _phases["gate_seconds"] = round(time.perf_counter() - _t_gate, 6)
    _phases.update({"chunks": len(prepared), "cache_hits": cache_hits,
                    "raw_entities": len(raw_entities), "raw_relations": len(raw_relations)})
    if not strict_gate:
        result.entities = _dedupe_entities([i for i in gate.passed
                                            if isinstance(i, ExtractedEntity)])
        result.relations = [i for i in gate.passed if isinstance(i, ExtractedRelation)]
        profile_metric_accumulate("instance_phases", _phases)
        return result

    kinds = [v.kind for v in gate.violations]
    result.discarded_count = gate.discarded_count
    result.withheld_count = gate.withheld_count
    result.promote_candidates = gate.promote_candidates
    result.promote_count = len(gate.promote_candidates)
    result.missing_evidence_count = kinds.count("missing_evidence")
    result.low_confidence_count = kinds.count("low_confidence")
    passed_ents = [i for i in gate.passed if isinstance(i, ExtractedEntity)]
    # 伪个体防御（L07 记录≠对象）：label 与所属类名同名的"实例"几乎必然是
    # 把类/子类降格成个体（如 名为「基本存款账户」的结算账户实例），闸门后兜底剔除
    pseudo = [e for e in passed_ents
              if normalize_label(e.label) == normalize_label(e.class_label)]
    if pseudo:
        passed_ents = [e for e in passed_ents if e not in pseudo]
        result.warnings.append(
            f"剔除 {len(pseudo)} 个类名当实例名的伪个体（{', '.join(e.label for e in pseudo[:5])}"
            f"{'…' if len(pseudo) > 5 else ''}）")
    result.entities = _dedupe_entities(passed_ents)
    # 伪个体剔除后，悬挂的关系（主/客体实例已删）一并丢弃
    dropped = {normalize_label(e.label) for e in pseudo}
    result.relations = [r for r in (i for i in gate.passed if isinstance(i, ExtractedRelation))
                        if normalize_label(r.subject_label) not in dropped
                        and normalize_label(r.object_label) not in dropped]
    result.gate_violations = [v.model_dump() for v in gate.violations[:500]]
    result.cache_hits = cache_hits
    # 多轮调用（主抽取/修复/展开）时累加各阶段耗时；最终实体/关系数看 instances_stats
    profile_metric_accumulate("instance_phases", _phases)
    return result


__all__ = [
    "ExtractedEntity", "ExtractedRelation", "ExtractionResult", "SchemaExtractionResult",
    "extract_schema", "extract_instances", "SCHEMA_PROMPT_JSON_SCHEMA", "SYSTEM_PROMPT",
    "INSTANCE_PROMPT_JSON_SCHEMA", "INSTANCE_SYSTEM_PROMPT",
    "LLM_CACHE_TTL", "_cache_key", "_cache_get", "_cache_put",
]
