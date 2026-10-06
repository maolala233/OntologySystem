# app/services/oxigraph_sink.py - Oxigraph 命名图写入（docs/design/02 §5，M4 worker-rdf 专用）
# 嵌入式 pyoxigraph（RocksDB 独占锁 → 仅 worker-rdf 单写者）。四命名图整体替换：
#   urn:onto:{project}:tbox / :abox / :prov / :pub:{version_no}
# 幂等语义：命名图先 clear 再 add（重建而非增量追加）。
from __future__ import annotations

import os

from app.core.config import settings
from app.core.logging import logger


_STORE = None


def _store():
    """单例 Store（RocksDB 独占锁：进程内共享一个实例；worker-rdf concurrency=1）。"""
    global _STORE
    if _STORE is not None:
        return _STORE
    import pyoxigraph as ox

    path = settings.OXIGRAPH_PATH
    os.makedirs(path, exist_ok=True)
    _STORE = ox.Store(path)
    return _STORE


def _nn(iri: str):
    import pyoxigraph as ox

    return ox.NamedNode(iri)


def _lit(value: str):
    import pyoxigraph as ox

    return ox.Literal(str(value))


def _quad(s, p, o, graph: str):
    import pyoxigraph as ox

    return ox.Quad(s, p, o, _nn(graph))


RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_OBJPROP = "http://www.w3.org/2002/07/owl#ObjectProperty"
RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
PROV = "http://www.w3.org/ns/prov#"


def build_project_quads(db, project_id: int) -> dict[str, list[tuple]]:
    """从行表构建 TBox/ABox/PROV 三元组（单元组 (s_iri, p_iri, o_iri_or_literal)）。"""
    from app.infrastructure.database import Entity, ProvenanceRecord, Relation, UploadedDocument

    ents = db.query(Entity).filter(Entity.project_id == project_id).all()
    by_id = {e.id: e for e in ents}

    tbox, abox, prov = [], [], []
    class_uris = {}
    for e in ents:
        if e.is_class_node:
            class_uris[e.label] = e.uri
            tbox.append((e.uri, RDF, OWL_CLASS))
            tbox.append((e.uri, RDFS_LABEL, e.label))

    for r in db.query(Relation).filter(Relation.project_id == project_id).all():
        s, o = by_id.get(r.subject_id), by_id.get(r.object_id)
        if s is None or o is None:
            continue
        if s.is_class_node and o.is_class_node:
            # 谓词即对象属性：以谓词 uri 表达 domain→range（简化 TBox 投影）
            pred_uri = f"urn:onto:{project_id}:predicate/{r.predicate}"
            tbox.append((pred_uri, RDF, OWL_OBJPROP))
            tbox.append((pred_uri, RDFS_DOMAIN, s.uri))
            tbox.append((pred_uri, RDFS_RANGE, o.uri))
        else:
            if not s.is_class_node:
                cls_uri = f"urn:onto:{project_id}:class/{s.class_label}"
                abox.append((s.uri, RDF, cls_uri))
                abox.append((s.uri, RDFS_LABEL, s.label))
            if not o.is_class_node:
                abox.append((s.uri, f"urn:onto:{project_id}:predicate/{r.predicate}", o.uri))

    docs = {d.id: d for d in db.query(UploadedDocument)
            .filter(UploadedDocument.project_id == project_id).all()}
    provs = (db.query(ProvenanceRecord)
             .filter(ProvenanceRecord.project_id == project_id).all())
    for p in provs:
        target = by_id.get(p.target_id)
        if target is None or not target.uri:
            continue
        prov_uri = f"urn:onto:{project_id}:prov/{p.id}"
        prov.append((prov_uri, RDF, f"{PROV}Entity"))
        if p.evidence_text:
            prov.append((prov_uri, f"{PROV}value", p.evidence_text))
        prov.append((target.uri, f"{PROV}wasDerivedFrom", prov_uri))
        doc = docs.get(p.source_document_id)
        if doc is not None and doc.storage_key:
            prov.append((prov_uri, f"{PROV}wasGeneratedBy",
                         f"urn:onto:{project_id}:doc/{p.source_document_id}"))
    return {"tbox": tbox, "abox": abox, "prov": prov}


def write_project_graphs(db, project_id: int, publication_version_no: int | None = None) -> dict:
    """命名图整体替换（tbox/abox/prov）+ 可选发布快照图 pub:{vno}（不可变副本）。"""
    store = _store()
    base = f"urn:onto:{project_id}"
    try:
        quads = build_project_quads(db, project_id)
        graphs = {
            f"{base}:tbox": quads["tbox"],
            f"{base}:abox": quads["abox"],
            f"{base}:prov": quads["prov"],
        }
        if publication_version_no is not None:
            graphs[f"{base}:pub:{publication_version_no}"] = (
                quads["tbox"] + quads["abox"])
        import pyoxigraph as ox

        counts = {}
        for gname, triples in graphs.items():
            g = _nn(gname)
            store.clear_graph(g)
            for s_iri, p_iri, o in triples:
                if o.startswith("urn:") or o.startswith("http"):
                    obj = _nn(o)
                else:
                    obj = _lit(o)
                store.add(_quad(_nn(s_iri), _nn(p_iri), obj, gname))
            counts[gname] = len(triples)
        store.flush()
        logger.info(f"[oxigraph] 项目 {project_id} 命名图写入: {counts}")
        return {"ok": True, "graphs": counts}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[oxigraph] 项目 {project_id} 写入失败: {e}")
        return {"ok": False, "error": str(e)[:300]}


def delete_project_graphs(project_id: int) -> dict:
    """项目下线灾备清理：删除全部命名图。"""
    store = _store()
    base = f"urn:onto:{project_id}"
    removed = []
    for g in list(store.named_graphs()):
        g_name = str(g).strip("<>")  # named_graphs 返回 N-Triples 形式 <iri>
        if g_name.startswith(base):
            store.clear_graph(g)
            store.remove_graph(g)
            removed.append(g_name)
    store.flush()
    return {"ok": True, "removed": removed}
