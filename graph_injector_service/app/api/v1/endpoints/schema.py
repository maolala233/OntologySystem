"""
Schema端点
提供Schema构建和管理的API接口
"""
import os
import shutil
from typing import List
from fastapi import APIRouter, HTTPException, Body, File, UploadFile, Form
from app.schemas.request import SchemaBuildRequest
from app.schemas.response import SchemaBuildResponse
from app.services.schema_service import SchemaBuilder
from app.utils.file_parser import parse_file, validate_file_type
from app.core.logging import logger
from app.core.config import settings
from app.core.exceptions import SchemaBuildException

router = APIRouter(prefix="/schema", tags=["schema"])
schema_builder = SchemaBuilder()


@router.post("/extract", response_model=SchemaBuildResponse)
async def extract_schema_from_files(
    files: List[UploadFile] = File(default=[]),
    text_content: str = Form(default=""),
    chunk_size: int = Form(default=1000),
    overlap_percentage: int = Form(default=10),
    additional_instructions: str = Form(default=""),
):
    """
    从上传的文档文件或文本内容中提取知识图谱Schema
    
    参数:
    - files: 上传的文档文件列表(支持PDF、DOCX、TXT、MD)
    - text_content: 直接提供的文本内容
    - chunk_size: 每个chunk的字符长度(100-10000)
    - overlap_percentage: chunk之间的重叠百分比(0-50)
    - additional_instructions: 额外的构建指令
    
    返回:
    - 提取的Schema结构，包含entity_types和relation_types
    
    流程:
    1. 解析上传的文档或接收文本内容
    2. 使用LLM从文档中提取Schema骨架JSON
    """
    try:
        logger.info(f"收到Schema提取请求: 文件数={len(files)}")

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

        additional = additional_instructions if additional_instructions else None

        logger.info(f"开始提取Schema，总文本长度={len(full_text)}")

        schema = await schema_builder.build_schema(
            text_content=full_text,
            chunk_size=chunk_size,
            overlap_percentage=overlap_percentage,
            additional_instructions=additional,
        )

        schema_path = schema_builder.save_schema(schema)

        object_types = [ot["name"] for ot in schema.get("object_types", schema.get("entity_types", []))]
        link_types = [lt["name"] for lt in schema.get("link_types", schema.get("relation_types", []))]
        action_types = [at["name"] for at in schema.get("action_types", [])]

        return SchemaBuildResponse(
            status="success",
            schema_path=schema_path,
            schema_content=schema,
            message=f"Ontology Schema提取完成，共提取 {len(object_types)} 个对象类型, "
                    f"{len(link_types)} 个链接类型, {len(action_types)} 个动作类型",
            entity_types=object_types,
            relation_types=link_types,
        )

    except HTTPException:
        raise
    except SchemaBuildException as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error(f"Schema提取失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Schema提取失败: {str(e)}")


@router.post("/build", response_model=SchemaBuildResponse)
async def build_schema(request: SchemaBuildRequest):
    """
    从文档内容构建知识图谱Schema
    
    参数:
    - text_content: 文档文本内容
    - chunk_size: 每个chunk的字符长度(100-10000)
    - overlap_percentage: chunk之间的重叠百分比(0-50)
    - additional_instructions: 额外的构建指令
    
    返回:
    - 构建的Schema结构，包含entity_types和relation_types
    """
    try:
        logger.info(f"收到Schema构建请求，文本长度={len(request.text_content)}")

        schema = await schema_builder.build_schema(
            text_content=request.text_content,
            chunk_size=request.chunk_size,
            overlap_percentage=request.overlap_percentage,
            additional_instructions=request.additional_instructions,
        )

        schema_path = schema_builder.save_schema(schema)

        entity_types = [et["name"] for et in schema.get("entity_types", [])]
        relation_types = [rt["name"] for rt in schema.get("relation_types", [])]

        return SchemaBuildResponse(
            status="success",
            schema_path=schema_path,
            schema_content=schema,
            message=f"Schema构建完成，共提取 {len(entity_types)} 个实体类型和 "
                    f"{len(relation_types)} 个关系类型",
            entity_types=entity_types,
            relation_types=relation_types,
        )

    except SchemaBuildException as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error(f"Schema构建失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Schema构建失败: {str(e)}")


@router.post("/load", response_model=SchemaBuildResponse)
async def load_schema(schema_path: str = Body(..., embed=True)):
    """
    从已有JSON文件加载Schema
    
    参数:
    - schema_path: Schema JSON文件的路径
    
    返回:
    - 加载的Schema内容
    """
    try:
        if not os.path.exists(schema_path):
            raise HTTPException(status_code=404, detail=f"Schema文件不存在: {schema_path}")

        schema = schema_builder.load_schema(schema_path)

        entity_types = [et["name"] for et in schema.get("entity_types", [])]
        relation_types = [rt["name"] for rt in schema.get("relation_types", [])]

        return SchemaBuildResponse(
            status="success",
            schema_path=schema_path,
            schema_content=schema,
            message=f"Schema加载成功，包含 {len(entity_types)} 个实体类型和 "
                    f"{len(relation_types)} 个关系类型",
            entity_types=entity_types,
            relation_types=relation_types,
        )

    except HTTPException:
        raise
    except SchemaBuildException as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error(f"Schema加载失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Schema加载失败: {str(e)}")


@router.get("/list")
async def list_schemas():
    """
    列出所有已保存的Schema文件
    """
    try:
        schema_dir = settings.schema_output_dir
        if not os.path.exists(schema_dir):
            return {"schemas": []}

        schemas = []
        for filename in os.listdir(schema_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(schema_dir, filename)
                stat = os.stat(filepath)
                schemas.append({
                    "filename": filename,
                    "path": filepath,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                })

        schemas.sort(key=lambda x: x["modified_at"], reverse=True)
        return {"schemas": schemas}

    except Exception as e:
        logger.error(f"列出Schema文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"列出Schema文件失败: {str(e)}")


@router.delete("/{filename}")
async def delete_schema(filename: str):
    """
    删除指定的Schema文件

    Args:
        filename: Schema文件名
    """
    try:
        schema_dir = settings.schema_output_dir
        filepath = os.path.join(schema_dir, filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail=f"Schema文件不存在: {filename}")

        os.remove(filepath)
        logger.info(f"Schema文件已删除: {filepath}")

        return {"status": "success", "message": f"Schema文件已删除: {filename}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除Schema文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除Schema文件失败: {str(e)}")
