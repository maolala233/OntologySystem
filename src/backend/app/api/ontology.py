from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Response, Form
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from app.infrastructure.database import get_db, Project, User, SystemConfig
from app.schemas.ontology import ProjectCreate, ProjectUpdate, ProjectResponse
from app.schemas.extraction import (
    SchemaExtractionRequest, SchemaExtractionResponse,
    InstanceExtractionRequest, InstanceExtractionResponse,
    SchemaGraph, OntologyClass, OntologyObjectProperty,
    GraphData, GraphNode, GraphEdge, NodeData, EdgeData,
    SaveGraphRequest,
)
from app.api.auth import get_current_user
from app.core.config import settings
import json
import os
import tempfile
from app.services.extractor import OntologyExtractor
from app.infrastructure.neo4j_client import neo4j_client
import pandas as pd
from rdflib import Graph, RDF, OWL, RDFS

router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────────────────────────
#  CRUD 基础接口（保持不变）
# ─────────────────────────────────────────────

# 获取我的项目列表
@router.get("/my", response_model=List[ProjectResponse])
def get_my_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.owner_id == current_user.id).all()
    return projects

# 获取公共已发布项目
@router.get("/public", response_model=List[ProjectResponse])
def get_public_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.is_published == True).all()
    return projects

# 获取单个项目详情
@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.is_published and project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project")
    return project

# 创建新项目
@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    new_project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
        graph_data={"nodes": [], "edges": []},
        is_published=False
    )
    db.add(new_project)
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
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to modify this project")

    if project_update.name is not None:
        db_project.name = project_update.name
    if project_update.description is not None:
        db_project.description = project_update.description
    if project_update.graph_data is not None:
        db_project.graph_data = project_update.graph_data

    db.commit()
    db.refresh(db_project)
    return db_project

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
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to delete this project")

    neo4j_deletion_success = True
    try:
        from app.core.logging import logger
        logger.info(f"Attempting to delete project {project_id} data from Neo4j")
        neo4j_deletion_success = neo4j_client.delete_project_data(project_id)
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"Exception occurred when deleting project data from Neo4j: {str(e)}")
        neo4j_deletion_success = False

    db.delete(db_project)
    db.commit()

    if neo4j_deletion_success:
        return {"message": "Project deleted successfully", "neo4j_sync": True}
    else:
        return {"message": "Project deleted successfully but Neo4j sync failed", "neo4j_sync": False}


# ─────────────────────────────────────────────
#  发布 / 取消发布
# ─────────────────────────────────────────────

@router.post("/{project_id}/publish", response_model=ProjectResponse)
def publish_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to publish this project")

    # 发布前：生命周期 TTL 同步 —— 将最新图数据重新序列化覆盖 TTL
    if db_project.graph_data:
        try:
            ttl_content = generate_ttl_from_graph_data(
                db_project.graph_data.get("nodes", []),
                db_project.graph_data.get("edges", [])
            )
            db_project.ttl_content = ttl_content
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to regenerate TTL: {str(e)}")

    # 同步到 Neo4j
    if db_project.graph_data:
        try:
            success = neo4j_client.sync_graph(db_project.id, db_project.graph_data)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to sync to Neo4j database")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Neo4j sync error: {str(e)}")

    db_project.is_published = True
    db.commit()
    db.refresh(db_project)
    return db_project


@router.post("/{project_id}/unpublish", response_model=ProjectResponse)
def unpublish_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to unpublish this project")

    db_project.is_published = False
    db.commit()
    db.refresh(db_project)
    return db_project


# ─────────────────────────────────────────────
#  ★ 模块一 API 1：骨架提取 (Schema Extraction)
#
#  POST /api/projects/{project_id}/extract-schema
#  输入：文件 + 可选 user_intent
#  输出：仅含 Class 和 ObjectProperty 的骨架图
# ─────────────────────────────────────────────

@router.post("/{project_id}/extract-schema")
async def extract_schema_endpoint(
    project_id: int,
    file: UploadFile = File(...),
    user_intent: Optional[str] = Form(None, description="用户意图/关注领域（可选）"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(500),
    request_interval: int = Form(2),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API 1 - 骨架提取】
    上传文档 → 提取 OWL Class + ObjectProperty + DataProperty 骨架 Schema。
    绝对禁止提取实例，结果供前端 Step 2 (Schema Review) 使用。
    用户审核骨架后，将 schema_graph 传给 API 2 进行实例提取。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    # 保存临时文件
    os.makedirs("temp_uploads", exist_ok=True)
    temp_path = os.path.join("temp_uploads", file.filename)
    with open(temp_path, "wb") as buf:
        buf.write(await file.read())

    try:
        # 解析文件文本
        from app.services.parser import FileParser
        parser = FileParser()
        text_content = parser.parse_file(temp_path) or ""

        # 获取 LLM 配置
        extractor = _build_extractor(db)

        # ── 调用第一阶段引擎 ──
        schema = extractor.extract_schema(
            text=text_content,
            user_intent=user_intent,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
        )

        # 转换为前端渲染格式
        graph_data = OntologyExtractor.schema_to_graph_data(schema)

        # 将骨架 schema 临时存到 project graph_data（供 API 2 参考）
        # 格式: {"schema": {...}, "nodes": [...], "edges": [...]}
        merged_graph = {
            "schema": schema,
            **graph_data,
        }
        db_project.graph_data = merged_graph
        db.commit()

        return {
            "schema_graph": schema,
            "graph_data": graph_data,
            "message": (
                f"骨架提取完成：{len(schema['classes'])} 个类，"
                f"{len(schema['object_properties'])} 个关系。"
                f"请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
            ),
        }

    except Exception as e:
        logger.error(f"[extract-schema] 错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"骨架提取失败: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ─────────────────────────────────────────────
#  ★ 模块一 API 2：实例提取 (Instance Extraction)
#
#  POST /api/projects/{project_id}/extract-instances
#  输入：文件 + 用户审核后的 schema_graph JSON
#  输出：完整图（Schema + 实例）
# ─────────────────────────────────────────────

@router.post("/{project_id}/extract-instances")
async def extract_instances_endpoint(
    project_id: int,
    request_body: InstanceExtractionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API 2 - 强约束实例提取】
    接收用户审核后的 Schema + 原始文本 → 提取 NamedIndividual。
    - 模型只能实例化 schema_graph.classes 中已定义的类；
    - 连线必须符合 domain → range 约束，否则后端自动丢弃；
    - 所有实例 ID 使用确定性算法生成。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    try:
        extractor = _build_extractor(db)

        # schema_graph 来自请求体（用户已审核版本）
        schema_dict = request_body.schema_graph.model_dump()

        # ── 调用第二阶段引擎 ──
        inst_result = extractor.extract_instances(
            text=request_body.text_content,
            schema_graph=schema_dict,
            chunk_size=request_body.chunk_size,
            chunk_overlap=request_body.chunk_overlap,
            request_interval=request_body.request_interval,
            product_code=request_body.product_code,
        )

        # 获取当前骨架 graph_data（前端画布最新状态）
        current_graph = db_project.graph_data or {}
        schema_graph_data = {
            "nodes": current_graph.get("nodes", []),
            "edges": current_graph.get("edges", []),
        }

        # 合并实例到完整图
        full_graph_data = OntologyExtractor.merge_instances_to_graph_data(
            schema_graph_data=schema_graph_data,
            instances=inst_result["instances"],
        )

        # 更新 project graph_data
        db_project.graph_data = {
            "schema": schema_dict,
            **full_graph_data,
        }
        db.commit()

        return {
            "instances": inst_result["instances"],
            "graph_data": full_graph_data,
            "discarded_edges_count": inst_result["discarded_edges_count"],
            "message": (
                f"实例提取完成：{len(inst_result['instances'])} 个实例。"
                f"{'⚠️ ' + str(inst_result['discarded_edges_count']) + ' 条不合规连线已自动丢弃。' if inst_result['discarded_edges_count'] > 0 else ''}"
                f"请在画布中微调后点击「保存草稿」。"
            ),
        }

    except Exception as e:
        logger.error(f"[extract-instances] 错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"实例提取失败: {str(e)}")


# ─────────────────────────────────────────────
#  旧版上传接口（兼容，内部已改为两阶段）
# ─────────────────────────────────────────────

@router.post("/{project_id}/upload")
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    scenario: Optional[str] = None,
    chunk_size: int = 15000,
    chunk_overlap: int = 500,
    request_interval: int = 2,
    product_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    旧版一步到位上传接口（兼容旧前端）。
    内部调用 build_ontology（两阶段兼容模式）。
    新前端请使用 /extract-schema + /extract-instances 两步流程。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    os.makedirs("temp_uploads", exist_ok=True)
    temp_path = os.path.join("temp_uploads", file.filename)
    with open(temp_path, "wb") as buf:
        buf.write(await file.read())

    try:
        filename_lower = file.filename.lower()

        if filename_lower.endswith('.ttl'):
            with open(temp_path, "r", encoding='utf-8') as ttl_file:
                ttl_content = ttl_file.read()
            nodes, edges = convert_ttl_to_graph_data(ttl_content)
            db_project.ttl_content = ttl_content
            db.commit()
            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": file.filename,
                "message": f"成功解析TTL文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
            }
        else:
            from app.services.parser import FileParser
            parser = FileParser()
            text_content = parser.parse_file(temp_path) or ""

            extractor = _build_extractor(db, chunk_size, chunk_overlap, request_interval)

            df = pd.DataFrame(columns=["主体 (Class)", "属性 (DataProp)", "关系 (ObjectProp)"])
            ttl_filename, msg = extractor.build_ontology(
                text_content,
                scenario or db_project.description or "通用知识领域本体提取",
                df,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                request_interval=request_interval,
                product_code=product_code,
            )

            with open(ttl_filename, 'r', encoding='utf-8') as f:
                ttl_content = f.read()

            db_project.ttl_content = ttl_content
            db.commit()

            nodes, edges = convert_ttl_to_graph_data(ttl_content)
            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": ttl_filename,
                "message": msg,
            }

    except Exception as e:
        logger.error(f"Extraction error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"本体提取失败: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@router.post("/{project_id}/upload-ttl")
async def upload_ttl_file(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """专门用于上传 TTL 文件的端点。"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    if not file.filename.lower().endswith('.ttl'):
        raise HTTPException(status_code=400, detail="Only TTL files are accepted")

    os.makedirs("temp_uploads", exist_ok=True)
    temp_path = os.path.join("temp_uploads", file.filename)
    with open(temp_path, "wb") as buf:
        buf.write(await file.read())

    try:
        with open(temp_path, "r", encoding='utf-8') as ttl_file:
            ttl_content = ttl_file.read()

        nodes, edges = convert_ttl_to_graph_data(ttl_content)
        db_project.ttl_content = ttl_content
        db.commit()

        return {
            "nodes": nodes,
            "edges": edges,
            "ttl_filename": file.filename,
            "message": f"成功解析TTL文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
        }
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"TTL parsing error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"TTL文件解析失败: {str(e)}")
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
    if db_project.owner_id != current_user.id:
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

        # 同步到 Neo4j
        try:
            neo4j_client.sync_graph(db_project.id, {"nodes": nodes, "edges": edges})
        except Exception as e:
            logger.error(f"Neo4j sync error during update: {str(e)}")

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
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not db_project.is_published and db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to download this project's TTL file")

    # 始终基于最新 graph_data 重新生成，保证下载内容是最新快照
    if db_project.graph_data:
        latest_ttl_content = generate_ttl_from_graph_data(
            db_project.graph_data.get("nodes", []),
            db_project.graph_data.get("edges", [])
        )
    else:
        latest_ttl_content = db_project.ttl_content

    if not latest_ttl_content:
        raise HTTPException(status_code=404, detail="TTL file not found for this project")

    temp_dir = "temp_downloads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"ontology_{db_project.id}_{db_project.name.replace(' ', '_')}.ttl"
    temp_path = os.path.join(temp_dir, temp_filename)

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(latest_ttl_content)
        with open(temp_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type="text/turtle",
            headers={"Content-Disposition": f'attachment; filename="{temp_filename}"'},
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ─────────────────────────────────────────────
#  内部辅助函数
# ─────────────────────────────────────────────

def _build_extractor(
    db: Session,
    chunk_size: int = 15000,
    chunk_overlap: int = 500,
    request_interval: int = 2,
) -> OntologyExtractor:
    """从数据库配置中读取 LLM 参数，构建 OntologyExtractor 实例。"""
    db_config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
    llm_config = db_config.value if db_config else {}

    api_key = llm_config.get("api_key") or settings.VLLM_API_KEY
    base_url = llm_config.get("base_url") or settings.VLLM_BASE_URL
    model = llm_config.get("model") or settings.VLLM_MODEL

    return OntologyExtractor(api_key=api_key, base_url=base_url, model=model)


def generate_ttl_from_graph_data(nodes: List[dict], edges: List[dict]) -> str:
    """
    ★ 全生命周期 TTL 同步：将前端 nodes+edges 反向序列化为标准 OWL TTL。
    使用 rdflib 保证 RDF 语义正确性。
    """
    from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace, URIRef

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
        if '#' in node_id or (node_id.startswith('http')):
            return URIRef(node_id)
        clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', node_id)
        return ex[clean]

    node_uris: Dict[str, URIRef] = {}
    for node in nodes:
        node_id = str(node['id'])
        node_label = node['data'].get('label', node_id)
        node_type = node['data'].get('type', 'owl:Class')
        uri = make_uri(node_id)
        node_uris[node_id] = uri

        if node_type == 'owl:Class':
            g.add((uri, RDF.type, OWL.Class))
        elif node_type == 'owl:NamedIndividual':
            g.add((uri, RDF.type, OWL.NamedIndividual))
        else:
            class_uri = make_uri(node_type)
            g.add((uri, RDF.type, class_uri))
            g.add((class_uri, RDF.type, OWL.Class))

        g.add((uri, RDFS.label, Literal(node_label, lang="zh")))

        for prop_name, prop_value in node['data'].get('properties', {}).items():
            if not prop_value:
                continue
            dataprop_uri = ex[re.sub(r'[^a-zA-Z0-9_]', '_', prop_name)]
            g.add((dataprop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((dataprop_uri, RDFS.label, Literal(prop_name, lang="zh")))
            g.add((uri, dataprop_uri, Literal(str(prop_value))))

    for edge in edges:
        source_id = str(edge['source'])
        target_id = str(edge['target'])
        relation_label = (
            edge.get('label')
            or (edge.get('data') or {}).get('label')
            or 'relatedTo'
        )

        if source_id in node_uris and target_id in node_uris:
            source_uri = node_uris[source_id]
            target_uri = node_uris[target_id]

            if relation_label in ('rdf:type', 'type'):
                g.add((source_uri, RDF.type, target_uri))
            elif relation_label == 'subClassOf':
                g.add((source_uri, RDFS.subClassOf, target_uri))
            else:
                import re as _re
                prop_id = _re.sub(r'[^a-zA-Z0-9_]', '_', relation_label)
                objprop_uri = ex[prop_id]
                g.add((objprop_uri, RDF.type, OWL.ObjectProperty))
                g.add((objprop_uri, RDFS.label, Literal(relation_label, lang="zh")))
                g.add((source_uri, objprop_uri, target_uri))

    return g.serialize(format="turtle")


# 为向后兼容保留旧函数名
generate_ttl_from_react_flow = generate_ttl_from_graph_data


def convert_ttl_to_graph_data(ttl_content: str):
    """
    将 TTL 转换为前端可渲染的 (nodes, edges) 二元组。
    """
    g = Graph()
    g.parse(data=ttl_content, format="turtle")

    nodes = []
    edges = []
    processed_nodes: set = set()

    def add_node(uri, node_type_category: str) -> str:
        node_id = str(uri).split('#')[-1] if '#' in str(uri) else str(uri).split('/')[-1]
        if node_id in processed_nodes:
            return node_id

        label = node_id
        for obj in g.objects(uri, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        props = {}
        for pred, obj in g.predicate_objects(uri):
            if str(pred) not in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                                  str(RDFS.domain), str(RDFS.range)]:
                p_name = str(pred).split('#')[-1]
                if not str(obj).startswith('http'):
                    props[p_name] = str(obj)

        nodes.append({
            "id": node_id,
            "type": "custom",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": label,
                "type": node_type_category,
                "properties": props,
            },
        })
        processed_nodes.add(node_id)
        return node_id

    for subj in g.subjects(RDF.type, OWL.Class):
        add_node(subj, "owl:Class")

    for subj in g.subjects(RDF.type, OWL.NamedIndividual):
        add_node(subj, "owl:NamedIndividual")

    # ObjectProperty domain → range 边
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        domain = list(g.objects(prop, RDFS.domain))
        range_ = list(g.objects(prop, RDFS.range))
        label_objs = list(g.objects(prop, RDFS.label))
        prop_label = str(label_objs[0]) if label_objs else str(prop).split('#')[-1]

        if domain and range_:
            source_id = add_node(domain[0], "owl:Class")
            target_id = add_node(range_[0], "owl:Class")
            edges.append({
                "id": f"e_{source_id}_{target_id}_{prop_label}",
                "source": source_id,
                "target": target_id,
                "label": prop_label,
                "type": "custom",
                "data": {"label": prop_label},
            })

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

    # 实例间普通关系边
    for subj, pred, obj in g.triples((None, None, None)):
        if str(pred) in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                         str(RDFS.domain), str(RDFS.range)]:
            continue
        subj_str = str(subj).split('#')[-1]
        if subj_str in processed_nodes and str(obj).startswith('http'):
            obj_str = str(obj).split('#')[-1]
            if obj_str in processed_nodes:
                pred_label = str(pred).split('#')[-1]
                edges.append({
                    "id": f"e_{subj_str}_{obj_str}_{pred_label}",
                    "source": subj_str,
                    "target": obj_str,
                    "label": pred_label,
                    "type": "custom",
                    "data": {"label": pred_label},
                })

    return nodes, edges


# 向后兼容（旧函数名）
convert_ttl_to_react_flow = convert_ttl_to_graph_data
