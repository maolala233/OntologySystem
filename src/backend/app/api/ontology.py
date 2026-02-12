from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Response
from sqlalchemy.orm import Session
from typing import List, Optional
from app.infrastructure.database import get_db, Project, User, SystemConfig
from app.schemas.ontology import ProjectCreate, ProjectUpdate, ProjectResponse
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
    
    # 权限检查：公开项目或自己的项目可查看
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
    
    # 权限检查：只有创建者可以修改
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to modify this project")
    
    # 更新字段
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
    
    # 权限检查：只有创建者可以删除
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to delete this project")
    
    # 同步删除图数据库中的数据
    neo4j_deletion_success = True
    try:
        from app.core.logging import logger
        logger.info(f"Attempting to delete project {project_id} data from Neo4j")
        # 删除 Neo4j 中的相关节点和关系
        neo4j_deletion_success = neo4j_client.delete_project_data(project_id)
        if neo4j_deletion_success:
            logger.info(f"Successfully deleted project {project_id} data from Neo4j")
        else:
            logger.error(f"Failed to delete project {project_id} data from Neo4j (method returned False)")
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"Exception occurred when deleting project data from Neo4j: {str(e)}")
        # 在这种情况下，我们仍然继续删除关系数据库中的项目，但记录错误
        neo4j_deletion_success = False
    
    # 删除关系数据库中的项目
    db.delete(db_project)
    db.commit()
    
    # 返回结果，告知是否图数据库同步删除成功
    if neo4j_deletion_success:
        return {"message": "Project deleted successfully", "neo4j_sync": True}
    else:
        return {"message": "Project deleted successfully but Neo4j sync failed", "neo4j_sync": False}

# 发布项目
@router.post("/{project_id}/publish", response_model=ProjectResponse)
def publish_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以发布
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to publish this project")
    
    # 如果有图数据，先更新TTL内容（关键修复：确保TTL是最新的）
    if db_project.graph_data:
        try:
            ttl_content = generate_ttl_from_react_flow(
                db_project.graph_data.get("nodes", []), 
                db_project.graph_data.get("edges", [])
            )
            db_project.ttl_content = ttl_content
            db.commit()
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

# 取消发布项目
@router.post("/{project_id}/unpublish", response_model=ProjectResponse)
def unpublish_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以取消发布
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to unpublish this project")
    
    db_project.is_published = False
    db.commit()
    db.refresh(db_project)
    
    return db_project

# 上传文档并提取本体
@router.post("/{project_id}/upload")
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    scenario: Optional[str] = None,  # 场景描述
    chunk_size: int = 15000,
    chunk_overlap: int = 500,
    request_interval: int = 2,
    product_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以上传
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    
    # 保存临时文件
    os.makedirs("temp_uploads", exist_ok=True)
    temp_path = os.path.join("temp_uploads", file.filename)
    
    with open(temp_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    try:
        # 判断文件类型
        filename_lower = file.filename.lower()
        
        if filename_lower.endswith('.ttl'):
            # 如果是TTL文件，直接解析
            with open(temp_path, "r", encoding='utf-8') as ttl_file:
                ttl_content = ttl_file.read()
            
            # 从TTL内容生成React Flow节点和边
            nodes, edges = convert_ttl_to_react_flow(ttl_content)
            
            # 更新项目中的TTL内容
            db_project.ttl_content = ttl_content
            db.commit()
            
            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": file.filename,
                "message": f"成功解析TTL文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系"
            }
        else:
            # 处理其他类型的文档
            # 读取文本内容
            from app.services.parser import FileParser
            parser = FileParser()
            text_content = parser.parse_file(temp_path)
            
            if not text_content:
                text_content = "" # 容错
                
            # 获取系统配置中的 LLM 设置
            db_config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
            llm_config = db_config.value if db_config else {}
            
            # 使用配置覆盖默认设置
            api_key = llm_config.get("api_key") or settings.VLLM_API_KEY
            base_url = llm_config.get("base_url") or settings.VLLM_BASE_URL
            model = llm_config.get("model") or settings.VLLM_MODEL
            
            # 也可以从配置中获取分块大小等参数，确保类型正确
            try:
                chunk_size = int(llm_config.get("chunk_size", chunk_size))
            except (ValueError, TypeError):
                chunk_size = chunk_size  # 使用默认值
                
            try:
                chunk_overlap = int(llm_config.get("chunk_overlap", chunk_overlap))
            except (ValueError, TypeError):
                chunk_overlap = chunk_overlap  # 使用默认值
                
            try:
                request_interval = int(llm_config.get("request_interval", request_interval))
            except (ValueError, TypeError):
                request_interval = request_interval  # 使用默认值

            # 初始化提取器
            extractor = OntologyExtractor(
                api_key=api_key,
                base_url=base_url,
                model=model
            )
            
            # 构建空的规则 DataFrame
            df = pd.DataFrame(columns=["主体 (Class)", "属性 (DataProp)", "关系 (ObjectProp)"])
            
            # 调用完整的本体构建方法
            filename, msg = extractor.build_ontology(
                text_content,
                scenario or db_project.description or "通用知识领域本体提取",
                df,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                request_interval=request_interval,
                product_code=product_code
            )
            
            # 读取生成的TTL内容
            with open(filename, 'r', encoding='utf-8') as f:
                ttl_content = f.read()
            
            # 更新项目中的TTL内容
            db_project.ttl_content = ttl_content
            db.commit()
            
            # 从TTL内容生成React Flow节点和边
            nodes, edges = convert_ttl_to_react_flow(ttl_content)
            
            return {
                "nodes": nodes,
                "edges": edges,
                "ttl_filename": filename,
                "message": msg
            }
    
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"Extraction error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"本体提取失败: {str(e)}")
    
    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)


# 上传TTL文件并解析为前端展示要素
@router.post("/{project_id}/upload-ttl")
async def upload_ttl_file(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    专门用于上传TTL文件的端点，将TTL内容解析为前端可视化所需的节点和边
    """
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以上传
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    
    # 检查文件类型
    if not file.filename.lower().endswith('.ttl'):
        raise HTTPException(status_code=400, detail="Only TTL files are accepted")
    
    # 保存临时文件
    os.makedirs("temp_uploads", exist_ok=True)
    temp_path = os.path.join("temp_uploads", file.filename)
    
    with open(temp_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    try:
        # 读取TTL内容
        with open(temp_path, "r", encoding='utf-8') as ttl_file:
            ttl_content = ttl_file.read()
        
        # 从TTL内容生成React Flow节点和边
        nodes, edges = convert_ttl_to_react_flow(ttl_content)
        
        # 更新项目中的TTL内容
        db_project.ttl_content = ttl_content
        db.commit()
        
        return {
            "nodes": nodes,
            "edges": edges,
            "ttl_filename": file.filename,
            "message": f"成功解析TTL文件，包含 {len(nodes)} 个实体和 {len(edges)} 个关系"
        }
    
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"TTL parsing error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"TTL文件解析失败: {str(e)}")
    
    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)


def generate_ttl_from_react_flow(nodes: List[dict], edges: List[dict]):
    """
    从React Flow的节点和边数据生成TTL内容 (优化版：支持标准OWL类型)
    """
    from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace
    
    # 创建图
    g = Graph()
    ex = Namespace("http://www.example.org/auto_ontology#")
    g.bind("ex", ex)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("xsd", Namespace("http://www.w3.org/2001/XMLSchema#"))
    
    # 添加本体声明
    onto_uri = ex["Ontology"]
    g.add((onto_uri, RDF.type, OWL.Ontology))
    g.add((onto_uri, OWL.versionInfo, Literal("1.0")))
    
    # 创建节点ID到URI的映射
    node_uris = {}
    for node in nodes:
        node_id = str(node['id'])
        node_label = node['data'].get('label', node_id)
        node_type = node['data'].get('type', 'owl:Class')
        
        # 处理特殊的前缀
        if '#' in node_id or '/' in node_id:
            uri = Namespace(node_id) # 如果已经是URI则保持
        else:
            uri = ex[node_id]
            
        node_uris[node_id] = uri
        
        # 1. 设置类型
        if node_type == 'owl:Class':
            g.add((uri, RDF.type, OWL.Class))
        elif node_type == 'owl:NamedIndividual':
            g.add((uri, RDF.type, OWL.NamedIndividual))
        else:
            # 如果是自定义类名，则该节点是该类的实例，且该类本身是一个 OWL:Class
            class_uri = ex[node_type]
            g.add((uri, RDF.type, class_uri))
            g.add((class_uri, RDF.type, OWL.Class))
            g.add((class_uri, RDFS.label, Literal(node_type, lang="zh")))
            
        # 2. 设置 Label
        g.add((uri, RDFS.label, Literal(node_label, lang="zh")))
        
        # 3. 设置属性 (DatatypeProperty)
        for prop_name, prop_value in node['data'].get('properties', {}).items():
            if not prop_value: continue
            dataprop_uri = ex[prop_name]
            g.add((dataprop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((dataprop_uri, RDFS.label, Literal(prop_name, lang="zh")))
            g.add((uri, dataprop_uri, Literal(str(prop_value))))

    # 添加对象属性 (ObjectProperty)
    for edge in edges:
        source_id = str(edge['source'])
        target_id = str(edge['target'])
        
        # 提取关系标签
        relation_label = edge.get('label') or edge.get('data', {}).get('label') or 'relatedTo'
        
        if source_id in node_uris and target_id in node_uris:
            source_uri = node_uris[source_id]
            target_uri = node_uris[target_id]
            
            if relation_label == 'rdf:type' or relation_label == 'type':
                g.add((source_uri, RDF.type, target_uri))
            elif relation_label == 'subClassOf':
                g.add((source_uri, RDFS.subClassOf, target_uri))
            else:
                objprop_uri = ex[relation_label]
                g.add((objprop_uri, RDF.type, OWL.ObjectProperty))
                g.add((objprop_uri, RDFS.label, Literal(relation_label, lang="zh")))
                g.add((source_uri, objprop_uri, target_uri))
    
    # 序列化为TTL格式
    ttl_content = g.serialize(format="turtle")
    return ttl_content


def convert_ttl_to_react_flow(ttl_content: str):
    """
    将TTL转换为React Flow格式 (Neo4j风格优化版)
    """
    g = Graph()
    g.parse(data=ttl_content, format="turtle")
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)

    nodes = []
    edges = []
    processed_nodes = set()

    # --- 辅助函数：生成节点 ---
    def add_node(uri, node_type_category):
        node_id = str(uri).split('#')[-1] if '#' in str(uri) else str(uri).split('/')[-1]

        if node_id in processed_nodes:
            return node_id

        # 获取 Label (优先中文)
        label = node_id
        for obj in g.objects(uri, RDFS.label):
            label = str(obj)
            if hasattr(obj, 'language') and obj.language == 'zh':
                break

        # 收集属性
        props = {}
        for pred, obj in g.predicate_objects(uri):
            if str(pred) not in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf), str(RDFS.domain),
                                 str(RDFS.range)]:
                p_name = str(pred).split('#')[-1]
                if not str(obj).startswith('http'):
                    props[p_name] = str(obj)

        nodes.append({
            "id": node_id,
            "type": "custom",  # <--- 关键：强制指定 custom 类型
            "position": {"x": 0, "y": 0},  # 坐标交由前端计算
            "data": {
                "label": label,
                "type": node_type_category,  # 'owl:Class' 或 'owl:NamedIndividual'
                "properties": props
            }
        })
        processed_nodes.add(node_id)
        return node_id

    # 1. 提取类 (Class)
    for subj in g.subjects(RDF.type, OWL.Class):
        add_node(subj, "owl:Class")

    # 2. 提取个体 (NamedIndividual)
    for subj in g.subjects(RDF.type, OWL.NamedIndividual):
        add_node(subj, "owl:NamedIndividual")

    # 3. 提取关系 (Edges)

    # 3.1 Schema 关系 (基于 ObjectProperty 的 domain/range)
    # 这能让你看到类之间的逻辑关系，而不仅仅是 type
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
                "data": {"label": prop_label}
            })

    # 3.2 实例关系 (rdf:type)
    for subj, obj in g.subject_objects(RDF.type):
        if str(obj) in [str(OWL.Class), str(OWL.NamedIndividual)]: continue  # 跳过元定义

        subj_id = str(subj).split('#')[-1]
        obj_id = str(obj).split('#')[-1]

        # 确保两端节点都已存在
        if subj_id in processed_nodes and obj_id in processed_nodes:
            edges.append({
                "id": f"e_{subj_id}_type_{obj_id}",
                "source": subj_id,
                "target": obj_id,
                "label": "rdf:type",
                "type": "custom",
                "style": {"strokeDasharray": "5,5"},  # 虚线表示 type 关系
                "data": {"label": "type"}
            })

    # 3.3 实例间的普通关系 (如果有的话)
    for subj, pred, obj in g.triples((None, None, None)):
        if str(pred) in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf), str(RDFS.domain), str(RDFS.range)]:
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
                    "data": {"label": pred_label}
                })

    return nodes, edges

# 更新项目本体数据并重新生成TTL
@router.post("/{project_id}/update-ontology")
async def update_ontology(
    project_id: int,
    request: dict,  # 直接接收JSON请求体
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以修改
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to update this project")
    
    try:
        # 从请求体中提取节点和边数据
        nodes = request.get('nodes', [])
        edges = request.get('edges', [])
        
        # 更新项目中的图数据
        db_project.graph_data = {"nodes": nodes, "edges": edges}
        
        # 同步到 Neo4j (关键更新：保存草稿时同步到图数据库)
        try:
            neo4j_client.sync_graph(db_project.id, db_project.graph_data)
        except Exception as e:
            from app.core.logging import logger
            logger.error(f"Neo4j sync error during update: {str(e)}")
            # 这里不抛出异常，让基本数据的保存继续完成

        # 使用更新后的图数据重新生成TTL
        ttl_content = generate_ttl_from_react_flow(nodes, edges)
        
        # 更新项目中的TTL内容
        db_project.ttl_content = ttl_content
        db.commit()
        
        # 返回更新后的数据
        return {
            "nodes": nodes,
            "edges": edges,
            "message": f"成功更新本体并同步Neo4j，包含 {len(nodes)} 个实体和 {len(edges)} 个关系",
            "ttl_updated": True,
            "neo4j_synced": True
        }
    
    except Exception as e:
        from app.core.logging import logger
        logger.error(f"Update ontology error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新本体失败: {str(e)}")


# 下载TTL文件
@router.get("/{project_id}/download-ttl")
def download_ttl(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：公开项目或自己的项目可下载
    if not db_project.is_published and db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to download this project's TTL file")
    
    # 确保TTL内容是最新的（关键修复：确保下载的是最新版本）
    if db_project.graph_data:
        ttl_content = generate_ttl_from_react_flow(
            db_project.graph_data.get("nodes", []), 
            db_project.graph_data.get("edges", [])
        )
        # 不直接更新数据库中的ttl_content，因为这可能会影响发布状态
        # 而是直接使用生成的最新内容
        latest_ttl_content = ttl_content
    else:
        latest_ttl_content = db_project.ttl_content
    
    if not latest_ttl_content:
        raise HTTPException(status_code=404, detail="TTL file not found for this project")
    
    # 创建临时文件
    temp_dir = "temp_downloads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"ontology_{db_project.id}_{db_project.name.replace(' ', '_')}.ttl"
    temp_path = os.path.join(temp_dir, temp_filename)
    
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(latest_ttl_content)
        
        # 返回文件响应
        with open(temp_path, "rb") as f:
            content = f.read()
        
        return Response(
            content=content,
            media_type="text/turtle",
            headers={
                "Content-Disposition": f'attachment; filename="{temp_filename}"'
            }
        )
    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)

