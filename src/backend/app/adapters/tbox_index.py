# app/adapters/tbox_index.py - 本体 TBox 向量索引（QA 类锚定召回）
# 把 schema 的类（label/别名/定义/属性名）嵌入独立 Milvus 集合 ontology_tbox：
# 问题嵌入 → top 类 → 类锚定枚举（"接入的系统有哪些"这类问题无需逐字命中实体名）。
# 参考 context-ontology-accelerator：本体索引是"问题→类锚点→图遍历种子"的枢纽。
from __future__ import annotations

import json
import logging

from app.infrastructure.vector_client import VectorStoreManager

logger = logging.getLogger(__name__)

TBOX_COLLECTION = "ontology_tbox"
SOURCE_TYPE = "graph_node"  # 复用主集合 schema 的 source_type 枚举；本集合内仅此类型


def _class_text(c: dict) -> str:
    """类 → 嵌入文本：label + 别名 + 定义 + 属性名（属性值不进索引，避免高频值噪声）。"""
    label = str((c or {}).get("label") or "").strip()
    if not label:
        return ""
    parts = [f"类：{label}"]
    aliases = [str(a).strip() for a in (c or {}).get("aliases") or [] if str(a).strip()]
    if aliases:
        parts.append(f"别名：{'、'.join(aliases)}")
    desc = str((c or {}).get("definition") or "").strip()
    if desc:
        parts.append(desc)
    prop_names = [str((p or {}).get("name") or "").strip()
                  for p in (c or {}).get("properties") or [] if str((p or {}).get("name") or "").strip()]
    if prop_names:
        parts.append(f"属性：{'、'.join(prop_names)}")
    return "。".join(parts)


def build_tbox_texts(gd: dict | None) -> list[tuple[str, dict]]:
    """graph_data["schema"] → [(嵌入文本, metadata)]。空骨架返回 []。"""
    schema = (gd or {}).get("schema") if isinstance(gd, dict) else None
    if not isinstance(schema, dict):
        return []
    out: list[tuple[str, dict]] = []
    for c in schema.get("classes") or []:
        text = _class_text(c)
        if not text:
            continue
        out.append((text, {"label": str(c.get("label")), "kind": "class"}))
    return out


def sync_tbox_index(project_id: int, gd: dict | None) -> int:
    """重建项目的 TBox 索引（先删后插，幂等）。返回写入条数；Milvus 不可用返回 0。"""
    items = build_tbox_texts(gd)
    if not items:
        return 0
    vm = VectorStoreManager(collection_name=TBOX_COLLECTION)
    if not vm.is_enabled:
        return 0
    try:
        vm.delete_by_expr(f"project_id == {int(project_id)}")
        texts = [t[:65000] for t, _m in items]
        metas = [dict(m, source_file="本体图谱", source_quote=t, chunk_text=t, type=SOURCE_TYPE)
                 for t, m in items]
        vm.insert_data(texts, metas, project_id=int(project_id),
                       source_types=[SOURCE_TYPE] * len(items))
        logger.info(f"[tbox-index] 项目 {project_id} 写入 {len(items)} 个类锚点")
        return len(items)
    except Exception as e:  # noqa: BLE001 —— 索引失败不阻断主流程
        logger.warning(f"[tbox-index] 项目 {project_id} 同步失败：{e}")
        return 0


def search_class_anchors(question: str, project_id: int, top_k: int = 3,
                         min_score: float = 0.45) -> list[tuple[str, float]]:
    """问题嵌入 → top 类锚点 [(类label, 相似度)]。集合为空时返回 []（不自动同步）。"""
    try:
        vm = VectorStoreManager(collection_name=TBOX_COLLECTION)
        if not vm.is_enabled:
            return []
        hits = vm.search_with_expr(
            question, f'project_id == {int(project_id)} and source_type == "{SOURCE_TYPE}"',
            top_k=top_k)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[tbox-index] 类锚点检索失败：{e}")
        return []
    out: list[tuple[str, float]] = []
    for h in hits:
        sim = max(0.0, min(1.0, float(h.get("distance") or 0.0)))
        if sim < min_score:
            continue
        label = str(((h.get("metadata") or {}).get("label")) or "").strip()
        if label:
            out.append((label, round(sim, 4)))
    return out
