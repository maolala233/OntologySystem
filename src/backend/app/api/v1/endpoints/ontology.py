# app/api/v1/endpoints/ontology.py - 本体生成端点
# 功能：提供本体文件生成、保存和同步到向量库的API接口

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
import pandas as pd
from app.schemas.request import ExtractionRequest
from app.schemas.response import OntologyResponse, ErrorResponse
from app.services.extractor import OntologyExtractor
from app.core.config import VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL, MILVUS_COLLECTION_NAME
from app.core.exceptions import ExtractionException
from app.core.logging import logger

router = APIRouter()


@router.post("/generate", response_model=OntologyResponse)
async def generate_ontology(request: ExtractionRequest):
    """
    生成本体文件
    """
    try:
        # 使用配置默认值 (默认为 vLLM)
        api_key = request.api_key or VLLM_API_KEY
        base_url = request.base_url or VLLM_BASE_URL
        model = request.model or VLLM_MODEL
        milvus_collection = request.milvus_collection or MILVUS_COLLECTION_NAME

        # 构建 DataFrame 用于规则
        df_data = []
        for rule in request.rules:
            df_data.append({
                "主体 (Class)": rule.get("cls_name") or rule.get("主体 (Class)"),
                "属性 (DataProp)": rule.get("attrs") or rule.get("属性 (DataProp)"),
                "关系 (ObjectProp)": rule.get("rels") or rule.get("关系 (ObjectProp)")
            })
        df = pd.DataFrame(df_data)

        # 调用核心逻辑
        backend = OntologyExtractor(api_key, base_url, model, collection_name=milvus_collection)
        filename, msg = backend.build_ontology(
            request.text_content,
            request.scenario,
            df,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
            request_interval=request.request_interval,
            product_code=request.product_code
        )

        if not filename:
            raise ExtractionException(msg)

        with open(filename, "r", encoding="utf-8") as f:
            ttl_content = f.read()

        return OntologyResponse(
            status="success",
            filename=filename,
            ttl_content=ttl_content,
            log=msg
        )

    except ExtractionException as e:
        logger.error(f"本体生成错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"生成本体时发生未知错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"生成本体失败: {str(e)}")


@router.post("/save")
async def save_ontology():
    """
    保存本体文件
    """
    try:
        # 保存本体文件的逻辑
        return {"status": "success", "message": "本体文件已保存"}
    except Exception as e:
        logger.error(f"保存本体时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"保存本体失败: {str(e)}")


@router.post("/sync-ttl")
async def sync_ttl_to_vector_store(ttl_file_path: str, delete_old: bool = True):
    """
    将TTL文件同步到向量库
    """
    try:
        backend = OntologyExtractor(VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL, MILVUS_COLLECTION_NAME)
        result = backend.sync_ttl_to_vector_store(ttl_file_path, delete_old=delete_old)
        
        return {"status": "success", "message": result}
    except Exception as e:
        logger.error(f"同步TTL到向量库时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"同步TTL到向量库失败: {str(e)}")