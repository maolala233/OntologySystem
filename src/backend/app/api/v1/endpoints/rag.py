# app/api/v1/endpoints/rag.py - RAG查询端点
# 功能：提供RAG查询、TTL文件加载到RAG引擎和获取Milvus集合列表的API接口

from fastapi import APIRouter, HTTPException
from typing import Optional
from app.schemas.request import RagQueryRequest
from app.schemas.response import QAResponse, ErrorResponse
from app.services.rag_engine import GraphRAG
from app.infrastructure.vector_client import VectorStoreManager
from app.infrastructure.llm_client import LLMClient
from app.core.config import VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL, MILVUS_COLLECTION_NAME
from app.core.exceptions import RAGException
from app.core.logging import logger

router = APIRouter()

# 全局 RAG 引擎实例
rag_engine = None


@router.post("/query", response_model=QAResponse)
async def query_rag(request: RagQueryRequest):
    """
    RAG 查询接口
    """
    global rag_engine
    
    # 清理 base_url
    clean_url = (request.base_url or VLLM_BASE_URL).strip()
    if clean_url.endswith("/"): clean_url = clean_url[:-1]
    if clean_url.endswith("/chat/completions"): clean_url = clean_url.replace("/chat/completions", "")
    
    try:
        # 创建 LLM 客户端
        llm_client = LLMClient(
            api_key=request.api_key or VLLM_API_KEY,
            base_url=clean_url,
            model=request.model or VLLM_MODEL
        )

        if not rag_engine:
            # 初始化 RAG 引擎
            vector_manager = VectorStoreManager(collection_name=request.milvus_collection or MILVUS_COLLECTION_NAME, create_if_missing=False)
            rag_engine = GraphRAG(vector_manager, llm_client, request.model or VLLM_MODEL)
        else:
            # 更新引擎的客户端和模型
            rag_engine.llm_client = llm_client
            rag_engine.model = request.model or VLLM_MODEL
            
            # 如果库名不一致，重新初始化向量库
            if rag_engine.vector_manager.collection_name != (request.milvus_collection or MILVUS_COLLECTION_NAME):
                rag_engine.vector_manager = VectorStoreManager(
                    collection_name=request.milvus_collection or MILVUS_COLLECTION_NAME, 
                    create_if_missing=False
                )

        answer, context, sources = rag_engine.query(
            question=request.question,
            k_hop=request.k_hop
        )
        
        # 格式化溯源信息
        source_md = "### 📚 知识溯源\n"
        for i, s in enumerate(sources):
            source_md += f"**[{i+1}] 来源**: `{s['source']}` | **分块**: `{s['chunk_id']}` | **主体**: `{s['subject']}`\n"
            source_md += f"> {s['text']}\n\n"
        
        return QAResponse(
            status="success",
            answer=answer,
            context=context,
            sources=source_md
        )
        
    except Exception as e:
        logger.error(f"RAG 查询错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"RAG 查询失败: {str(e)}")


@router.post("/load-ttl")
async def load_ttl_to_rag(ttl_file_path: str, milvus_collection: Optional[str] = None):
    """
    加载 TTL 文件到 RAG 引擎
    """
    global rag_engine
    
    try:
        collection_name = milvus_collection or MILVUS_COLLECTION_NAME
        
        # 初始化 backend
        backend = OntologyExtractor(VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL, collection_name=collection_name)
        
        # 同步到 Milvus
        sync_msg = backend.sync_ttl_to_vector_store(ttl_file_path, delete_old=True)
        
        # 加载到 GraphRAG
        vector_manager = VectorStoreManager(collection_name=collection_name, create_if_missing=False)
        llm_client = LLMClient(VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL)
        
        rag_engine = GraphRAG(vector_manager, llm_client, VLLM_MODEL)
        rag_engine.load_from_ttl(ttl_file_path, clear_graph=True)
        
        return {"status": "success", "message": f"TTL 文件加载成功: {sync_msg}"}
        
    except Exception as e:
        logger.error(f"加载 TTL 文件到 RAG 引擎错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"加载 TTL 文件失败: {str(e)}")


@router.get("/collections")
async def get_milvus_collections():
    """
    获取 Milvus 集合列表
    """
    try:
        vm = VectorStoreManager(create_if_missing=False)
        cols = vm.list_collections()
        return {"status": "success", "collections": cols}
    except Exception as e:
        logger.error(f"获取 Milvus 集合列表错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取集合列表失败: {str(e)}")