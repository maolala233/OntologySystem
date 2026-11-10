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
RDFS_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
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
        else:
            # 实例属性值落图（CQ SPARQL 实答的前提：属性值问题如"失败次数是多
            # 少"此前永远查不到——值只存在行表 JSON，图里只有类型/标签/关系边）
            n_prop = 0
            for pk, pv in (e.props or {}).items():
                if not str(pk).strip() or n_prop >= 30:
                    continue
                if isinstance(pv, (dict, list)):
                    continue  # 结构值不落图（口径：标量属性值）
                abox.append((e.uri, f"urn:onto:{project_id}:prop/{str(pk).strip()[:80]}",
                             str(pv)[:500]))  # 写入循环按前缀自动判 IRI/字面量
                n_prop += 1

    for r in db.query(Relation).filter(Relation.project_id == project_id).all():
        s, o = by_id.get(r.subject_id), by_id.get(r.object_id)
        if s is None or o is None:
            continue
        if s.is_class_node and o.is_class_node:
            if r.predicate in ("subclass_of", "subClassOf", "rdfs:subClassOf"):
                # 类层次必须以 subClassOf 落图——写成 ObjectProperty+domain/range，
                # SPARQL 属性路径与类层次查询就全部失明
                tbox.append((s.uri, RDFS_SUBCLASS, o.uri))
            else:
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
            elif r.predicate in ("instance_of", "rdf:type", "type"):
                # 实例→类节点的类型边落 rdf:type（当普通属性边 SPARQL 层类型查询就瞎了）
                abox.append((s.uri, RDF, o.uri))

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


# ── SPARQL 只读查询（L15/L16/L27 落地面：数据已在四命名图里，缺的只是读接口）──

class StoreBusy(RuntimeError):
    """RocksDB 独占锁被 worker-rdf 持有：API 进程打不开 Store，须走 rdf 队列中继。"""


def _term_out(t) -> dict | None:
    """RDF term → JSON 友好形状（NamedNode/Literal 取 .value，勿用 str()——其带 <> 引号）。"""
    import pyoxigraph as ox

    if t is None:
        return None
    if isinstance(t, ox.NamedNode):
        return {"type": "uri", "value": t.value}
    if isinstance(t, ox.Literal):
        return {"type": "literal", "value": t.value}
    if isinstance(t, ox.BlankNode):
        return {"type": "bnode", "value": t.value}
    return {"type": "unknown", "value": str(t)}  # RDF-star Triple 等兜底


def run_sparql(store, sparql: str, project_id: int | None = None, limit: int = 200) -> dict:
    """在已打开的 Store 上执行只读 SPARQL（store.query 天然只读，UPDATE 语法直接报错）。

    project_id 给定时做项目隔离：default_graph = 该项目全部命名图的并集
    （裸 SELECT ?s ?p ?o 即见本项目数据），named_graphs 同步限定（GRAPH 子句跨不出项目）。
    """
    import pyoxigraph as ox

    kwargs: dict = {}
    if project_id is not None:
        base = f"urn:onto:{project_id}:"
        graphs = [g for g in store.named_graphs() if str(g).strip("<>").startswith(base)]
        if not graphs:
            return {"type": "select", "columns": [], "rows": [],
                    "note": f"项目 {project_id} 在 Oxigraph 中尚无命名图（等 worker-rdf 投影）"}
        kwargs["default_graph"] = graphs
        kwargs["named_graphs"] = graphs

    result = store.query(sparql, **kwargs)
    kind = type(result).__name__

    if kind == "QuerySolutions":  # SELECT
        variables = list(result.variables)
        columns = [str(v).lstrip("?") for v in variables]
        rows = []
        for sol in result:
            if len(rows) >= limit:
                break
            rows.append({c: _term_out(sol[v]) for c, v in zip(columns, variables)})
        return {"type": "select", "columns": columns, "rows": rows, "row_count": len(rows)}
    if kind == "QueryTriples":  # CONSTRUCT / DESCRIBE
        triples = []
        for tr in result:
            if len(triples) >= limit:
                break
            triples.append({"subject": _term_out(tr.subject),
                            "predicate": _term_out(tr.predicate),
                            "object": _term_out(tr.object)})
        return {"type": "triples", "triples": triples, "triple_count": len(triples)}
    return {"type": "boolean", "value": bool(result)}  # ASK → QueryBoolean


def query_sparql(sparql: str, project_id: int | None = None, limit: int = 200) -> dict:
    """worker-rdf（持锁进程）用的查询入口：复用单例 Store。"""
    return run_sparql(_store(), sparql, project_id, limit)


def _exec_sparql_isolated(store, sparql: str, project_id: int | None,
                          limit: int) -> tuple[dict | None, str | None]:
    """隔离执行：异常只回传消息字符串，不外传异常对象。

    之所以不直接抛——异常的 traceback 拽着本函数 frame 里的 store 引用，
    Store 活多久 RocksDB 独占锁就占多久，短连接查询会把锁闷死。
    """
    try:
        return run_sparql(store, sparql, project_id, limit), None
    except Exception as e:  # noqa: BLE001 —— 异常在此消化为消息，frame 随返回销毁
        return None, f"{type(e).__name__}: {e}"


def query_sparql_standalone(sparql: str, project_id: int | None = None,
                            limit: int = 200) -> dict:
    """API 进程用的短连接查询：临时开 Store、查完即释放（不占常驻锁）。

    worker-rdf 正持锁时抛 StoreBusy，由调用方走 rdf 队列中继；
    SPARQL 语法错误以 SyntaxError 形式抛出（消息内联，无 traceback 链）。
    """
    import gc

    import pyoxigraph as ox

    path = settings.OXIGRAPH_PATH
    os.makedirs(path, exist_ok=True)
    try:
        store = ox.Store(path)
    except Exception as e:  # noqa: BLE001 —— RocksDB 独占锁冲突等
        raise StoreBusy(str(e)[:300]) from e
    try:
        payload, err = _exec_sparql_isolated(store, sparql, project_id, limit)
    finally:
        del store
        gc.collect()  # 引用计数即刻归零 → Rust Drop 释放 RocksDB 锁
    if err is not None:
        if err.startswith("SyntaxError"):
            raise SyntaxError(err[len("SyntaxError: "):])
        raise RuntimeError(err)
    return payload
