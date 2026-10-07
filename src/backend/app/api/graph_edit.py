# app/api/graph_edit.py - 实例探索 · 实例/关系手动编辑（R13）
# 数据链路与画布保存同源：只改 Project.graph_data（blob 为准）→ 监听器重建
# entities/relations 行表 + outbox 同步 Neo4j；TTL 反序列化保持全生命周期同步。
# 每次编辑落一版 kind=manual（版本时间轴可追溯"谁在什么时候手动改了什么"）；
# MinIO 不可用时编辑不阻断，仅跳过打版（响应里 version_recorded=false 提示）。
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_project_role
from app.core.exceptions import APIError, NotFoundError
from app.infrastructure.database import Project, User
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/projects/{project_id}/graph", tags=["graph-edit"])

CLASS_TYPES = {"owl:Class", "Class"}
INDIVIDUAL_TYPES = {"owl:NamedIndividual", "Instance"}
TYPE_RELATIONS = {"instance_of", "rdf:type", "type"}

# 版本 label 列宽 64
_LABEL_MAX = 64


def _project_or_404(project_id: int, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return proj


def _nodes(gd: dict) -> list:
    return gd.setdefault("nodes", [])


def _edges(gd: dict) -> list:
    return gd.setdefault("edges", [])


def _node_data(n: dict) -> dict:
    d = n.get("data")
    return d if isinstance(d, dict) else {}


def _edge_relation(e: dict) -> str:
    d = e.get("data") if isinstance(e.get("data"), dict) else {}
    return str(d.get("relation") or e.get("label") or "")


def _is_type_edge(e: dict, class_ids: set[str]) -> bool:
    return _edge_relation(e) in TYPE_RELATIONS and str(e.get("target")) in class_ids


def _find_instance(nodes: list, node_id: str) -> dict:
    n = next((x for x in nodes if str(x.get("id")) == node_id
              and _node_data(x).get("type") in INDIVIDUAL_TYPES), None)
    if n is None:
        raise NotFoundError(f"实例不存在（或不是实例节点）: {node_id}")
    return n


def _class_ids(nodes: list) -> set[str]:
    return {str(n.get("id")) for n in nodes if _node_data(n).get("type") in CLASS_TYPES}


def _instance_of_edge(nodes: list, edges: list, node_id: str) -> dict | None:
    cids = _class_ids(nodes)
    return next((e for e in edges
                 if str(e.get("source")) == node_id and _is_type_edge(e, cids)), None)


def _find_edge(edges: list, edge_id: str) -> dict:
    e = next((x for x in edges if str(x.get("id") or "") == edge_id), None)
    if e is None:
        raise NotFoundError(f"关系不存在: {edge_id}")
    return e


def _label_of(nodes: list, node_id: str) -> str:
    n = next((x for x in nodes if str(x.get("id")) == node_id), None)
    return str(_node_data(n).get("label") or node_id) if n else str(node_id)


def _commit_edit(db: Session, project: Project, user: User, op_label: str,
                 description: str = "") -> dict:
    """TTL 同步 + 手动编辑落版（kind=manual）+ 提交（触发行表双写/Neo4j outbox）。"""
    from app.api.ontology import generate_ttl_from_graph_data

    gd = project.graph_data or {}
    project.graph_data = gd
    flag_modified(project, "graph_data")
    project.ttl_content = generate_ttl_from_graph_data(
        gd.get("nodes") or [], gd.get("edges") or [])

    version_recorded = True
    version_no = None
    try:
        from app.adapters.versioning import create_version

        ver = create_version(db, project, "manual", user.id,
                             label=op_label[:_LABEL_MAX], description=description[:500])
        version_no = ver.version_no
    except Exception as e:  # noqa: BLE001 —— 打版失败不阻断编辑（MinIO 不可用等）
        from app.core.logging import logger

        version_recorded = False
        logger.warning(f"[graph-edit] 项目 {project.id} 手动编辑打版失败: {e}")

    log_action(db, user.id, "graph.manual_edit", "project", project.id,
               {"op": op_label, "version_no": version_no})
    db.commit()
    return {"version_recorded": version_recorded, "version_no": version_no}


def _clean_props(raw: dict | None) -> dict:
    props = {}
    for k, v in (raw or {}).items():
        key = str(k).strip()
        if not key:
            continue
        props[key] = v if isinstance(v, (int, float, bool)) else str(v)
    return props


@router.post("/instances")
def add_instance(project_id: int, body: dict,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """手动新增实例：{class_node_id, label, properties?, position?}。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    label = str((body or {}).get("label") or "").strip()
    class_node_id = str((body or {}).get("class_node_id") or "").strip()
    if not label or not class_node_id:
        raise APIError("label 与 class_node_id 必填", code="INVALID_PARAMS", http_status=400)

    gd = project.graph_data or {}
    nodes = _nodes(gd)
    cls = next((n for n in nodes if str(n.get("id")) == class_node_id
                and _node_data(n).get("type") in CLASS_TYPES), None)
    if cls is None:
        raise APIError(f"类节点不存在: {class_node_id}", code="CLASS_NOT_FOUND", http_status=404)
    class_label = str(_node_data(cls).get("label") or "未分类")

    dup = next((n for n in nodes
                if _node_data(n).get("type") in INDIVIDUAL_TYPES
                and _node_data(n).get("label") == label
                and _node_data(n).get("class_label") == class_label), None)
    if dup is not None:
        raise APIError(f"实例「{label}」已存在于类「{class_label}」", code="DUPLICATE", http_status=400)

    pos = body.get("position") if isinstance(body.get("position"), dict) else {}
    node_id = f"manual_{secrets.token_hex(6)}"
    node = {
        "id": node_id, "type": "custom",
        "position": {"x": float(pos.get("x") or 120 + len(nodes) * 18 % 420),
                     "y": float(pos.get("y") or 120 + len(nodes) * 26 % 320)},
        "data": {"label": label, "type": "owl:NamedIndividual", "class_label": class_label,
                 "properties": _clean_props(body.get("properties")),
                 "_source": "manual", "_created_by": current_user.username},
    }
    edge = {
        "id": f"edge_{secrets.token_hex(6)}", "source": node_id, "target": class_node_id,
        "data": {"label": "rdf:type", "relation": "instance_of"},
    }
    nodes.append(node)
    _edges(gd).append(edge)
    project.graph_data = gd

    res = _commit_edit(db, project, current_user,
                       f"手动：新增实例「{label}」",
                       f"类 {class_label} · 属性 {len(node['data']['properties'])} 项")
    return {"node_id": node_id, "class_label": class_label, **res}


@router.patch("/instances/{node_id}")
def update_instance(project_id: int, node_id: str, body: dict,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """手动修改实例：{label?, class_node_id?, properties?}（缺省字段保持不变）。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    body = body or {}
    gd = project.graph_data or {}
    nodes = _nodes(gd)
    node = _find_instance(nodes, node_id)
    data = _node_data(node)

    changes: list[str] = []
    if "label" in body:
        new_label = str(body.get("label") or "").strip()
        if not new_label:
            raise APIError("label 不能为空", code="INVALID_PARAMS", http_status=400)
        if new_label != data.get("label"):
            changes.append(f"名称「{data.get('label')}」→「{new_label}」")
            data["label"] = new_label

    if "class_node_id" in body:
        new_cid = str(body.get("class_node_id") or "").strip()
        cls = next((n for n in nodes if str(n.get("id")) == new_cid
                    and _node_data(n).get("type") in CLASS_TYPES), None)
        if cls is None:
            raise APIError(f"类节点不存在: {new_cid}", code="CLASS_NOT_FOUND", http_status=404)
        new_class_label = str(_node_data(cls).get("label") or "未分类")
        if new_class_label != data.get("class_label"):
            changes.append(f"类别「{data.get('class_label')}」→「{new_class_label}」")
        data["class_label"] = new_class_label
        # 重挂 rdf:type 边（删旧挂新）
        edges = _edges(gd)
        old = _instance_of_edge(nodes, edges, node_id)
        if old is not None and str(old.get("target")) != new_cid:
            edges.remove(old)
        if old is None or str(old.get("target")) != new_cid:
            edges.append({
                "id": f"edge_{secrets.token_hex(6)}", "source": node_id, "target": new_cid,
                "data": {"label": "rdf:type", "relation": "instance_of"},
            })

    if "properties" in body:
        props = _clean_props(body.get("properties"))
        if props != (data.get("properties") or {}):
            changes.append(f"属性调整为 {len(props)} 项")
        data["properties"] = props

    if not changes:
        raise APIError("没有需要修改的字段", code="NO_CHANGES", http_status=400)
    project.graph_data = gd
    res = _commit_edit(db, project, current_user,
                       f"手动：修改实例「{data.get('label')}」",
                       "；".join(changes))
    return {"node_id": node_id, "changes": changes, **res}


@router.delete("/instances/{node_id}")
def delete_instance(project_id: int, node_id: str,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """手动删除实例：连带删除其全部关系边与 rdf:type 边。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    gd = project.graph_data or {}
    nodes = _nodes(gd)
    node = _find_instance(nodes, node_id)
    label = _node_data(node).get("label") or node_id
    nodes.remove(node)
    edges = _edges(gd)
    removed = [e for e in edges
               if str(e.get("source")) == node_id or str(e.get("target")) == node_id]
    for e in removed:
        edges.remove(e)
    project.graph_data = gd
    res = _commit_edit(db, project, current_user,
                       f"手动：删除实例「{label}」",
                       f"连带删除 {len(removed)} 条关系边")
    return {"deleted": True, "removed_edges": len(removed), **res}


@router.post("/relations")
def add_relation(project_id: int, body: dict,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """手动新增关系：{source_node_id, target_node_id, predicate}（实例间）。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    body = body or {}
    src = str(body.get("source_node_id") or "").strip()
    dst = str(body.get("target_node_id") or "").strip()
    predicate = str(body.get("predicate") or "").strip()
    if not src or not dst or not predicate:
        raise APIError("source_node_id、target_node_id、predicate 必填",
                       code="INVALID_PARAMS", http_status=400)
    if src == dst:
        raise APIError("不能给自己添加关系", code="INVALID_PARAMS", http_status=400)

    gd = project.graph_data or {}
    nodes = _nodes(gd)
    s_node = _find_instance(nodes, src)
    d_node = _find_instance(nodes, dst)
    edges = _edges(gd)
    dup = next((e for e in edges if str(e.get("source")) == src
                and str(e.get("target")) == dst
                and _edge_relation(e) == predicate), None)
    if dup is not None:
        raise APIError("相同的三元组关系已存在", code="DUPLICATE", http_status=400)
    edge = {
        "id": f"edge_{secrets.token_hex(6)}", "source": src, "target": dst,
        "data": {"label": predicate, "relation": predicate,
                 "_source": "manual", "_created_by": current_user.username},
    }
    edges.append(edge)
    project.graph_data = gd
    res = _commit_edit(db, project, current_user,
                       f"手动：新增关系「{_label_of(nodes, src)} →{predicate}→ {_label_of(nodes, dst)}」")
    return {"edge_id": edge["id"], **res}


@router.patch("/relations/{edge_id}")
def update_relation(project_id: int, edge_id: str, body: dict,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """手动修改关系谓词：{predicate}。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    body = body or {}
    predicate = str(body.get("predicate") or "").strip()
    if not predicate:
        raise APIError("predicate 必填", code="INVALID_PARAMS", http_status=400)
    gd = project.graph_data or {}
    nodes = _nodes(gd)
    edges = _edges(gd)
    edge = _find_edge(edges, edge_id)
    if _edge_relation(edge) in TYPE_RELATIONS:
        raise APIError("rdf:type 边不支持改谓词（请修改实例的类别）",
                       code="INVALID_EDGE", http_status=400)
    old_pred = _edge_relation(edge)
    dup = next((e for e in edges if e is not edge
                and str(e.get("source")) == edge.get("source")
                and str(e.get("target")) == edge.get("target")
                and _edge_relation(e) == predicate), None)
    if dup is not None:
        raise APIError("相同的三元组关系已存在", code="DUPLICATE", http_status=400)
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    data["relation"] = predicate
    data["label"] = predicate
    edge["data"] = data
    project.graph_data = gd
    res = _commit_edit(db, project, current_user,
                       f"手动：修改关系谓词「{old_pred}」→「{predicate}」")
    return {"edge_id": edge_id, **res}


@router.delete("/relations/{edge_id}")
def delete_relation(project_id: int, edge_id: str,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """手动删除关系边。"""
    require_project_role(project_id, "editor", current_user, db)
    project = _project_or_404(project_id, db)
    gd = project.graph_data or {}
    nodes = _nodes(gd)
    edges = _edges(gd)
    edge = _find_edge(edges, edge_id)
    pred = _edge_relation(edge)
    edges.remove(edge)
    project.graph_data = gd
    src_label = _label_of(nodes, str(edge.get("source")))
    dst_label = _label_of(nodes, str(edge.get("target")))
    res = _commit_edit(db, project, current_user,
                       f"手动：删除关系「{src_label} →{pred}→ {dst_label}」")
    return {"deleted": True, **res}
