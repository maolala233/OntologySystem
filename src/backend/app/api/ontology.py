import asyncio
import json
import os
import tempfile
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import pandas as pd
import requests as req
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from rdflib import OWL, RDF, RDFS, Graph
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.auth import get_current_user
from app.core.config import ensure_dirs, settings
from app.core.deps import is_super_admin, require_module
from app.core.logging import logger
from app.infrastructure.database import KnowledgeDomain, Project, ProjectMember, UploadedDocument, User, get_db
from app.infrastructure.neo4j_client import neo4j_client
from app.schemas.extraction import (
    SaveGraphRequest,
)
from app.schemas.ontology import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])

EXTRACTION_SEMAPHORE = asyncio.Semaphore(3)

# ─────────────────────────────────────────────
#  CRUD 基础接口（保持不变）
# ─────────────────────────────────────────────

# 获取我的项目列表
@router.get("/my", response_model=list[ProjectResponse])
def get_my_projects(scope: str = "mine", current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """我的项目列表；scope=all 仅超级管理员可用，返回全部项目（含归属人）。"""
    from sqlalchemy.orm import joinedload
    from app.core.deps import is_super_admin
    q = db.query(Project).options(joinedload(Project.owner))
    if scope == "all":
        if not is_super_admin(current_user):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅超级管理员可查看全部项目")
        return q.order_by(Project.id).all()
    return q.filter(Project.owner_id == current_user.id).order_by(Project.id).all()

# 获取公共已发布项目
@router.get("/public", response_model=list[ProjectResponse])
def get_public_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.is_published == True).all()
    return projects

# 获取单个项目详情
@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy.orm import joinedload

    # 使用 joinedload 预加载 domain 和 owner 信息
    project = db.query(Project).options(
        joinedload(Project.domain),
        joinedload(Project.owner)
    ).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from app.core.deps import require_project_role
    require_project_role(project_id, "viewer", current_user, db)  # R11：超管直通/成员/owner/已发布公开
    return project

# 创建新项目
@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    domain_id: Optional[int] = None,
    domain_name: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 处理知识域关联
    resolved_domain_id = domain_id

    # 如果提供了 domain_name，查找或创建知识域
    if domain_name:
        domain = db.query(KnowledgeDomain).filter(
            KnowledgeDomain.name == domain_name
        ).first()
        if not domain:
            # 创建新知识域
            new_domain = KnowledgeDomain(name=domain_name)
            db.add(new_domain)
            db.commit()
            db.refresh(new_domain)
            resolved_domain_id = new_domain.id
        else:
            resolved_domain_id = domain.id

    new_project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
        domain_id=resolved_domain_id,
        graph_data={"nodes": [], "edges": []},
        is_published=False
    )
    db.add(new_project)
    db.flush()
    # M3-7 收口（02 §3.3）：owner 与 members 冗余同步——建项目即写 members 行，
    # 免除 require_project_role 的 owner_id 兜底查询（M1 遗留债务）
    db.add(ProjectMember(project_id=new_project.id, user_id=current_user.id,
                         role="owner", created_by=current_user.id))
    db.commit()
    db.refresh(new_project)
    return new_project

# 更新项目（保存草稿）
@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_update: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="No permission to modify this project")

    # ★ 已发布状态下禁止修改知识域
    if project_update.domain_id is not None and db_project.is_published:
        raise HTTPException(
            status_code=400,
            detail="项目已发布，无法修改知识域。请先取消发布状态，然后再修改知识域配置。"
        )

    if project_update.name is not None:
        db_project.name = project_update.name
    if project_update.description is not None:
        db_project.description = project_update.description
    if project_update.graph_data is not None:
        existing_graph_data = db_project.graph_data or {}
        new_graph_data = project_update.graph_data
        if isinstance(existing_graph_data, dict) and "schema" in existing_graph_data:
            if "schema" not in new_graph_data:
                new_graph_data["schema"] = existing_graph_data["schema"]
        db_project.graph_data = new_graph_data
    if project_update.domain_id is not None:
        db_project.domain_id = project_update.domain_id

    db.commit()
    db.refresh(db_project)
    return db_project


@router.post("/{project_id}/schema/import-from", dependencies=[Depends(require_module("schema_build"))])
def import_schema_from_project(
    project_id: int,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """跨项目框架复用：把源项目（可指定其框架版本快照）的 TBox 复制为本项目的框架层。

    body: {source_project_id: int, version_no?: int, persist?: bool}
    （version_no 缺省 = 源项目当前画布框架；persist 缺省 true）。
    语义：替换本项目框架层（类节点 + 类间关系 + schema 键），保留本项目已有实例节点/边。
    persist=false 时只计算合并结果并返回（graph 字段），不写库——由前端把结果放到画布，
    用户点「保存」后经常规保存链路落库并同步 Neo4j。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.adapters.versioning import create_version, load_graph_for
    from app.core.deps import require_module, require_project_role
    from app.core.exceptions import APIError, NotFoundError
    from app.services.audit_service import log_action
    from app.services.graph_rows import CLASS_TYPES

    require_project_role(project_id, "editor", current_user, db)
    target = db.query(Project).filter(Project.id == project_id).first()
    if target is None:
        raise NotFoundError(f"项目不存在: {project_id}")

    body = body or {}
    src_id = body.get("source_project_id")
    version_no = body.get("version_no")
    if not isinstance(src_id, int):
        raise APIError("source_project_id 必须为整数", code="INVALID_SOURCE_PROJECT", http_status=400)
    if src_id == project_id:
        raise APIError("源项目不能是当前项目", code="INVALID_SOURCE_PROJECT", http_status=400)
    source = db.query(Project).filter(Project.id == src_id).first()
    if source is None:
        raise NotFoundError(f"源项目不存在: {src_id}")
    require_project_role(src_id, "viewer", current_user, db)  # 源项目需对用户可见

    if version_no is not None:
        if not isinstance(version_no, int):
            raise APIError("version_no 必须为整数", code="INVALID_SCHEMA_VERSION", http_status=400)
        from app.infrastructure.database import OntologyVersion
        ver = (db.query(OntologyVersion)
               .filter(OntologyVersion.project_id == src_id,
                       OntologyVersion.version_no == version_no)
               .first())
        if ver is None:
            raise NotFoundError(f"源项目框架版本不存在: v{version_no}")
        gd = load_graph_for(db, source, ver) or {}
        source_label = f"v{version_no}"
    else:
        gd = source.graph_data if isinstance(source.graph_data, dict) else {}
        source_label = "当前框架"

    # 提取源框架（TBox）：类节点 + 类间关系 + schema 键
    class_nodes = [n for n in (gd.get("nodes") or [])
                   if (n.get("data") or {}).get("type") in CLASS_TYPES]
    if not class_nodes:
        raise APIError(f"源项目{source_label}没有可用框架", code="NO_SCHEMA_IN_SOURCE", http_status=400)
    class_ids = {str(n.get("id")) for n in class_nodes}
    class_edges = [e for e in (gd.get("edges") or [])
                   if str(e.get("source")) in class_ids and str(e.get("target")) in class_ids]
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) and gd["schema"].get("classes") else None
    if schema is None:
        label_by_id = {str(n.get("id")): str((n.get("data") or {}).get("label") or "") for n in class_nodes}
        schema = {
            "classes": [
                {"label": (n.get("data") or {}).get("label"),
                 "aliases": (n.get("data") or {}).get("aliases") or [],
                 "definition": (n.get("data") or {}).get("definition") or ""}
                for n in class_nodes
            ],
            "object_properties": [
                {"label": (e.get("data") or {}).get("label") or (e.get("data") or {}).get("relation"),
                 "domain": label_by_id.get(str(e.get("source"))),
                 "range": label_by_id.get(str(e.get("target")))}
                for e in class_edges
                if (e.get("data") or {}).get("label") or (e.get("data") or {}).get("relation")
            ],
        }

    # 合并进目标：替换框架层，保留目标已有实例节点/边；导入类 id 与实例 id 冲突时跳过
    tgd = dict(target.graph_data) if isinstance(target.graph_data, dict) else {}
    old_nodes = tgd.get("nodes") or []
    inst_nodes = [n for n in old_nodes if (n.get("data") or {}).get("type") not in CLASS_TYPES]
    inst_ids = {str(n.get("id")) for n in inst_nodes}
    imported_nodes = [n for n in class_nodes if str(n.get("id")) not in inst_ids]
    old_edges = tgd.get("edges") or []
    inst_edges = [e for e in old_edges
                  if str(e.get("source")) in inst_ids or str(e.get("target")) in inst_ids]
    tgd["nodes"] = imported_nodes + inst_nodes
    tgd["edges"] = class_edges + inst_edges
    tgd["schema"] = schema

    if body.get("persist") is False:
        return {"persisted": False,
                "imported_classes": len(imported_nodes), "imported_relations": len(class_edges),
                "graph": {"nodes": tgd["nodes"], "edges": tgd["edges"], "schema": schema}}

    target.graph_data = tgd
    flag_modified(target, "graph_data")
    db.commit()  # 触发 graph-rows 监听器 → entities/relations 双写

    # 留痕：自动打一版框架快照（失败不影响导入结果）
    new_version_no = None
    try:
        ver = create_version(db, target, "schema", current_user.id,
                             label=f"导入框架（{source.name} {source_label}）")
        db.commit()
        new_version_no = ver.version_no
    except Exception:  # noqa: BLE001
        db.rollback()

    log_action(db, current_user.id, "schema.import", "project", project_id,
               {"source_project_id": src_id, "source_version_no": version_no,
                "classes": len(imported_nodes), "relations": len(class_edges)})
    db.commit()
    return {"imported_classes": len(imported_nodes), "imported_relations": len(class_edges),
            "version_no": new_version_no}


# 删除项目
@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    # 超管（username='admin'）可删除任何项目；普通管理员仅限自己名下
    if db_project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="No permission to delete this project")

    # M4：Neo4j 清理改走 outbox（删除 → 监听器发 project.deleted → worker-graph
    # 消费 DETACH DELETE；Oxigraph 命名图由 worker-rdf 清理），API 进程不直写图存储
    # 成员行先清理（M1 起建项目同步建 owner 成员行，外键约束先于项目行删除）
    from app.infrastructure.database import ProjectMember

    db.query(ProjectMember).filter(ProjectMember.project_id == project_id).delete()
    db.delete(db_project)
    db.commit()

    return {"message": "Project deleted successfully", "neo4j_sync": "async"}


# ─────────────────────────────────────────────
#  发布 / 取消发布 —— M3-6 收编至 app/api/publications.py
#  （门禁链 + 版本快照 + publications + 审计；is_published 兼容写保留）
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  ★ 模块一 API 1：骨架提取 (Schema Extraction)
#
#  POST /api/projects/{project_id}/extract-schema
#  输入：文件 + 可选 user_intent
#  输出：仅含 Class 和 ObjectProperty 的骨架图
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ★ 基于已上传文档 ID 进行骨架提取（新增）
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ★ 基于已上传文档 ID 进行实例提取（新增）
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ★ 模块一 API 2：实例提取 (Instance Extraction)
#
#  POST /api/projects/{project_id}/extract-instances
#  输入：文件 + 用户审核后的 schema_graph JSON
#  输出：完整图（Schema + 实例）
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  旧版上传接口（兼容，内部已改为两阶段）
# ─────────────────────────────────────────────

@router.post("/{project_id}/parse-ttl-schema")
async def parse_ttl_schema(
    project_id: int,
    files: list[UploadFile] = File(..., description="TTL 或 JSON 文件列表"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    解析 TTL/JSON 文件提取骨架 Schema（仅包含类和 ObjectProperty）。
    用于 Step 1 构建类结构，之后可进行 Step 2 实例提取。
    支持 TTL 文件和平台导出的 JSON 格式。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_paths = []
    try:
        for uploaded_file in files:
            fname = uploaded_file.filename.lower()
            if not (fname.endswith('.json') or fname.endswith(RDF_IMPORT_EXTS)):
                raise HTTPException(status_code=400, detail=f"只支持 RDF（ttl/nt/rdf/owl/xml/jsonld/trig）或平台 JSON 文件：{uploaded_file.filename}")
            temp_path = os.path.join(settings.TEMP_DIR, uploaded_file.filename)
            with open(temp_path, "wb") as buf:
                buf.write(await uploaded_file.read())
            temp_paths.append(temp_path)

        logger.info(f"[parse-ttl-schema] 已保存 {len(temp_paths)} 个文件")

        all_nodes = []
        all_edges = []
        combined_ttl_content = ""
        has_json = any(p.lower().endswith('.json') for p in temp_paths)
        has_ttl = any(p.lower().endswith(RDF_IMPORT_EXTS) for p in temp_paths)

        for temp_path in temp_paths:
            if temp_path.lower().endswith('.json'):
                with open(temp_path, encoding='utf-8') as f:
                    json_data = json.load(f)
                nodes, edges = _convert_json_schema_to_graph(json_data)
                all_nodes.extend(nodes)
                all_edges.extend(edges)
            else:
                with open(temp_path, encoding='utf-8') as ttl_file:
                    raw_content = ttl_file.read()
                # 非 turtle 的 RDF 序列化（nt/rdfxml/jsonld/trig/owl）先归一化为 turtle
                ttl_content = _normalize_rdf_to_turtle(raw_content, None, os.path.basename(temp_path))
                combined_ttl_content += ttl_content + "\n\n"
                nodes, edges = convert_ttl_to_graph_data(ttl_content)
                all_nodes.extend(nodes)
                all_edges.extend(edges)

        seen_node_ids = set()
        unique_nodes = []
        for node in all_nodes:
            if node['id'] not in seen_node_ids:
                seen_node_ids.add(node['id'])
                unique_nodes.append(node)

        seen_edge_ids = set()
        unique_edges = []
        for edge in all_edges:
            edge_data = edge.get('data', {})
            edge_key = f"{edge['source']}_{edge['target']}_{edge_data.get('relation', '')}_{edge_data.get('label', '')}"
            if edge_key not in seen_edge_ids:
                seen_edge_ids.add(edge_key)
                unique_edges.append(edge)

        if has_ttl:
            schema_dict = extract_schema_from_ttl(combined_ttl_content)
        else:
            schema_dict = _build_schema_from_json_data(unique_nodes, unique_edges)

        graph_data = {
            "nodes": unique_nodes,
            "edges": unique_edges,
        }

        db_project.graph_data = {
            "schema": schema_dict,
            **graph_data,
        }
        if combined_ttl_content:
            db_project.ttl_content = combined_ttl_content
        db.commit()

        class_count = len([n for n in unique_nodes if is_owl_class_type(n['data'].get('type'))])

        return {
            "schema_graph": schema_dict,
            "graph_data": graph_data,
            "message": f"骨架解析成功：{class_count} 个类",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[parse-ttl-schema] 错误：{e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"骨架解析失败：{str(e)}")
    finally:
        for temp_path in temp_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)


@router.post("/{project_id}/upload-ttl")
async def upload_ttl_file(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """专门用于上传 TTL 文件的端点（完整解析，包含实例）。"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    fname_lower = file.filename.lower()
    is_json = fname_lower.endswith('.json')
    if not is_json and not fname_lower.endswith(RDF_IMPORT_EXTS):
        raise HTTPException(status_code=400, detail="Only RDF (ttl/nt/rdf/owl/xml/jsonld/trig) and platform JSON files are accepted")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_path = os.path.join(settings.TEMP_DIR, file.filename)
    with open(temp_path, "wb") as buf:
        buf.write(await file.read())

    try:
        if is_json:
            with open(temp_path, encoding='utf-8') as json_file:
                json_data = json.load(json_file)
            nodes, edges = _convert_json_schema_to_graph(json_data)

            # 构建 schema 并保存 graph_data，确保前端能展示实例框架
            schema_dict = _build_schema_from_json_data(nodes, edges)
            db_project.graph_data = {
                "schema": schema_dict,
                "nodes": nodes,
                "edges": edges,
            }

            # 从 nodes+edges 生成 TTL，保持 ttl_content 一致
            try:
                ttl_content = generate_ttl_from_graph_data(nodes, edges)
                db_project.ttl_content = ttl_content
            except Exception as ttl_err:
                from app.core.logging import logger
                logger.warning(f"JSON导入后生成TTL失败: {ttl_err}")
                db_project.ttl_content = ""

            db.commit()

            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": file.filename,
                "message": f"成功解析 JSON 文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
            }
        else:
            with open(temp_path, encoding='utf-8') as ttl_file:
                raw_content = ttl_file.read()
            # 非 turtle 的 RDF 序列化（nt/rdfxml/jsonld/trig/owl）先归一化为 turtle
            ttl_content = _normalize_rdf_to_turtle(raw_content, None, file.filename)

            nodes, edges = convert_ttl_to_graph_data(ttl_content)
            db_project.ttl_content = ttl_content

            # 同时保存 graph_data，确保前端能展示实例框架
            schema_dict = build_schema_from_graph_data(nodes, edges)
            db_project.graph_data = {
                "schema": schema_dict,
                "nodes": nodes,
                "edges": edges,
            }

            db.commit()

            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": file.filename,
                "message": f"成功解析 TTL 文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
            }
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"TTL parsing error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"TTL 文件解析失败：{str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ─────────────────────────────────────────────
#  保存草稿（含 TTL 全生命周期同步）
# ─────────────────────────────────────────────

@router.post("/{project_id}/update-ontology")
async def update_ontology(
    project_id: int,
    request: SaveGraphRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    保存画布当前状态（草稿）。
    全生命周期 TTL 同步：将前端最新 nodes+edges 反向序列化为 TTL，
    保证下载的 TTL 永远是最新快照。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="No permission to update this project")

    try:
        nodes = request.nodes
        edges = request.edges

        # 更新图数据
        existing_schema = (db_project.graph_data or {}).get("schema", {})
        db_project.graph_data = {
            "schema": existing_schema,
            "nodes": nodes,
            "edges": edges,
        }

        # M4：Neo4j 同步改走 outbox（graph_data 变更 → 监听器发 project.graph_rebuilt
        # → worker-graph 消费），API 进程不再直写图数据库（02 §5 单写者模式）
        # ★ 全生命周期 TTL 同步：重新序列化覆盖物理 TTL
        ttl_content = generate_ttl_from_graph_data(nodes, edges)
        db_project.ttl_content = ttl_content
        db.commit()

        return {
            "nodes": nodes,
            "edges": edges,
            "message": f"草稿已保存并同步 TTL，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
            "ttl_updated": True,
            "neo4j_synced": True,
        }

    except Exception as e:
        logger.error(f"Update ontology error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新本体失败: {str(e)}")


# ─────────────────────────────────────────────
#  下载 TTL
# ─────────────────────────────────────────────

@router.get("/{project_id}/download-ttl")
def download_ttl(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """下载 TTL（Turtle 序列化）。与 GET /{project_id}/export?format=turtle 等价，旧路径保留兼容。"""
    from app.adapters.exporting import ExportFormat

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    _check_export_permission(db_project, current_user)
    return _build_export_response(db_project, ExportFormat.TURTLE)


def _convert_json_schema_to_graph(json_data: dict) -> tuple:
    """
    将平台导出的 JSON 格式（entities + relationships）转换为 graph nodes + edges。
    JSON 格式与 download-json 导出的格式一致。

    ★ 实例处理：实例（owl:NamedIndividual）创建为独立节点，
    通过 relationships 中的 type 关系找到所属类。
    前端通过 expandedNodeIds 控制实例的显示/隐藏。
    """
    import hashlib
    import math

    entities = json_data.get("entities", [])
    relationships = json_data.get("relationships", [])

    # 先处理 schema 实体（类），再处理实例
    schema_entities = []
    instance_entities = []

    for ent in entities:
        entity_type = ent.get("entity_type", "")
        node_type = ent.get("type", "")
        if entity_type == "实例" or node_type == "owl:NamedIndividual":
            instance_entities.append(ent)
        else:
            schema_entities.append(ent)

    node_id_map = {}  # name -> node_id (仅 schema 实体)
    nodes = []
    edges = []

    cols = max(1, int(math.ceil(math.sqrt(len(schema_entities)))))

    # 1. 创建 schema 节点（类）
    for idx, ent in enumerate(schema_entities):
        name = ent.get("name", "")
        if not name:
            continue
        node_type = ent.get("type", "owl:Class")
        props = ent.get("properties", {})
        desc = ent.get("description", "")

        raw_id = ""
        node_id = f"node_{hashlib.md5(name.encode()).hexdigest()[:12]}"

        col = idx % cols
        row = idx // cols
        pos_x = col * 280 + 100
        pos_y = row * 160 + 100

        node_data = {
            "label": name,
            "type": node_type,
            "properties": props,
        }
        if desc:
            node_data["description"] = desc

        node = {
            "id": node_id,
            "type": "custom",
            "position": {"x": pos_x, "y": pos_y},
            "data": node_data,
        }
        nodes.append(node)
        node_id_map[name] = node_id

    # 2. 创建实例节点（owl:NamedIndividual）
    # 通过 relationships 中的 type 关系找到实例所属的类
    instance_name_to_id = {}  # 实例 name -> instance node_id
    instance_counter = 0

    for ent in instance_entities:
        name = ent.get("name", "")
        if not name:
            continue

        instance_counter += 1
        instance_id = f"inst_{hashlib.md5(name.encode()).hexdigest()[:12]}_{instance_counter}"
        props = ent.get("properties", {})
        desc = ent.get("description", "")

        node_data = {
            "label": name,
            "type": "owl:NamedIndividual",
            "properties": props,
        }
        if desc:
            node_data["description"] = desc

        node = {
            "id": instance_id,
            "type": "custom",
            "position": {"x": 0, "y": 0},  # 实例位置由前端布局
            "data": node_data,
        }
        nodes.append(node)
        instance_name_to_id[name] = instance_id

    # 3. 处理 relationships 中的边
    # 处理所有 relationships
    # ★ 关键：当实例名和类名相同时，需要根据 relation 类型判断 source/target 是实例还是类
    # - type 关系：source 一定是实例，target 一定是类
    # - 其他关系：source/target 优先查类，如果不存在则查实例
    for rel in relationships:
        source_name = rel.get("source_name", "")
        target_name = rel.get("target_name", "")
        relation = rel.get("relation", "")
        label = rel.get("label", relation or "相关")

        if relation == "type":
            # type 关系：source 是实例，target 是类
            source_id = instance_name_to_id.get(source_name)
            target_id = node_id_map.get(target_name)
        else:
            # 其他关系：优先查类，不存在则查实例
            source_id = node_id_map.get(source_name) or instance_name_to_id.get(source_name)
            target_id = node_id_map.get(target_name) or instance_name_to_id.get(target_name)

        if not source_id or not target_id:
            continue

        # type 关系：实例 -> 类，转为 instance_of 边
        if relation == "type":
            edge = {
                "id": f"e_{source_id}_{target_id}_instance_of_rdf:type",
                "source": source_id,
                "target": target_id,
                "type": "smoothstep",
                "data": {
                    "label": "rdf:type",
                    "relation": "instance_of",
                    "properties": rel.get("properties", {}),
                },
            }
            edges.append(edge)
        else:
            edge = {
                "id": f"e_{source_id}_{target_id}_{relation}_{label}",
                "source": source_id,
                "target": target_id,
                "type": "smoothstep",
                "data": {
                    "label": label,
                    "relation": relation,
                    "properties": rel.get("properties", {}),
                },
            }
            edges.append(edge)

    return nodes, edges


def _build_schema_from_json_data(nodes: list[dict], edges: list[dict]) -> dict:
    """
    从 graph nodes 和 edges 构建 schema 字典（兼容 TTL 导入场景）。
    用于纯 JSON 上传时生成 schema_graph 数据结构。
    """
    classes = []
    object_properties = []

    # 先构建 node_id -> label 映射
    node_id_to_label = {}
    for node in nodes:
        data = node.get("data", {})
        label = data.get("label", "")
        node_id_to_label[node.get("id", "")] = label

    class_info = {}
    for node in nodes:
        data = node.get("data", {})
        label = data.get("label", "")
        node_type = data.get("type", "owl:Class")
        class_info[node["id"]] = {
            "id": node["id"],
            "label": label,
            "type": node_type,
            "description": data.get("description", ""),
            "properties": data.get("properties", {}),
        }

    for node_id, info in class_info.items():
        # 从 properties dict 提取属性列表
        prop_defs = []
        for prop_name, prop_type in info.get("properties", {}).items():
            prop_defs.append({
                "name": prop_name,
                "data_type": prop_type if isinstance(prop_type, str) else "string",
                "description": "",
            })

        classes.append({
            "id": info["id"],
            "label": info["label"],
            "type": info["type"],
            "description": info.get("description", ""),
            "parent_classes": [],
            "properties": list(info.get("properties", {}).keys()),
            "data_properties": list(info.get("properties", {}).keys()),
            "direct_properties": list(info.get("properties", {}).keys()),
            "property_definitions": prop_defs,
        })

    for edge in edges:
        data = edge.get("data", {})
        relation = data.get("relation", "")

        # 跳过内部边
        if relation in ("rdf:type", "type", "subClassOf", "subclass_of"):
            continue

        source_info = class_info.get(edge.get("source", ""))
        target_info = class_info.get(edge.get("target", ""))
        if not source_info or not target_info:
            continue
        object_properties.append({
            "id": edge["id"],
            "label": data.get("label", relation),
            "domain": source_info["label"],
            "range": target_info["label"],
        })

    return {
        "classes": classes,
        "object_properties": object_properties,
    }


@router.get("/{project_id}/download-json")
def download_json(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """下载平台 JSON（entities+relationships+meta）。

    与 GET /{project_id}/export?format=json 等价，旧路径保留兼容。
    """
    from app.adapters.exporting import ExportFormat

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    _check_export_permission(db_project, current_user)
    return _build_export_response(db_project, ExportFormat.JSON)


# 导入侧接受的 RDF 扩展名（.json 走平台 JSON 分流，不在此列）
RDF_IMPORT_EXTS = ('.ttl', '.nt', '.ntriples', '.n3', '.rdf', '.owl', '.xml', '.jsonld', '.trig')


def _normalize_rdf_to_turtle(content: str, parse_format: str | None, filename: str = "") -> str:
    """任意 RDF 序列化归一化为 turtle——内部管线统一以 turtle 流转。

    parse_format 来自 sniff_rdf_format（None 视为 turtle）；解析失败直接抛 400。
    """
    from app.adapters.exporting import sniff_rdf_format

    fmt = parse_format if parse_format is not None else sniff_rdf_format(filename, content) or "turtle"
    if fmt == "turtle":
        return content
    try:
        from rdflib import ConjunctiveGraph, Graph
        g = ConjunctiveGraph() if fmt == "trig" else Graph()
        g.parse(data=content, format=fmt)
        return g.serialize(format="turtle")
    except Exception as e:
        logger.error(f"[rdf-import] 解析失败 format={fmt} file={filename}: {e}")
        raise HTTPException(status_code=400, detail=f"RDF 文件解析失败（{fmt} 格式）：{e}")


def _build_export_response(db_project: Project, fmt) -> Response:
    """统一导出：JSON=平台格式（含 meta）；其余=semantica 适配层 RDF 序列化。"""
    from app.adapters.exporting import ExportFormat, export_rdf

    nodes, edges = [], []
    if isinstance(db_project.graph_data, dict):
        nodes = db_project.graph_data.get("nodes") or []
        edges = db_project.graph_data.get("edges") or []

    if fmt == ExportFormat.JSON:
        if not nodes and not edges:
            raise HTTPException(status_code=404, detail="No graph data found for this project")
        json_data = _build_export_json_data(db_project, nodes, edges)
        json_content = json.dumps(json_data, ensure_ascii=False, indent=2)
        media_type = "application/json; charset=utf-8"
        ext = "json"
    else:
        # 始终基于最新 graph_data 重新生成 turtle，保证下载内容是最新快照；
        # graph_data 为空时回退历史 ttl_content
        turtle = ""
        if nodes or edges:
            try:
                turtle = generate_ttl_from_graph_data(nodes, edges)
            except Exception as e:
                logger.error(f"[export] generate_ttl_from_graph_data 失败：{e}")
        if not turtle:
            turtle = db_project.ttl_content or ""
        if not turtle:
            raise HTTPException(status_code=404, detail="TTL file not found for this project")
        try:
            content, media_type, ext = export_rdf(turtle, fmt)
        except Exception as e:
            logger.error(f"[export] RDF 序列化失败 format={fmt}: {e}")
            raise HTTPException(status_code=500, detail=f"RDF serialization failed: {e}")
        json_content = content

    project_name = db_project.name
    filename = f"ontology_{project_name}.{ext}"
    encoded_filename = quote(filename, safe='')
    ascii_filename = f"ontology_project_{db_project.id}.{ext}"
    return Response(
        content=json_content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}; filename=\"{ascii_filename}\"",
            "Content-Type": media_type,
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


def _check_export_permission(db_project: Project, current_user: User) -> None:
    if db_project.is_published or db_project.owner_id == current_user.id or is_super_admin(current_user):
        return
    raise HTTPException(status_code=403, detail="No permission to export this project")


@router.get("/{project_id}/export")
def export_project(
    project_id: int,
    format: str = Query("turtle", description="导出格式：turtle/ntriples/rdfxml/jsonld/trig/owl/json"),
    include_inferred: bool = Query(False, description="仅 turtle：附加语义推理分节（横幅分隔，非原始事实）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """统一导出端点（semantica 适配层）：6 种 RDF 序列化 + 平台 JSON（含 meta，可导回）。"""
    from app.adapters.exporting import ExportFormat

    try:
        fmt = ExportFormat(format)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式: {format}，"
                            f"可选: {[f.value for f in ExportFormat if f.value in ('turtle', 'ntriples', 'rdfxml', 'jsonld', 'trig', 'owl', 'json')]}")
    if fmt not in ("turtle", "ntriples", "rdfxml", "jsonld", "trig", "owl", "json"):
        raise HTTPException(status_code=400, detail=f"格式 {format} 的导出待后续分期")
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    _check_export_permission(db_project, current_user)
    # 推理期 R2：turtle 可附推理分节（事实与推理横幅分隔）
    if include_inferred and fmt == ExportFormat.TURTLE:
        from urllib.parse import quote as _q

        from app.services.reasoning_service import export_ttl_with_inference

        content = export_ttl_with_inference(db, db_project, profile="owlrl")
        project_name = db_project.name
        encoded_filename = _q(f"ontology_{project_name}_inferred.ttl", safe='')
        ascii_filename = f"ontology_project_{db_project.id}_inferred.ttl"
        return Response(
            content=content,
            media_type="text/turtle; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}; filename=\"{ascii_filename}\"",
            },
        )
    return _build_export_response(db_project, fmt)


def _build_export_json_data(db_project: Project, nodes: list, edges: list) -> dict:
    """平台 JSON 导出格式：entities + relationships + meta（meta 不影响导入解析）。"""
    from app.services.inject_service import (
        _build_entity_description,
        _build_relation_description,
        _friendly_type,
    )

    entities = []
    relationships = []
    node_id_to_label: dict[str, str] = {}

    # 先构建完整的 node_id -> label 映射
    for node in nodes:
        data = node.get("data", {})
        label = data.get("label", node.get("id", ""))
        node_id_to_label[node.get("id", "")] = label

    for node in nodes:
        data = node.get("data", {})
        label = data.get("label", node.get("id", ""))
        node_type = data.get("type", "Class")
        props = data.get("properties", {})
        # 过滤内部元数据字段，不导出给用户
        _internal_keys = {"_source_file", "_source_quote", "_source_chunk_index", "_domain"}
        props = {k: v for k, v in props.items() if k not in _internal_keys}
        # description 单独存储在 data.description，不在 properties 中
        desc = data.get("description", "")
        # 清理 properties 中可能混入的 description
        props = {k: v for k, v in props.items() if k != "description"}

        # 优先使用原始 description，仅在无 description 时才用自动生成的
        ent_desc = desc
        if not ent_desc:
            ent_desc = _build_entity_description(label, node_type, props)

        entity_data = {
            "name": label,
            "type": node_type,
            "entity_type": _friendly_type(node_type),
            "properties": props,
            "description": ent_desc,
        }
        conf = data.get("confidence")
        if isinstance(conf, (int, float)):
            entity_data["confidence"] = round(float(conf), 2)
        entities.append(entity_data)

    for edge in edges:
        data = edge.get("data", {})
        source_id = edge.get("source", "")
        target_id = edge.get("target", "")
        relation = data.get("relation", "")
        rel_label = data.get("label", relation or "相关")
        source_name = node_id_to_label.get(source_id, source_id)
        target_name = node_id_to_label.get(target_id, target_id)

        # instance_of 边导出为 type 关系（与原始 JSON 格式一致）
        export_relation = "type" if relation == "instance_of" else relation
        export_label = "type" if relation == "instance_of" else rel_label

        rel_desc = _build_relation_description(source_name, export_label, target_name)

        rel_item = {
            "source_name": source_name,
            "target_name": target_name,
            "relation": export_relation,
            "label": export_label,
            "description": rel_desc,
            "properties": data.get("properties", {}),
        }
        conf = data.get("confidence")
        if isinstance(conf, (int, float)):
            rel_item["confidence"] = round(float(conf), 2)
        relationships.append(rel_item)

    return {
        "meta": {
            "format": "ontology-platform/1.0",
            "project_id": db_project.id,
            "project_name": db_project.name,
            "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "entity_count": len(entities),
            "relationship_count": len(relationships),
            "hint": "平台 JSON 导出格式，可用于 parse-ttl-schema / upload-ttl 导入回平台；"
                    "RDF 格式请使用 /export 端点的 turtle/ntriples/rdfxml/jsonld/trig/owl",
        },
        "entities": entities,
        "relationships": relationships,
    }


# ─────────────────────────────────────────────
#  内部辅助函数
# ─────────────────────────────────────────────

def is_owl_class_type(t: str | None) -> bool:
    """类节点判定：'owl:Class' 为标准值，'Class' 为历史画布遗留值（前端 CLASS_TYPE_VALUES 同步接受两者）。"""
    return t in ('owl:Class', 'Class')


def build_schema_from_graph_data(nodes: list[dict], edges: list[dict]) -> dict:
    """
    从 nodes 和 edges 动态构建 schema（兼容 TTL 导入场景）。
    当 TTL 文件导入骨架时，graph_data 中可能没有 schema 字段，
    但有 nodes 和 edges，本函数从这些数据中提取 schema。
    
    ★ 属性继承：子类自动继承父类的所有属性，无需重复定义。
    """
    from app.core.logging import logger

    classes = []
    object_properties = []

    logger.info(f"[build_schema_from_graph_data] 开始从 {len(nodes)} 个节点和 {len(edges)} 个边构建 schema")

    class_info = {}
    node_id_to_label = {}
    nodes_by_id = {str(n['id']): n for n in nodes}

    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if is_owl_class_type(node_type):
            node_id = str(node['id'])
            node_label = node.get('data', {}).get('label', node_id)
            raw_id = node.get('data', {}).get('raw_id', '')
            node_id_to_label[node_id] = node_label
            if raw_id:
                node_id_to_label[raw_id] = node_label

    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if is_owl_class_type(node_type):
            node_id = str(node['id'])
            node_label = node.get('data', {}).get('label', node_id)

            parent_classes = []
            for edge in edges:
                edge_data = edge.get('data', {})
                edge_relation = edge_data.get('relation', '')
                edge_label = edge.get('label', '')
                if edge_relation == 'subclass_of' or edge_label in ('subClassOf', 'subclass_of'):
                    if edge.get('source') == node_id:
                        parent_classes.append(edge.get('target'))

            direct_properties = []
            node_data = node.get('data', {})
            properties = node_data.get('properties', {})
            if isinstance(properties, dict):
                direct_properties = list(properties.keys())

            class_info[node_id] = {
                'label': node_label,
                'parent_classes': parent_classes,
                'direct_properties': direct_properties,
            }

    def get_inherited_properties(class_id: str, visited: set = None) -> list:
        if visited is None:
            visited = set()
        if class_id in visited or class_id not in class_info:
            return []
        visited.add(class_id)
        inherited_props = []
        info = class_info[class_id]
        for parent_id in info['parent_classes']:
            if parent_id in class_info:
                parent_direct_props = class_info[parent_id]['direct_properties']
                inherited_props.extend(parent_direct_props)
                parent_inherited_props = get_inherited_properties(parent_id, visited)
                inherited_props.extend(parent_inherited_props)
        return inherited_props

    for node_id, info in class_info.items():
        direct_props = info['direct_properties']
        inherited_props = get_inherited_properties(node_id)
        all_properties = list(set(direct_props + inherited_props))
        inherited_props_set = set(inherited_props)
        direct_props_set = set(direct_props)

        properties_with_source = []
        for prop in all_properties:
            if prop in direct_props_set:
                properties_with_source.append({'name': prop, 'source': 'direct'})
            else:
                source_class = None
                for parent_id in info['parent_classes']:
                    if parent_id in class_info and prop in class_info[parent_id]['direct_properties']:
                        source_class = class_info[parent_id]['label']
                        break
                properties_with_source.append({'name': prop, 'source': 'inherited', 'from': source_class})

        logger.info(f"[build_schema_from_graph_data] 添加类：{node_id}, label={info['label']}, parent_classes={info['parent_classes']}, direct_properties={direct_props}, inherited_properties={inherited_props}")

        classes.append({
            "id": node_id,
            "label": info['label'],
            "parent_classes": info['parent_classes'],
            "disjoint_with": (nodes_by_id.get(node_id, {}).get('data', {}).get('axioms') or {}).get('disjoint_with') or [],
            "data_properties": all_properties,
            "direct_properties": direct_props,
            "inherited_properties": inherited_props,
            "properties_with_source": properties_with_source,
        })

    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if is_owl_class_type(node_type):
            node_id = str(node['id'])
            for cls in classes:
                if cls['id'] == node_id:
                    all_properties = cls['data_properties']
                    prop_defs = node.get('data', {}).get('property_definitions')
                    if not prop_defs:
                        prop_defs = [{"name": p, "description": "", "data_type": "string"} for p in all_properties]
                    cls["property_definitions"] = prop_defs
                    break

    for edge in edges:
        edge_data = edge.get('data', {})
        relation = edge_data.get('relation', '')
        label = edge.get('label', '') or edge_data.get('label', '')

        if relation and relation not in ('rdf:type', 'type', 'subClassOf', 'subclass_of'):
            if label in ('subClassOf', 'subclass_of'):
                continue

            prop_id = edge_data.get('prop_id', '')
            if not prop_id or prop_id in ('', '_', '__', '___'):
                prop_id = label if label else relation

            src_node_id = edge.get('source', '')
            tgt_node_id = edge.get('target', '')
            src_label = node_id_to_label.get(src_node_id, src_node_id)
            tgt_label = node_id_to_label.get(tgt_node_id, tgt_node_id)

            logger.info(f"[build_schema_from_graph_data] 添加 ObjectProperty: {prop_id}, label={label}, domain={src_label}({src_node_id}), range={tgt_label}({tgt_node_id})")

            object_properties.append({
                "id": prop_id,
                "label": label,
                "domain": src_label,
                "range": tgt_label,
            })
            if edge_data.get('cardinality'):
                object_properties[-1]["cardinality"] = edge_data['cardinality']
            if edge_data.get('description'):
                object_properties[-1]["description"] = edge_data['description']
            # ★ 公理：关系特征 + 互逆 + 基数随 schema 带出（校验/MCP 下游消费）
            axioms = edge_data.get('axioms') or {}
            for flag in ('functional', 'transitive', 'symmetric'):
                if axioms.get(flag):
                    object_properties[-1][flag] = True
            if axioms.get('inverse_of'):
                object_properties[-1]["inverse_of"] = axioms['inverse_of']
            min_card = edge_data.get('min_cardinality')
            max_card = edge_data.get('max_cardinality')
            if min_card is not None or max_card is not None:
                object_properties[-1]["cardinality_restrictions"] = [{
                    "class": src_label,
                    "min": min_card,
                    "max": max_card,
                }]

    logger.info(f"[build_schema_from_graph_data] 构建完成：{len(classes)} 个类，{len(object_properties)} 个 ObjectProperty")

    return {
        "classes": classes,
        "object_properties": object_properties,
    }


def generate_ttl_from_graph_data(nodes: list[dict], edges: list[dict]) -> str:
    """
    全生命周期 TTL 同步：将前端 nodes+edges 反向序列化为标准 OWL TTL。
    使用 rdflib 保证 RDF 语义正确性。
    """
    import hashlib

    from rdflib import XSD, Literal, Namespace, URIRef

    g = Graph()
    ex = Namespace("http://www.example.org/auto_ontology#")
    g.bind("ex", ex)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("xsd", Namespace("http://www.w3.org/2001/XMLSchema#"))

    onto_uri = ex["Ontology"]
    g.add((onto_uri, RDF.type, OWL.Ontology))
    g.add((onto_uri, OWL.versionInfo, Literal("2.0")))

    import re
    def make_uri(node_id: str) -> URIRef:
        """
        将节点 ID 转换为安全的 URI。
        
        ★ 修复：确保即使 ID 包含特殊字符也不会生成多余下划线
        """
        if '#' in node_id or node_id.startswith('http'):
            return URIRef(node_id)

        # 首先去除首尾空白
        node_id = node_id.strip()

        # 检查是否是有效的 ID 格式（如 C_xxx, I_xxx, OP_xxx）
        if re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', node_id):
            return ex[node_id]

        # 对于包含特殊字符的 ID，使用 MD5 哈希生成安全 URI
        md5_hash = hashlib.md5(node_id.encode('utf-8')).hexdigest()[:8]
        # 根据 ID 前缀确定类型
        prefix = "Node"
        if node_id.startswith('C_'):
            prefix = "C"
        elif node_id.startswith('I_'):
            prefix = "I"
        elif node_id.startswith('OP_'):
            prefix = "OP"
        elif node_id.startswith('DP_'):
            prefix = "DP"

        return ex[f"{prefix}_{md5_hash}"]

    def generate_safe_prop_id(original_name: str) -> str:
        """
        生成一个安全的属性 ID，使用 MD5 哈希保证唯一性（可区分同音词如"使用"和"实用"）。
        
        参数:
        - original_name: 原始名称（可能是中文）
        
        返回:
        - 安全的 ASCII ID，格式：prop_{8 位 MD5 哈希}
        """
        if not original_name:
            return "prop_empty"

        # 始终使用 MD5 哈希保证唯一性（区分同音词如"使用"和"实用"）
        md5_hash = hashlib.md5(original_name.encode('utf-8')).hexdigest()
        return f"prop_{md5_hash[:8]}"

    node_uris: dict[str, URIRef] = {}
    for node in nodes:
        node_id = str(node['id'])
        node_label = node['data'].get('label', node_id)
        node_type = node['data'].get('type', 'owl:Class')
        uri = make_uri(node_id)
        node_uris[node_id] = uri

        if is_owl_class_type(node_type):
            g.add((uri, RDF.type, OWL.Class))
            desc = node['data'].get('description', '')
            if desc:
                g.add((uri, RDFS.comment, Literal(desc, lang="zh")))
        elif node_type == 'owl:NamedIndividual':
            g.add((uri, RDF.type, OWL.NamedIndividual))
        else:
            class_uri = make_uri(node_type)
            g.add((uri, RDF.type, class_uri))
            g.add((class_uri, RDF.type, OWL.Class))

        g.add((uri, RDFS.label, Literal(node_label, lang="zh")))

        for prop_name, prop_value in node['data'].get('properties', {}).items():
            if prop_name.startswith('_source_'):
                continue
            dataprop_id = generate_safe_prop_id(prop_name)
            if not dataprop_id:
                continue

            dataprop_uri = ex[dataprop_id]
            g.add((dataprop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((dataprop_uri, RDFS.label, Literal(prop_name, lang="zh")))

            if is_owl_class_type(node_type):
                g.add((dataprop_uri, RDFS.domain, uri))

            if prop_value:
                g.add((uri, dataprop_uri, Literal(str(prop_value), lang="zh") if isinstance(prop_value, str) else Literal(prop_value)))

    # 首先收集所有已定义的 ObjectProperty（从 schema 中）
    # 这样可以避免重复创建 ObjectProperty，也能保持正确的标签
    existing_obj_properties = {}  # relation -> (prop_id, label)

    # 定义无效的 prop_id 集合
    INVALID_PROP_IDS = {'', '_', '__', '___', '____', '_____', '______', '_______', '________'}

    # 遍历边，先收集所有 ObjectProperty 关系及其 prop_id
    for edge in edges:
        edge_data = edge.get('data', {})
        relation = edge_data.get('relation', '')
        prop_id = edge_data.get('prop_id', '')
        label = edge_data.get('label', relation)

        # 只收集 ObjectProperty 关系（排除 subClassOf 和 type）
        if relation and relation not in ('rdf:type', 'type', 'subClassOf', 'subclass_of'):
            # 检查 prop_id 是否有效
            if prop_id and prop_id not in INVALID_PROP_IDS:
                existing_obj_properties[relation] = (prop_id, label)
            else:
                # 如果没有有效的 prop_id，使用 label 生成有意义的 prop_id
                generated_id = generate_safe_prop_id(label)
                if not generated_id:
                    # Fallback if generate_safe_prop_id somehow fails
                    generated_id = f"prop_{abs(hash(label)) % 10000}"
                existing_obj_properties[relation] = (generated_id, label)

    # ---- 公理导出 ①：类间互斥（owl:disjointWith，按 label 引用目标类）----
    label_to_uri = {}
    for node in nodes:
        node_data = node.get('data', {})
        if is_owl_class_type(node_data.get('type', 'owl:Class')):
            label_to_uri[node_data.get('label', '')] = node_uris[str(node['id'])]

    for node in nodes:
        node_data = node.get('data', {})
        disjoint = (node_data.get('axioms') or {}).get('disjoint_with') or []
        src_uri = node_uris.get(str(node['id']))
        if not src_uri:
            continue
        for peer_label in disjoint:
            peer_uri = label_to_uri.get(peer_label)
            if peer_uri is not None and peer_uri != src_uri:
                g.add((src_uri, OWL.disjointWith, peer_uri))

    # ---- 公理导出 ②：关系特征 + 互逆 + 基数 Restriction ----
    prop_uri_by_label = {}  # 关系 label -> objprop URI（inverseOf 解析用，先收集）
    edge_axioms_by_label = {}  # 关系 label -> (edge_data, source_id)
    for edge in edges:
        edge_data = edge.get('data', {})
        relation_label = edge.get('label') or edge_data.get('label') or 'relatedTo'
        relation = edge_data.get('relation', relation_label)
        if relation in ('rdf:type', 'type', 'subClassOf', 'subclass_of'):
            continue
        if relation_label not in edge_axioms_by_label:
            edge_axioms_by_label[relation_label] = edge_data

    for edge in edges:
        source_id = str(edge['source'])
        target_id = str(edge['target'])
        edge_data = edge.get('data', {})

        relation_label = (
            edge.get('label')
            or edge_data.get('label')
            or 'relatedTo'
        )
        relation = edge_data.get('relation', relation_label)

        if source_id in node_uris and target_id in node_uris:
            source_uri = node_uris[source_id]
            target_uri = node_uris[target_id]

            if relation_label in ('rdf:type', 'type'):
                g.add((source_uri, RDF.type, target_uri))
            elif relation_label in ('subClassOf', 'subclass_of') or relation == 'subclass_of':
                g.add((source_uri, RDFS.subClassOf, target_uri))
            else:
                prop_info = existing_obj_properties.get(relation)
                if prop_info:
                    prop_id, label = prop_info
                else:
                    prop_id = generate_safe_prop_id(relation_label)
                    if not prop_id:
                        prop_id = f"prop_{abs(hash(relation_label)) % 10000}"

                objprop_uri = ex[prop_id]
                g.add((objprop_uri, RDF.type, OWL.ObjectProperty))
                g.add((objprop_uri, RDFS.label, Literal(relation_label, lang="zh")))
                g.add((source_uri, objprop_uri, target_uri))
                prop_uri_by_label[relation_label] = objprop_uri

    # 关系特征公理（functional/transitive/symmetric/inverseOf）与基数 Restriction
    for relation_label, edge_data in edge_axioms_by_label.items():
        objprop_uri = prop_uri_by_label.get(relation_label)
        if objprop_uri is None:
            continue
        axioms = edge_data.get('axioms') or {}
        if axioms.get('functional'):
            g.add((objprop_uri, RDF.type, OWL.FunctionalProperty))
        if axioms.get('transitive'):
            g.add((objprop_uri, RDF.type, OWL.TransitiveProperty))
        if axioms.get('symmetric'):
            g.add((objprop_uri, RDF.type, OWL.SymmetricProperty))
        inverse_label = str(axioms.get('inverse_of') or '').strip()
        inverse_uri = prop_uri_by_label.get(inverse_label)
        if inverse_uri is not None and inverse_uri != objprop_uri:
            g.add((objprop_uri, OWL.inverseOf, inverse_uri))

        src_uri = None
        for edge in edges:
            lbl = edge.get('label') or edge.get('data', {}).get('label') or 'relatedTo'
            if lbl == relation_label and str(edge['source']) in node_uris:
                src_uri = node_uris[str(edge['source'])]
                break
        if src_uri is None:
            continue
        min_card = edge_data.get('min_cardinality')
        max_card = edge_data.get('max_cardinality')
        if min_card is None and max_card is None:
            continue
        restriction = ex[f"restr_{abs(hash(f'{relation_label}_{str(src_uri)}')) % 100000}"]
        g.add((src_uri, RDFS.subClassOf, restriction))
        g.add((restriction, RDF.type, OWL.Restriction))
        g.add((restriction, OWL.onProperty, objprop_uri))
        if min_card is not None:
            g.add((restriction, OWL.minCardinality, Literal(int(min_card))))
        if max_card is not None:
            g.add((restriction, OWL.maxCardinality, Literal(int(max_card))))

    return g.serialize(format="turtle")


# 为向后兼容保留旧函数名
generate_ttl_from_react_flow = generate_ttl_from_graph_data


def extract_schema_from_ttl(ttl_content: str) -> dict:
    """
    从 TTL 内容中提取骨架 Schema（包含类、ObjectProperty 和 DatatypeProperty）。
    用于支持上传 TTL 文件构建类结构。
    
    重要：TTL 文件中子类可能没有显式声明 a owl:Class，而是通过 rdfs:subClassOf 关系隐式成为类。
    本函数会同时处理显式和隐式声明的类。
    
    ★ 属性继承：子类自动继承父类的所有属性，无需重复定义。
    """
    from rdflib import URIRef

    from app.core.logging import logger

    g = Graph()
    g.parse(data=ttl_content, format="turtle")

    classes = []
    object_properties = []
    datatype_properties = []

    # 首先收集所有 DatatypeProperty 及其 domain 信息
    datatype_prop_domains = {}  # prop_uri -> [domain_classes]
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_id = str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]
        label = prop_id
        for obj in g.objects(prop, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取 domain（可能是一个类或多个类）
        domains = []
        for domain in g.objects(prop, RDFS.domain):
            domain_id = str(domain).split('#')[-1] if '#' in str(domain) else str(domain).split('/')[-1]
            domains.append(domain_id)

        datatype_prop_domains[str(prop)] = {
            'id': prop_id,
            'label': label,
            'domains': domains,
        }

    # ★ 新增：收集 rdf:Property（根据 rdfs:range 判断是数据属性还是对象属性）
    xsd_namespace = "http://www.w3.org/2001/XMLSchema#"
    xsd_datatypes = ['string', 'integer', 'decimal', 'float', 'double', 'boolean',
                     'date', 'dateTime', 'time', 'duration', 'anyURI', 'byte',
                     'short', 'long', 'unsignedByte', 'unsignedShort', 'unsignedLong']

    for prop in g.subjects(RDF.type, RDF.Property):
        prop_uri = str(prop)
        # 跳过已经作为 owl:DatatypeProperty 处理的属性
        if prop_uri in datatype_prop_domains:
            continue

        prop_id = str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]
        label = prop_id
        for obj in g.objects(prop, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取 domain
        domains = []
        for domain in g.objects(prop, RDFS.domain):
            domain_id = str(domain).split('#')[-1] if '#' in str(domain) else str(domain).split('/')[-1]
            domains.append(domain_id)

        # 获取 range
        ranges = list(g.objects(prop, RDFS.range))
        range_uri = str(ranges[0]) if ranges else None

        # 判断是否是数据属性：range 是 XSD 数据类型
        is_datatype = False
        if range_uri:
            if range_uri.startswith(xsd_namespace):
                range_type = range_uri.split('#')[-1] if '#' in range_uri else range_uri.split('/')[-1]
                if range_type in xsd_datatypes:
                    is_datatype = True
            if range_uri == str(RDFS.Literal):
                is_datatype = True

        if is_datatype:
            datatype_prop_domains[prop_uri] = {
                'id': prop_id,
                'label': label,
                'domains': domains,
            }
            logger.info(f"[extract_schema_from_ttl] rdf:Property '{prop_id}' 被识别为数据属性（range={range_uri}）")

    logger.info(f"[extract_schema_from_ttl] 找到 {len(datatype_prop_domains)} 个数据属性")

    # 收集所有类 URI（包括显式和隐式声明的类）
    class_uris = set()

    # 1. 显式声明为 owl:Class 的节点
    for subj in g.subjects(RDF.type, OWL.Class):
        class_uris.add(subj)

    # 2. 通过 rdfs:subClassOf 关系隐式成为类的节点（作为子类或父类）
    for subj, obj in g.subject_objects(RDFS.subClassOf):
        class_uris.add(subj)  # 子类
        class_uris.add(obj)   # 父类

    # 3. 作为 ObjectProperty 的 domain 或 range 的节点（也是类）
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        for domain in g.objects(prop, RDFS.domain):
            class_uris.add(domain)
        for range_ in g.objects(prop, RDFS.range):
            class_uris.add(range_)

    # ★ 公理：基数 Restriction 节点不是类，排除出类集合
    class_uris = {u for u in class_uris if (u, RDF.type, OWL.Restriction) not in g}

    # ★ 第一步：先收集所有类的直接属性和父类关系
    class_info = {}  # cls_id -> {'label': str, 'parent_classes': list, 'direct_properties': list}

    for cls in class_uris:
        cls_uri = str(cls)
        cls_id = str(cls).split('#')[-1] if '#' in str(cls) else str(cls).split('/')[-1]
        label = cls_id
        for obj in g.objects(cls, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取父类（基数 Restriction 不是类，跳过）
        parent_classes = []
        for parent in g.objects(cls, RDFS.subClassOf):
            if (parent, RDF.type, OWL.Restriction) in g:
                continue
            parent_id = str(parent).split('#')[-1] if '#' in str(parent) else str(parent).split('/')[-1]
            parent_classes.append(parent_id)

        # ★ 公理：类间互斥（owl:disjointWith）——记为对端的 label
        disjoint_with = []
        for peer in g.objects(cls, OWL.disjointWith):
            peer_label = None
            for obj in g.objects(peer, RDFS.label):
                peer_label = str(obj)
                if hasattr(obj, 'language') and obj.language == 'zh':
                    break
            disjoint_with.append(peer_label or (str(peer).split('#')[-1] if '#' in str(peer) else str(peer).split('/')[-1]))

        # 获取数据属性 - 通过 rdfs:domain 关联
        direct_properties = []
        for prop_uri, prop_info in datatype_prop_domains.items():
            if cls_id in prop_info['domains']:
                direct_properties.append(prop_info['label'])

        class_info[cls_id] = {
            'label': label,
            'parent_classes': parent_classes,
            'direct_properties': list(set(direct_properties)),
            'disjoint_with': disjoint_with,
        }

    # ★ 第二步：递归计算每个类的继承属性
    def get_inherited_properties(class_id: str, visited: set = None) -> list:
        """
        递归获取类的所有继承属性（从父类链向上追溯）。
        """
        if visited is None:
            visited = set()

        if class_id in visited or class_id not in class_info:
            return []

        visited.add(class_id)

        inherited_props = []
        info = class_info[class_id]

        # 遍历所有父类
        for parent_id in info['parent_classes']:
            if parent_id in class_info:
                # 获取父类的直接属性
                parent_direct_props = class_info[parent_id]['direct_properties']
                inherited_props.extend(parent_direct_props)

                # 递归获取父类的继承属性
                parent_inherited_props = get_inherited_properties(parent_id, visited)
                inherited_props.extend(parent_inherited_props)

        return inherited_props

    # ★ 第三步：构建最终的类列表（合并直接属性和继承属性）
    for cls_id, info in class_info.items():
        direct_props = info['direct_properties']
        inherited_props = get_inherited_properties(cls_id)

        # 合并属性（去重）
        all_properties = list(set(direct_props + inherited_props))

        # 区分直接属性和继承属性
        inherited_props_set = set(inherited_props)
        direct_props_set = set(direct_props)

        # 标记属性来源
        properties_with_source = []
        for prop in all_properties:
            if prop in direct_props_set:
                properties_with_source.append({'name': prop, 'source': 'direct'})
            else:
                # 找出继承来源（哪个父类）
                source_class = None
                for parent_id in info['parent_classes']:
                    if parent_id in class_info and prop in class_info[parent_id]['direct_properties']:
                        source_class = class_info[parent_id]['label']
                        break
                properties_with_source.append({'name': prop, 'source': 'inherited', 'from': source_class})

        logger.info(f"[extract_schema_from_ttl] 添加类：{cls_id}, label={info['label']}, parent_classes={info['parent_classes']}, direct_properties={direct_props}, inherited_properties={inherited_props}")

        classes.append({
            "id": cls_id,
            "label": info['label'],
            "parent_classes": info['parent_classes'],
            "disjoint_with": info.get('disjoint_with') or [],
            "data_properties": all_properties,  # 所有属性（用于实例提取）
            "direct_properties": direct_props,  # 直接定义的属性
            "inherited_properties": inherited_props,  # 继承的属性
            "properties_with_source": properties_with_source,  # 带来源标记的属性
        })

    # 提取所有 ObjectProperty 定义
    object_property_defs = {}  # prop_id -> {'id': str, 'label': str, 'domain': str, 'range': str}
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        prop_id = str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]
        label = prop_id
        for obj in g.objects(prop, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取 domain 和 range
        domains = []
        ranges = []
        for domain in g.objects(prop, RDFS.domain):
            domain_id = str(domain).split('#')[-1] if '#' in str(domain) else str(domain).split('/')[-1]
            domains.append(domain_id)
        for range_ in g.objects(prop, RDFS.range):
            range_id = str(range_).split('#')[-1] if '#' in str(range_) else str(range_).split('/')[-1]
            ranges.append(range_id)

        prop_def = {
            "id": prop_id,
            "label": label,
            "domain": domains[0] if domains else "",
            "range": ranges[0] if ranges else "",
        }
        # ★ 公理：关系特征（函数性/传递/对称）与互逆
        if (prop, RDF.type, OWL.FunctionalProperty) in g:
            prop_def["functional"] = True
        if (prop, RDF.type, OWL.TransitiveProperty) in g:
            prop_def["transitive"] = True
        if (prop, RDF.type, OWL.SymmetricProperty) in g:
            prop_def["symmetric"] = True
        for inv in g.objects(prop, OWL.inverseOf):
            inv_label = None
            for obj in g.objects(inv, RDFS.label):
                inv_label = str(obj)
                if hasattr(obj, 'language') and obj.language == 'zh':
                    break
            prop_def["inverse_of"] = inv_label or (str(inv).split('#')[-1] if '#' in str(inv) else str(inv).split('/')[-1])
        object_properties.append(prop_def)
        object_property_defs[prop_id] = prop_def

        # 同时通过 label 建立索引，方便后续查找
        object_property_defs[label] = prop_def

    # ★ 公理：基数 Restriction（挂在 rdfs:subClassOf 链上的 owl:Restriction）
    # 形如 <类> rdfs:subClassOf [ a owl:Restriction; owl:onProperty <谓词>;
    #                              owl:minCardinality/maxCardinality N ].
    # 还原为 prop_def["cardinality_restrictions"]: [{class, min, max}]
    prop_uri_to_label = {}
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        label_objs = list(g.objects(prop, RDFS.label))
        zh = [o for o in label_objs if hasattr(o, 'language') and o.language == 'zh']
        prop_uri_to_label[str(prop)] = str(zh[0]) if zh else (str(label_objs[0]) if label_objs else
            (str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]))
    for cls_uri, restriction in g.subject_objects(RDFS.subClassOf):
        if (restriction, RDF.type, OWL.Restriction) not in g:
            continue
        on_props = list(g.objects(restriction, OWL.onProperty))
        if not on_props:
            continue
        prop_label = prop_uri_to_label.get(str(on_props[0]))
        if prop_label is None:
            continue
        mins = list(g.objects(restriction, OWL.minCardinality))
        maxs = list(g.objects(restriction, OWL.maxCardinality))
        if not mins and not maxs:
            continue
        cls_id = str(cls_uri).split('#')[-1] if '#' in str(cls_uri) else str(cls_uri).split('/')[-1]
        on_prop_id = str(on_props[0]).split('#')[-1] if '#' in str(on_props[0]) else str(on_props[0]).split('/')[-1]
        prop_def = object_property_defs.get(prop_label) or object_property_defs.get(on_prop_id)
        if prop_def is None:
            continue
        # 类名优先用 label（前端/校验/导出均以 label 为主键）
        cls_label = None
        for obj in g.objects(cls_uri, RDFS.label):
            cls_label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break
        restr = prop_def.setdefault("cardinality_restrictions", [])
        restr.append({
            "class": cls_label or cls_id,
            "min": int(mins[0]) if mins else None,
            "max": int(maxs[0]) if maxs else None,
        })

    # ★ 新增：提取类之间的实际关系边（类节点通过 ObjectProperty 连接到其他类节点）
    # 这些关系边在 TTL 中表现为：类节点以某个 ObjectProperty 作为谓词，指向另一个类节点
    class_relations = []  # 存储类之间的关系边

    # 收集所有已知的 ObjectProperty URI
    object_property_uris = set()
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        object_property_uris.add(str(prop))

    # 遍历所有类节点，查找它们通过 ObjectProperty 连接到其他类节点的关系
    for cls_uri in class_uris:
        cls_id = str(cls_uri).split('#')[-1] if '#' in str(cls_uri) else str(cls_uri).split('/')[-1]

        # 遍历该类的所有谓词-对象对
        for pred, obj in g.predicate_objects(cls_uri):
            pred_uri = str(pred)
            pred_id = pred_uri.split('#')[-1] if '#' in pred_uri else pred_uri.split('/')[-1]

            # 跳过元属性
            if pred_uri in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                            str(RDFS.domain), str(RDFS.range)]:
                continue

            # 检查谓词是否是 ObjectProperty（通过 URI 或定义检查）
            if pred_uri in object_property_uris or pred_id in object_property_defs:
                # 检查对象是否是一个类（URI 形式，且在 class_uris 中）
                if isinstance(obj, URIRef) and obj in class_uris:
                    obj_id = str(obj).split('#')[-1] if '#' in str(obj) else str(obj).split('/')[-1]

                    # 获取关系的 label
                    prop_def = object_property_defs.get(pred_id) or object_property_defs.get(pred_uri)
                    rel_label = prop_def['label'] if prop_def else pred_id

                    # 添加类关系边
                    class_relations.append({
                        "source_class": cls_id,
                        "target_class": obj_id,
                        "property_id": pred_id,
                        "property_label": rel_label,
                    })

                    logger.info(f"[extract_schema_from_ttl] 发现类关系边：{cls_id} -> {obj_id} (通过 {rel_label})")

    logger.info(f"[extract_schema_from_ttl] 共提取 {len(class_relations)} 条类关系边")

    return {
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": list(datatype_prop_domains.values()),
        "class_relations": class_relations,  # ★ 新增：类之间的实际关系边
    }


def _restriction_for(g, prop, domain_list) -> tuple:
    """取谓词的基数 Restriction：(min, max)。优先 domain 类上的声明，否则取全局第一条。"""
    fallback = None
    for restr in g.subjects(OWL.onProperty, prop):
        if (restr, RDF.type, OWL.Restriction) not in g:
            continue
        mins = list(g.objects(restr, OWL.minCardinality))
        maxs = list(g.objects(restr, OWL.maxCardinality))
        pair = (int(mins[0]) if mins else None, int(maxs[0]) if maxs else None)
        if pair == (None, None):
            continue
        for dom in domain_list:
            if (dom, RDFS.subClassOf, restr) in g:
                return pair
        if fallback is None:
            fallback = pair
    return fallback or (None, None)


def _edge_axiom_data(g, prop, domain_list) -> dict:
    """收集一条 ObjectProperty 的公理（特征/互逆/基数），供画布边 data 回写。"""
    out: dict = {}
    axioms = {}
    if (prop, RDF.type, OWL.FunctionalProperty) in g:
        axioms["functional"] = True
    if (prop, RDF.type, OWL.TransitiveProperty) in g:
        axioms["transitive"] = True
    if (prop, RDF.type, OWL.SymmetricProperty) in g:
        axioms["symmetric"] = True
    for inv in g.objects(prop, OWL.inverseOf):
        inv_label_objs = list(g.objects(inv, RDFS.label))
        axioms["inverse_of"] = str(inv_label_objs[0]) if inv_label_objs else (
            str(inv).split('#')[-1] if '#' in str(inv) else str(inv).split('/')[-1])
    if axioms:
        out["axioms"] = axioms
    min_card, max_card = _restriction_for(g, prop, domain_list)
    if min_card is not None:
        out["min_cardinality"] = min_card
    if max_card is not None:
        out["max_cardinality"] = max_card
    return out


def convert_ttl_to_graph_data(ttl_content: str):
    """
    将 TTL 转换为前端可渲染的 (nodes, edges) 二元组。
    支持解析多种属性类型作为节点的自定义属性。
    
    支持的属性类型：
    - owl:DatatypeProperty - OWL 数据属性
    - owl:ObjectProperty - OWL 对象属性
    - rdf:Property - 基础 RDF 属性（根据 rdfs:range 判断是数据属性还是对象属性）
    
    支持的类类型：
    - owl:Class - OWL 类
    - rdfs:Class - RDFS 类
    - 通过 rdfs:subClassOf 隐式声明的类
    
    注意：TTL 中的属性定义只是声明了属性的存在，而不是具体的属性值。
    只有当 TTL 中有实际的属性值时，才会被解析为节点的 properties。
    
    本函数会将 schema 中定义的 data_properties 添加到对应类节点的 properties 中，
    以便前端在编辑节点时显示这些预定义的属性。
    """
    from rdflib import Literal, Namespace, URIRef

    from app.core.logging import logger

    g = Graph()
    g.parse(data=ttl_content, format="turtle")
    ex = Namespace("http://www.example.org/auto_ontology#")

    nodes = []
    edges = []
    processed_nodes: set = set()

    # ★ 改进：收集所有数据属性（支持 owl:DatatypeProperty 和 rdf:Property）
    datatype_props = {}  # prop_uri -> {'id': str, 'label': str, 'domain': str}

    # 1. 收集 owl:DatatypeProperty
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_uri = str(prop)
        prop_id = str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]
        label = prop_id
        for obj in g.objects(prop, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取 domain
        domains = list(g.objects(prop, RDFS.domain))
        domain_id = str(domains[0]).split('#')[-1] if domains else None

        datatype_props[prop_uri] = {
            'id': prop_id,
            'label': label,
            'domain': domain_id,
        }

    # ★ 新增：2. 收集 rdf:Property（根据 rdfs:range 判断是数据属性还是对象属性）
    # XSD 数据类型范围（如 xsd:string, xsd:integer, xsd:decimal, xsd:date 等）
    xsd_namespace = "http://www.w3.org/2001/XMLSchema#"
    xsd_datatypes = ['string', 'integer', 'decimal', 'float', 'double', 'boolean',
                     'date', 'dateTime', 'time', 'duration', 'anyURI', 'byte',
                     'short', 'long', 'unsignedByte', 'unsignedShort', 'unsignedLong']

    for prop in g.subjects(RDF.type, RDF.Property):
        prop_uri = str(prop)
        # 跳过已经作为 owl:DatatypeProperty 或 owl:ObjectProperty 处理的属性
        if prop_uri in datatype_props:
            continue

        prop_id = str(prop).split('#')[-1] if '#' in str(prop) else str(prop).split('/')[-1]
        label = prop_id
        for obj in g.objects(prop, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 获取 domain
        domains = list(g.objects(prop, RDFS.domain))
        domain_id = str(domains[0]).split('#')[-1] if domains else None

        # 获取 range
        ranges = list(g.objects(prop, RDFS.range))
        range_uri = str(ranges[0]) if ranges else None

        # 判断是否是数据属性：range 是 XSD 数据类型
        is_datatype = False
        if range_uri:
            # 检查是否是 XSD 数据类型
            if range_uri.startswith(xsd_namespace):
                range_type = range_uri.split('#')[-1] if '#' in range_uri else range_uri.split('/')[-1]
                if range_type in xsd_datatypes:
                    is_datatype = True
            # rdfs:Literal 也是数据类型
            if range_uri == str(RDFS.Literal):
                is_datatype = True

        if is_datatype:
            datatype_props[prop_uri] = {
                'id': prop_id,
                'label': label,
                'domain': domain_id,
            }
            logger.info(f"[convert_ttl_to_graph_data] rdf:Property '{prop_id}' 被识别为数据属性（range={range_uri}）")

    # 调试日志：打印收集到的数据属性
    logger.info(f"[convert_ttl_to_graph_data] 找到 {len(datatype_props)} 个数据属性（含 owl:DatatypeProperty 和 rdf:Property）: {list(datatype_props.values())}")

    def add_node(uri, node_type_category: str) -> str:
        node_uri = str(uri)
        node_id = str(uri).split('#')[-1] if '#' in str(uri) else str(uri).split('/')[-1]
        if node_id in processed_nodes:
            return node_id

        label = node_id
        for obj in g.objects(uri, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        props = {}
        description = ''

        for obj in g.objects(uri, RDFS.comment):
            desc_val = str(obj)
            if desc_val and not desc_val.startswith('参数类型:'):
                description = desc_val
            break

        for pred, obj in g.predicate_objects(uri):
            pred_uri = str(pred)

            if pred_uri in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                            str(RDFS.domain), str(RDFS.range),
                            str(ex["isActionType"]), str(ex["isActionInstance"]),
                            str(ex["isActionParameter"])]:
                continue

            if pred_uri in datatype_props:
                prop_info = datatype_props[pred_uri]
                prop_label = prop_info['label']
                if isinstance(obj, Literal) or not str(obj).startswith('http'):
                    props[prop_label] = str(obj)
            else:
                p_name = str(pred).split('#')[-1]
                if isinstance(obj, Literal) or not str(obj).startswith('http'):
                    props[p_name] = str(obj)

        if node_type_category == "owl:Class":
            for prop_uri, prop_info in datatype_props.items():
                domain_id = prop_info['domain']
                if domain_id == node_id and prop_info['label'] not in props:
                    props[prop_info['label']] = ""

        node_data = {
            "label": label,
            "type": node_type_category,
            "properties": props,
        }
        if description:
            node_data["description"] = description

        # ★ 公理：类间互斥（owl:disjointWith）回写节点 data，前端可编辑
        if node_type_category == "owl:Class":
            disjoint = []
            for peer in g.objects(uri, OWL.disjointWith):
                peer_label = None
                for obj in g.objects(peer, RDFS.label):
                    peer_label = str(obj)
                    if hasattr(obj, 'language') and obj.language == 'zh':
                        break
                disjoint.append(peer_label or (str(peer).split('#')[-1] if '#' in str(peer) else str(peer).split('/')[-1]))
            if disjoint:
                node_data["axioms"] = {"disjoint_with": disjoint}

        nodes.append({
            "id": node_id,
            "type": "custom",
            "position": {"x": 0, "y": 0},
            "data": node_data,
        })
        processed_nodes.add(node_id)
        return node_id

    # ★ 改进：收集所有类 URI（支持 owl:Class, rdfs:Class 和隐式声明的类）
    class_uris = set()

    # 1. 显式声明为 owl:Class 的节点
    for subj in g.subjects(RDF.type, OWL.Class):
        class_uris.add(subj)

    # 2. 显式声明为 rdfs:Class 的节点（新增支持）
    for subj in g.subjects(RDF.type, RDFS.Class):
        class_uris.add(subj)

    # 3. 通过 rdfs:subClassOf 关系隐式成为类的节点（作为子类或父类）
    for subj, obj in g.subject_objects(RDFS.subClassOf):
        class_uris.add(subj)  # 子类
        class_uris.add(obj)   # 父类

    # 4. 作为 ObjectProperty 的 domain 或 range 的节点（也是类）
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        for domain in g.objects(prop, RDFS.domain):
            class_uris.add(domain)
        for range_ in g.objects(prop, RDFS.range):
            class_uris.add(range_)

    # 5. 作为 rdf:Property（对象属性）的 domain 或 range 的节点（新增支持）
    for prop in g.subjects(RDF.type, RDF.Property):
        prop_uri = str(prop)
        # 跳过已作为数据属性处理的
        if prop_uri in datatype_props:
            continue
        for domain in g.objects(prop, RDFS.domain):
            class_uris.add(domain)
        for range_ in g.objects(prop, RDFS.range):
            # 只有当 range 不是 XSD 数据类型时，才作为类
            range_uri = str(range_)
            if not range_uri.startswith(xsd_namespace) and range_uri != str(RDFS.Literal):
                class_uris.add(range_)

    # ★ 公理：基数 Restriction 节点不是类，排除出类集合（避免画布出现 restr_xxx 伪类）
    class_uris = {u for u in class_uris if (u, RDF.type, OWL.Restriction) not in g}

    logger.info(f"[convert_ttl_to_graph_data] 收集到 {len(class_uris)} 个类（含 owl:Class, rdfs:Class 和隐式类）")

    # 添加所有类节点
    for uri in class_uris:
        add_node(uri, "owl:Class")

    # 添加所有实例节点
    for subj in g.subjects(RDF.type, OWL.NamedIndividual):
        add_node(subj, "owl:NamedIndividual")

    # rdfs:subClassOf 边（子类关系）
    for subj, obj in g.subject_objects(RDFS.subClassOf):
        subj_id = str(subj).split('#')[-1] if '#' in str(subj) else str(subj).split('/')[-1]
        obj_id = str(obj).split('#')[-1] if '#' in str(obj) else str(obj).split('/')[-1]
        if subj_id in processed_nodes and obj_id in processed_nodes:
            edges.append({
                "id": f"e_subclass_{subj_id}_{obj_id}",
                "source": subj_id,
                "target": obj_id,
                "label": "rdfs:subClassOf",
                "type": "custom",
                "data": {"label": "subClassOf", "relation": "subclass_of"},
            })
            logger.info(f"[convert_ttl_to_graph_data] 添加子类关系边：{subj_id} -> {obj_id}")

    # ObjectProperty domain → range 边
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        domain = list(g.objects(prop, RDFS.domain))
        range_ = list(g.objects(prop, RDFS.range))
        label_objs = list(g.objects(prop, RDFS.label))
        prop_label = str(label_objs[0]) if label_objs else str(prop).split('#')[-1]

        prop_uri_str = str(prop)
        prop_id = prop_uri_str.split('#')[-1] if '#' in prop_uri_str else prop_uri_str.split('/')[-1]

        if domain and range_:
            source_id = add_node(domain[0], "owl:Class")
            target_id = add_node(range_[0], "owl:Class")
            edge_data = {
                "label": prop_label,
                "relation": prop_label,
                "prop_id": prop_id,
            }
            edge_data.update(_edge_axiom_data(g, prop, domain))
            edges.append({
                "id": f"e_{source_id}_{target_id}_{prop_label}",
                "source": source_id,
                "target": target_id,
                "label": prop_label,
                "type": "custom",
                "data": edge_data,
            })

    # ★ 新增：rdf:Property（对象属性）domain → range 边
    for prop in g.subjects(RDF.type, RDF.Property):
        prop_uri = str(prop)
        # 跳过已作为数据属性处理的
        if prop_uri in datatype_props:
            continue

        # 获取 domain 和 range
        domain = list(g.objects(prop, RDFS.domain))
        range_ = list(g.objects(prop, RDFS.range))
        label_objs = list(g.objects(prop, RDFS.label))
        prop_label = str(label_objs[0]) if label_objs else str(prop).split('#')[-1]

        # 获取 prop_id（从 URI 中提取）
        prop_id = prop_uri.split('#')[-1] if '#' in prop_uri else prop_uri.split('/')[-1]

        # 检查 range 是否是 XSD 数据类型（如果是，则跳过，因为已作为数据属性处理）
        if range_:
            range_uri = str(range_[0])
            if range_uri.startswith(xsd_namespace) or range_uri == str(RDFS.Literal):
                continue  # 这是数据属性，已处理

        if domain and range_:
            source_id = add_node(domain[0], "owl:Class")
            target_id = add_node(range_[0], "owl:Class")
            edges.append({
                "id": f"e_{source_id}_{target_id}_{prop_label}",
                "source": source_id,
                "target": target_id,
                "label": prop_label,
                "type": "custom",
                "data": {
                    "label": prop_label,
                    "relation": prop_label,
                    "prop_id": prop_id,
                },
            })
            logger.info(f"[convert_ttl_to_graph_data] 添加 rdf:Property 对象属性边：{source_id} -> {target_id} ({prop_label})")

    # rdf:type 虚线边
    for subj, obj in g.subject_objects(RDF.type):
        if str(obj) in [str(OWL.Class), str(OWL.NamedIndividual)]:
            continue
        subj_id = str(subj).split('#')[-1]
        obj_id = str(obj).split('#')[-1]
        if subj_id in processed_nodes and obj_id in processed_nodes:
            edges.append({
                "id": f"e_{subj_id}_type_{obj_id}",
                "source": subj_id,
                "target": obj_id,
                "label": "rdf:type",
                "type": "custom",
                "style": {"strokeDasharray": "5,5"},
                "data": {"label": "type"},
            })

    # ★ 新增：类节点之间的 ObjectProperty 关系边
    # 处理 TTL 中类节点直接使用 ObjectProperty 作为谓词连接到其他类节点的情况
    # 例如：ex:Node_051bc9b3 ex:投资产生费用 ex:Node_a50b211b .
    # 收集所有 ObjectProperty URI 和 label
    objprop_uri_to_label = {}  # prop_uri -> prop_label
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        prop_uri = str(prop)
        prop_id = prop_uri.split('#')[-1] if '#' in prop_uri else prop_uri.split('/')[-1]
        label_objs = list(g.objects(prop, RDFS.label))
        prop_label = str(label_objs[0]) if label_objs else prop_id
        objprop_uri_to_label[prop_uri] = prop_label

    # 遍历所有类节点，查找它们通过 ObjectProperty 连接到其他类节点的关系
    for cls_uri in class_uris:
        cls_id = str(cls_uri).split('#')[-1] if '#' in str(cls_uri) else str(cls_uri).split('/')[-1]

        # 遍历该类的所有谓词-对象对
        for pred, obj in g.predicate_objects(cls_uri):
            pred_uri = str(pred)
            pred_id = pred_uri.split('#')[-1] if '#' in pred_uri else pred_uri.split('/')[-1]

            # 跳过元属性
            if pred_uri in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                            str(RDFS.domain), str(RDFS.range)]:
                continue

            # 跳过 DatatypeProperty（已作为属性处理）
            if pred_uri in datatype_props:
                continue

            # 检查谓词是否是 ObjectProperty（通过 URI 检查）
            if pred_uri in objprop_uri_to_label:
                # 检查对象是否是一个类（URI 形式，且在 class_uris 中）
                if isinstance(obj, URIRef) and obj in class_uris:
                    obj_id = str(obj).split('#')[-1] if '#' in str(obj) else str(obj).split('/')[-1]

                    # 获取关系的 label
                    rel_label = objprop_uri_to_label.get(pred_uri, pred_id)

                    # 添加类关系边（如果节点已处理）
                    if cls_id in processed_nodes and obj_id in processed_nodes:
                        edge_id = f"e_{cls_id}_{obj_id}_{pred_id}"
                        # 检查是否已存在相同的边（避免重复）
                        existing_edge_ids = {e['id'] for e in edges}
                        if edge_id not in existing_edge_ids:
                            edge_data = {
                                "label": rel_label,
                                "relation": rel_label,
                                "prop_id": pred_id,
                            }
                            edge_data.update(_edge_axiom_data(g, URIRef(pred_uri), [cls_uri]))
                            edges.append({
                                "id": edge_id,
                                "source": cls_id,
                                "target": obj_id,
                                "label": rel_label,
                                "type": "custom",
                                "data": edge_data,
                            })
                            logger.info(f"[convert_ttl_to_graph_data] 添加类间 ObjectProperty 边：{cls_id} -> {obj_id} ({rel_label})")

    # 实例间普通关系边（ObjectProperty 或其他关系）
    for subj, pred, obj in g.triples((None, None, None)):
        if str(pred) in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                         str(RDFS.domain), str(RDFS.range)]:
            continue
        # 跳过 DatatypeProperty（已作为属性处理）
        if str(pred) in datatype_props:
            continue
        subj_str = str(subj).split('#')[-1]
        if subj_str in processed_nodes and str(obj).startswith('http'):
            obj_str = str(obj).split('#')[-1]
            if obj_str in processed_nodes:
                pred_label = str(pred).split('#')[-1]
                # 检查是否已存在相同的边（避免重复添加类间关系边）
                edge_id = f"e_{subj_str}_{obj_str}_{pred_label}"
                existing_edge_ids = {e['id'] for e in edges}
                if edge_id not in existing_edge_ids:
                    edges.append({
                        "id": edge_id,
                        "source": subj_str,
                        "target": obj_str,
                        "label": pred_label,
                        "type": "custom",
                        "data": {"label": pred_label},
                    })

    logger.info(f"[convert_ttl_to_graph_data] 返回 {len(nodes)} 个节点，{len(edges)} 个边")
    return nodes, edges


# 向后兼容（旧函数名）
convert_ttl_to_react_flow = convert_ttl_to_graph_data


# ─────────────────────────────────────────────
#  ★ 任务进度和取消 API
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  文档管理 API 已收编至 app/api/documents.py（M3-1，03 §18）：
#  GET/DELETE /api/projects/{id}/documents[...] 走新路由（模块码+项目角色守卫、软删、MinIO）。
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  ★ GraphRAG 问答已收编至 app/api/qa.py（M5，03 §15 / 07 §4）：
#  POST /api/projects/{id}/qa（SSE 流式）+ history + sources/{ref_id}，
#  检索后端 adapters/retrieval（权限过滤强制 project_id）。
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  ★ RAGFlow 图谱注入接口
# ─────────────────────────────────────────────

@router.get("/{project_id}/inject-config")
async def get_inject_config(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="无权限访问此项目")

    config = project.inject_config or {}
    masked_config = {}
    for k, v in config.items():
        if k == "es_password" and v:
            masked_config[k] = "******"
        elif k == "ragflow_api_key" and v:
            masked_config[k] = v[:4] + "****" + v[-4:] if len(v) > 8 else "****"
        elif k == "embedding_api_key" and v:
            masked_config[k] = v[:4] + "****" + v[-4:] if len(v) > 8 else "****"
        else:
            masked_config[k] = v

    # M2：嵌入模型信息改读 model_configs（adapters/provider 解析，失败回落 env）
    sys_embedding = {}
    try:
        from app.adapters.provider import resolve_embedding
        emb = resolve_embedding(project_id, db=db)
        sys_embedding = {
            "embedding_base_url": emb.base_url,
            "embedding_model": emb.model_name,
            "embedding_api_key": emb.api_key or "",
            "embedding_dim": emb.dims or (emb.params or {}).get("embedding_dim"),
        }
        sys_embedding = {k: v for k, v in sys_embedding.items() if v}
    except Exception as emb_err:
        logger.warning(f"[inject-config] 嵌入配置解析失败，返回空: {emb_err}")

    is_admin = current_user.role == "admin"

    return {
        "status": "success",
        "data": masked_config,
        "system_embedding": sys_embedding,
        "is_admin": is_admin,
    }


@router.post("/{project_id}/inject-config")
async def save_inject_config(
    project_id: int,
    config: dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="无权限访问此项目")

    existing_config = project.inject_config or {}

    for key in ["es_host", "es_port", "es_user", "es_password", "es_use_ssl",
                "ragflow_api_key", "ragflow_host", "user_id", "kb_id",
                "embedding_base_url", "embedding_model", "embedding_api_key", "embedding_dim"]:
        if key in config:
            if key == "es_password" and config[key] == "******":
                pass
            elif key == "ragflow_api_key" and "****" in str(config[key]):
                pass
            elif key == "embedding_api_key" and "****" in str(config[key]):
                pass
            else:
                existing_config[key] = config[key]

    project.inject_config = existing_config
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(project, "inject_config")
    db.commit()

    return {"status": "success", "message": "注入配置已保存"}


@router.post("/{project_id}/test-inject-connection")
async def test_inject_connection(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="无权限访问此项目")

    config = project.inject_config
    if not config:
        return {"status": "error", "message": "请先配置注入参数"}

    es_host = config.get("es_host", "localhost")
    es_port = int(config.get("es_port", 9200))
    es_user = config.get("es_user", "elastic")
    es_password = config.get("es_password", "")
    es_use_ssl = config.get("es_use_ssl", False)

    from app.infrastructure.es_client import ESClient
    es = ESClient(host=es_host, port=es_port, user=es_user, password=es_password, use_ssl=es_use_ssl)
    result = es.test_connection()
    es.close()

    if result.get("status") == "ok":
        return {"status": "success", "message": f"ES连接成功 (版本: {result.get('version', 'unknown')})"}
    else:
        return {"status": "error", "message": f"ES连接失败: {result.get('message', '未知错误')}"}


@router.post("/{project_id}/ragflow-fetch-info")
async def ragflow_fetch_info(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    body: dict = None
):
    """通过 RAGFlow API Key 自动获取用户ID和知识库列表"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="无权限访问此项目")

    # 优先使用请求体中的参数，其次使用已保存的配置
    body = body or {}
    ragflow_api_key = body.get("ragflow_api_key", "") or (project.inject_config or {}).get("ragflow_api_key", "")
    ragflow_host = body.get("ragflow_host", "") or (project.inject_config or {}).get("ragflow_host", "")

    if not ragflow_api_key or not ragflow_host:
        return {"status": "error", "message": "请先配置 RAGFlow API Key 和 Host"}

    # 去除末尾斜杠
    ragflow_host = ragflow_host.rstrip("/")

    try:
        headers = {"Authorization": f"Bearer {ragflow_api_key}"}

        # 获取知识库列表（RAGFlow v0.17+ 无 user/info 接口，tenant_id 从 datasets 中提取）
        kb_resp = req.get(f"{ragflow_host}/api/v1/datasets", headers=headers, timeout=10)
        if kb_resp.status_code != 200:
            return {"status": "error", "message": f"RAGFlow API 返回错误: HTTP {kb_resp.status_code}"}

        kb_json = kb_resp.json()
        if kb_json.get("code") != 0:
            return {"status": "error", "message": f"RAGFlow API 返回错误: {kb_json.get('message', '未知错误')}"}

        kb_list = kb_json.get("data", [])

        # 从第一个 dataset 的 tenant_id 提取 user_id
        user_id = ""
        if kb_list:
            user_id = kb_list[0].get("tenant_id", "")

        return {
            "status": "success",
            "user_id": user_id,
            "datasets": [{"id": ds.get("id", ""), "name": ds.get("name", "")} for ds in kb_list]
        }
    except Exception as e:
        logger.error(f"获取RAGFlow信息失败: {e}")
        return {"status": "error", "message": f"获取RAGFlow信息失败: {str(e)}"}


@router.post("/{project_id}/inject-to-ragflow")
async def inject_to_ragflow(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="无权限访问此项目")

    config = project.inject_config
    if not config:
        raise HTTPException(status_code=400, detail="请先配置注入参数")

    graph_data = project.graph_data
    if not graph_data or not isinstance(graph_data, dict):
        raise HTTPException(status_code=400, detail="项目没有图谱数据，请先构建本体")

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    if not nodes:
        raise HTTPException(status_code=400, detail="图谱中没有节点，请先构建本体")

    kb_id = config.get("kb_id", "")
    if not kb_id:
        raise HTTPException(status_code=400, detail="请配置知识库(kb_id)")

    ragflow_host = config.get("ragflow_host", "http://localhost:9380").rstrip("/")
    ragflow_api_key = config.get("ragflow_api_key", "")
    if not ragflow_api_key:
        raise HTTPException(status_code=400, detail="请配置 RAGFlow API Key")

    import httpx

    # 调用 RAGFlow 图谱注入 API
    inject_url = f"{ragflow_host}/api/v1/datasets/{kb_id}/knowledge_graph/inject/ontology"
    payload = {
        "nodes": nodes,
        "edges": edges,
        "merge_mode": "replace",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                inject_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {ragflow_api_key}",
                },
            )

        if resp.status_code != 200:
            raise Exception(f"RAGFlow API 返回 HTTP {resp.status_code}: {resp.text}")

        result = resp.json()
        if result.get("code") != 0:
            raise Exception(f"RAGFlow API 错误: {result.get('message', '未知错误')}")

        data = result.get("data", {})
        return {
            "status": "success",
            "data": {
                "success": True,
                "entities_created": data.get("entities_created", 0),
                "relations_created": data.get("relations_created", 0),
                "graph_updated": data.get("graph_updated", False),
                "ty2ents_updated": data.get("ty2ents_updated", False),
                "has_vectors": data.get("has_vectors", False),
            },
        }
    except httpx.HTTPError as e:
        from app.core.logging import logger
        logger.error(f"调用RAGFlow图谱注入API失败: {e}")
        raise HTTPException(status_code=500, detail=f"注入失败: 网络错误 - {str(e)}")
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"注入RAGFlow失败: {e}")
        raise HTTPException(status_code=500, detail=f"注入失败: {str(e)}")
