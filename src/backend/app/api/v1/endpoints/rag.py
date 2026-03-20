# app/api/v1/endpoints/rag.py - 双路 RAG 查询端点
# 功能：融合 Neo4j 图检索和 Milvus 向量检索，实现双路溯源问答

from fastapi import APIRouter, HTTPException, Body
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from app.services.rag_engine import rag_engine as dual_path_rag_engine
from app.services.extractor import OntologyExtractor
from app.infrastructure.vector_client import VectorStoreManager
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.neo4j_client import neo4j_client
from app.core.config import settings

# 通过 settings 对象访问配置值
VLLM_API_KEY = settings.VLLM_API_KEY
VLLM_BASE_URL = settings.VLLM_BASE_URL
VLLM_MODEL = settings.VLLM_MODEL
MILVUS_COLLECTION_NAME = settings.MILVUS_COLLECTION_NAME
from app.core.exceptions import RAGException
from app.core.logging import logger

router = APIRouter()


class DualPathRAGRequest(BaseModel):
    """双路 RAG 查询请求"""
    question: str
    project_id: int
    domains: Optional[List[str]] = None  # 知识域过滤
    top_k: int = 5
    use_text2cypher: bool = True
    use_advanced_text2cypher: bool = True  # ★ 新增：是否使用 3 步大模型驱动的检索
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    milvus_collection: Optional[str] = None


class Reference(BaseModel):
    """溯源引用"""
    id: int
    domain: str
    file: str
    chunk_index: int
    quote: str
    type: str  # "vector_chunk" | "graph_edge" | "graph_node"


class DualPathRAGResponse(BaseModel):
    """双路 RAG 查询响应"""
    status: str
    answer: str
    references: List[Reference]
    debug_info: Optional[Dict[str, Any]] = None


@router.post("/dual-path-query", response_model=DualPathRAGResponse)
async def dual_path_query(request: DualPathRAGRequest):
    """
    双路 RAG 查询接口
    
    融合 Neo4j 图检索和 Milvus 向量检索，实现双路溯源问答：
    - 路径 A：Neo4j 结构化图检索 - 获取精确的关系图谱路径
    - 路径 B：Milvus 语义向量召回 - 获取丰富的文本切片
    - 智能上下文融合 - 拼接两路结果
    - 最终 LLM 生成 - 生成回答并标注引用
    
    返回格式：
    {
        "status": "success",
        "answer": "阳光橙安盈的风险等级为 R2[1]，其主要的管理人是中信银行 [2]。",
        "references": [
            {
                "id": 1,
                "domain": "理财业务",
                "file": "阳光橙说明书.pdf",
                "chunk_index": 12,
                "quote": "本理财产品名为阳光橙安盈，风险等级为 R2。",
                "type": "vector_chunk"
            },
            {
                "id": 2,
                "domain": "理财业务",
                "file": "阳光橙说明书.pdf",
                "chunk_index": 15,
                "quote": "阳光橙安盈由中信银行担任主要管理人。",
                "type": "graph_edge"
            }
        ],
        "debug_info": {
            "graph_facts_count": 5,
            "vector_results_count": 3,
            "graph_references_count": 2,
            "vector_references_count": 3
        }
    }
    """
    logger.info("=" * 80)
    logger.info("[API] 双路 RAG 查询接口被调用")
    logger.info(f"[API] ★ 请求参数:")
    logger.info(f"  - question: {request.question}")
    logger.info(f"  - project_id: {request.project_id}")
    logger.info(f"  - domains: {request.domains}")
    logger.info(f"  - top_k: {request.top_k}")
    logger.info(f"  - use_text2cypher: {request.use_text2cypher}")
    logger.info(f"  - api_key: {'***' if request.api_key else 'None'}")
    logger.info(f"  - base_url: {request.base_url or 'default'}")
    logger.info(f"  - model: {request.model or 'default'}")
    logger.info(f"  - milvus_collection: {request.milvus_collection or 'default'}")
    logger.info("=" * 80)
    
    # 清理 base_url
    clean_url = (request.base_url or VLLM_BASE_URL).strip()
    if clean_url.endswith("/"):
        clean_url = clean_url[:-1]
    if clean_url.endswith("/chat/completions"):
        clean_url = clean_url.replace("/chat/completions", "")
    
    try:
        # 更新 RAG 引擎的客户端配置
        if request.api_key or clean_url != VLLM_BASE_URL or request.model:
            dual_path_rag_engine.llm_client = LLMClient(
                api_key=request.api_key or VLLM_API_KEY,
                base_url=clean_url,
                model=request.model or VLLM_MODEL
            )
        
        if request.milvus_collection:
            dual_path_rag_engine.vector_store = VectorStoreManager(
                collection_name=request.milvus_collection,
                create_if_missing=False
            )
        
        # 执行双路 RAG 查询
        result = dual_path_rag_engine.query(
            question=request.question,
            project_id=request.project_id,
            domains=request.domains,
            top_k=request.top_k,
            use_text2cypher=request.use_text2cypher,
            use_advanced_text2cypher=request.use_advanced_text2cypher,  # ★ 使用 3 步大模型驱动的检索
            schema=None,  # 可以传入 Schema 以增强 Text2Cypher
        )
        
        # 格式化引用为 Pydantic 模型
        references = []
        for ref in result.get("references", []):
            references.append(Reference(
                id=ref.get("id", 0),
                domain=ref.get("domain", ""),
                file=ref.get("file", ""),
                chunk_index=ref.get("chunk_index", 0),
                quote=ref.get("quote", ""),
                type=ref.get("type", "unknown"),
            ))
        
        response = DualPathRAGResponse(
            status="success",
            answer=result.get("answer", ""),
            references=references,
            debug_info=result.get("debug_info", {}),
        )
        
        # ★ 详细日志：打印响应
        logger.info("=" * 80)
        logger.info("[API] ★ 双路 RAG 查询响应:")
        logger.info(f"  status: {response.status}")
        logger.info(f"  answer: {response.answer[:200]}..." if len(response.answer) > 200 else f"  answer: {response.answer}")
        logger.info(f"  references_count: {len(response.references)}")
        for i, ref in enumerate(response.references[:5]):
            logger.info(f"    [{i+1}] id={ref.id}, type={ref.type}, file={ref.file}, quote={ref.quote[:50]}...")
        if len(response.references) > 5:
            logger.info(f"    ... 还有 {len(response.references) - 5} 条引用")
        logger.info(f"  debug_info: {response.debug_info}")
        logger.info("=" * 80)
        
        return response
        
    except Exception as e:
        logger.error(f"双路 RAG 查询错误：{str(e)}")
        raise HTTPException(status_code=500, detail=f"双路 RAG 查询失败：{str(e)}")


@router.post("/query", response_model=DualPathRAGResponse)
async def query_rag(request: DualPathRAGRequest):
    """
    RAG 查询接口（兼容旧接口，转发到双路 RAG 引擎）
    """
    return await dual_path_query(request)


@router.post("/load-ttl")
async def load_ttl_to_rag(
    ttl_file_path: str = Body(..., embed=True),
    project_id: Optional[int] = None,
    domain: Optional[str] = None,
    milvus_collection: Optional[str] = None,
):
    """
    加载 TTL 文件到 RAG 引擎
    
    参数:
    - ttl_file_path: TTL 文件路径
    - project_id: 项目 ID，用于逻辑隔离
    - domain: 知识域名称，用于按知识域过滤
    - milvus_collection: Milvus 集合名称
    """
    try:
        collection_name = milvus_collection or MILVUS_COLLECTION_NAME
        
        # 初始化 extractor
        backend = OntologyExtractor(
            VLLM_API_KEY, 
            VLLM_BASE_URL, 
            VLLM_MODEL, 
            collection_name=collection_name
        )
        
        # 同步到 Milvus（包含溯源信息）
        sync_msg = backend.sync_ttl_to_vector_store(
            ttl_file_path, 
            delete_old=True,
            project_id=project_id,
            domain=domain,
        )
        
        logger.info(f"TTL 同步完成：{sync_msg}")
        
        return {
            "status": "success", 
            "message": f"TTL 文件加载成功：{sync_msg}",
            "project_id": project_id,
            "domain": domain,
        }
        
    except Exception as e:
        logger.error(f"加载 TTL 文件到 RAG 引擎错误：{str(e)}")
        raise HTTPException(status_code=500, detail=f"加载 TTL 文件失败：{str(e)}")


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
        logger.error(f"获取 Milvus 集合列表错误：{str(e)}")
        raise HTTPException(status_code=500, detail=f"获取集合列表失败：{str(e)}")


@router.get("/neo4j/status")
async def get_neo4j_status():
    """
    获取 Neo4j 连接状态
    """
    try:
        if neo4j_client.driver:
            return {
                "status": "connected",
                "uri": neo4j_client.uri,
            }
        else:
            return {
                "status": "disconnected",
                "message": "Neo4j 驱动未初始化",
            }
    except Exception as e:
        logger.error(f"获取 Neo4j 状态错误：{str(e)}")
        return {
            "status": "error",
            "message": str(e),
        }