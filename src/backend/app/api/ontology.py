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
        db_project.graph_data = project_update.graph_data
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
    chunk_overlap: int = Form(500),
    request_interval: int = Form(2),
    async_mode: str = Form("true", description="是否异步执行（支持取消）"),
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

    logger.info(f"[extract-schema-from-documents] 收到请求 - project_id={project_id}, doc_ids={doc_ids}")

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

    # 解析文件文本（支持多文件合并）
    from app.services.parser import FileParser
    parser = FileParser()
    text_contents = []
    for temp_path in temp_paths:
        if os.path.exists(temp_path):
            text_content = parser.parse_file(temp_path) or ""
            text_contents.append(text_content)
            logger.info(f"[extract-schema-from-documents] 文件解析完成 - file={temp_path}, text_length={len(text_content)}")
        else:
            logger.warning(f"[extract-schema-from-documents] 文件不存在 - {temp_path}")
    
    # 合并所有文件内容
    combined_text = "\n\n".join(text_contents)
    logger.info(f"[extract-schema-from-documents] 合并后总文本长度={len(combined_text)}")

    # 获取 LLM 配置
    extractor = _build_extractor(db)

    # 异步模式
    is_async_mode = async_mode.lower() == "true"
    
    if is_async_mode:
        # 异步模式：创建任务并后台执行
        task_id = task_manager.create_task(message="开始骨架提取...")
        logger.info(f"[extract-schema-from-documents] 任务已创建 - task_id={task_id}")
        task_manager.start_task(task_id, message="开始骨架提取...", detail=f"正在解析 {len(documents)} 个文档...")
        
        # 后台执行提取任务
        async def run_extraction():
            try:
                def progress_callback(progress: float, message: str):
                    task_manager.update_progress(task_id, progress=progress, message=message)
                
                # 在线程池中运行同步提取方法（使用合并后的文本）
                schema = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: extractor.extract_schema_only(
                        text=combined_text,
                        user_intent=user_intent,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        request_interval=request_interval,
                        task_id=task_id,
                        progress_callback=progress_callback,
                    )
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
                    result={"schema_graph": schema, "graph_data": graph_data, "text_content": combined_text},
                    message=f"骨架提取完成：{len(schema['classes'])} 个类，{len(schema['object_properties'])} 个关系（来自 {len(documents)} 个文档）"
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
        # 同步模式
        schema = extractor.extract_schema_only(
            text=combined_text,
            user_intent=user_intent,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
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

        return {
            "schema_graph": schema,
            "graph_data": graph_data,
            "text_content": combined_text,
            "message": (
                f"骨架提取完成：{len(schema['classes'])} 个类，"
                f"{len(schema['object_properties'])} 个关系（来自 {len(documents)} 个文档）。"
                f"请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
            ),
        }


@router.post("/{project_id}/extract-schema")
async def extract_schema_endpoint(
    project_id: int,
    files: List[UploadFile] = File(..., description="支持多文件上传"),
    user_intent: Optional[str] = Form(None, description="用户意图/关注领域（可选）"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(500),
    request_interval: int = Form(2),
    async_mode: str = Form("false", description="是否异步执行（支持取消）"),
    save_documents: bool = Form("true", description="是否保存文档记录到数据库"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 正确解析布尔值：只有 "true" (不区分大小写) 才为 True
    is_async_mode = async_mode.lower() == "true"
    # 正确解析 save_documents 参数
    should_save_documents = save_documents.lower() == "true" or save_documents is True
    
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
        os.makedirs("src/backend/uploads", exist_ok=True)
        os.makedirs(f"src/backend/uploads/projects/{project_id}", exist_ok=True)
    
    # 保存临时文件（支持多文件）
    os.makedirs("temp_uploads", exist_ok=True)
    temp_paths = []
    saved_docs = []
    
    try:
        for uploaded_file in files:
            # 如果需要保存文档记录，创建永久存储
            if should_save_documents:
                import uuid
                unique_filename = f"{uuid.uuid4()}_{uploaded_file.filename}"
                file_path = os.path.join(f"src/backend/uploads/projects/{project_id}", unique_filename)
                
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
                temp_path = os.path.join("temp_uploads", uploaded_file.filename)
                with open(temp_path, "wb") as buf:
                    buf.write(await uploaded_file.read())
                temp_paths.append(temp_path)
        
        # 如果需要保存文档记录，提交数据库事务
        if should_save_documents and saved_docs:
            db.commit()
            logger.info(f"[extract-schema] 已保存 {len(saved_docs)} 个文档记录到数据库")
        
        logger.info(f"[extract-schema] 共保存 {len(temp_paths)} 个文件")

        # 解析文件文本（支持多文件合并）
        from app.services.parser import FileParser
        parser = FileParser()
        text_contents = []
        for temp_path in temp_paths:
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
        extractor = _build_extractor(db)

        if is_async_mode:
            # 异步模式：创建任务并后台执行
            task_id = task_manager.create_task(message="开始骨架提取...")
            logger.info(f"[extract-schema] 任务已创建 - task_id={task_id}")
            task_manager.start_task(task_id, message="开始骨架提取...", detail=f"正在解析 {len(temp_paths)} 个文件...")
            
            # 后台执行提取任务
            async def run_extraction():
                try:
                    def progress_callback(progress: float, message: str):
                        task_manager.update_progress(task_id, progress=progress, message=message)
                    
                    # 在线程池中运行同步提取方法（使用合并后的文本）
                    schema = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: extractor.extract_schema_only(
                            text=combined_text,
                            user_intent=user_intent,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                            request_interval=request_interval,
                            task_id=task_id,
                            progress_callback=progress_callback,
                        )
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
                        result={"schema_graph": schema, "graph_data": graph_data, "text_content": combined_text},
                        message=f"骨架提取完成：{len(schema['classes'])} 个类，{len(schema['object_properties'])} 个关系（来自 {len(files)} 个文件）"
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
            # 同步模式（默认，保持向后兼容）
            schema = extractor.extract_schema_only(
                text=combined_text,
                user_intent=user_intent,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                request_interval=request_interval,
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

            return {
                "schema_graph": schema,
                "graph_data": graph_data,
                "text_content": combined_text,
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
                    f"骨架提取完成：{len(schema['classes'])} 个类，"
                    f"{len(schema['object_properties'])} 个关系（来自 {len(files)} 个文件）。"
                    f"请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
                ),
            }

    except Exception as e:
        logger.error(f"[extract-schema] 错误：{e}", exc_info=True)
        if should_save_documents:
            db.rollback()
        raise HTTPException(status_code=500, detail=f"骨架提取失败：{str(e)}")
    finally:
        # 清理临时文件（只清理 temp_uploads 目录中的文件）
        for temp_path in temp_paths:
            if temp_path.startswith("temp_uploads") and os.path.exists(temp_path):
                os.remove(temp_path)


# ─────────────────────────────────────────────
#  ★ 基于已上传文档 ID 进行实例提取（新增）
# ─────────────────────────────────────────────

@router.post("/{project_id}/extract-instances-from-documents")
async def extract_instances_from_documents(
    project_id: int,
    document_ids: str = Form(..., description="已上传文档 ID 列表，逗号分隔"),
    chunk_size: int = Form(15000),
    chunk_overlap: int = Form(500),
    request_interval: int = Form(2),
    async_mode: str = Form("true", description="是否异步执行（支持取消）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    【API - 基于已上传文档进行实例提取】
    基于数据库中已上传的文档 ID 进行实例提取，不需要重新上传文件。
    适用于文档管理 Modal 中点击"开始实例提取"的场景。
    
    关键点：
    1. 从数据库获取已上传文档
    2. 解析文档获取文本内容
    3. 从项目 graph_data 中获取 schema（用户已审核的骨架）
    4. 使用 schema 约束进行实例提取
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

    # 解析文件文本（支持多文件合并）
    from app.services.parser import FileParser
    parser = FileParser()
    text_contents = []
    for temp_path in temp_paths:
        if os.path.exists(temp_path):
            text_content = parser.parse_file(temp_path) or ""
            text_contents.append(text_content)
            logger.info(f"[extract-instances-from-documents] 文件解析完成 - file={temp_path}, text_length={len(text_content)}")
        else:
            logger.warning(f"[extract-instances-from-documents] 文件不存在 - {temp_path}")
    
    # 合并所有文件内容
    combined_text = "\n\n".join(text_contents)
    logger.info(f"[extract-instances-from-documents] 合并后总文本长度={len(combined_text)}")

    # 从项目 graph_data 中获取 schema（用户已审核的版本）
    schema_dict = (db_project.graph_data or {}).get("schema", {})
    
    # 如果没有 schema 字段，尝试从 nodes 和 edges 动态构建（兼容 TTL 导入场景）
    if not schema_dict or not schema_dict.get("classes"):
        nodes = (db_project.graph_data or {}).get("nodes", [])
        edges = (db_project.graph_data or {}).get("edges", [])
        if nodes or edges:
            # 从 nodes 和 edges 构建 schema
            schema_dict = build_schema_from_graph_data(nodes, edges)
    
    if not schema_dict or not schema_dict.get("classes"):
        raise HTTPException(status_code=400, detail="请先提取骨架再进行实例提取")

    # 获取 LLM 配置
    extractor = _build_extractor(db)

    # 异步模式
    is_async_mode = async_mode.lower() == "true"
    
    if is_async_mode:
        # 异步模式：创建任务并后台执行
        task_id = task_manager.create_task(message="开始实例提取...")
        logger.info(f"[extract-instances-from-documents] 任务已创建 - task_id={task_id}")
        task_manager.start_task(task_id, message="开始实例提取...", detail=f"正在解析 {len(documents)} 个文档...")
        
        # 后台执行提取任务
        async def run_extraction():
            try:
                def progress_callback(progress: float, message: str):
                    task_manager.update_progress(task_id, progress=progress, message=message)
                
                # 在线程池中运行同步提取方法
                inst_result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: extractor.extract_instances_with_constraints(
                        text=combined_text,
                        schema_graph=schema_dict,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        request_interval=request_interval,
                        task_id=task_id,
                        progress_callback=progress_callback,
                    )
                )

                # 从 schema 构建基础图数据
                schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
                
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

                task_manager.complete_task(
                    task_id,
                    result={
                        "instances": inst_result["instances"],
                        "graph_data": full_graph_data,
                        "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
                        "schema_graph": schema_dict,
                        "text_content": combined_text,
                    },
                    message=f"实例提取完成：{len(inst_result['instances'])} 个实例" + (
                        f" ({inst_result.get('discarded_edges_count', 0)} 条不合规连线已自动丢弃)" 
                        if inst_result.get("discarded_edges_count", 0) > 0 else ""
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
        # 同步模式
        inst_result = extractor.extract_instances_with_constraints(
            text=combined_text,
            schema_graph=schema_dict,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            task_id=None,
        )

        # 从 schema 构建基础图数据
        schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
        
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
            "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
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

        if async_mode:
            # 异步模式：创建任务并后台执行
            task_id = task_manager.create_task(message="开始实例提取...")
            task_manager.start_task(task_id, message="开始实例提取...", detail="正在解析文件...")
            
            # 后台执行提取任务
            async def run_extraction():
                try:
                    def progress_callback(progress: float, message: str):
                        task_manager.update_progress(task_id, progress=progress, message=message)
                    
                    # 在线程池中运行同步提取方法
                    inst_result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: extractor.extract_instances_with_constraints(
                            text=request_data.get("text_content", ""),
                            schema_graph=schema_dict,
                            chunk_size=request_data.get("chunk_size", 15000),
                            chunk_overlap=request_data.get("chunk_overlap", 500),
                            request_interval=request_data.get("request_interval", 2),
                            product_code=request_data.get("product_code"),
                            task_id=task_id,
                            progress_callback=progress_callback,
                        )
                    )

                    # 【关键修复】从前端传递的 schema_graph 构建基础图数据，而不是依赖数据库
                    # 这样确保实例是添加到用户指定的 schema 框架上
                    schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
                    
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

                    task_manager.complete_task(
                        task_id,
                        result={
                            "instances": inst_result["instances"],
                            "graph_data": full_graph_data,
                            "discarded_edges_count": inst_result.get("discarded_edges_count", 0),
                            "schema_graph": schema_dict,
                            "text_content": request_data.get("text_content", ""),
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
            # 同步模式（默认，保持向后兼容）
            # ── 调用第二阶段引擎（带 Schema 约束） ──
            inst_result = extractor.extract_instances_with_constraints(
                text=request_data.get("text_content", ""),
                schema_graph=schema_dict,
                chunk_size=request_data.get("chunk_size", 15000),
                chunk_overlap=request_data.get("chunk_overlap", 500),
                request_interval=request_data.get("request_interval", 2),
                product_code=request_data.get("product_code"),
            )

            # 【关键修复】从前端传递的 schema_graph 构建基础图数据，而不是依赖数据库
            # 这样确保实例是添加到用户指定的 schema 框架上
            schema_graph_data = OntologyExtractor.schema_to_graph_data(schema_dict)
            
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


@router.post("/{project_id}/parse-files")
async def parse_files(
    project_id: int,
    files: List[UploadFile] = File(..., description="文件列表（支持 PDF/DOC/DOCX/TXT）"),
    save_documents: bool = Form(True, description="是否保存文档记录到数据库"),
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

    # 创建永久存储目录
    os.makedirs("src/backend/uploads", exist_ok=True)
    os.makedirs(f"src/backend/uploads/projects/{project_id}", exist_ok=True)
    
    temp_paths = []
    saved_docs = []
    
    try:
        for uploaded_file in files:
            # 生成唯一文件名避免冲突
            import uuid
            unique_filename = f"{uuid.uuid4()}_{uploaded_file.filename}"
            file_path = os.path.join(f"src/backend/uploads/projects/{project_id}", unique_filename)
            
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

        # 解析文件文本（支持多文件合并）
        parser = FileParser()
        text_contents = []
        for temp_path in temp_paths:
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
    files: List[UploadFile] = File(..., description="TTL 文件列表"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    解析 TTL 文件提取骨架 Schema（仅包含类和 ObjectProperty）。
    用于 Step 1 构建类结构，之后可进行 Step 2 实例提取。
    """
    from app.core.logging import logger
    
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")

    os.makedirs("temp_uploads", exist_ok=True)
    temp_paths = []
    try:
        for uploaded_file in files:
            if not uploaded_file.filename.lower().endswith('.ttl'):
                raise HTTPException(status_code=400, detail=f"只支持 TTL 文件：{uploaded_file.filename}")
            temp_path = os.path.join("temp_uploads", uploaded_file.filename)
            with open(temp_path, "wb") as buf:
                buf.write(await uploaded_file.read())
            temp_paths.append(temp_path)
        
        logger.info(f"[parse-ttl-schema] 已保存 {len(temp_paths)} 个 TTL 文件")

        # 解析所有 TTL 文件
        all_nodes = []
        all_edges = []
        combined_ttl_content = ""
        
        for temp_path in temp_paths:
            with open(temp_path, "r", encoding='utf-8') as ttl_file:
                ttl_content = ttl_file.read()
                combined_ttl_content += ttl_content + "\n\n"
                
                nodes, edges = convert_ttl_to_graph_data(ttl_content)
                all_nodes.extend(nodes)
                all_edges.extend(edges)
        
        # 去重节点和边
        seen_node_ids = set()
        unique_nodes = []
        for node in all_nodes:
            if node['id'] not in seen_node_ids:
                seen_node_ids.add(node['id'])
                unique_nodes.append(node)
        
        seen_edge_ids = set()
        unique_edges = []
        for edge in all_edges:
            edge_id = f"{edge['source']}_{edge['target']}_{edge.get('label', '')}"
            if edge_id not in seen_edge_ids:
                seen_edge_ids.add(edge_id)
                unique_edges.append(edge)
        
        # 从 TTL 中提取 schema（仅类和 ObjectProperty）
        schema_dict = extract_schema_from_ttl(combined_ttl_content)
        
        # 构建 graph_data
        graph_data = {
            "nodes": unique_nodes,
            "edges": unique_edges,
        }
        
        # 更新项目 graph_data
        db_project.graph_data = {
            "schema": schema_dict,
            **graph_data,
        }
        db_project.ttl_content = combined_ttl_content
        db.commit()
        
        # 仅统计类的数量
        class_count = len([n for n in unique_nodes if n['data'].get('type') == 'owl:Class'])

        return {
            "schema_graph": schema_dict,
            "graph_data": graph_data,
            "message": f"TTL 骨架解析成功：{class_count} 个类",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[parse-ttl-schema] 错误：{e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"TTL 骨架解析失败：{str(e)}")
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


# ─────────────────────────────────────────────
#  内部辅助函数
# ─────────────────────────────────────────────

def build_schema_from_graph_data(nodes: List[dict], edges: List[dict]) -> dict:
    """
    从 nodes 和 edges 动态构建 schema（兼容 TTL 导入场景）。
    当 TTL 文件导入骨架时，graph_data 中可能没有 schema 字段，
    但有 nodes 和 edges，本函数从这些数据中提取 schema。
    """
    from app.core.logging import logger
    
    classes = []
    object_properties = []
    
    logger.info(f"[build_schema_from_graph_data] 开始从 {len(nodes)} 个节点和 {len(edges)} 个边构建 schema")
    
    # 从节点中提取类
    for node in nodes:
        node_type = node.get('data', {}).get('type', '')
        if node_type == 'owl:Class':
            node_id = str(node['id'])
            node_label = node.get('data', {}).get('label', node_id)
            
            # 获取父类（通过 subclassOf 边）
            parent_classes = []
            for edge in edges:
                edge_data = edge.get('data', {})
                edge_relation = edge_data.get('relation', '')
                edge_label = edge.get('label', '')
                # 检查是否是 subclassOf 关系
                if edge_relation == 'subclass_of' or edge_label in ('subClassOf', 'subclass_of'):
                    if edge.get('source') == node_id:
                        parent_classes.append(edge.get('target'))
            
            # 获取数据属性（从 properties 中提取键名）
            data_properties = []
            node_data = node.get('data', {})
            properties = node_data.get('properties', {})
            if isinstance(properties, dict):
                data_properties = list(properties.keys())
            
            logger.info(f"[build_schema_from_graph_data] 添加类：{node_id}, label={node_label}, parent_classes={parent_classes}, data_properties={data_properties}")
            
            classes.append({
                "id": node_id,
                "label": node_label,
                "parent_classes": parent_classes,
                "data_properties": data_properties,
            })
    
    # 从边中提取 ObjectProperty
    for edge in edges:
        edge_data = edge.get('data', {})
        relation = edge_data.get('relation', '')
        label = edge.get('label', '') or edge_data.get('label', '')
        
        # 只提取 ObjectProperty 关系（排除 subClassOf 和 type）
        if relation and relation not in ('rdf:type', 'type', 'subClassOf', 'subclass_of'):
            # 也检查 label 是否是 subclassOf 关系
            if label in ('subClassOf', 'subclass_of'):
                continue
                
            prop_id = edge_data.get('prop_id', '')
            # 如果 prop_id 为空或无效，使用 label 生成
            if not prop_id or prop_id in ('', '_', '__', '___'):
                prop_id = label if label else relation
            
            logger.info(f"[build_schema_from_graph_data] 添加 ObjectProperty: {prop_id}, label={label}, domain={edge.get('source')}, range={edge.get('target')}")
            
            object_properties.append({
                "id": prop_id,
                "label": label,
                "domain": edge.get('source', ''),
                "range": edge.get('target', ''),
            })
    
    logger.info(f"[build_schema_from_graph_data] 构建完成：{len(classes)} 个类，{len(object_properties)} 个 ObjectProperty")
    
    return {
        "classes": classes,
        "object_properties": object_properties,
    }


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
        if '#' in node_id or (node_id.startswith('http')):
            return URIRef(node_id)
        clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', node_id)
        return ex[clean]

    def generate_safe_prop_id(original_name: str) -> str:
        """
        生成一个安全的属性ID，优先使用拼音，如果包含非ASCII字符则使用MD5哈希。
        """
        if not original_name:
            return ""
        
        # 检查是否包含非ASCII字符
        if not original_name.isascii():
            try:
                from pypinyin import lazy_pinyin
                # 尝试使用拼音
                pinyin_id = '_'.join(lazy_pinyin(original_name))
                # 确保拼音结果是有效的URI片段
                pinyin_id = re.sub(r'[^a-zA-Z0-9_]', '_', pinyin_id)
                if pinyin_id and pinyin_id.strip('_') != '':
                    return pinyin_id
            except ImportError:
                pass # Fallback to MD5 if pypinyin is not available

            # 如果拼音失败或不可用，使用MD5哈希
            md5_hash = hashlib.md5(original_name.encode('utf-8')).hexdigest()
            return f"prop_{md5_hash}"
        else:
            # 如果是纯ASCII，直接清理
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', original_name)
            if not clean_id or clean_id.strip('_') == '':
                # Fallback for empty or all-underscore results from cleaning
                md5_hash = hashlib.md5(original_name.encode('utf-8')).hexdigest()
                return f"prop_{md5_hash}"
            return clean_id

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
            dataprop_id = generate_safe_prop_id(prop_name)
            if not dataprop_id:  # Should not happen with MD5 fallback
                continue
            
            dataprop_uri = ex[dataprop_id]
            g.add((dataprop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((dataprop_uri, RDFS.label, Literal(prop_name, lang="zh")))
            
            # 如果是类节点，添加 rdfs:domain 关联（即使无值，也声明属性骨架）
            if node_type == 'owl:Class':
                g.add((dataprop_uri, RDFS.domain, uri))
            
            # 只有在有实际值时才添加三元组数据
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
                # 使用标准的 rdfs:subClassOf
                g.add((source_uri, RDFS.subClassOf, target_uri))
            else:
                # 优先使用 schema 中定义的 prop_id
                prop_info = existing_obj_properties.get(relation)
                if prop_info:
                    prop_id, label = prop_info
                else:
                    # 如果没有找到，使用 relation_label 生成
                    prop_id = generate_safe_prop_id(relation_label)
                    if not prop_id:
                        # Fallback if generate_safe_prop_id somehow fails
                        prop_id = f"prop_{abs(hash(relation_label)) % 10000}"
                
                objprop_uri = ex[prop_id]
                g.add((objprop_uri, RDF.type, OWL.ObjectProperty))
                g.add((objprop_uri, RDFS.label, Literal(relation_label, lang="zh")))
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
    """
    from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef
    
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
    
    # 提取所有类
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
        data_properties = []
        for prop_uri, prop_info in datatype_prop_domains.items():
            if cls_id in prop_info['domains']:
                data_properties.append(prop_info['label'])
        
        classes.append({
            "id": cls_id,
            "label": label,
            "parent_classes": parent_classes,
            "data_properties": list(set(data_properties)),
        })
    
    # 提取所有 ObjectProperty
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
        
        object_properties.append({
            "id": prop_id,
            "label": label,
            "domain": domains[0] if domains else "",
            "range": ranges[0] if ranges else "",
        })
    
    return {
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": list(datatype_prop_domains.values()),
    }


def convert_ttl_to_graph_data(ttl_content: str):
    """
    将 TTL 转换为前端可渲染的 (nodes, edges) 二元组。
    支持解析 DatatypeProperty 作为节点的自定义属性。
    
    注意：TTL 中的 DatatypeProperty 定义（如 ex:documentTitle a owl:DatatypeProperty）
    只是声明了属性的存在，而不是具体的属性值。
    只有当 TTL 中有实际的属性值（如 ex:SomeDocument ex:documentTitle "报告标题"）时，
    才会被解析为节点的 properties。
    
    本函数会将 schema 中定义的 data_properties 添加到对应类节点的 properties 中，
    以便前端在编辑节点时显示这些预定义的属性。
    
    重要：TTL 文件中子类可能没有显式声明 a owl:Class，而是通过 rdfs:subClassOf 关系隐式成为类。
    本函数会同时处理显式和隐式声明的类。
    """
    from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef, Literal
    
    g = Graph()
    g.parse(data=ttl_content, format="turtle")

    nodes = []
    edges = []
    processed_nodes: set = set()

    # 首先收集所有 DatatypeProperty 及其 domain 信息
    datatype_props = {}  # prop_uri -> {'id': str, 'label': str, 'domain': str}
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
    
    # 调试日志：打印收集到的 DatatypeProperty
    from app.core.logging import logger
    logger.info(f"[convert_ttl_to_graph_data] 找到 {len(datatype_props)} 个 DatatypeProperty: {list(datatype_props.values())}")

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
        
        # 1. 解析直接关联的数据属性值（通过谓词 - 对象匹配）
        for pred, obj in g.predicate_objects(uri):
            pred_uri = str(pred)
            
            # 跳过元属性
            if pred_uri in [str(RDF.type), str(RDFS.label), str(RDFS.subClassOf),
                            str(RDFS.domain), str(RDFS.range)]:
                continue
            
            # 检查这个谓词是否是一个 DatatypeProperty
            if pred_uri in datatype_props:
                prop_info = datatype_props[pred_uri]
                prop_label = prop_info['label']
                # 只收集字面量值（非 URI）
                if isinstance(obj, Literal) or not str(obj).startswith('http'):
                    props[prop_label] = str(obj)
                    logger.info(f"[convert_ttl_to_graph_data] 节点 {node_id} 添加属性 {prop_label} = {str(obj)}")
            else:
                # 对于未显式声明为 DatatypeProperty 的谓词，也尝试收集
                p_name = str(pred).split('#')[-1]
                if isinstance(obj, Literal) or not str(obj).startswith('http'):
                    props[p_name] = str(obj)

        # 2. 如果是类节点，添加 schema 中定义的 data_properties 作为预定义属性
        if node_type_category == "owl:Class":
            for prop_uri, prop_info in datatype_props.items():
                domain_id = prop_info['domain']
                if domain_id == node_id and prop_info['label'] not in props:
                    # 将预定义属性添加到 properties 中，值为空字符串（表示待填写）
                    props[prop_info['label']] = ""
                    logger.info(f"[convert_ttl_to_graph_data] 类 {node_id} 添加预定义属性 {prop_info['label']}")

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
    
    logger.info(f"[convert_ttl_to_graph_data] 收集到 {len(class_uris)} 个类")

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
        
        # 获取 prop_id（从 URI 中提取）
        prop_uri_str = str(prop)
        prop_id = prop_uri_str.split('#')[-1] if '#' in prop_uri_str else prop_uri_str.split('/')[-1]

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
                    "prop_id": prop_id,  # 添加 prop_id 以便导出时使用
                },
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
                edges.append({
                    "id": f"e_{subj_str}_{obj_str}_{pred_label}",
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
