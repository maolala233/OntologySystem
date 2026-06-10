import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Response, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from urllib.parse import quote
from app.infrastructure.database import get_db, Project, User, SystemConfig, UploadedDocument
from app.schemas.ontology import ProjectCreate, ProjectUpdate, ProjectResponse
from app.schemas.domain import KnowledgeDomainCreate
from app.infrastructure.database import KnowledgeDomain
from app.schemas.extraction import (
    SchemaExtractionRequest, SchemaExtractionResponse,
    InstanceExtractionRequest, InstanceExtractionResponse,
    SchemaGraph, OntologyClass, OntologyObjectProperty,
    GraphData, GraphNode, GraphEdge, NodeData, EdgeData,
    SaveGraphRequest,
)
from app.api.auth import get_current_user
from app.core.config import settings, ensure_dirs
import json
import os
import tempfile
import requests as req
from app.services.extractor import OntologyExtractor
from app.infrastructure.neo4j_client import neo4j_client
import pandas as pd
from rdflib import Graph, RDF, OWL, RDFS

router = APIRouter(prefix="/api/projects", tags=["projects"])

EXTRACTION_SEMAPHORE = asyncio.Semaphore(3)

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
    from sqlalchemy.orm import joinedload
    
    # 使用 joinedload 预加载 domain 和 owner 信息
    project = db.query(Project).options(
        joinedload(Project.domain),
        joinedload(Project.owner)
    ).filter(Project.id == project_id).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.is_published and project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project")
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

    # ★ 发布前必须配置知识域
    if not db_project.domain_id:
        raise HTTPException(
            status_code=400, 
            detail="发布失败：请先配置知识域。点击工具栏「知识域」按钮进行配置。"
        )

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

# ─────────────────────────────────────────────
#  ★ 基于已上传文档 ID 进行骨架提取（新增）
# ─────────────────────────────────────────────

@router.post("/{project_id}/extract-schema-from-documents")
async def extract_schema_from_documents(
    project_id: int,
    document_ids: str = Form(..., description="已上传文档 ID 列表，逗号分隔"),
    user_intent: Optional[str] = Form(None, description="用户意图/关注领域（可选）"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(10, description="分块重叠百分比(0-50)"),
    request_interval: int = Form(2),
    async_mode: str = Form("true", description="是否异步执行（支持取消）"),
    disable_think: bool = Form(True, description="是否禁用思考模式（Qwen3等思考模型）"),
    vl_enabled: bool = Form(False, description="是否启用VL视觉模型解析文档图片"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API - 基于已上传文档进行骨架提取】
    基于数据库中已上传的文档 ID 进行骨架提取，不需要重新上传文件。
    适用于文档管理 Modal 中点击"开始骨架提取"的场景。
    """
    from app.core.logging import logger
    from app.infrastructure.task_manager import task_manager, TaskCancelledError

    # 解析文档 ID 列表
    doc_ids = [int(id.strip()) for id in document_ids.split(',') if id.strip()]
    if not doc_ids:
        raise HTTPException(status_code=400, detail="请提供至少一个文档 ID")

    logger.info(f"[extract-schema-from-documents] 收到请求 - project_id={project_id}, doc_ids={doc_ids}, vl_enabled={vl_enabled}")

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    # 从数据库获取文档记录
    documents = db.query(UploadedDocument).filter(
        UploadedDocument.id.in_(doc_ids),
        UploadedDocument.project_id == project_id
    ).all()

    if not documents:
        raise HTTPException(status_code=404, detail="未找到指定的文档")

    if len(documents) != len(doc_ids):
        raise HTTPException(status_code=400, detail="部分文档不存在或不属于该项目")

    # 获取文件路径列表
    temp_paths = [doc.file_path for doc in documents if doc.file_path]
    logger.info(f"[extract-schema-from-documents] 共 {len(temp_paths)} 个文件")

    from app.services.parser import FileParser
    parser = FileParser(vl_enabled=vl_enabled)
    logger.info(f"[extract-schema-from-documents] FileParser vl_enabled={parser.vl_enabled}")
    text_contents = []
    for temp_path in temp_paths:
        if os.path.exists(temp_path):
            if vl_enabled:
                text_content = await parser.async_parse_file(temp_path) or ""
            else:
                text_content = parser.parse_file(temp_path) or ""
            text_contents.append(text_content)
            logger.info(f"[extract-schema-from-documents] 文件解析完成 - file={temp_path}, text_length={len(text_content)}")
        else:
            logger.warning(f"[extract-schema-from-documents] 文件不存在 - {temp_path}")
    
    # 合并所有文件内容
    combined_text = "\n\n".join(text_contents)
    logger.info(f"[extract-schema-from-documents] 合并后总文本长度={len(combined_text)}")

    # 获取 LLM 配置
    extractor = _build_extractor(db, disable_think=disable_think)

    # 异步模式
    is_async_mode = async_mode == "true" if isinstance(async_mode, str) else bool(async_mode)
    
    if is_async_mode:
        # 异步模式：创建任务并后台执行
        task_id = task_manager.create_task(message="开始骨架提取...")
        logger.info(f"[extract-schema-from-documents] 任务已创建 - task_id={task_id}")
        task_manager.start_task(task_id, message="开始骨架提取...", detail=f"正在解析 {len(documents)} 个文档...")
        
        # 后台执行提取任务
        async def run_extraction():
            async with EXTRACTION_SEMAPHORE:
                try:
                    def progress_callback(progress: float, message: str):
                        task_manager.update_progress(task_id, progress=progress, message=message)
                    
                    schema = await extractor.async_extract_schema_only(
                            text=combined_text,
                            user_intent=user_intent,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                            request_interval=request_interval,
                            task_id=task_id,
                            progress_callback=progress_callback,
                        )
                    
                    # 转换为前端渲染格式
                    graph_data = OntologyExtractor.schema_to_graph_data(schema)
                    
                    # 将骨架 schema 临时存到 project graph_data
                    merged_graph = {
                        "schema": schema,
                        **graph_data,
                    }
                    # 在异步上下文中需要重新获取db_project并使用新的session
                    from app.infrastructure.database import SessionLocal
                    _db = SessionLocal()
                    try:
                        _project = _db.query(Project).filter(Project.id == project_id).first()
                        if _project:
                            _project.graph_data = merged_graph
                            _db.commit()
                            logger.info(f"[extract-schema-from-documents] Schema已保存到数据库 - nodes={len(graph_data.get('nodes',[]))}, edges={len(graph_data.get('edges',[]))}")
                        else:
                            logger.error(f"[extract-schema-from-documents] 项目 {project_id} 不存在，无法保存schema")
                    except Exception as db_err:
                        _db.rollback()
                        logger.error(f"[extract-schema-from-documents] 保存schema到数据库失败: {db_err}")
                    finally:
                        _db.close()
                    
                    task_manager.complete_task(
                        task_id,
                        result={"schema_graph": schema, "graph_data": graph_data, "text_content": combined_text, "metadata": schema.get("metadata")},
                        message=f"骨架提取完成：{len(schema.get('object_types', schema.get('classes', [])))} 个对象类型，{len(schema.get('link_types', schema.get('object_properties', [])))} 个链接类型，{len(schema.get('action_types', []))} 个动作类型（来自 {len(documents)} 个文档）"
                    )
                except TaskCancelledError:
                    task_manager.cancel_task(task_id, "用户取消任务")
                except Exception as e:
                    logger.error(f"[extract-schema-from-documents] 错误：{e}", exc_info=True)
                    task_manager.fail_task(task_id, str(e), "骨架提取失败")
        
        # 启动后台任务
        asyncio.create_task(run_extraction())
        
        return {
            "task_id": task_id,
            "message": "任务已启动，请使用 task_id 查询进度",
        }
    else:
        schema = await extractor.async_extract_schema_only(
            text=combined_text,
            user_intent=user_intent,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
        )

        graph_data = OntologyExtractor.schema_to_graph_data(schema)

        merged_graph = {
            "schema": schema,
            **graph_data,
        }
        db_project.graph_data = merged_graph
        db.commit()

        return {
            "schema_graph": schema,
            "graph_data": graph_data,
            "text_content": combined_text,
            "metadata": schema.get("metadata"),
            "message": (
                f"骨架提取完成：{len(schema.get('object_types', schema.get('classes', [])))} 个对象类型，"
                f"{len(schema.get('link_types', schema.get('object_properties', [])))} 个链接类型，"
                f"{len(schema.get('action_types', []))} 个动作类型（来自 {len(documents)} 个文档）。"
                f"请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
            ),
        }


@router.post("/{project_id}/extract-schema")
async def extract_schema_endpoint(
    project_id: int,
    files: List[UploadFile] = File(..., description="支持多文件上传"),
    user_intent: Optional[str] = Form(None, description="用户意图/关注领域（可选）"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(10, description="分块重叠百分比(0-50)"),
    request_interval: int = Form(2),
    async_mode: str = Form("false", description="是否异步执行（支持取消）"),
    save_documents: bool = Form("true", description="是否保存文档记录到数据库"),
    disable_think: bool = Form(True, description="是否禁用思考模式（Qwen3等思考模型）"),
    vl_enabled: bool = Form(False, description="是否启用VL视觉模型解析文档图片"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 正确解析布尔值：只有 "true" (不区分大小写) 才为 True
    is_async_mode = async_mode == "true" if isinstance(async_mode, str) else bool(async_mode)
    should_save_documents = save_documents == "true" if isinstance(save_documents, str) else bool(save_documents)
    
    """
    【API 1 - 骨架提取】
    上传文档 → 提取 OWL Class + ObjectProperty + DataProperty 骨架 Schema。
    绝对禁止提取实例，结果供前端 Step 2 (Schema Review) 使用。
    用户审核骨架后，将 schema_graph 传给 API 2 进行实例提取。
    
    返回包含：
    - schema_graph: 原始 Schema 数据（classes + object_properties）
    - graph_data: 前端可渲染的 {nodes, edges}
    - text_content: 解析后的文本内容（供阶段 2 使用）
    - task_id: 任务 ID（仅当 async_mode=True 时返回）
    - saved_documents: 保存的文档记录列表（当 save_documents=true 时）
    """
    from app.core.logging import logger
    from app.infrastructure.task_manager import task_manager, TaskCancelledError

    logger.info(f"[extract-schema] 收到请求 - project_id={project_id}, async_mode={async_mode}, is_async_mode={is_async_mode}, file_count={len(files)}, save_documents={should_save_documents}")

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    # 创建永久存储目录（用于保存文档记录）
    if should_save_documents:
        ensure_dirs()
        os.makedirs(f"{settings.UPLOAD_PROJECTS_DIR}/{project_id}", exist_ok=True)
    
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_paths = []
    saved_docs = []
    
    try:
        for uploaded_file in files:
            # 如果需要保存文档记录，创建永久存储
            if should_save_documents:
                import uuid
                unique_filename = f"{uuid.uuid4()}_{uploaded_file.filename}"
                file_path = os.path.join(f"{settings.UPLOAD_PROJECTS_DIR}/{project_id}", unique_filename)
                
                # 保存文件到永久目录
                with open(file_path, "wb") as buf:
                    buf.write(await uploaded_file.read())
                
                # 获取文件大小
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                
                # 获取文件类型
                file_ext = uploaded_file.filename.split('.')[-1].lower() if '.' in uploaded_file.filename else ''
                
                # 创建数据库记录
                doc_record = UploadedDocument(
                    project_id=project_id,
                    filename=uploaded_file.filename,
                    file_path=file_path,
                    file_size=file_size,
                    file_type=file_ext,
                )
                db.add(doc_record)
                saved_docs.append(doc_record)
                temp_paths.append(file_path)
                
                logger.info(f"[extract-schema] 已保存文档 - {uploaded_file.filename} -> {file_path}")
            else:
                # 不保存文档记录，只保存到临时目录
                temp_path = os.path.join(settings.TEMP_DIR, uploaded_file.filename)
                with open(temp_path, "wb") as buf:
                    buf.write(await uploaded_file.read())
                temp_paths.append(temp_path)
        
        # 如果需要保存文档记录，提交数据库事务
        if should_save_documents and saved_docs:
            db.commit()
            logger.info(f"[extract-schema] 已保存 {len(saved_docs)} 个文档记录到数据库")
        
        logger.info(f"[extract-schema] 共保存 {len(temp_paths)} 个文件")

        from app.services.parser import FileParser
        parser = FileParser(vl_enabled=vl_enabled)
        text_contents = []
        for temp_path in temp_paths:
            if vl_enabled:
                text_content = await parser.async_parse_file(temp_path) or ""
            else:
                text_content = parser.parse_file(temp_path) or ""
            text_contents.append(text_content)
            logger.info(f"[extract-schema] 文件解析完成 - file={temp_path}, text_length={len(text_content)}")
            
            # 如果保存了文档记录，同时更新文本内容
            if saved_docs:
                for doc in saved_docs:
                    if doc.file_path == temp_path:
                        doc.text_content = text_content
                        break
        
        if saved_docs:
            db.commit()
        
        # 合并所有文件内容
        combined_text = "\n\n".join(text_contents)
        logger.info(f"[extract-schema] 合并后总文本长度={len(combined_text)}")

        # 获取 LLM 配置
        extractor = _build_extractor(db, disable_think=disable_think)

        if is_async_mode:
            # 异步模式：创建任务并后台执行
            task_id = task_manager.create_task(message="开始骨架提取...")
            logger.info(f"[extract-schema] 任务已创建 - task_id={task_id}")
            task_manager.start_task(task_id, message="开始骨架提取...", detail=f"正在解析 {len(temp_paths)} 个文件...")
            
            # 后台执行提取任务
            async def run_extraction():
                async with EXTRACTION_SEMAPHORE:
                    try:
                        def progress_callback(progress: float, message: str):
                            task_manager.update_progress(task_id, progress=progress, message=message)
                        
                        schema = await extractor.async_extract_schema_only(
                                text=combined_text,
                                user_intent=user_intent,
                                chunk_size=chunk_size,
                                chunk_overlap=chunk_overlap,
                                request_interval=request_interval,
                                task_id=task_id,
                                progress_callback=progress_callback,
                            )
                        
                        # 转换为前端渲染格式
                        graph_data = OntologyExtractor.schema_to_graph_data(schema)
                        
                        # 将骨架 schema 临时存到 project graph_data
                        merged_graph = {
                            "schema": schema,
                            **graph_data,
                        }
                        db_project.graph_data = merged_graph
                        db.commit()
                        
                        task_manager.complete_task(
                            task_id,
                            result={"schema_graph": schema, "graph_data": graph_data, "text_content": combined_text, "metadata": schema.get("metadata")},
                            message=f"骨架提取完成：{len(schema.get('object_types', schema.get('classes', [])))} 个对象类型，{len(schema.get('link_types', schema.get('object_properties', [])))} 个链接类型，{len(schema.get('action_types', []))} 个动作类型（来自 {len(files)} 个文件）"
                        )
                    except TaskCancelledError:
                        task_manager.cancel_task(task_id, "用户取消任务")
                    except Exception as e:
                        logger.error(f"[extract-schema] 错误：{e}", exc_info=True)
                        task_manager.fail_task(task_id, str(e), "骨架提取失败")
            
            # 启动后台任务
            asyncio.create_task(run_extraction())
            
            return {
                "task_id": task_id,
                "message": "任务已启动，请使用 task_id 查询进度",
                "saved_documents": [
                    {
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_size": doc.file_size,
                        "file_type": doc.file_type,
                    }
                    for doc in saved_docs
                ] if saved_docs else [],
            }
        else:
            schema = await extractor.async_extract_schema_only(
                text=combined_text,
                user_intent=user_intent,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                request_interval=request_interval,
            )

            graph_data = OntologyExtractor.schema_to_graph_data(schema)

            merged_graph = {
                "schema": schema,
                **graph_data,
            }
            db_project.graph_data = merged_graph
            db.commit()

            return {
                "schema_graph": schema,
                "graph_data": graph_data,
                "text_content": combined_text,
                "metadata": schema.get("metadata"),
                "saved_documents": [
                    {
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_size": doc.file_size,
                        "file_type": doc.file_type,
                    }
                    for doc in saved_docs
                ] if saved_docs else [],
                "message": (
                    f"骨架提取完成：{len(schema.get('object_types', schema.get('classes', [])))} 个对象类型，"
                    f"{len(schema.get('link_types', schema.get('object_properties', [])))} 个链接类型，"
                    f"{len(schema.get('action_types', []))} 个动作类型（来自 {len(files)} 个文件）。"
                    f"请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
                ),
            }

    except Exception as e:
        logger.error(f"[extract-schema] 错误：{e}", exc_info=True)
        if should_save_documents:
            db.rollback()
        raise HTTPException(status_code=500, detail=f"骨架提取失败：{str(e)}")
    finally:
        for temp_path in temp_paths:
            if temp_path.startswith(settings.TEMP_DIR) and os.path.exists(temp_path):
                os.remove(temp_path)


# ─────────────────────────────────────────────
#  ★ 基于已上传文档 ID 进行实例提取（新增）
# ─────────────────────────────────────────────

@router.post("/{project_id}/extract-instances-from-documents")
async def extract_instances_from_documents(
    project_id: int,
    document_ids: str = Form(..., description="已上传文档 ID 列表，逗号分隔"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(10, description="分块重叠百分比(0-50)"),
    request_interval: int = Form(2),
    async_mode: str = Form("true", description="是否异步执行（支持取消）"),
    disable_think: bool = Form(True, description="是否禁用思考模式（Qwen3等思考模型）"),
    vl_enabled: bool = Form(False, description="是否启用VL视觉模型解析文档图片"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API - 基于已上传文档进行实例提取】
    基于数据库中已上传的文档 ID 进行实例提取，不需要重新上传文件。
    适用于文档管理 Modal 中点击"开始实例提取"的场景。
    
    关键点：
    1. 从数据库获取已上传文档
    2. 解析文档获取文本内容（带文档元数据：filename, chunk_index）
    3. 从项目 graph_data 中获取 schema（用户已审核的骨架）
    4. 使用 schema 约束进行实例提取，传递知识域信息
    """
    from app.core.logging import logger
    from app.infrastructure.task_manager import task_manager, TaskCancelledError

    # 解析文档 ID 列表
    doc_ids = [int(id.strip()) for id in document_ids.split(',') if id.strip()]
    if not doc_ids:
        raise HTTPException(status_code=400, detail="请提供至少一个文档 ID")

    logger.info(f"[extract-instances-from-documents] 收到请求 - project_id={project_id}, doc_ids={doc_ids}")

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    # 从数据库获取文档记录
    documents = db.query(UploadedDocument).filter(
        UploadedDocument.id.in_(doc_ids),
        UploadedDocument.project_id == project_id
    ).all()

    if not documents:
        raise HTTPException(status_code=404, detail="未找到指定的文档")

    if len(documents) != len(doc_ids):
        raise HTTPException(status_code=400, detail="部分文档不存在或不属于该项目")

    # 获取文件路径列表
    temp_paths = [doc.file_path for doc in documents if doc.file_path]
    logger.info(f"[extract-instances-from-documents] 共 {len(temp_paths)} 个文件")

    from app.services.parser import FileParser
    parser = FileParser(vl_enabled=vl_enabled)
    documents_list = []  # [{"text": str, "filename": str}]
    
    # 同时从数据库记录中获取原始文件名
    doc_id_to_filename = {doc.id: doc.filename for doc in documents}
    logger.info(f"[extract-instances-from-documents] 数据库中的文件名映射：{doc_id_to_filename}")
    
    for doc_idx, temp_path in enumerate(temp_paths):
        if os.path.exists(temp_path):
            if vl_enabled:
                text_content = await parser.async_parse_file(temp_path) or ""
            else:
                text_content = parser.parse_file(temp_path) or ""
            # ★ 关键修复：优先使用数据库记录中的原始文件名
            original_filename = doc_id_to_filename.get(documents[doc_idx].id, documents[doc_idx].filename)
            
            # 如果数据库记录中的文件名不可用，从路径中提取
            if not original_filename or original_filename == temp_path:
                original_filename = os.path.basename(temp_path)
                # 去除 UUID 前缀（格式：{uuid}_{original_filename}）
                if '_' in original_filename:
                    parts = original_filename.split('_', 1)
                    if len(parts) == 2:
                        original_filename = parts[1]
            
            documents_list.append({"text": text_content, "filename": original_filename})
            logger.info(f"[extract-instances-from-documents] 文件解析完成 - file={temp_path}, original_filename={original_filename}, text_length={len(text_content)}")
        else:
            logger.warning(f"[extract-instances-from-documents] 文件不存在 - {temp_path}")
    
    # 合并所有文件内容
    combined_text = "\n\n".join([doc["text"] for doc in documents_list])
    logger.info(f"[extract-instances-from-documents] 合并后总文本长度={len(combined_text)}")

    # 从项目 graph_data 中获取 schema（用户已审核的版本）
    schema_dict = (db_project.graph_data or {}).get("schema", {})
    
    # 如果没有 schema 字段，尝试从 nodes 和 edges 动态构建（兼容 TTL 导入场景）
    if not schema_dict or not (schema_dict.get("classes") or schema_dict.get("object_types")):
        nodes = (db_project.graph_data or {}).get("nodes", [])
        edges = (db_project.graph_data or {}).get("edges", [])
        if nodes or edges:
            schema_dict = build_schema_from_graph_data(nodes, edges)
            logger.info(f"[extract-instances-from-documents] 从 graph_data 动态构建 schema：{len(schema_dict.get('classes', []))} 个类，{len(schema_dict.get('object_properties', []))} 个 ObjectProperty，{len(schema_dict.get('action_types', []))} 个 ActionType")
    else:
        # schema 字段存在，但可能缺少用户通过前端添加的 action_types
        # 需要从 graph_data 的节点中检查是否有 AT_ 前缀的节点
        existing_action_types = schema_dict.get("action_types", [])
        existing_at_names = {at.get("name", "") for at in existing_action_types} | {at.get("label", "") for at in existing_action_types}
        
        nodes = (db_project.graph_data or {}).get("nodes", [])
        edges = (db_project.graph_data or {}).get("edges", [])
        
        missing_action_types = []
        for node in nodes:
            node_type = node.get('data', {}).get('type', '')
            if node_type == 'owl:Class':
                raw_id = node.get('data', {}).get('raw_id', '')
                node_id = str(node.get('id', ''))
                node_label = node.get('data', {}).get('label', '')
                is_action = raw_id.startswith('AT_') or node_id.startswith('AT_')
                if is_action and node_label not in existing_at_names and node_id not in existing_at_names:
                    target_object_type = ""
                    for edge in edges:
                        edge_data = edge.get('data', {})
                        edge_relation = edge_data.get('relation', '')
                        if edge_relation == 'action' and edge.get('source') == node_id:
                            tgt_id = edge.get('target', '')
                            for n2 in nodes:
                                if str(n2.get('id', '')) == tgt_id:
                                    target_object_type = n2.get('data', {}).get('label', tgt_id)
                                    break
                            break
                    
                    parameters = node.get('data', {}).get('parameters', [])
                    if not parameters:
                        prop_defs = node.get('data', {}).get('property_definitions', [])
                        parameters = [{"name": p.get("name", ""), "data_type": p.get("data_type", "string")} for p in prop_defs if isinstance(p, dict)]
                    
                    missing_action_types.append({
                        "id": raw_id or node_id,
                        "name": node_label,
                        "label": node_label,
                        "description": node.get('data', {}).get('description', ''),
                        "target_object_type": target_object_type,
                        "parameters": parameters,
                    })
        
        if missing_action_types:
            if "action_types" not in schema_dict:
                schema_dict["action_types"] = []
            schema_dict["action_types"].extend(missing_action_types)
            logger.info(f"[extract-instances-from-documents] 从 graph_data 补充了 {len(missing_action_types)} 个缺失的 ActionType 到 schema_dict")
    
    if not schema_dict or not (schema_dict.get("classes") or schema_dict.get("object_types")):
        raise HTTPException(status_code=400, detail="请先提取骨架再进行实例提取")

    at_count = len(schema_dict.get("action_types", []))
    logger.info(f"[extract-instances-from-documents] schema_dict 包含 action_types: {at_count} 个")
    if at_count > 0:
        for at in schema_dict.get("action_types", []):
            logger.info(f"[extract-instances-from-documents]   - ActionType: name={at.get('name')}, label={at.get('label')}, target={at.get('target_object_type')}")

    # 获取知识域信息（用于注入到 Prompt 中）
    domain_name = ""
    if db_project.domain_id:
        domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == db_project.domain_id).first()
        if domain:
            domain_name = domain.name
    # 同时支持 domains 字段（多知识域逗号分隔）
    domains_str = db_project.domains or domain_name
    
    # 获取 LLM 配置
    extractor = _build_extractor(db, disable_think=disable_think)

    # 异步模式
    is_async_mode = async_mode == "true" if isinstance(async_mode, str) else bool(async_mode)
    
    if is_async_mode:
        # 异步模式：创建任务并后台执行
        task_id = task_manager.create_task(message="开始实例提取...")
        logger.info(f"[extract-instances-from-documents] 任务已创建 - task_id={task_id}")
        task_manager.start_task(task_id, message="开始实例提取...", detail=f"正在解析 {len(documents)} 个文档...")
        
        # 后台执行提取任务
        async def run_extraction():
            from app.infrastructure.database import SessionLocal as InstSessionLocal
            async with EXTRACTION_SEMAPHORE:
                try:
                    def progress_callback(progress: float, message: str):
                        task_manager.update_progress(task_id, progress=progress, message=message)
                    
                    inst_result = await extractor.async_extract_instances_with_constraints(
                            text=combined_text,
                            schema_graph=schema_dict,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                            request_interval=request_interval,
                            product_code=domains_str,
                            task_id=task_id,
                            progress_callback=progress_callback,
                            documents=documents_list,
                        )

                    # 在异步上下文中重新获取graph_data
                    _schema_db = InstSessionLocal()
                    try:
                        _schema_proj = _schema_db.query(Project).filter(Project.id == project_id).first()
                        existing_nodes = (_schema_proj.graph_data or {}).get("nodes", []) if _schema_proj else []
                        existing_edges = (_schema_proj.graph_data or {}).get("edges", []) if _schema_proj else []
                    finally:
                        _schema_db.close()
                    
                    # ★ 关键修复：直接使用所有原始节点和边作为schema，避免过滤导致类和关系丢失
                    schema_graph_data = {"nodes": existing_nodes, "edges": existing_edges}
                    
                    full_graph_data = OntologyExtractor.merge_instances_to_graph_data(
                        schema_graph_data=schema_graph_data,
                        instances=inst_result["instances"],
                        action_instances=inst_result.get("action_instances", []),
                    )

                    # 在异步上下文中需要重新获取db对象并使用新的session
                    from app.infrastructure.database import SessionLocal as InstSessionLocal
                    _inst_db = InstSessionLocal()
                    try:
                        _inst_project = _inst_db.query(Project).filter(Project.id == project_id).first()
                        if _inst_project:
                            _inst_project.graph_data = {
                                "schema": schema_dict,
                                **full_graph_data,
                            }
                            _inst_db.commit()
                            logger.info(f"[extract-instances-from-documents] 实例数据已保存到数据库 - nodes={len(full_graph_data.get('nodes',[]))}, edges={len(full_graph_data.get('edges',[]))}")
                        else:
                            logger.error(f"[extract-instances-from-documents] 项目 {project_id} 不存在，无法保存实例数据")
                    except Exception as db_err:
                        _inst_db.rollback()
                        logger.error(f"[extract-instances-from-documents] 保存实例数据到数据库失败: {db_err}")
                    finally:
                        _inst_db.close()

                    try:
                        domain_name = ""
                        _vdb = InstSessionLocal()
                        try:
                            _vproject = _vdb.query(Project).filter(Project.id == project_id).first()
                            if _vproject and _vproject.domain_id:
                                domain = _vdb.query(KnowledgeDomain).filter(KnowledgeDomain.id == _vproject.domain_id).first()
                                if domain:
                                    domain_name = domain.name
                        finally:
                            _vdb.close()
                        
                        ttl_content = generate_ttl_from_graph_data(full_graph_data["nodes"], full_graph_data["edges"])
                        
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.ttl', delete=False, encoding='utf-8') as f:
                            f.write(ttl_content)
                            temp_ttl_path = f.name
                        
                        try:
                            collection_name = f"project_{project_id}"
                            extractor.sync_ttl_to_vector_store(
                                ttl_file_path=temp_ttl_path,
                                project_id=project_id,
                                domain=domain_name,
                                collection_name=collection_name,
                            )
                        except Exception as e:
                            logger.error(f"[extract-instances-from-documents] 向量入库失败：{e}")
                        finally:
                            if temp_ttl_path and os.path.exists(temp_ttl_path):
                                os.remove(temp_ttl_path)
                    except Exception as e:
                        logger.error(f"[extract-instances-from-documents] 向量入库异常：{e}")

                    task_manager.complete_task(
                        task_id,
                        result={
                            "instances": inst_result["instances"],
                            "graph_data": full_graph_data,
                            "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
                            "schema_graph": schema_dict,
                            "text_content": combined_text,
                            "metadata": inst_result.get("metadata"),
                        },
                        message=f"实例提取完成：{len(inst_result['instances'])} 个实例" + (
                            f" ({inst_result.get('discarded_edges_count', 0)} 条不合规连线已自动丢弃)" 
                            if inst_result.get('discarded_edges_count', 0) > 0 else ""
                        )
                    )
                except TaskCancelledError:
                    task_manager.cancel_task(task_id, "用户取消任务")
                except Exception as e:
                    logger.error(f"[extract-instances-from-documents] 错误：{e}", exc_info=True)
                    task_manager.fail_task(task_id, str(e), "实例提取失败")
        
        # 启动后台任务
        asyncio.create_task(run_extraction())
        
        return {
            "task_id": task_id,
            "message": "任务已启动，请使用 task_id 查询进度",
        }
    else:
        inst_result = await extractor.async_extract_instances_with_constraints(
            text=combined_text,
            schema_graph=schema_dict,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            product_code=domains_str,
            task_id=None,
            documents=documents_list,
        )

        # ★ 关键修复：直接使用所有原始节点和边作为schema，避免过滤导致类和关系丢失
        existing_nodes = (db_project.graph_data or {}).get("nodes", [])
        existing_edges = (db_project.graph_data or {}).get("edges", [])
        
        schema_graph_data = {"nodes": existing_nodes, "edges": existing_edges}
        logger.info(f"[extract-instances-from-documents] 同步模式：使用现有骨架图 - {len(existing_nodes)} 个类节点，{len(existing_edges)} 条边")
        
        # 合并实例到完整图
        full_graph_data = OntologyExtractor.merge_instances_to_graph_data(
            schema_graph_data=schema_graph_data,
            instances=inst_result["instances"],
            action_instances=inst_result.get("action_instances", []),
        )

        # 更新 project graph_data
        db_project.graph_data = {
            "schema": schema_dict,
            **full_graph_data,
        }
        db.commit()

        # ★ 向量入库：将提取的实例同步到向量库（同步模式）
        try:
            # 获取知识域信息
            domain_name = ""
            if db_project.domain_id:
                domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == db_project.domain_id).first()
                if domain:
                    domain_name = domain.name
            
            # 构建 TTL 内容用于向量入库
            ttl_content = generate_ttl_from_graph_data(full_graph_data["nodes"], full_graph_data["edges"])
            
            # 保存临时 TTL 文件
            temp_ttl_path = None
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ttl', delete=False, encoding='utf-8') as f:
                f.write(ttl_content)
                temp_ttl_path = f.name
            
            try:
                # 同步到向量库
                extractor.sync_ttl_to_vector_store(
                    ttl_file_path=temp_ttl_path,
                    project_id=project_id,
                    domain=domain_name,
                )
                logger.info(f"[extract-instances-from-documents] 向量入库成功（同步模式）：project_id={project_id}, domain={domain_name}")
            except Exception as e:
                logger.error(f"[extract-instances-from-documents] 向量入库失败（同步模式）：{e}")
            finally:
                # 清理临时文件
                if temp_ttl_path and os.path.exists(temp_ttl_path):
                    os.remove(temp_ttl_path)
        except Exception as e:
            logger.error(f"[extract-instances-from-documents] 向量入库异常（同步模式）：{e}")

        return {
            "instances": inst_result["instances"],
            "graph_data": full_graph_data,
            "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
            "metadata": inst_result.get("metadata"),
            "message": (
                f"实例提取完成：{len(inst_result['instances'])} 个实例。"
                f"{'⚠️ ' + str(inst_result.get('discarded_edges_count', 0)) + ' 条不合规连线已自动丢弃。' if inst_result.get('discarded_edges_count', 0) > 0 else ''}"
                f"请在画布中微调后点击「保存草稿」。"
            ),
        }


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
    request_body: str = Form(..., description="JSON 格式的请求体，包含 text_content, schema_graph 等"),
    async_mode: bool = Form(False, description="是否异步执行（支持取消）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API 2 - 强约束实例提取】
    接收用户审核后的 Schema + 原始文本 → 提取 NamedIndividual。
    - 模型只能实例化 schema_graph.classes 中已定义的类；
    - 连线必须符合 domain → range 约束，否则后端自动丢弃；
    - 所有实例 ID 使用确定性算法生成。
    
    返回包含：
    - instances: 实例列表
    - graph_data: 前端可渲染的 {nodes, edges}
    - task_id: 任务 ID（仅当 async_mode=True 时返回）
    """
    from app.core.logging import logger
    from app.infrastructure.task_manager import task_manager, TaskCancelledError
    import json as json_module

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    try:
        # 解析 JSON 格式的请求体
        try:
            request_data = json_module.loads(request_body)
        except json_module.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON in request_body: {str(e)}")
        
        extractor = _build_extractor(db)

        # schema_graph 来自请求体（用户已审核版本）
        schema_dict = request_data.get("schema_graph", {})
        
        # 构造 documents 列表以传递溯源信息
        text_content = request_data.get("text_content", "")
        source_name = request_data.get("source_name", "") or request_data.get("document_name", "")
        documents_list = None
        if text_content:
            doc_name = source_name if source_name else "手动输入"
            documents_list = [{"text": text_content, "filename": doc_name}]

        if async_mode:
            # 异步模式：创建任务并后台执行
            task_id = task_manager.create_task(message="开始实例提取...")
            task_manager.start_task(task_id, message="开始实例提取...", detail="正在解析文件...")
            
            # 后台执行提取任务
            async def run_extraction():
                async with EXTRACTION_SEMAPHORE:
                    try:
                        def progress_callback(progress: float, message: str):
                            task_manager.update_progress(task_id, progress=progress, message=message)
                        
                        inst_result = await extractor.async_extract_instances_with_constraints(
                                text=text_content,
                                schema_graph=schema_dict,
                                chunk_size=request_data.get("chunk_size", 15000),
                                chunk_overlap=request_data.get("chunk_overlap", 10),
                                request_interval=request_data.get("request_interval", 2),
                                product_code=request_data.get("product_code"),
                                task_id=task_id,
                                progress_callback=progress_callback,
                                documents=documents_list,
                            )

                        schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
                        
                        full_graph_data = OntologyExtractor.merge_instances_to_graph_data(
                            schema_graph_data=schema_graph_data,
                            instances=inst_result["instances"],
                            action_instances=inst_result.get("action_instances", []),
                        )

                        db_project.graph_data = {
                            "schema": schema_dict,
                            **full_graph_data,
                        }
                        db.commit()

                        task_manager.complete_task(
                            task_id,
                            result={
                                "instances": inst_result["instances"],
                                "graph_data": full_graph_data,
                                "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
                                "schema_graph": schema_dict,
                                "text_content": request_data.get("text_content", ""),
                                "metadata": inst_result.get("metadata"),
                            },
                            message=f"实例提取完成：{len(inst_result['instances'])} 个实例" + (
                                f" ({inst_result.get('discarded_edges_count', 0)} 条不合规连线已自动丢弃)" 
                                if inst_result.get("discarded_edges_count", 0) > 0 else ""
                            )
                        )
                    except TaskCancelledError:
                        task_manager.cancel_task(task_id, "用户取消任务")
                    except Exception as e:
                        logger.error(f"[extract-instances] 错误：{e}", exc_info=True)
                        task_manager.fail_task(task_id, str(e), "实例提取失败")
            
            # 启动后台任务
            asyncio.create_task(run_extraction())
            
            return {
                "task_id": task_id,
                "message": "任务已启动，请使用 task_id 查询进度",
            }
        else:
            inst_result = await extractor.async_extract_instances_with_constraints(
                text=text_content,
                schema_graph=schema_dict,
                chunk_size=request_data.get("chunk_size", 15000),
                chunk_overlap=request_data.get("chunk_overlap", 10),
                request_interval=request_data.get("request_interval", 2),
                product_code=request_data.get("product_code"),
                documents=documents_list,
            )

            schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
            
            full_graph_data = OntologyExtractor.merge_instances_to_graph_data(
                schema_graph_data=schema_graph_data,
                instances=inst_result["instances"],
                action_instances=inst_result.get("action_instances", []),
            )

            db_project.graph_data = {
                "schema": schema_dict,
                **full_graph_data,
            }
            db.commit()

            return {
                "instances": inst_result["instances"],
                "graph_data": full_graph_data,
                "discarded_edges_count": inst_result["discarded_edges_count"],
                "metadata": inst_result.get("metadata"),
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
    chunk_overlap: int = 10,
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

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_path = os.path.join(settings.TEMP_DIR, file.filename)
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
            parser = FileParser(vl_enabled=False)
            text_content = parser.parse_file(temp_path) or ""

            extractor = _build_extractor(db, chunk_size, chunk_overlap, request_interval, disable_think=True)

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


@router.post("/{project_id}/parse-files")
async def parse_files(
    project_id: int,
    files: List[UploadFile] = File(..., description="文件列表（支持 PDF/DOC/DOCX/TXT）"),
    save_documents: bool = Form(True, description="是否保存文档记录到数据库"),
    vl_enabled: bool = Form(False, description="是否启用VL视觉模型解析文档图片"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    解析文件获取文本内容（不提取 schema）。
    用于 TTL 导入骨架后，上传文档进行实例提取的场景。
    只解析文件获取文本，不修改任何 schema 数据。
    
    参数:
    - save_documents: 是否保存文档记录到数据库（默认 True）
    """
    from app.core.logging import logger
    from app.services.parser import FileParser
    import shutil

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    ensure_dirs()
    os.makedirs(f"{settings.UPLOAD_PROJECTS_DIR}/{project_id}", exist_ok=True)
    
    temp_paths = []
    saved_docs = []
    
    try:
        for uploaded_file in files:
            import uuid
            unique_filename = f"{uuid.uuid4()}_{uploaded_file.filename}"
            file_path = os.path.join(f"{settings.UPLOAD_PROJECTS_DIR}/{project_id}", unique_filename)
            
            # 保存文件
            with open(file_path, "wb") as buf:
                buf.write(await uploaded_file.read())
            temp_paths.append(file_path)
            
            # 获取文件大小
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            
            # 获取文件类型
            file_ext = uploaded_file.filename.split('.')[-1].lower() if '.' in uploaded_file.filename else ''
            
            logger.info(f"[parse-files] 已保存文件 - {uploaded_file.filename} -> {file_path}")
            
            # 如果要求保存文档记录，创建数据库记录
            if save_documents:
                doc_record = UploadedDocument(
                    project_id=project_id,
                    filename=uploaded_file.filename,
                    file_path=file_path,
                    file_size=file_size,
                    file_type=file_ext,
                )
                db.add(doc_record)
                saved_docs.append(doc_record)
        
        if save_documents and saved_docs:
            db.commit()
            logger.info(f"[parse-files] 已保存 {len(saved_docs)} 个文档记录到数据库")
        
        logger.info(f"[parse-files] 共保存 {len(temp_paths)} 个文件")

        parser = FileParser(vl_enabled=vl_enabled)
        text_contents = []
        for temp_path in temp_paths:
            if vl_enabled:
                text_content = await parser.async_parse_file(temp_path) or ""
            else:
                text_content = parser.parse_file(temp_path) or ""
            text_contents.append(text_content)
            logger.info(f"[parse-files] 文件解析完成 - file={temp_path}, text_length={len(text_content)}")
            
            # 如果保存了文档记录，同时更新文本内容
            if saved_docs:
                for doc in saved_docs:
                    if doc.file_path == temp_path:
                        doc.text_content = text_content
                        break
        
        if saved_docs:
            db.commit()
        
        # 合并所有文件内容
        combined_text = "\n\n".join(text_contents)
        logger.info(f"[parse-files] 合并后总文本长度={len(combined_text)}")

        return {
            "text_content": combined_text,
            "message": f"文件解析完成：{len(files)} 个文件，总文本长度={len(combined_text)} 字符",
            "saved_documents": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "file_size": doc.file_size,
                    "file_type": doc.file_type,
                }
                for doc in saved_docs
            ] if saved_docs else [],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[parse-files] 错误：{e}", exc_info=True)
        if save_documents:
            db.rollback()
        raise HTTPException(status_code=500, detail=f"文件解析失败：{str(e)}")


@router.post("/{project_id}/parse-ttl-schema")
async def parse_ttl_schema(
    project_id: int,
    files: List[UploadFile] = File(..., description="TTL 或 JSON 文件列表"),
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
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_paths = []
    try:
        for uploaded_file in files:
            fname = uploaded_file.filename.lower()
            if not (fname.endswith('.ttl') or fname.endswith('.json')):
                raise HTTPException(status_code=400, detail=f"只支持 TTL/JSON 文件：{uploaded_file.filename}")
            temp_path = os.path.join(settings.TEMP_DIR, uploaded_file.filename)
            with open(temp_path, "wb") as buf:
                buf.write(await uploaded_file.read())
            temp_paths.append(temp_path)
        
        logger.info(f"[parse-ttl-schema] 已保存 {len(temp_paths)} 个文件")

        all_nodes = []
        all_edges = []
        combined_ttl_content = ""
        has_json = any(p.lower().endswith('.json') for p in temp_paths)
        has_ttl = any(p.lower().endswith('.ttl') for p in temp_paths)

        for temp_path in temp_paths:
            if temp_path.lower().endswith('.json'):
                with open(temp_path, "r", encoding='utf-8') as f:
                    json_data = json.load(f)
                nodes, edges = _convert_json_schema_to_graph(json_data)
                all_nodes.extend(nodes)
                all_edges.extend(edges)
            else:
                with open(temp_path, "r", encoding='utf-8') as ttl_file:
                    ttl_content = ttl_file.read()
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
        
        class_count = len([n for n in unique_nodes if n['data'].get('type') == 'owl:Class'])

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
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    is_json = file.filename.lower().endswith('.json')
    if not file.filename.lower().endswith('.ttl') and not is_json:
        raise HTTPException(status_code=400, detail="Only TTL and JSON files are accepted")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    temp_path = os.path.join(settings.TEMP_DIR, file.filename)
    with open(temp_path, "wb") as buf:
        buf.write(await file.read())

    try:
        if is_json:
            with open(temp_path, "r", encoding='utf-8') as json_file:
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
            with open(temp_path, "r", encoding='utf-8') as ttl_file:
                ttl_content = ttl_file.read()

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
    """
    下载 TTL 文件。
    文件名格式：ontology_[项目名称].ttl
    直接使用中文项目名称（URL 编码）。
    """
    from app.core.logging import logger
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not db_project.is_published and db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to download this project's TTL file")

    # 始终基于最新 graph_data 重新生成，保证下载内容是最新快照
    latest_ttl_content = ""
    
    # 优先从 graph_data 生成
    if db_project.graph_data:
        nodes = db_project.graph_data.get("nodes", [])
        edges = db_project.graph_data.get("edges", [])
        if nodes or edges:
            try:
                latest_ttl_content = generate_ttl_from_graph_data(nodes, edges)
            except Exception as e:
                logger.error(f"generate_ttl_from_graph_data 失败：{e}")
    
    # 如果 graph_data 为空或生成失败，使用 ttl_content
    if not latest_ttl_content:
        latest_ttl_content = db_project.ttl_content or ""

    if not latest_ttl_content:
        raise HTTPException(status_code=404, detail="TTL file not found for this project")

    # 生成文件名：ontology_[项目名称].ttl
    project_name = db_project.name
    filename = f"ontology_{project_name}.ttl"
    
    logger.info(f"[download-ttl] 项目名称：{project_name}, 文件名：{filename}")
    
    # 使用 quote 进行 URL 编码，确保中文文件名正确传递
    # safe='' 表示所有特殊字符都要编码
    from urllib.parse import quote
    encoded_filename = quote(filename, safe='')
    
    logger.info(f"[download-ttl] 编码后文件名：{encoded_filename}")
    
    # 直接返回内容，不创建临时文件
    # 注意：Starlette 的 Response 默认使用 latin-1 编码 headers
    # 所以 Content-Disposition 必须只包含 ASCII 字符
    # filename* 使用 RFC 5987 格式，已经是 URL 编码的 ASCII 字符串
    # filename 参数使用 ASCII 兼容的替代名称
    
    # 创建一个 ASCII 兼容的 filename（用于不支持 filename* 的浏览器）
    ascii_filename = f"ontology_project_{project_id}.ttl"
    
    response = Response(
        content=latest_ttl_content.encode('utf-8'),
        media_type="text/turtle; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}; filename=\"{ascii_filename}\"",
            "Content-Type": "text/turtle; charset=utf-8",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
    
    logger.info(f"[download-ttl] Content-Disposition: attachment; filename*=UTF-8''{encoded_filename}; filename=\"{ascii_filename}\"")
    
    return response


def _convert_json_schema_to_graph(json_data: dict) -> tuple:
    """
    将平台导出的 JSON 格式（entities + relationships）转换为 graph nodes + edges。
    JSON 格式与 download-json 导出的格式一致。
    支持 entity_type 为"动作类型"的实体，自动设置 AT_ 前缀的 raw_id。

    ★ 实例处理：实例（owl:NamedIndividual）创建为独立节点，
    通过 relationships 中的 type 关系找到所属类/动作类型，
    通过 action 关系找到动作实例的目标类。
    前端通过 expandedNodeIds 控制实例的显示/隐藏。
    """
    import hashlib
    import math

    entities = json_data.get("entities", [])
    relationships = json_data.get("relationships", [])

    # 先处理 schema 实体（类 + 动作类型），再处理实例
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

    # 1. 创建 schema 节点（类 + 动作类型）
    for idx, ent in enumerate(schema_entities):
        name = ent.get("name", "")
        if not name:
            continue
        node_type = ent.get("type", "owl:Class")
        entity_type = ent.get("entity_type", "")
        props = ent.get("properties", {})
        desc = ent.get("description", "")

        is_action = entity_type == "动作类型" or node_type == "owl:ActionType"

        if is_action:
            raw_id = f"AT_{hashlib.md5(name.encode()).hexdigest()[:8]}"
            node_id = raw_id
        else:
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
        if raw_id:
            node_data["raw_id"] = raw_id
        if is_action:
            node_data["parameters"] = ent.get("parameters", [])

        node = {
            "id": node_id,
            "type": "custom",
            "position": {"x": pos_x, "y": pos_y},
            "data": node_data,
        }
        nodes.append(node)
        node_id_map[name] = node_id

    # 2. 创建实例节点（owl:NamedIndividual）
    # 通过 relationships 中的 type 关系找到实例所属的类/动作类型
    instance_name_to_id = {}  # 实例 name -> instance node_id
    instance_counter = 0

    # 先收集哪些实例是动作实例（type 关系指向动作类型）
    action_instance_names = set()
    for rel in relationships:
        if rel.get("relation") == "type":
            source_name = rel.get("source_name", "")
            target_name = rel.get("target_name", "")
            # 如果 target 是动作类型节点，则 source 是动作实例
            if target_name in node_id_map and node_id_map[target_name].startswith('AT_'):
                action_instance_names.add(source_name)

    for ent in instance_entities:
        name = ent.get("name", "")
        if not name:
            continue

        instance_counter += 1
        instance_id = f"inst_{hashlib.md5(name.encode()).hexdigest()[:12]}_{instance_counter}"
        props = ent.get("properties", {})
        desc = ent.get("description", "")
        target_obj_type = ent.get("target_object_type", "")

        is_action_instance = name in action_instance_names

        node_data = {
            "label": name,
            "type": "owl:NamedIndividual",
            "properties": props,
        }
        if desc:
            node_data["description"] = desc
        if target_obj_type:
            node_data["target_object_type"] = target_obj_type
        if is_action_instance:
            node_data["_is_action_instance"] = True

        node = {
            "id": instance_id,
            "type": "custom",
            "position": {"x": 0, "y": 0},  # 实例位置由前端布局
            "data": node_data,
        }
        nodes.append(node)
        instance_name_to_id[name] = instance_id

    # 3. 处理 relationships 中的边
    # 先处理 schema 实体之间的 action 边（target_object_type）
    existing_action_edges = set()
    for rel in relationships:
        if rel.get("relation") == "action":
            source_name = rel.get("source_name", "")
            target_name = rel.get("target_name", "")
            existing_action_edges.add((source_name, target_name))

    # 为有 target_object_type 的动作类型实体生成 action 边（仅当 relationships 中不存在时）
    for ent in schema_entities:
        name = ent.get("name", "")
        if not name:
            continue
        target_obj_type = ent.get("target_object_type", "")
        if not target_obj_type:
            continue
        if (name, target_obj_type) in existing_action_edges:
            continue
        source_id = node_id_map.get(name)
        target_id = node_id_map.get(target_obj_type)
        if not source_id or not target_id:
            continue
        edge = {
            "id": f"e_{source_id}_{target_id}_action_{name}",
            "source": source_id,
            "target": target_id,
            "type": "smoothstep",
            "data": {
                "label": name,
                "relation": "action",
                "properties": {},
            },
        }
        edges.append(edge)

    # 处理所有 relationships
    # ★ 关键：当实例名和类名相同时，需要根据 relation 类型判断 source/target 是实例还是类
    # - type 关系：source 一定是实例，target 一定是类/动作类型
    # - action 关系：如果 source 在 instance_name_to_id 中，则是动作实例
    # - 其他关系：source/target 优先查类，如果不存在则查实例
    for rel in relationships:
        source_name = rel.get("source_name", "")
        target_name = rel.get("target_name", "")
        relation = rel.get("relation", "")
        label = rel.get("label", relation or "相关")

        if relation == "type":
            # type 关系：source 是实例，target 是类/动作类型
            source_id = instance_name_to_id.get(source_name)
            target_id = node_id_map.get(target_name)
        elif relation == "action":
            # action 关系：需要判断 source 是动作类型还是动作实例
            # 如果 source_name 同时是动作类型名和实例名，优先使用动作类型（schema）
            if source_name in node_id_map:
                source_id = node_id_map.get(source_name)
            elif source_name in instance_name_to_id:
                source_id = instance_name_to_id.get(source_name)
            else:
                source_id = None
            # target 可能是类，也可能是实例（如动作实例指向目标实例）
            target_id = node_id_map.get(target_name) or instance_name_to_id.get(target_name)
        else:
            # 其他关系：优先查类，不存在则查实例
            source_id = node_id_map.get(source_name) or instance_name_to_id.get(source_name)
            target_id = node_id_map.get(target_name) or instance_name_to_id.get(target_name)

        if not source_id or not target_id:
            continue

        # type 关系：实例 -> 类/动作类型，转为 instance_of 边
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


def _build_schema_from_json_data(nodes: List[dict], edges: List[dict]) -> dict:
    """
    从 graph nodes 和 edges 构建 schema 字典（兼容 TTL 导入场景）。
    用于纯 JSON 上传时生成 schema_graph 数据结构。
    支持 AT_ 前缀的动作类识别，从 action 边提取 target_object_type。
    """
    classes = []
    object_properties = []
    action_types = []

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
        raw_id = data.get("raw_id", "")
        is_action = raw_id.startswith('AT_') or node.get("id", "").startswith('AT_')
        class_info[node["id"]] = {
            "id": node["id"],
            "label": label,
            "type": node_type,
            "description": data.get("description", ""),
            "is_action": is_action,
            "parameters": data.get("parameters", []),
            "properties": data.get("properties", {}),
        }

    for node_id, info in class_info.items():
        if info["is_action"]:
            # 从 action 边中提取 target_object_type
            target_object_type = ""
            for edge in edges:
                edge_data = edge.get("data", {})
                if edge_data.get("relation") == "action" and edge.get("source") == node_id:
                    target_id = edge.get("target", "")
                    target_object_type = node_id_to_label.get(target_id, target_id)
                    break

            action_types.append({
                "id": info["id"],
                "name": info["label"],
                "label": info["label"],
                "description": info.get("description", ""),
                "target_object_type": target_object_type,
                "parameters": info.get("parameters", []),
            })
        else:
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

        # 跳过 action 边（已在 action_types 中处理）和内部边
        if relation == "action":
            continue
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
        "action_types": action_types,
    }


@router.get("/{project_id}/download-json")
def download_json(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    下载 JSON 文件（ES 注入格式）。
    与 inject_to_ragflow 使用相同的 _convert_graph_data 逻辑，
    确保导出的 JSON 与注入到 ES 的数据格式完全一致。
    """
    from app.core.logging import logger

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not db_project.is_published and db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to download this project's JSON file")

    graph_data = db_project.graph_data
    if not graph_data or not isinstance(graph_data, dict):
        raise HTTPException(status_code=404, detail="No graph data found for this project")

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    if not nodes and not edges:
        raise HTTPException(status_code=404, detail="Graph data is empty")

    from app.services.inject_service import GraphInjectService, _friendly_type, _build_entity_description, _build_relation_description

    entities = []
    relationships = []
    node_id_to_label: Dict[str, str] = {}

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
        raw_id = data.get("raw_id", "")
        node_id = node.get("id", "")
        is_action = raw_id.startswith('AT_') or node_id.startswith('AT_')

        if is_action and node_type == "owl:Class":
            node_type = "owl:ActionType"

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
        if is_action and data.get("parameters"):
            entity_data["parameters"] = data["parameters"]
        # ActionType节点：从action边中提取target_object_type
        if node_type == "owl:ActionType" or is_action:
            for edge in edges:
                edge_data = edge.get("data", {})
                if edge_data.get("relation") == "action" and edge.get("source") == node_id:
                    target_id = edge.get("target", "")
                    target_label = node_id_to_label.get(target_id, target_id)
                    if target_label:
                        entity_data["target_object_type"] = target_label
                    break
        # 实例节点：从 data.target_object_type 提取（动作实例）
        if node_type == "owl:NamedIndividual" and data.get("target_object_type"):
            entity_data["target_object_type"] = data["target_object_type"]

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

        relationships.append({
            "source_name": source_name,
            "target_name": target_name,
            "relation": export_relation,
            "label": export_label,
            "description": rel_desc,
            "properties": data.get("properties", {}),
        })

    json_data = {
        "entities": entities,
        "relationships": relationships,
    }

    project_name = db_project.name
    filename = f"ontology_{project_name}.json"

    from urllib.parse import quote
    encoded_filename = quote(filename, safe='')
    ascii_filename = f"ontology_project_{project_id}.json"

    json_content = json.dumps(json_data, ensure_ascii=False, indent=2)

    response = Response(
        content=json_content.encode('utf-8'),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}; filename=\"{ascii_filename}\"",
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )

    return response


# ─────────────────────────────────────────────
#  内部辅助函数
# ─────────────────────────────────────────────

def build_schema_from_graph_data(nodes: List[dict], edges: List[dict]) -> dict:
    """
    从 nodes 和 edges 动态构建 schema（兼容 TTL 导入场景）。
    当 TTL 文件导入骨架时，graph_data 中可能没有 schema 字段，
    但有 nodes 和 edges，本函数从这些数据中提取 schema。
    
    ★ 属性继承：子类自动继承父类的所有属性，无需重复定义。
    ★ Action Types：从 raw_id 以 AT_ 开头的 owl:Class 节点中提取动作类型。
    """
    from app.core.logging import logger
    
    classes = []
    object_properties = []
    action_types = []
    
    logger.info(f"[build_schema_from_graph_data] 开始从 {len(nodes)} 个节点和 {len(edges)} 个边构建 schema")
    
    class_info = {}
    action_type_info = {}
    node_id_to_label = {}
    
    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if node_type == 'owl:Class':
            node_id = str(node['id'])
            node_label = node.get('data', {}).get('label', node_id)
            raw_id = node.get('data', {}).get('raw_id', '')
            node_id_to_label[node_id] = node_label
            if raw_id:
                node_id_to_label[raw_id] = node_label
    
    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if node_type == 'owl:Class':
            node_id = str(node['id'])
            node_label = node.get('data', {}).get('label', node_id)
            raw_id = node.get('data', {}).get('raw_id', '')
            
            is_action_type = raw_id.startswith('AT_') or node_id.startswith('AT_')
            
            if is_action_type:
                target_object_type = ""
                for edge in edges:
                    edge_data = edge.get('data', {})
                    edge_relation = edge_data.get('relation', '')
                    if edge_relation == 'action' and edge.get('source') == node_id:
                        target_node_id = edge.get('target', '')
                        target_object_type = node_id_to_label.get(target_node_id, target_node_id)
                        break
                
                parameters = node.get('data', {}).get('parameters', [])
                if not parameters:
                    prop_defs = node.get('data', {}).get('property_definitions', [])
                    parameters = [{"name": p.get("name", ""), "data_type": p.get("data_type", "string")} for p in prop_defs if isinstance(p, dict)]
                
                action_type_info[node_id] = {
                    'label': node_label,
                    'name': node_label,
                    'target_object_type': target_object_type,
                    'parameters': parameters,
                    'description': node.get('data', {}).get('description', ''),
                }
                logger.info(f"[build_schema_from_graph_data] 发现 ActionType: node_id={node_id}, label={node_label}, raw_id={raw_id}, target_object_type={target_object_type}")
                continue
            
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
            "data_properties": all_properties,
            "direct_properties": direct_props,
            "inherited_properties": inherited_props,
            "properties_with_source": properties_with_source,
        })
    
    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if node_type == 'owl:Class':
            node_id = str(node['id'])
            raw_id = node.get('data', {}).get('raw_id', '')
            is_action_type = raw_id.startswith('AT_') or node_id.startswith('AT_')
            if is_action_type:
                continue
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
        
        if relation == 'action':
            continue
        
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
    
    for at_id, at_info in action_type_info.items():
        action_types.append({
            "id": at_id,
            "name": at_info['name'],
            "label": at_info['label'],
            "description": at_info['description'],
            "target_object_type": at_info['target_object_type'],
            "parameters": at_info['parameters'],
        })
        logger.info(f"[build_schema_from_graph_data] 添加 ActionType: {at_id}, label={at_info['label']}, target={at_info['target_object_type']}")
    
    logger.info(f"[build_schema_from_graph_data] 构建完成：{len(classes)} 个类，{len(object_properties)} 个 ObjectProperty，{len(action_types)} 个 ActionType")
    
    return {
        "classes": classes,
        "object_properties": object_properties,
        "action_types": action_types,
    }


def _build_extractor(
    db: Session,
    chunk_size: int = 15000,
    chunk_overlap: int = 10,
    request_interval: int = 2,
    disable_think: bool = True,
) -> OntologyExtractor:
    db_config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
    llm_config = db_config.value if db_config else {}

    api_key = llm_config.get("api_key") or settings.VLLM_API_KEY
    base_url = llm_config.get("base_url") or settings.VLLM_BASE_URL
    model = llm_config.get("model") or settings.VLLM_MODEL

    extractor = OntologyExtractor(api_key=api_key, base_url=base_url, model=model)
    if disable_think:
        extractor.llm_client.think_mode = "disabled"
    else:
        extractor.llm_client.think_mode = "enabled"
    return extractor


def generate_ttl_from_graph_data(nodes: List[dict], edges: List[dict]) -> str:
    """
    全生命周期 TTL 同步：将前端 nodes+edges 反向序列化为标准 OWL TTL。
    使用 rdflib 保证 RDF 语义正确性。
    支持 Action Type：导出为 owl:Class + ex:isActionType "true"^^xsd:boolean 标注，
    动作参数导出为 DatatypeProperty 并标注 ex:isActionParameter。
    """
    from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace, URIRef, XSD
    import hashlib

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
        elif node_id.startswith('AT_'):
            prefix = "AT"
        elif node_id.startswith('I_') or node_id.startswith('action_I_'):
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

    node_uris: Dict[str, URIRef] = {}
    for node in nodes:
        node_id = str(node['id'])
        node_label = node['data'].get('label', node_id)
        node_type = node['data'].get('type', 'owl:Class')
        raw_id = node['data'].get('raw_id', '')
        is_action_type = (node_type == 'owl:Class') and (raw_id.startswith('AT_') or node_id.startswith('AT_'))
        is_action_instance = node['data'].get('_is_action_instance', False)
        uri = make_uri(node_id)
        node_uris[node_id] = uri

        if node_type == 'owl:Class':
            g.add((uri, RDF.type, OWL.Class))
            if is_action_type:
                g.add((uri, ex["isActionType"], Literal("true", datatype=XSD.boolean)))
                desc = node['data'].get('description', '')
                if desc:
                    g.add((uri, RDFS.comment, Literal(desc, lang="zh")))
        elif node_type == 'owl:NamedIndividual':
            g.add((uri, RDF.type, OWL.NamedIndividual))
            if is_action_instance:
                g.add((uri, ex["isActionInstance"], Literal("true", datatype=XSD.boolean)))
        else:
            class_uri = make_uri(node_type)
            g.add((uri, RDF.type, class_uri))
            g.add((class_uri, RDF.type, OWL.Class))

        g.add((uri, RDFS.label, Literal(node_label, lang="zh")))

        if is_action_type and node['data'].get('parameters'):
            for param in node['data']['parameters']:
                if isinstance(param, dict) and param.get('name'):
                    param_id = f"action_param_{hashlib.md5(param['name'].encode('utf-8')).hexdigest()[:8]}"
                    param_uri = ex[param_id]
                    g.add((param_uri, RDF.type, OWL.DatatypeProperty))
                    g.add((param_uri, RDFS.label, Literal(param['name'], lang="zh")))
                    g.add((param_uri, RDFS.domain, uri))
                    g.add((param_uri, ex["isActionParameter"], Literal("true", datatype=XSD.boolean)))
                    if param.get('data_type'):
                        g.add((param_uri, RDFS.comment, Literal(f"参数类型: {param['data_type']}", lang="zh")))

        for prop_name, prop_value in node['data'].get('properties', {}).items():
            if prop_name.startswith('_source_'):
                continue
            dataprop_id = generate_safe_prop_id(prop_name)
            if not dataprop_id:
                continue
            
            dataprop_uri = ex[dataprop_id]
            g.add((dataprop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((dataprop_uri, RDFS.label, Literal(prop_name, lang="zh")))
            
            if node_type == 'owl:Class':
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
                if relation == 'action':
                    g.add((objprop_uri, ex["isActionProperty"], Literal("true", datatype=XSD.boolean)))
                g.add((source_uri, objprop_uri, target_uri))

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
    from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef
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
        
        # 获取父类
        parent_classes = []
        for parent in g.objects(cls, RDFS.subClassOf):
            parent_id = str(parent).split('#')[-1] if '#' in str(parent) else str(parent).split('/')[-1]
            parent_classes.append(parent_id)
        
        # 获取数据属性 - 通过 rdfs:domain 关联
        direct_properties = []
        for prop_uri, prop_info in datatype_prop_domains.items():
            if cls_id in prop_info['domains']:
                direct_properties.append(prop_info['label'])
        
        class_info[cls_id] = {
            'label': label,
            'parent_classes': parent_classes,
            'direct_properties': list(set(direct_properties)),
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
        object_properties.append(prop_def)
        object_property_defs[prop_id] = prop_def
        
        # 同时通过 label 建立索引，方便后续查找
        object_property_defs[label] = prop_def
    
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
    from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef, Literal
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
        raw_id = ''
        is_action_type = False
        is_action_instance = False
        parameters = []
        description = ''

        for obj in g.objects(uri, ex["isActionType"]):
            if str(obj).lower() == 'true':
                is_action_type = True
                raw_id = f"AT_{node_id}" if not node_id.startswith('AT_') else node_id
                break

        for obj in g.objects(uri, ex["isActionInstance"]):
            if str(obj).lower() == 'true':
                is_action_instance = True
                break

        for obj in g.objects(uri, RDFS.comment):
            desc_val = str(obj)
            if desc_val and not desc_val.startswith('参数类型:'):
                description = desc_val
            break
        
        for pred, obj in g.predicate_objects(uri):
            pred_uri = str(pred)
            
            if pred_uri in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                            str(RDFS.domain), str(RDFS.range),
                            str(ex["isActionType"]), str(ex["isActionInstance"])]:
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

        if is_action_type:
            for prop_uri, prop_info in datatype_props.items():
                is_action_param = False
                for _ in g.objects(URIRef(prop_uri), ex["isActionParameter"]):
                    is_action_param = True
                    break
                if is_action_param and prop_info['domain'] == node_id:
                    param_comment = ''
                    for c in g.objects(URIRef(prop_uri), RDFS.comment):
                        param_comment = str(c)
                        break
                    param_data_type = ''
                    if param_comment.startswith('参数类型: '):
                        param_data_type = param_comment[len('参数类型: '):]
                    parameters.append({
                        'name': prop_info['label'],
                        'data_type': param_data_type,
                    })
                    continue
                if prop_info['domain'] == node_id and prop_info['label'] not in props:
                    props[prop_info['label']] = ""
        elif node_type_category == "owl:Class":
            for prop_uri, prop_info in datatype_props.items():
                domain_id = prop_info['domain']
                if domain_id == node_id and prop_info['label'] not in props:
                    props[prop_info['label']] = ""

        node_data = {
            "label": label,
            "type": node_type_category,
            "properties": props,
        }
        if is_action_type:
            node_data["raw_id"] = raw_id
            node_data["description"] = description
            node_data["parameters"] = parameters
        if is_action_instance:
            node_data["_is_action_instance"] = True
            node_data["raw_id"] = raw_id

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

        is_action_prop = False
        for _ in g.objects(prop, ex["isActionProperty"]):
            is_action_prop = True
            break

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
                    "relation": "action" if is_action_prop else prop_label,
                    "prop_id": prop_id,
                },
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
                            edges.append({
                                "id": edge_id,
                                "source": cls_id,
                                "target": obj_id,
                                "label": rel_label,
                                "type": "custom",
                                "data": {
                                    "label": rel_label,
                                    "relation": rel_label,
                                    "prop_id": pred_id,
                                },
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

@router.get("/{project_id}/task/{task_id}/progress")
async def get_task_progress(
    project_id: int,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取任务进度（轮询方式）
    """
    from app.infrastructure.task_manager import task_manager
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project's tasks")
    
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task.to_dict()


@router.get("/{project_id}/task/{task_id}/progress-stream")
async def stream_task_progress(
    project_id: int,
    task_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    流式推送任务进度（Server-Sent Events）
    前端使用 EventSource 连接
    
    支持两种认证方式：
    1. Cookie/Session 认证（通过 Depends(get_current_user)）
    2. URL 参数 token 认证（用于 EventSource，因为 EventSource 不支持自定义 headers）
    """
    from app.infrastructure.task_manager import task_manager
    from app.core.logging import logger
    
    logger.info(f"[progress-stream] 收到请求 - project_id={project_id}, task_id={task_id}")
    
    # 尝试从 URL 参数获取 token 进行认证（EventSource 方式）
    token = request.query_params.get("token")
    
    current_user = None
    if token:
        # 从 token 解析用户
        from app.api.auth import verify_token
        try:
            current_user = verify_token(token, db=db)
            logger.info(f"[progress-stream] 认证成功 - user={current_user.username}")
        except HTTPException as e:
            logger.warning(f"SSE 认证失败：{e.detail}")
            raise e
        except Exception as e:
            logger.warning(f"SSE 认证失败：{e}")
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    else:
        # 尝试从 Cookie/Session 获取用户（传统方式）
        try:
            # 对于 SSE，我们允许无认证访问（仅用于开发环境）
            # 生产环境应该要求认证
            raise HTTPException(status_code=401, detail="Authentication required. Please provide a token.")
        except HTTPException:
            raise
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        logger.warning(f"[progress-stream] 项目不存在 - {project_id}")
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        logger.warning(f"[progress-stream] 用户无权访问 - project={project_id}, user={current_user.id}")
        raise HTTPException(status_code=403, detail="No permission to access this project's tasks")
    
    task = task_manager.get_task(task_id)
    logger.info(f"[progress-stream] 任务查询结果 - task={task}")
    if not task:
        logger.warning(f"[progress-stream] 任务不存在 - {task_id}")
        raise HTTPException(status_code=404, detail="Task not found")
    
    async def event_generator():
        """生成 SSE 事件流"""
        try:
            while True:
                task = task_manager.get_task(task_id)
                if not task:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Task not found"})
                    }
                    break
                
                yield {
                    "event": "progress",
                    "data": json.dumps(task.to_dict())
                }
                
                # 如果任务已完成/失败/取消，发送最终状态并断开
                if task.status.value in ("completed", "failed", "cancelled"):
                    yield {
                        "event": task.status.value,
                        "data": json.dumps(task.to_dict())
                    }
                    break
                
                # 等待 500ms 后再次推送
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info(f"SSE 连接被取消：{task_id}")
        except Exception as e:
            logger.error(f"SSE 错误：{e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }
    
    return EventSourceResponse(event_generator())


@router.post("/{project_id}/task/{task_id}/cancel")
async def cancel_task(
    project_id: int,
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    取消任务
    支持两种认证方式：
    1. Bearer Token (Authorization header)
    2. URL 参数 token (用于不支持 headers 的场景)
    """
    from app.infrastructure.task_manager import task_manager
    from app.core.logging import logger
    from app.api.auth import verify_token
    
    try:
        # 尝试从 URL 参数获取 token（主要方式）
        token = request.query_params.get("token")
        
        # 如果没有 URL 参数，尝试从 Authorization header 获取
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:]
        
        if not token:
            logger.warning("取消任务：未提供 token")
            raise HTTPException(status_code=401, detail="Authentication required")
        
        # 验证 token 获取用户
        current_user = verify_token(token, db=db)
        logger.info(f"取消任务：用户认证成功 - {current_user.username}")
        
        # 验证项目权限
        db_project = db.query(Project).filter(Project.id == project_id).first()
        if not db_project:
            logger.warning(f"取消任务：项目不存在 - {project_id}")
            raise HTTPException(status_code=404, detail="Project not found")
        if db_project.owner_id != current_user.id:
            logger.warning(f"取消任务：用户无权取消 - project={project_id}, user={current_user.id}")
            raise HTTPException(status_code=403, detail="No permission to cancel this project's tasks")
        
        # 获取任务
        task = task_manager.get_task(task_id)
        if not task:
            logger.warning(f"取消任务：任务不存在 - {task_id}")
            raise HTTPException(status_code=404, detail="Task not found")
        
        logger.info(f"取消任务：{task_id}, 当前状态：{task.status.value}")
        
        # 检查任务是否已经完成或已取消
        if task.status.value in ("completed", "failed", "cancelled"):
            logger.info(f"取消任务：任务已在终端状态 - {task.status.value}")
            return {
                "message": f"Task already in terminal state: {task.status.value}",
                "task_id": task_id,
                "status": task.status.value
            }
        
        # 执行取消
        success = task_manager.cancel_task(task_id, "用户取消任务")
        logger.info(f"取消任务结果：success={success}")
        
        if success:
            return {"message": "Task cancelled successfully", "task_id": task_id, "status": "cancelled"}
        else:
            return {"message": "Failed to cancel task", "task_id": task_id}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消任务异常：{e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"取消任务失败：{str(e)}")


@router.get("/{project_id}/tasks")
async def get_project_tasks(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取项目所有任务列表
    """
    from app.infrastructure.task_manager import task_manager
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project's tasks")
    
    all_tasks = task_manager.get_all_tasks()
    
    # 返回所有任务（前端可以过滤）
    return {
        "tasks": {task_id: task.to_dict() for task_id, task in all_tasks.items()}
    }


# ─────────────────────────────────────────────
#  ★ 文档管理 API
# ─────────────────────────────────────────────

@router.get("/{project_id}/documents")
async def get_project_documents(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取项目已上传的文档列表
    """
    from app.core.logging import logger
    import pytz
    from datetime import timezone
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project's documents")
    
    documents = db.query(UploadedDocument).filter(
        UploadedDocument.project_id == project_id
    ).order_by(UploadedDocument.created_at.desc()).all()
    
    # 获取时区
    tz_utc = pytz.UTC
    tz_china = pytz.timezone('Asia/Shanghai')
    
    return {
        "documents": [
            {
                "id": doc.id,
                "filename": doc.filename,
                "file_size": doc.file_size,
                "file_type": doc.file_type,
                "created_at": tz_utc.localize(doc.created_at).astimezone(tz_china).isoformat() if doc.created_at else None,
                "updated_at": tz_utc.localize(doc.updated_at).astimezone(tz_china).isoformat() if doc.updated_at else None,
            }
            for doc in documents
        ]
    }


@router.delete("/{project_id}/documents/{doc_id}")
async def delete_project_document(
    project_id: int,
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    删除项目已上传的文档
    """
    from app.core.logging import logger
    import os
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to delete this project's documents")
    
    document = db.query(UploadedDocument).filter(
        UploadedDocument.id == doc_id,
        UploadedDocument.project_id == project_id
    ).first()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # 删除物理文件（如果存在）
    if document.file_path and os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
            logger.info(f"[delete-document] 已删除物理文件：{document.file_path}")
        except Exception as e:
            logger.warning(f"[delete-document] 删除物理文件失败：{e}")
    
    # 删除数据库记录
    db.delete(document)
    db.commit()
    
    logger.info(f"[delete-document] 已删除文档记录：{doc_id}, filename={document.filename}")
    
    return {
        "message": f"文档 '{document.filename}' 已删除",
        "doc_id": doc_id
    }


@router.post("/{project_id}/documents/clear-all")
async def clear_all_documents(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    清空项目所有已上传的文档
    """
    from app.core.logging import logger
    import os
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to delete this project's documents")
    
    documents = db.query(UploadedDocument).filter(
        UploadedDocument.project_id == project_id
    ).all()
    
    deleted_count = 0
    for doc in documents:
        # 删除物理文件
        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except Exception as e:
                logger.warning(f"[clear-documents] 删除物理文件失败：{doc.file_path}, {e}")
        
        db.delete(doc)
        deleted_count += 1
    
    db.commit()
    
    logger.info(f"[clear-documents] 已清空 {deleted_count} 个文档")
    
    return {
        "message": f"已清空 {deleted_count} 个文档",
        "deleted_count": deleted_count
    }


# ─────────────────────────────────────────────
#  ★ GraphRAG 问答接口（双路召回）
# ─────────────────────────────────────────────

@router.post("/{project_id}/qa")
async def qa_endpoint(
    project_id: int,
    question: str = Form(..., description="用户问题"),
    selected_domains: Optional[str] = Form(None, description="选中的知识域列表，逗号分隔"),
    top_k: int = Form(5, description="召回的 Top-K 结果数量"),
    use_dual_path: str = Form("true", description="是否使用双路召回（向量 + 图）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    【GraphRAG 问答接口 - 双路召回】
    基于 Neo4j 图检索 + Milvus 向量检索 + LLM 生成回答，支持知识域过滤和溯源。
    
    双路召回：
    - 路径 A：Neo4j 图检索 - 获取精确的结构化关系（如产品编号→产品类型）
    - 路径 B：Milvus 向量检索 - 获取丰富的文本切片
    
    返回格式：
    {
        "answer": "回答内容 [1][2]...",
        "references": [
            {"id": 1, "file": "filename.pdf", "quote": "原文引用...", "type": "graph_node"|"graph_edge"|"vector_chunk"},
            ...
        ],
        "debug_info": {
            "graph_facts_count": 3,
            "vector_results_count": 5,
        }
    }
    """
    from app.core.logging import logger
    from app.services.rag_engine import DualPathRAGEngine
    from app.infrastructure.vector_client import VectorStoreManager
    from app.infrastructure.llm_client import LLMClient
    from app.infrastructure.database import SystemConfig
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id and not db_project.is_published:
        raise HTTPException(status_code=403, detail="No permission to access this project")
    
    # 获取 LLM 配置
    db_config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
    llm_config = db_config.value if db_config else {}
    
    logger.info(f"[QA] 数据库 LLM 配置：{llm_config}")
    
    api_key = llm_config.get("api_key") or settings.VLLM_API_KEY
    base_url = llm_config.get("base_url") or settings.VLLM_BASE_URL
    model = llm_config.get("model") or settings.VLLM_MODEL
    
    logger.info(f"[QA] 最终 LLM 配置：api_key={api_key[:10] if api_key else 'None'}..., base_url={base_url}, model={model}")
    
    # 解析知识域列表
    domains = None
    if selected_domains and selected_domains.strip():
        domains = [d.strip() for d in selected_domains.split(',') if d.strip()]
    
    # ★ 关键修复：正确解析布尔值参数
    # FastAPI 的 Form 参数无法正确将字符串 "true" 转换为布尔值 True
    use_dual_path_bool = use_dual_path.lower() == "true" if isinstance(use_dual_path, str) else bool(use_dual_path)
    
    logger.info(f"[QA] use_dual_path 参数原始值={use_dual_path}, 解析后={use_dual_path_bool}")
    
    # ★ 双路召回模式：使用 DualPathRAGEngine
    if use_dual_path_bool:
        logger.info("=" * 80)
        logger.info("[QA] 使用双路召回模式（Neo4j + Milvus）")
        
        # ★ 关键修复：传递 LLM 配置给引擎
        engine = DualPathRAGEngine(api_key=api_key, base_url=base_url, model=model)
        logger.info(f"[QA] DualPathRAGEngine 已初始化：model={model}, base_url={base_url}")
        
        # ★ 关键修复：不传入 schema，让 RAG 引擎从 Neo4j 实时获取
        # 原因：SQLite 中的 graph_data.schema 可能为空或过期，而 Neo4j 中存储的是最新数据
        # RAG 引擎的 query 方法会在 schema=None 时自动调用 neo4j_client.get_project_schema()
        schema = None
        
        try:
            result = engine.query(
                question=question,
                project_id=project_id,
                domains=domains,
                top_k=top_k,
                use_text2cypher=True,
                schema=schema,  # ★ 设置为 None，让 RAG 引擎从 Neo4j 获取
                db_session=db,  # ★ 传递 db_session 用于从 SQLite 获取 schema（作为 fallback）
            )
            
            logger.info(f"[QA] 双路召回成功：{len(result.get('references', []))} 条引用")
            return result
            
        except Exception as e:
            logger.error(f"[QA] 双路召回失败：{e}", exc_info=True)
            # Fallback 到单一向量检索
    
    # ★ 单一向量检索模式（Fallback 或 用户选择）
    logger.info("=" * 80)
    logger.info("[QA] 使用单一向量检索模式")
    
    # 初始化向量管理器，默认使用 knowledge_graph_rag collection
    vector_manager = VectorStoreManager(collection_name="knowledge_graph_rag")
    
    if not vector_manager.is_enabled or not vector_manager.collection:
        raise HTTPException(status_code=503, detail="向量库未启用或不可用")
    
    # 构建 Milvus 过滤表达式
    expr = ""
    if domains:
        domain_expr = ' or '.join([f'domain == "{d}"' for d in domains])
        expr = domain_expr
    
    logger.info(f"[QA] 使用过滤表达式：{expr if expr else '无过滤'}")
    
    # 向量召回
    try:
        results = vector_manager.search_with_expr(query_text=question, expr=expr, top_k=top_k)
    except Exception as e:
        logger.error(f"[QA] 向量召回失败：{e}")
        raise HTTPException(status_code=500, detail=f"向量检索失败：{str(e)}")
    
    if not results:
        logger.info("[QA] 未找到相关知识片段")
        logger.info("=" * 80)
        return {
            "answer": "未找到相关知识片段，请尝试其他问题或上传更多文档。",
            "references": []
        }
    
    # ★ 详细日志：打印向量召回结果
    logger.info(f"[QA] ★ 向量召回结果：{len(results)} 条")
    for i, result in enumerate(results[:5]):
        metadata = result.get("metadata", {})
        logger.info(f"  [结果{i+1}] file={metadata.get('source_file')}, domain={metadata.get('domain')}, distance={result.get('distance', 0):.4f}")
        logger.info(f"            quote={metadata.get('source_quote', '')[:100]}...")
    if len(results) > 5:
        logger.info(f"  ... 还有 {len(results) - 5} 条结果")
    
    # 构建参考上下文
    context_parts = []
    references = []
    
    for i, result in enumerate(results, 1):
        metadata = result.get("metadata", {})
        source_file = metadata.get("source_file", "未知文件")
        source_quote = metadata.get("source_quote", metadata.get("text", result.get("text", "")))
        
        context_parts.append(f"[{i}] {source_quote}")
        references.append({
            "id": i,
            "file": source_file,
            "quote": source_quote,
        })
    
    context = "\n\n".join(context_parts)
    
    # 构建 Prompt
    system_prompt = """你是一位知识图谱问答专家。请基于提供的参考片段回答问题。

【要求】：
1. 只根据参考片段中的信息回答，不要编造未知内容。
2. 在回答句末标注引用标号，例如 [1]、[2]。
3. 如果参考片段中没有相关信息，请如实告知用户。
4. 请直接回答，不要返回 JSON 格式。
"""
    
    user_prompt = f"""【参考片段】：
{context}

【用户问题】：
{question}

请回答用户的问题，并在句末标注引用标号。请直接返回文本回答。
"""
    
    # 调用 LLM 生成回答（不要求 JSON 格式）
    llm_client = LLMClient(api_key=api_key, base_url=base_url, model=model)
    try:
        # 使用 call_llm_text 方法，不要求 JSON 格式
        response = llm_client.call_llm_text(system_prompt, user_prompt, max_retries=3, stream=False)
        answer = response.get("content", "") if isinstance(response, dict) else str(response)
    except Exception as e:
        logger.error(f"[QA] LLM 调用失败：{e}")
        raise HTTPException(status_code=500, detail=f"LLM 生成回答失败：{str(e)}")
    
    return {
        "answer": answer,
        "references": references,
    }


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
    if project.owner_id != current_user.id:
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

    sys_llm_config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
    sys_embedding = {}
    if sys_llm_config and sys_llm_config.value:
        sv = sys_llm_config.value if isinstance(sys_llm_config.value, dict) else {}
        for ek in ["embedding_base_url", "embedding_model", "embedding_api_key", "embedding_dim"]:
            if ek in sv and sv[ek]:
                sys_embedding[ek] = sv[ek]

    is_admin = current_user.username == "admin"

    return {
        "status": "success",
        "data": masked_config,
        "system_embedding": sys_embedding,
        "is_admin": is_admin,
    }


@router.post("/{project_id}/inject-config")
async def save_inject_config(
    project_id: int,
    config: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id != current_user.id:
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
    if project.owner_id != current_user.id:
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
    if project.owner_id != current_user.id:
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
    if project.owner_id != current_user.id:
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
        logger.error(f"调用RAGFlow图谱注入API失败: {e}")
        raise HTTPException(status_code=500, detail=f"注入失败: 网络错误 - {str(e)}")
    except Exception as e:
        logger.error(f"注入RAGFlow失败: {e}")
        raise HTTPException(status_code=500, detail=f"注入失败: {str(e)}")
