"""
实例抽取端点
提供基于已有Schema从文档中抽取实体和关系实例的API接口
"""
import os
import shutil
from typing import List
from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from app.services.instance_service import InstanceBuilder
from app.services.schema_service import SchemaBuilder
from app.utils.file_parser import parse_file, validate_file_type
from app.core.logging import logger
from app.core.exceptions import InstanceBuildException

router = APIRouter(prefix="/instance", tags=["instance"])
instance_builder = InstanceBuilder()
schema_builder = SchemaBuilder()


@router.post("/extract")
async def extract_instances(
    schema_path: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    text_content: str = Form(default=""),
    chunk_size: int = Form(default=8000),
    overlap_percentage: int = Form(default=15),
    additional_instructions: str = Form(default=""),
):
    """
    基于已有Schema从文档中抽取实体和关系实例

    参数:
    - schema_path: 已有Schema JSON文件的路径
    - files: 上传的文档文件列表(支持PDF、DOCX、TXT、MD)
    - text_content: 直接提供的文本内容
    - chunk_size: 每个chunk的字符长度(1000-15000)，默认8000
    - overlap_percentage: chunk之间的重叠百分比(0-50)，默认15
    - additional_instructions: 额外的抽取指令

    返回:
    - 抽取的实体和关系实例，包含nodes和relationships两个列表
    """
    try:
        logger.info(f"收到实例抽取请求: schema_path={schema_path}, 文件数={len(files)}")

        # 加载Schema
        if not os.path.exists(schema_path):
            raise HTTPException(status_code=400, detail=f"Schema文件不存在: {schema_path}")

        schema = schema_builder.load_schema(schema_path)
        entity_types = [et["name"] for et in schema.get("entity_types", [])]
        relation_types = [rt["name"] for rt in schema.get("relation_types", [])]
        logger.info(f"Schema加载成功: 实体类型={len(entity_types)}, 关系类型={len(relation_types)}")

        # 解析文档
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

        logger.info(f"开始抽取实例，总文本长度={len(full_text)}")

        result = await instance_builder.build_instances(
            text_content=full_text,
            ontology=schema,
            chunk_size=chunk_size,
            overlap_percentage=overlap_percentage,
            additional_instructions=additional,
        )

        object_types = [ot["name"] for ot in schema.get("object_types", schema.get("entity_types", []))]
        link_types = [lt["name"] for lt in schema.get("link_types", schema.get("relation_types", []))]

        return {
            "status": "success",
            "schema_path": schema_path,
            "entity_types_found": object_types,
            "relation_types_found": link_types,
            "nodes": result.get("nodes", []),
            "edges": result.get("edges", []),
            "relationships": result.get("relationships", []),
            "metadata": result.get("metadata", {}),
            "message": f"Ontology实例抽取完成: {len(result.get('nodes', []))}个对象, "
                       f"{len(result.get('edges', []))}个链接",
        }

    except HTTPException:
        raise
    except InstanceBuildException as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error(f"实例抽取失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"实例抽取失败: {str(e)}")
