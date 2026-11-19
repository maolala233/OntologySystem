# app/services/mcp_graph_tools.py - MCP 图谱工具集（按 testfile/工具.docx 契约）
# 统一只读工具：类型/实体/关系/邻域/子图/路径/语义检索。所有 dataId 支持
# 行表 id、uri 或 label；filter 使用受限 SQL 风格表达式（= / in / and / or / 括号）。
from __future__ import annotations

import re
from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, Project, Relation


# ── helpers ────────────────────────────────────────────────────────────────

def _data(node: dict) -> dict:
    return node.get("data") if isinstance(node.get("data"), dict) else {}


def _node_out(e: Entity) -> dict:
    return {"dataId": str(e.id), "uri": e.uri, "label": e.label,
            "typeNameEn": e.class_label, "classLabel": e.class_label,
            "properties": e.props or {}, "confidence": float(e.confidence or 0),
            "status": e.status}


def _rel_out(r: Relation, by_id: dict[int, Entity]) -> dict:
    s, o = by_id.get(r.subject_id), by_id.get(r.object_id)
    return {"dataId": str(r.id), "fromDataId": str(r.subject_id),
            "toDataId": str(r.object_id), "relationTypeNameEn": r.predicate,
            "predicate": r.predicate,
            "from": _node_out(s) if s else None, "to": _node_out(o) if o else None,
            "confidence": float(r.confidence or 0), "status": r.status}


def _schema(db: Session, pid: int) -> dict:
    p = db.query(Project).filter(Project.id == pid).first()
    gd = p.graph_data if p and isinstance(p.graph_data, dict) else {}
    return gd.get("schema") if isinstance(gd.get("schema"), dict) else {}


def _resolve(db: Session, pid: int, data_id: Any) -> Entity | None:
    if data_id is None:
        return None
    s = str(data_id).strip()
    if not s:
        return None
    if s.isdigit():
        return db.query(Entity).filter(Entity.project_id == pid,
                                       Entity.id == int(s)).first()
    e = db.query(Entity).filter(Entity.project_id == pid,
                                Entity.uri == s).first()
    if e:
        return e
    return db.query(Entity).filter(Entity.project_id == pid,
                                   Entity.label == s).first()


def _entities(db: Session, pid: int, include_classes: bool = False):
    q = db.query(Entity).filter(Entity.project_id == pid,
                                Entity.status.notin_(("merged", "rejected")))
    if not include_classes:
        q = q.filter(Entity.is_class_node.is_(False))
    return q.all()


def _relations(db: Session, pid: int):
    return db.query(Relation).filter(Relation.project_id == pid,
                                     Relation.status != "rejected").all()


# ── filter parser（受限，不 eval）──────────────────────────────────────────

_TOK = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bIN\b|=|'[^']*'|\"[^\"]*\"|[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.\u4e00-\u9fff]*|[-+]?\d+(?:\.\d+)?|true|false", re.I)


def _filter_entities(entities: list[Entity], expr: str | None) -> list[Entity]:
    expr = (expr or "").strip()
    if not expr:
        return entities
    tokens = _TOK.findall(expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        t = peek()
        pos += 1
        return t

    def attr(e: Entity, name: str):
        n = name.lower().replace("_", "")
        if n in ("dataid", "id"):
            return str(e.id)
        if n in ("label", "name"):
            return e.label
        if n in ("typenameen", "typenamene", "classlabel", "class", "type"):
            return e.class_label
        key = name.split(".", 1)[-1] if name.lower().startswith("properties.") else name
        return (e.props or {}).get(key)

    def value(tok: str):
        if tok is None:
            raise ValueError("filter 缺少值")
        if tok.startswith(("'", '"')):
            return tok[1:-1]
        if tok.lower() in ("true", "false"):
            return tok.lower() == "true"
        try:
            return float(tok) if "." in tok else int(tok)
        except ValueError:
            return tok

    def factor(e: Entity):
        t = peek()
        if t == "(":
            take()
            ok = expr_parse(e)
            if peek() == ")":
                take()
            return ok
        name = take()
        op = (take() or "").upper()
        if not name or op not in ("=", "IN"):
            raise ValueError("filter 语法错误")
        actual = attr(e, name)
        if op == "=":
            return str(actual) == str(value(take()))
        vals = []
        if peek() == "(":
            take()
        while peek() is not None and peek() != ")":
            vals.append(str(value(take())))
            if peek() == ",":
                take()
        if peek() == ")":
            take()
        return str(actual) in vals

    def term(e: Entity):
        ok = factor(e)
        while (peek() or "").upper() == "AND":
            take()
            rhs = factor(e)
            ok = ok and rhs
        return ok

    def expr_parse(e: Entity):
        ok = term(e)
        while (peek() or "").upper() == "OR":
            take()
            rhs = term(e)
            ok = ok or rhs
        return ok

    out: list[Entity] = []
    for e in entities:
        pos = 0
        if expr_parse(e):
            out.append(e)
    return out


# ── tool implementations ───────────────────────────────────────────────────

def graph_schema_get(db: Session, pid: int, args: dict) -> dict:
    s = _schema(db, pid)
    return {"classes": s.get("classes") or [],
            "objectProperties": s.get("object_properties") or [],
            "datatypeProperties": s.get("datatype_properties") or []}


def entity_type_list(db: Session, pid: int, args: dict) -> dict:
    from app.services.class_membership import class_distribution

    s = _schema(db, pid)
    counts = class_distribution(db, pid)
    items = []
    for c in s.get("classes") or []:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or "")
        items.append({"typeNameEn": label, "label": label,
                      "definition": c.get("definition") or "",
                      "count": int(counts.get(label, 0))})
    return {"items": items, "total": len(items)}


def relation_type_list(db: Session, pid: int, args: dict) -> dict:
    s = _schema(db, pid)
    counts: dict[str, int] = {}
    for r in _relations(db, pid):
        counts[r.predicate] = counts.get(r.predicate, 0) + 1
    items = []
    for o in s.get("object_properties") or []:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "")
        items.append({"typeNameEn": label, "label": label,
                      "domain": o.get("domain") or "",
                      "range": o.get("range") or "",
                      "count": counts.get(label, 0)})
    return {"items": items, "total": len(items)}


def entity_type_detail(db: Session, pid: int, args: dict) -> dict:
    name = str(args.get("typeNameEn") or "").strip()
    for c in _schema(db, pid).get("classes") or []:
        if isinstance(c, dict) and str(c.get("label") or "") == name:
            return {"type": c}
    return {"type": None, "error": f"实体类型不存在: {name}"}


def relation_type_detail(db: Session, pid: int, args: dict) -> dict:
    name = str(args.get("typeNameEn") or "").strip()
    for o in _schema(db, pid).get("object_properties") or []:
        if isinstance(o, dict) and str(o.get("label") or "") == name:
            return {"type": o}
    return {"type": None, "error": f"关系类型不存在: {name}"}


def entity_get(db: Session, pid: int, args: dict) -> dict:
    e = _resolve(db, pid, args.get("dataId"))
    return {"entity": _node_out(e) if e else None,
            "error": None if e else "实体不存在"}


def entities_get(db: Session, pid: int, args: dict) -> dict:
    ids = args.get("dataIds") or []
    found = [_resolve(db, pid, i) for i in ids]
    items = [_node_out(e) for e in found if e is not None]
    missing = [i for i, e in zip(ids, found) if e is None]
    return {"items": items, "missing": missing}


def relation_get(db: Session, pid: int, args: dict) -> dict:
    rid = args.get("dataId")
    try:
        r = db.query(Relation).filter(Relation.project_id == pid,
                                      Relation.id == int(rid)).first()
    except (TypeError, ValueError):
        r = None
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    return {"relation": _rel_out(r, by_id) if r else None,
            "error": None if r else "关系不存在"}


def relations_get(db: Session, pid: int, args: dict) -> dict:
    ids = {int(i) for i in (args.get("dataIds") or []) if str(i).isdigit()}
    rows = db.query(Relation).filter(Relation.project_id == pid,
                                     Relation.id.in_(ids or [-1])).all()
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    return {"items": [_rel_out(r, by_id) for r in rows]}


def entity_find(db: Session, pid: int, args: dict) -> dict:
    from app.services.class_membership import membership_ids

    ents = _entities(db, pid)
    tn = str(args.get("typeNameEn") or "").strip()
    if tn:
        # 多类成员（如 C250405N/C2504081）主类可能是别的类，靠 instance_of
        # 也算该类；只按 class_label 会漏。
        mids = membership_ids(db, pid, tn)
        ents = [e for e in ents if e.class_label == tn or e.id in mids]
    ents = _filter_entities(ents, args.get("filter"))
    limit = max(1, min(int(args.get("limit") or 50), 500))
    return {"items": [_node_out(e) for e in ents[:limit]],
            "total": len(ents), "returned": min(len(ents), limit)}


def neighbors_get(db: Session, pid: int, args: dict) -> dict:
    anchor = _resolve(db, pid, args.get("entityDataId"))
    if anchor is None:
        return {"items": [], "error": "锚点实体不存在"}
    direction = str(args.get("direction") or "BOTH").upper()
    rel_types = set(args.get("relationTypeNameEns") or [])
    neighbor_types = set(args.get("neighborTypeNameEns") or [])
    limit = max(1, min(int(args.get("limit") or 30), 200))
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    items = []
    for r in _relations(db, pid):
        if rel_types and r.predicate not in rel_types:
            continue
        other = None
        if direction in ("OUT", "BOTH") and r.subject_id == anchor.id:
            other = by_id.get(r.object_id)
        if other is None and direction in ("IN", "BOTH") and r.object_id == anchor.id:
            other = by_id.get(r.subject_id)
        if other is None:
            continue
        if neighbor_types and other.class_label not in neighbor_types:
            continue
        items.append({"entity": _node_out(other), "relation": _rel_out(r, by_id),
                      "direction": "OUT" if r.subject_id == anchor.id else "IN"})
    return {"items": items[:limit], "total": len(items)}


def relations_between(db: Session, pid: int, args: dict) -> dict:
    a = _resolve(db, pid, args.get("fromDataId"))
    b = _resolve(db, pid, args.get("toDataId"))
    if a is None or b is None:
        return {"items": [], "error": "起点或终点实体不存在"}
    direction = str(args.get("direction") or "BOTH").upper()
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    out = []
    for r in _relations(db, pid):
        if direction == "OUT" and not (r.subject_id == a.id and r.object_id == b.id):
            continue
        if direction == "IN" and not (r.subject_id == b.id and r.object_id == a.id):
            continue
        if direction == "BOTH" and {r.subject_id, r.object_id} != {a.id, b.id}:
            continue
        out.append(_rel_out(r, by_id))
    return {"items": out, "total": len(out)}


def subgraph_expand(db: Session, pid: int, args: dict) -> dict:
    anchors = [e for e in (_resolve(db, pid, i) for i in (args.get("anchorDataIds") or []))
               if e is not None]
    if not anchors:
        return {"nodes": [], "edges": [], "error": "锚点实体不存在"}
    hops = max(0, min(int(args.get("hops") or 1), 3))
    max_nodes = max(1, min(int(args.get("maxNodes") or 100), 500))
    direction = str(args.get("direction") or "BOTH").upper()
    ent_types = set(args.get("entityTypeNameEns") or [])
    rel_types = set(args.get("relationTypeNameEns") or [])
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    rels = _relations(db, pid)
    seen = {e.id for e in anchors}
    frontier = [e.id for e in anchors]
    for _ in range(hops):
        nxt = []
        for r in rels:
            if rel_types and r.predicate not in rel_types:
                continue
            pairs = []
            if direction in ("OUT", "BOTH") and r.subject_id in frontier:
                pairs.append(r.object_id)
            if direction in ("IN", "BOTH") and r.object_id in frontier:
                pairs.append(r.subject_id)
            for oid in pairs:
                if oid in seen or oid not in by_id:
                    continue
                if ent_types and by_id[oid].class_label not in ent_types:
                    continue
                seen.add(oid); nxt.append(oid)
                if len(seen) >= max_nodes:
                    break
            if len(seen) >= max_nodes:
                break
        frontier = nxt
        if not frontier or len(seen) >= max_nodes:
            break
    nodes = [_node_out(by_id[i]) for i in seen if i in by_id]
    edges = [_rel_out(r, by_id) for r in rels
             if r.subject_id in seen and r.object_id in seen
             and (not rel_types or r.predicate in rel_types)]
    return {"nodes": nodes, "edges": edges, "nodeCount": len(nodes),
            "edgeCount": len(edges)}


def _adjacency(db: Session, pid: int) -> tuple[dict[int, list[tuple[int, Relation]]], dict[int, Entity]]:
    by_id = {e.id: e for e in db.query(Entity).filter(Entity.project_id == pid).all()}
    adj: dict[int, list[tuple[int, Relation]]] = {}
    for r in _relations(db, pid):
        if r.subject_id in by_id and r.object_id in by_id:
            adj.setdefault(r.subject_id, []).append((r.object_id, r))
            adj.setdefault(r.object_id, []).append((r.subject_id, r))
    return adj, by_id


def paths_find(db: Session, pid: int, args: dict) -> dict:
    a = _resolve(db, pid, args.get("fromDataId"))
    b = _resolve(db, pid, args.get("toDataId"))
    if a is None or b is None:
        return {"paths": [], "error": "起点或终点实体不存在"}
    max_depth = max(1, min(int(args.get("maxDepth") or 4), 4))
    limit = max(1, min(int(args.get("limit") or 5), 50))
    adj, by_id = _adjacency(db, pid)
    paths: list[list[dict]] = []
    stack = [(a.id, [a.id], [])]
    while stack and len(paths) < limit:
        cur, node_path, rel_path = stack.pop()
        if len(rel_path) >= max_depth:
            continue
        for nxt, r in adj.get(cur, []):
            if nxt in node_path:
                continue
            new_nodes = node_path + [nxt]
            new_rels = rel_path + [r]
            if nxt == b.id:
                paths.append([_rel_out(rr, by_id) for rr in new_rels])
                if len(paths) >= limit:
                    break
            else:
                stack.append((nxt, new_nodes, new_rels))
    return {"paths": paths, "total": len(paths)}


def path_exists(db: Session, pid: int, args: dict) -> dict:
    return {"exists": bool(shortest_path(db, pid, args).get("path"))}


def shortest_path(db: Session, pid: int, args: dict) -> dict:
    a = _resolve(db, pid, args.get("fromDataId"))
    b = _resolve(db, pid, args.get("toDataId"))
    if a is None or b is None:
        return {"path": [], "error": "起点或终点实体不存在"}
    adj, by_id = _adjacency(db, pid)
    q = deque([(a.id, [a.id], [])])
    seen = {a.id}
    while q:
        cur, nodes, rels = q.popleft()
        if cur == b.id:
            return {"path": [_rel_out(r, by_id) for r in rels],
                    "nodeIds": nodes, "length": len(rels)}
        for nxt, r in adj.get(cur, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            q.append((nxt, nodes + [nxt], rels + [r]))
    return {"path": [], "length": None}


def semantic_search(db: Session, pid: int, args: dict) -> dict:
    from app.adapters.retrieval import RetrievalQuery, retrieve_structured

    q = str(args.get("queryText") or "").strip()
    if not q:
        return {"items": [], "error": "queryText 不能为空"}
    top_k = max(1, min(int(args.get("topK") or 10), 30))
    payload = retrieve_structured(RetrievalQuery(
        project_id=pid, question=q, top_k=top_k, use_graph=True,
        use_inferred=False), db=db)
    items = []
    for r in payload.get("results") or []:
        items.append({"rank": r.get("rank"), "score": r.get("score"),
                      "refType": r.get("ref_type"), "quote": r.get("quote"),
                      "docFile": r.get("doc_file"), "dataId": None})
    return {"items": items, "total": len(items), "query": q}


TOOL_IMPL = {
    "graph_schema_get": graph_schema_get,
    "entity_type_list": entity_type_list,
    "relation_type_list": relation_type_list,
    "entity_type_detail": entity_type_detail,
    "relation_type_detail": relation_type_detail,
    "entity_get": entity_get,
    "entities_get": entities_get,
    "relation_get": relation_get,
    "relations_get": relations_get,
    "entity_find": entity_find,
    "neighbors_get": neighbors_get,
    "relations_between": relations_between,
    "subgraph_expand": subgraph_expand,
    "paths_find": paths_find,
    "path_exists": path_exists,
    "shortest_path": shortest_path,
    "semantic_search": semantic_search,
}


def call_tool(name: str, db: Session, project_id: int, args: dict | None = None) -> dict:
    fn = TOOL_IMPL.get(name)
    if fn is None:
        raise ValueError(f"未知图谱工具: {name}")
    return fn(db, project_id, args or {})


__all__ = ["TOOL_IMPL", "call_tool"]
