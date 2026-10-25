# app/services/semantic_index.py - 本体词汇索引与语义匹配（S19/S20 共用，docs/15）
# 单一事实源：MCP resolve_semantics / check_answerable 与 QA Tier0 权威注入共用同一套
# 四级匹配（精确名→精确别名→归一子集+context 消歧）与覆盖分级（no/partial/likely）。
# 合同：ambiguous 返回候选绝不猜；unavailable 明示"不要编造"；确定性、无 LLM。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, ProvenanceRecord, UploadedDocument

MENTION_MAX = 5
AXIOM_CAP = 10          # 单概念附带公理上限
EVIDENCE_CAP = 3        # 单概念附带证据上限


def build_index(db: Session, project_id: int) -> list[Entity]:
    """词汇索引：TBox/ABox 实体行（label/别名/归一名），一次查询本调用内复用。"""
    return (db.query(Entity)
            .filter(Entity.project_id == project_id, Entity.status != "rejected")
            .all())


def match_mention(index: list[Entity], mention: str, context: str = "") -> dict:
    """四级匹配：精确名→精确别名→归一子集；同层多命中=ambiguous（返回候选不猜）。"""
    m = str(mention or "").strip().lower()
    if not m:
        return {"status": "unavailable", "candidates": [], "via": None}
    exact_label = [e for e in index if (e.label or "").strip().lower() == m]
    if len(exact_label) == 1:
        return {"status": "supported", "candidates": exact_label, "via": "exact_name"}
    if len(exact_label) > 1:
        return {"status": "ambiguous", "candidates": exact_label[:3], "via": "exact_name"}
    alias_hit = [e for e in index
                 if any(str(a).strip().lower() == m for a in (e.aliases or []))]
    if len(alias_hit) == 1:
        return {"status": "supported", "candidates": alias_hit, "via": "exact_alias"}
    if len(alias_hit) > 1:
        return {"status": "ambiguous", "candidates": alias_hit[:3], "via": "exact_alias"}
    fuzzy = [e for e in index
             if m in (e.label_normalized or "").lower()
             or (e.label or "").lower().find(m) >= 0]
    if len(fuzzy) == 1:
        return {"status": "partial", "candidates": fuzzy, "via": "normalized_contains"}
    if len(fuzzy) > 1:
        ctx_tokens = set(str(context or "").lower())
        scored = [(sum(1 for ch in (e.label or "").lower() if ch in ctx_tokens), e)
                  for e in fuzzy]
        scored.sort(key=lambda t: -t[0])
        if len(scored) >= 2 and scored[0][0] > scored[1][0] and scored[0][0] > 0:
            return {"status": "supported", "candidates": [scored[0][1]],
                    "via": "normalized_contains+context"}
        return {"status": "ambiguous", "candidates": fuzzy[:3], "via": "normalized_contains"}
    return {"status": "unavailable", "candidates": [], "via": None}


def entity_axioms(gd: dict, canvas_id: str, label: str) -> list[dict]:
    """概念适用公理：schema 权威键（类级/属性级，含 S16 来源标记）+ 画布镜像补足。"""
    out: list[dict] = []
    schema = gd.get("schema") or {}
    prov = schema.get("axiom_provenance") or {}

    def _add(kind, detail, key=None):
        meta = prov.get(key) if key else None
        out.append({"kind": kind, **detail,
                    "source_type": (meta or {}).get("source_type", "ai"),
                    "certified": bool((meta or {}).get("certified"))})

    for c in schema.get("classes") or []:
        if not isinstance(c, dict) or str(c.get("label") or "") != label:
            continue
        for peer in c.get("disjoint_with") or []:
            _add("disjoint_with", {"classes": [label, str(peer)]},
                 f"disjoint_with:{min(label, str(peer))}|{max(label, str(peer))}")
        for parent in c.get("parent_classes") or []:
            _add("subclass_of", {"classes": [label, str(parent)]},
                 f"subclass_of:{label}|{parent}")
        for peer in c.get("equivalent_to") or []:
            _add("equivalent_classes", {"classes": [label, str(peer)]},
                 f"equivalent_classes:{min(label, str(peer))}|{max(label, str(peer))}")
        if len(out) >= AXIOM_CAP:
            return out[:AXIOM_CAP]
    for op_ in schema.get("object_properties") or []:
        if not isinstance(op_, dict):
            continue
        ol = str(op_.get("label") or "")
        if str(op_.get("domain") or "") == label or str(op_.get("range") or "") == label:
            if op_.get("functional"):
                _add("functional", {"predicate": ol}, f"functional:{ol}")
            if op_.get("inverse_of"):
                _add("inverse_of", {"predicates": [ol, str(op_["inverse_of"])]})
            if op_.get("domain") and op_.get("range"):
                _add("domain_range", {"predicate": ol, "domain": op_["domain"],
                                      "range": op_["range"]})
        for r in (op_.get("cardinality_restrictions") or []):
            if isinstance(r, dict) and str(r.get("class") or "") == label:
                _add("cardinality", {"predicate": ol, "min": r.get("min"),
                                     "max": r.get("max")})
        if len(out) >= AXIOM_CAP:
            break
    for n in gd.get("nodes") or []:
        if str(n.get("id") or "") != canvas_id:
            continue
        ax = (n.get("data") or {}).get("axioms") or {}
        for peer in ax.get("disjoint_with") or []:
            key = f"disjoint_with:{min(label, str(peer))}|{max(label, str(peer))}"
            if not any(a.get("kind") == "disjoint_with" and a.get("classes") == [label, str(peer)]
                       for a in out):
                _add("disjoint_with", {"classes": [label, str(peer)]}, key)
        break
    return out[:AXIOM_CAP]


def entity_evidence(db: Session, project_id: int, entity_id: int) -> list[dict]:
    """概念溯源证据（原文引用+文档+切片），cap EVIDENCE_CAP。"""
    rows = (db.query(ProvenanceRecord, UploadedDocument.filename)
            .join(UploadedDocument, ProvenanceRecord.source_document_id == UploadedDocument.id)
            .filter(ProvenanceRecord.project_id == project_id,
                    ProvenanceRecord.target_type == "entity",
                    ProvenanceRecord.target_id == entity_id)
            .limit(EVIDENCE_CAP).all())
    return [{"evidence": p.evidence_text, "document": fname, "chunk_index": p.chunk_index,
             "method": p.extraction_method} for p, fname in rows]


def grade_coverage(index: list[Entity], gd: dict, question: str) -> dict:
    """确定性覆盖分级（无 LLM）：问题与本体词汇（实体 label/别名 + TBox 谓词）命中分级。

    coverage: no（0 命中）/ partial（1）/ likely（≥2）；只回答"术语覆盖"，
    不承诺语义可答——诚实不超 claim。
    """
    ql = str(question or "").strip().lower()
    matched: list[dict] = []
    seen: set[str] = set()
    if ql:
        for e in index:
            label = (e.label or "").strip()
            if len(label) < 2:
                continue
            forms = [label.lower()] + [str(a).strip().lower() for a in (e.aliases or [])
                                       if len(str(a)) >= 2]
            if any(f in ql for f in forms) and e.uri not in seen:
                seen.add(e.uri)
                matched.append({"uri": e.uri, "label": label,
                                "kind": "class" if e.is_class_node else "instance",
                                "class_label": e.class_label,
                                "source_type": e.source_type,
                                "certified": bool(e.certified)})
        for op_ in (gd.get("schema") or {}).get("object_properties") or []:
            if not isinstance(op_, dict):
                continue
            label = str(op_.get("label") or "").strip()
            if len(label) >= 2 and label.lower() in ql:
                matched.append({"label": label, "kind": "property",
                                "domain": op_.get("domain"), "range": op_.get("range"),
                                "functional": bool(op_.get("functional"))})
        # S24：治理指标也属本体词汇（口径命中计入覆盖）
        for m in (gd.get("schema") or {}).get("metrics") or []:
            if not isinstance(m, dict):
                continue
            label = str(m.get("label") or "").strip()
            if len(label) >= 2 and label.lower() in ql:
                matched.append({"label": label, "kind": "metric",
                                "expression": m.get("expression"),
                                "unit": m.get("unit"),
                                "certified": bool(m.get("certified"))})
    n = len(matched)
    coverage = "no" if n == 0 else ("partial" if n == 1 else "likely")
    return {"coverage": coverage, "matched": matched, "matched_count": n}


def nearest_terms(index: list[Entity], question: str, cap: int = 5) -> list[str]:
    """拒答建议：与问题词元重叠最高的术语（fuzzy 提示，供澄清/补建参考）。"""
    q = str(question or "").strip().lower()
    if not q:
        return []
    scored = []
    for e in index:
        label = (e.label or "").strip()
        if len(label) < 2:
            continue
        overlap = sum(1 for ch in label.lower() if ch in q)
        if overlap >= 2:
            scored.append((overlap, label))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [label for _, label in scored[:cap]]
