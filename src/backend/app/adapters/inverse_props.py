# app/adapters/inverse_props.py - 反向属性自动补全（O2-B7，Ontology 101 §5.1）
# 声明了 inverse_of 的谓词对：写入一边自动补另一边（实例层落地）。
# 抽取产物扩展（extract_tasks）与手动加边（graph_edit）共用本模块；
# 合并/拆分走既有 merge_entities 关系迁移（反向边是普通边，随迁移自然一致）；
# TTL 导出不变（ontology.py 已导出 owl:inverseOf）。
# 纯函数可测。

from __future__ import annotations

from typing import Any

from app.adapters.resolution import normalize_label


def inverse_map(schema: dict | None) -> dict[str, str]:
    """schema.object_properties[].inverse_of → 双向映射 {谓词: 逆谓词}。

    label 归一用 normalize_label（与检测/消解同口径），返回值保留原始 label。
    """
    out: dict[str, str] = {}
    for op in (schema or {}).get("object_properties") or []:
        if not isinstance(op, dict):
            continue
        label = str(op.get("label") or "").strip()
        inv = str(op.get("inverse_of") or "").strip()
        if not label or not inv or label == inv:
            continue
        out[normalize_label(label)] = inv
        out[normalize_label(inv)] = label
    return out


def expand_inverse_relations(relations: list, schema: dict | None) -> tuple[list, int]:
    """实例关系列表 → 追加反向关系（声明了 inverse_of 的谓词对）。

    relations：ExtractedRelation 或同形状对象/字典。反向关系沿用原边的
    evidence/chunk_index/confidence（逻辑蕴含事实）；已有显式反向边或
    自逆谓词 (s,p,s) 不重复生成。返回 (新列表, 补全条数)。
    """
    imap = inverse_map(schema)
    if not imap:
        return list(relations), 0

    def _get(r: Any, k: str, default=""):
        if isinstance(r, dict):
            return r.get(k, default)
        return getattr(r, k, default)

    # 显式边集合：正向与反向的 (s,p,o) 都登记，避免补出的边与人工/LLM 已写边重复
    explicit: set[tuple] = set()
    for r in relations:
        s = str(_get(r, "subject_label") or "").strip()
        p = str(_get(r, "predicate") or "").strip()
        o = str(_get(r, "object_label") or "").strip()
        if s and p and o:
            explicit.add((normalize_label(s), normalize_label(p), normalize_label(o)))

    added: list = []
    for r in relations:
        s = str(_get(r, "subject_label") or "").strip()
        p = str(_get(r, "predicate") or "").strip()
        o = str(_get(r, "object_label") or "").strip()
        if not (s and p and o):
            continue
        inv = imap.get(normalize_label(p))
        if not inv:
            continue
        rev_key = (normalize_label(o), normalize_label(inv), normalize_label(s))
        if rev_key in explicit:
            continue
        explicit.add(rev_key)
        if isinstance(r, dict):
            mirror = dict(r)
            mirror["subject_label"], mirror["object_label"] = o, s
            mirror["predicate"] = inv
        else:
            mirror = type(r)(subject_label=o, predicate=inv, object_label=s,
                             props=dict(_get(r, "props", {}) or {}),
                             confidence=_get(r, "confidence", 0.0),
                             evidence=_get(r, "evidence", ""),
                             chunk_index=_get(r, "chunk_index", 0))
        added.append(mirror)
    return list(relations) + added, len(added)


def inverse_predicate_of(predicate: str, schema: dict | None) -> str | None:
    """单谓词查询：声明的逆谓词 label（graph_edit 手动加边用）。"""
    return inverse_map(schema).get(normalize_label(str(predicate or "").strip()))


__all__ = ["inverse_map", "expand_inverse_relations", "inverse_predicate_of"]
