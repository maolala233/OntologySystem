# app/adapters/coref.py - 指代消解 L0 层（O2-B10）
# 实例抽取时按 chunk 产出指代链（"该公司/该行/其"→canonical 实体），
# 在三层消解（归一/pinyin/向量，resolution.py）之前合并别名提及，
# 降低消解待审聚类数量。复用既有 LLM 调用（json_schema 输出）与缓存。
# 可关闭（settings.COREF_ENABLED / extract_instances(coref_enabled=False)）；
# LLM 失败静默跳过（L0 是 best-effort 增强层，不阻断主抽取）。

from __future__ import annotations

from typing import Any, Callable, Optional

COREF_PROMPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "mentions": {
            "type": "array",
            "description": "切片中的指代提及及其指向的正式实体",
            "items": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string",
                              "description": "指代称呼（代词/省略主语/简称），逐字来自切片"},
                    "canonical": {"type": "string",
                                  "description": "指向的正式实体名，必须取自【切片实体清单】"},
                },
                "required": ["alias", "canonical"],
            },
        },
    },
    "required": ["mentions"],
}

COREF_SYSTEM_PROMPT = (
    "你是知识图谱构建专家。给定一段文档切片和从中抽取的实体清单，"
    "找出切片中的指代提及（代词如\"该公司/该行/其/该产品\"、省略主语、明确简称）"
    "各自指向实体清单中的哪个正式实体。\n"
    "规则：\n"
    "1. canonical 必须严格取自【切片实体清单】，不要发明新实体。\n"
    "2. 只输出有把握的指代（指代对象明确）；拿不准的不要输出。\n"
    "3. 与实体名完全相同的提及不要输出（那不是指代）。\n"
    "4. 只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema。"
)


def coref_user_prompt(chunk_text: str, labels: list[str], chunk_no: int = 0) -> str:
    return ("【切片实体清单】（canonical 只能取自这里）\n"
            + "、".join(labels[:60])
            + f"\n\n【文档切片 #{chunk_no}】\n{chunk_text}")


def apply_coref(entities: list, relations: list,
                mapping_by_chunk: dict[int, dict[str, str]]) -> tuple[list, list, int]:
    """把每 chunk 的 alias→canonical 映射应用回抽取产物（纯函数）。

    entities/relations：ExtractedEntity/ExtractedRelation 或同形状对象。
    命中映射的实体改名为 canonical；随后 (label,class) 相同的实体按既有
    _dedupe_entities 口径去重。返回 (entities, relations, relabeled_count)。
    """
    if not mapping_by_chunk:
        return entities, relations, 0

    def _get(item: Any, k: str, default=None):
        return item.get(k, default) if isinstance(item, dict) else getattr(item, k, default)

    def _set(item: Any, k: str, v) -> None:
        if isinstance(item, dict):
            item[k] = v
        else:
            setattr(item, k, v)

    relabeled = 0
    for e in entities:
        chunk = _get(e, "chunk_index", 0)
        mapping = mapping_by_chunk.get(chunk) or {}
        label = str(_get(e, "label") or "").strip()
        target = mapping.get(label)
        if target and target != label:
            _set(e, "label", target)
            relabeled += 1
    for r in relations:
        chunk = _get(r, "chunk_index", 0)
        mapping = mapping_by_chunk.get(chunk) or {}
        for field in ("subject_label", "object_label"):
            label = str(_get(r, field) or "").strip()
            target = mapping.get(label)
            if target and target != label:
                _set(r, field, target)
                relabeled += 1
    return entities, relations, relabeled


def run_coref_ll0(chunks: list, entities: list, relations: list,
                  llm_call: Callable, cancel_cb: Optional[Callable] = None,
                  use_cache: bool = True,
                  cache_key_fn: Optional[Callable] = None,
                  cache_get: Optional[Callable] = None,
                  cache_put: Optional[Callable] = None) -> tuple[dict[int, dict[str, str]], dict]:
    """按 chunk 跑指代消解（L0）：每 chunk 一次 LLM 调用（仅当 chunk 内 ≥2 实体）。

    chunks：[{"index": i, "text": str, ...}]；entities：抽取产物（看 chunk_index）。
    返回 (mapping_by_chunk, stats)；单 chunk 失败只记 warnings，不阻断。
    """
    labels_by_chunk: dict[int, list[str]] = {}
    for e in entities:
        chunk = getattr(e, "chunk_index", 0) if not isinstance(e, dict) \
            else int(e.get("chunk_index") or 0)
        label = (e.get("label") if isinstance(e, dict) else getattr(e, "label", "")) or ""
        label = str(label).strip()
        if label:
            bucket = labels_by_chunk.setdefault(chunk, [])
            if label not in bucket:
                bucket.append(label)

    mapping_by_chunk: dict[int, dict[str, str]] = {}
    stats = {"chunks_scanned": 0, "llm_calls": 0, "mentions": 0, "failures": 0}
    for chunk in sorted(labels_by_chunk):
        labels = labels_by_chunk[chunk]
        if len(labels) < 2:
            continue  # 单实体 chunk 无指代消解空间
        text = next((str(c.get("text") or "") for c in chunks
                     if c.get("index") == chunk), "")
        if not text:
            continue
        stats["chunks_scanned"] += 1
        if cancel_cb is not None:
            cancel_cb()
        payload = None
        key = None
        if use_cache and cache_key_fn is not None and cache_get is not None:
            key = cache_key_fn("coref", COREF_SYSTEM_PROMPT,
                               coref_user_prompt(text, labels, chunk))
            payload = cache_get(key)
        if payload is None:
            stats["llm_calls"] += 1
            try:
                raw = llm_call(COREF_SYSTEM_PROMPT,
                               coref_user_prompt(text, labels, chunk),
                               COREF_PROMPT_JSON_SCHEMA)
            except Exception:  # noqa: BLE001 —— L0 失败静默跳过
                stats["failures"] += 1
                continue
            payload = raw if isinstance(raw, dict) else None
            if payload is not None and use_cache and key is not None \
                    and cache_put is not None:
                cache_put(key, payload)
        if not isinstance(payload, dict):
            stats["failures"] += 1
            continue
        label_set = set(labels)
        mapping: dict[str, str] = {}
        for m in payload.get("mentions") or []:
            if not isinstance(m, dict):
                continue
            alias = str(m.get("alias") or "").strip()
            canonical = str(m.get("canonical") or "").strip()
            # alias 通常是抽取产物里的代词/简称实体（在 label_set 里很正常），
            # 只拒绝：canonical 不在清单（发明实体）、自映射、重复 alias
            if alias and canonical in label_set and alias != canonical \
                    and alias not in mapping:
                mapping[alias] = canonical
        if mapping:
            mapping_by_chunk[chunk] = mapping
            stats["mentions"] += len(mapping)
    return mapping_by_chunk, stats


__all__ = ["COREF_SYSTEM_PROMPT", "COREF_PROMPT_JSON_SCHEMA", "coref_user_prompt",
           "apply_coref", "run_coref_ll0"]
