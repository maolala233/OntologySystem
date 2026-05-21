"""
注入端点
提供知识图谱注入的完整API接口
"""
import asyncio
import os
import shutil
from typing import List
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from app.schemas.request import InjectRequest, InjectTaskRequest
from app.schemas.response import InjectResponse, TaskStatusResponse
from app.services.orchestrator import InjectionOrchestrator, task_manager
from app.utils.file_parser import parse_file, validate_file_type
from app.core.logging import logger
from app.core.exceptions import GraphInjectorException

router = APIRouter(prefix="/inject", tags=["inject"])
orchestrator = InjectionOrchestrator()


@router.post("/", response_model=InjectResponse)
async def inject_graph(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(default=[]),
    text_content: str = Form(default=""),
    chunk_size: int = Form(default=1000),
    overlap_percentage: int = Form(default=10),
    use_existing_schema: bool = Form(default=False),
    existing_schema_path: str = Form(default=""),
    ragflow_kb_id: str = Form(...),
    ragflow_tenant_id: str = Form(...),
    ragflow_api_key: str = Form(...),
    additional_instructions: str = Form(default=""),
):
    """
    执行知识图谱注入
    
    参数:
    - files: 上传的文档文件列表(支持PDF、DOCX、TXT、MD)
    - text_content: 直接提供的文本内容
    - chunk_size: 每个chunk的字符长度(100-10000)
    - overlap_percentage: chunk之间的重叠百分比(0-50)
    - use_existing_schema: 是否使用已有的Schema文件
    - existing_schema_path: 已有Schema JSON文件的路径(当use_existing_schema为true时必填)
    - ragflow_kb_id: RAGFlow知识库ID
    - ragflow_tenant_id: RAGFlow租户ID
    - ragflow_api_key: RAGFlow API Key
    - additional_instructions: 额外的构建指令
    
    流程:
    1. 解析上传的文档或接收文本内容
    2. 如果未选择已有Schema，则从文档中构建Schema骨架JSON
    3. 使用Schema构建实体和关系实例
    4. 将实例数据注入到ES库中
    """
    try:
        logger.info(f"收到注入请求: 文件数={len(files)}, kb_id={ragflow_kb_id}")

        full_text = text_content
        temp_file_paths = []

        if files:
            os.makedirs("data/temp_uploads", exist_ok=True)
            for uploaded_file in files:
                if not uploaded_file.filename:
                    continue

                if not validate_file_type(uploaded_file.filename):
                    raise HTTPException(
                        status_code=400,
                        detail=f"不支持的文件类型: {uploaded_file.filename}。支持: PDF, DOCX, TXT, MD"
                    )

                temp_path = os.path.join("data/temp_uploads", uploaded_file.filename)
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(uploaded_file.file, buffer)
                temp_file_paths.append(temp_path)

                logger.info(f"文件已保存: {temp_path}")

            for temp_path in temp_file_paths:
                try:
                    file_text = parse_file(temp_path)
                    full_text += f"\n{file_text}"
                    logger.info(f"文件解析成功: {temp_path}, 文本长度={len(file_text)}")
                except Exception as e:
                    logger.error(f"文件解析失败: {temp_path}, 错误: {e}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件解析失败 [{os.path.basename(temp_path)}]: {str(e)}"
                    )

            for path in temp_file_paths:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

        if not full_text.strip():
            raise HTTPException(status_code=400, detail="请提供文本内容或上传文件")

        task_id = task_manager.create_task()

        existing_schema = existing_schema_path if use_existing_schema else None
        additional = additional_instructions if additional_instructions else None

        background_tasks.add_task(
            _run_injection_task,
            task_id=task_id,
            text_content=full_text,
            chunk_size=chunk_size,
            overlap_percentage=overlap_percentage,
            use_existing_schema=use_existing_schema,
            existing_schema_path=existing_schema,
            ragflow_kb_id=ragflow_kb_id,
            ragflow_tenant_id=ragflow_tenant_id,
            ragflow_api_key=ragflow_api_key,
            additional_instructions=additional,
        )

        return InjectResponse(
            status="success",
            task_id=task_id,
            message="注入任务已提交，正在后台处理",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"注入请求失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"注入请求失败: {str(e)}")


@router.post("/sync", response_model=InjectResponse)
async def inject_graph_sync(
    files: List[UploadFile] = File(default=[]),
    text_content: str = Form(default=""),
    chunk_size: int = Form(default=1000),
    overlap_percentage: int = Form(default=10),
    use_existing_schema: bool = Form(default=False),
    existing_schema_path: str = Form(default=""),
    ragflow_kb_id: str = Form(...),
    ragflow_tenant_id: str = Form(...),
    ragflow_api_key: str = Form(...),
    additional_instructions: str = Form(default=""),
):
    """
    同步执行知识图谱注入(等待完成后返回结果)
    
    参数与异步版本相同，但会等待整个流程完成才返回结果
    注意: 对于大文档可能耗时较长，建议使用异步版本
    """
    try:
        logger.info(f"收到同步注入请求: 文件数={len(files)}, kb_id={ragflow_kb_id}")

        full_text = text_content
        temp_file_paths = []

        if files:
            os.makedirs("data/temp_uploads", exist_ok=True)
            for uploaded_file in files:
                if not uploaded_file.filename:
                    continue

                if not validate_file_type(uploaded_file.filename):
                    raise HTTPException(
                        status_code=400,
                        detail=f"不支持的文件类型: {uploaded_file.filename}"
                    )

                temp_path = os.path.join("data/temp_uploads", uploaded_file.filename)
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(uploaded_file.file, buffer)
                temp_file_paths.append(temp_path)

            for temp_path in temp_file_paths:
                try:
                    file_text = parse_file(temp_path)
                    full_text += f"\n{file_text}"
                except Exception as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件解析失败: {str(e)}"
                    )

            for path in temp_file_paths:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

        if not full_text.strip():
            raise HTTPException(status_code=400, detail="请提供文本内容或上传文件")

        task_id = task_manager.create_task()
        existing_schema = existing_schema_path if use_existing_schema else None
        additional = additional_instructions if additional_instructions else None

        result = await orchestrator.execute_injection(
            task_id=task_id,
            text_content=full_text,
            chunk_size=chunk_size,
            overlap_percentage=overlap_percentage,
            use_existing_schema=use_existing_schema,
            existing_schema_path=existing_schema,
            ragflow_kb_id=ragflow_kb_id,
            ragflow_tenant_id=ragflow_tenant_id,
            ragflow_api_key=ragflow_api_key,
            additional_instructions=additional,
        )

        injection_stats = result.get("injection", {})
        entities_injected = result.get("instances", {}).get("entities", 0)
        relations_injected = result.get("instances", {}).get("relationships", 0)

        return InjectResponse(
            status="success",
            task_id=task_id,
            message="注入流程完成",
            summary=result,
            entities_injected=entities_injected,
            relationships_injected=relations_injected,
        )

    except HTTPException:
        raise
    except GraphInjectorException as e:
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger.error(f"同步注入请求失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"注入请求失败: {str(e)}")


@router.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    查询注入任务状态

    Args:
        task_id: 任务ID
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        progress=task["progress"],
        message=task["message"],
        result=task.get("result"),
        error_message=task.get("error_message"),
        created_at=task.get("created_at"),
        completed_at=task.get("completed_at"),
    )


@router.get("/tasks", response_model=List[TaskStatusResponse])
async def list_tasks():
    """列出所有任务"""
    tasks = list(task_manager.tasks.values())
    tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return [
        TaskStatusResponse(
            task_id=t["task_id"],
            status=t["status"],
            progress=t["progress"],
            message=t["message"],
            result=t.get("result"),
            error_message=t.get("error_message"),
            created_at=t.get("created_at"),
            completed_at=t.get("completed_at"),
        )
        for t in tasks
    ]


async def _run_injection_task(
    task_id: str, text_content: str, chunk_size: int, overlap_percentage: int,
    use_existing_schema: bool, existing_schema_path: str, ragflow_kb_id: str,
    ragflow_tenant_id: str, ragflow_api_key: str, additional_instructions: str,
):
    """后台任务执行函数"""
    try:
        await orchestrator.execute_injection(
            task_id=task_id,
            text_content=text_content,
            chunk_size=chunk_size,
            overlap_percentage=overlap_percentage,
            use_existing_schema=use_existing_schema,
            existing_schema_path=existing_schema_path,
            ragflow_kb_id=ragflow_kb_id,
            ragflow_tenant_id=ragflow_tenant_id,
            ragflow_api_key=ragflow_api_key,
            additional_instructions=additional_instructions,
        )
    except Exception as e:
        logger.error(f"后台注入任务失败 task_id={task_id}: {e}", exc_info=True)
        task_manager.fail_task(task_id, str(e))
