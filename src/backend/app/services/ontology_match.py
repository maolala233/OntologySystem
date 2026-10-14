# app/services/ontology_match.py - 导入本体与项目骨架的 schema matching（O3-B17）
# label 相似度（Jaro-Winkler，复用消解层的实现）为主，向量余弦为辅（注入 embeddings_fn）；
# 匹配建议带相似度证据物化为 review_items(schema_match_suggestion)，批准写入别名/映射。
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.adapters.resolution import jaro_winkler
from app.adapters.schema_gate import _norm
from app.infrastructure.database import OntologyImport, ReviewItem

logger = logging.getLogger(__name__)

# JW 主阈值；向量通道的预筛阈值与终判阈值（复用 M3-5 消解层的经验值）
JW_SUGGEST_THRESHOLD = 0.85
VECTOR_PREFILTER_JW = 0.60
VECTOR_SUGGEST_THRESHOLD = 0.85
MAX_SUGGESTIONS_PER_RUN = 30
MAX_VECTOR_PAIRS = 200


def _import_classes(import_row: OntologyImport, db: Session) -> list[dict]:
    """导入本体的类清单（label + definition）：优先 MinIO 快照缓存，回落重新解析不在范围。"""
    from app.adapters.versioning import get_snapshot_json

    if import_row.storage_key:
        payload = get_snapshot_json(import_row.storage_key)
        if payload:
            return [c for c in (payload.get("classes") or []) if isinstance(c, dict) and c.get("label")]
    return []


def _project_classes(schema: dict) -> list[dict]:
    return [c for c in (schema.get("classes") or []) if isinstance(c, dict) and c.get("label")]


def match_suggestions(db: Session, project_id: int, import_row: OntologyImport,
                      project_schema: dict,
                      embeddings_fn=None) -> list[dict]:
    """导入类 × 项目类的匹配建议：JW≥0.85 直推；JW 预筛后向量余弦≥0.85 补充。

    返回 [{import_label, target_label, similarity, layer(jw|vector), evidence}]
    已批准/已建议的 (import_label, target_label) 不重复出。
    """
    imp_classes = _import_classes(import_row, db)
    proj_classes = _project_classes(project_schema or {})
    if not imp_classes or not proj_classes:
        return []
    existing = set()
    pend = (db.query(ReviewItem.payload)
            .filter(ReviewItem.project_id == project_id,
                    ReviewItem.item_type == "schema_match_suggestion",
                    ReviewItem.status.in_(("pending", "claimed"))).all())
    for (payload,) in pend:
        if isinstance(payload, dict):
            existing.add((str(payload.get("import_label") or "").lower(),
                          str(payload.get("target_label") or "").lower()))
    for m in import_row.mappings or []:
        if isinstance(m, dict):
            existing.add((str(m.get("import_label") or "").lower(),
                          str(m.get("target_label") or "").lower()))

    # 项目侧 label → 别名集合（归一化匹配用）
    proj_norm: dict[str, dict] = {}
    for c in proj_classes:
        keys = {str(c.get("label") or "").lower()}
        keys |= {str(a or "").lower() for a in (c.get("aliases") or [])}
        for k in keys:
            proj_norm.setdefault(k, c)

    suggestions: list[dict] = []
    vector_pairs: list[tuple[dict, dict, float]] = []
    for imp in imp_classes:
        imp_label = str(imp.get("label"))
        best: tuple[float, dict | None] = (0.0, None)
        for proj in proj_classes:
            sim = jaro_winkler(imp_label, str(proj.get("label")))
            for alias in proj.get("aliases") or []:
                sim = max(sim, jaro_winkler(imp_label, str(alias)))
            if sim > best[0]:
                best = (sim, proj)
        sim, target = best
        if target is not None and sim >= JW_SUGGEST_THRESHOLD:
            key = (imp_label.lower(), str(target.get("label")).lower())
            if key not in existing:
                existing.add(key)
                suggestions.append({
                    "import_label": imp_label,
                    "target_label": target.get("label"),
                    "similarity": round(sim, 3), "layer": "jw",
                    "evidence": {
                        "import_definition": str(imp.get("definition") or "")[:300],
                        "target_definition": str(target.get("definition") or "")[:300],
                    }})
        elif target is not None and sim >= VECTOR_PREFILTER_JW:
            vector_pairs.append((imp, target, sim))
        if len(suggestions) >= MAX_SUGGESTIONS_PER_RUN:
            return suggestions[:MAX_SUGGESTIONS_PER_RUN]

    # 向量通道：预筛对子交向量终判（对 JW 盲区：音远义近）
    if embeddings_fn is not None and vector_pairs and len(suggestions) < MAX_SUGGESTIONS_PER_RUN:
        vector_pairs = vector_pairs[:MAX_VECTOR_PAIRS]
        try:
            labels = ([imp.get("label") for imp, _, _ in vector_pairs]
                      + [str(t.get("label")) for _, t, _ in vector_pairs])
            vectors = embeddings_fn(labels)
            imp_vecs = vectors[:len(vector_pairs)]
            proj_vecs = vectors[len(vector_pairs):]
            for (imp, target, jw), iv, pv in zip(vector_pairs, imp_vecs, proj_vecs):
                from app.adapters.resolution import _cosine

                sim = _cosine(list(iv), list(pv))
                key = (str(imp.get("label")).lower(), str(target.get("label")).lower())
                if sim >= VECTOR_SUGGEST_THRESHOLD and key not in existing:
                    existing.add(key)
                    suggestions.append({
                        "import_label": imp.get("label"),
                        "target_label": target.get("label"),
                        "similarity": round(sim, 3), "layer": "vector",
                        "evidence": {
                            "jw": round(jw, 3),
                            "import_definition": str(imp.get("definition") or "")[:300],
                            "target_definition": str(target.get("definition") or "")[:300],
                        }})
                    if len(suggestions) >= MAX_SUGGESTIONS_PER_RUN:
                        break
        except Exception as exc:  # noqa: BLE001 —— 向量通道故障只降级不阻断
            logger.warning(f"[ontology-match] 向量通道失败，仅 JW 结果：{exc}")
    return suggestions[:MAX_SUGGESTIONS_PER_RUN]


def materialize_match_suggestions(db: Session, project_id: int, import_row: OntologyImport,
                                  suggestions: list[dict]) -> list[int]:
    """建议 → review_items(schema_match_suggestion)（kind=map；approve 写别名+映射留痕）。"""
    ids: list[int] = []
    for s in suggestions:
        item = ReviewItem(
            project_id=project_id,
            item_type="schema_match_suggestion",
            payload={"import_id": import_row.id,
                     "import_filename": import_row.filename,
                     "import_label": s["import_label"],
                     "target_label": s["target_label"],
                     "kind": "map",
                     "similarity": s["similarity"],
                     "layer": s["layer"],
                     "evidence": s.get("evidence") or {}},
            reason=f"本体对齐：「{s['import_label']}」≈「{s['target_label']}」"
                   f"（{s['layer']} 相似度 {s['similarity']}）",
            priority="low", status="pending")
        db.add(item)
        db.flush()
        ids.append(item.id)
    return ids


def apply_mapping(db: Session, project, payload: dict) -> dict:
    """批准落地（review_service 调用）：导入类 label 注入目标类别名 + mappings 留痕。"""
    from sqlalchemy.orm.attributes import flag_modified

    import_label = str(payload.get("import_label") or "").strip()
    target_label = str(payload.get("target_label") or "").strip()
    if not import_label or not target_label:
        return {"skipped": "缺少 import_label/target_label"}
    gd = project.graph_data if isinstance(project.graph_data, dict) else {}
    schema = gd.setdefault("schema", {})
    hit = None
    for c in schema.get("classes") or []:
        if isinstance(c, dict) and str(c.get("label") or "") == target_label:
            hit = c
            break
    if hit is None:
        return {"skipped": f"目标类不在 TBox: {target_label}"}
    aliases = hit.setdefault("aliases", [])
    # 别名按归一化去重（含大小写/全半角），且不与主 label 重复
    if import_label not in aliases and _norm(import_label) != _norm(target_label) \
            and _norm(import_label) not in {_norm(a) for a in aliases}:
        aliases.append(import_label)
    row = db.query(OntologyImport).filter(
        OntologyImport.id == int(payload.get("import_id") or 0),
        OntologyImport.project_id == project.id).first()
    if row is not None:
        mappings = list(row.mappings or [])
        mappings.append({"import_label": import_label, "target_label": target_label,
                         "kind": str(payload.get("kind") or "map"),
                         "similarity": payload.get("similarity"),
                         "layer": payload.get("layer")})
        row.mappings = mappings
    project.graph_data = gd
    flag_modified(project, "graph_data")
    db.flush()
    return {"mapped": {"import_label": import_label, "target_label": target_label}}


__all__ = ["match_suggestions", "materialize_match_suggestions", "apply_mapping",
           "JW_SUGGEST_THRESHOLD", "VECTOR_SUGGEST_THRESHOLD"]
