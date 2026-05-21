"""
系统健康检查和连通性测试端点
"""
from fastapi import APIRouter
from app.schemas.response import HealthResponse, ConnectivityTestResponse
from app.infrastructure.es_client import es_client
from app.infrastructure.llm_client import llm_client
from app.infrastructure.embedding_client import embedding_client
from app.core.logging import logger
from app.core.config import settings
import httpx
from datetime import datetime

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    服务健康检查
    返回服务状态和各组件连接状态
    """
    components = {}
    overall_status = "healthy"

    try:
        es_info = es_client.test_connection()
        components["elasticsearch"] = "healthy"
        logger.debug(f"ES健康检查: {es_info}")
    except Exception as e:
        components["elasticsearch"] = f"unhealthy: {str(e)}"
        overall_status = "degraded"
        logger.warning(f"ES健康检查失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{settings.llm.base_url}/models")
            if response.status_code == 200:
                components["llm"] = "healthy"
            else:
                components["llm"] = f"unhealthy: HTTP {response.status_code}"
                overall_status = "degraded"
    except Exception as e:
        components["llm"] = f"unhealthy: {str(e)}"
        overall_status = "degraded"
        logger.warning(f"LLM健康检查失败: {e}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if "ollama" in settings.embed.base_url.lower() or ":11434" in settings.embed.base_url:
                response = await client.get(f"{settings.embed.base_url}/api/tags")
            else:
                response = await client.get(f"{settings.embed.base_url}/models")
            if response.status_code == 200:
                components["embedding"] = "healthy"
            else:
                components["embedding"] = f"unhealthy: HTTP {response.status_code}"
                overall_status = "degraded"
    except Exception as e:
        components["embedding"] = f"unhealthy: {str(e)}"
        overall_status = "degraded"
        logger.warning(f"Embedding健康检查失败: {e}")

    return HealthResponse(
        status=overall_status,
        timestamp=datetime.now().isoformat(),
        version="1.0.0",
        components=components,
    )


@router.post("/test-connectivity/llm", response_model=ConnectivityTestResponse)
async def test_llm_connectivity():
    """测试LLM服务连通性"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if "ollama" in settings.llm.base_url.lower() or ":11434" in settings.llm.base_url:
                response = await client.post(
                    f"{settings.llm.base_url}/api/generate",
                    json={
                        "model": settings.llm.model,
                        "prompt": "Hello",
                        "stream": False,
                    },
                )
            else:
                headers = {}
                if settings.llm.api_key:
                    headers["Authorization"] = f"Bearer {settings.llm.api_key}"
                response = await client.post(
                    f"{settings.llm.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": settings.llm.model,
                        "messages": [{"role": "user", "content": "Hello"}],
                        "max_tokens": 10,
                    },
                )

            if response.status_code == 200:
                return ConnectivityTestResponse(
                    status="success",
                    component="llm",
                    message=f"LLM连通性测试成功 (模型: {settings.llm.model})",
                    details={"base_url": settings.llm.base_url, "model": settings.llm.model},
                )
            else:
                return ConnectivityTestResponse(
                    status="error",
                    component="llm",
                    message=f"LLM连通性测试失败: HTTP {response.status_code}",
                    details={"response": response.text[:200]},
                )

    except Exception as e:
        return ConnectivityTestResponse(
            status="error",
            component="llm",
            message=f"LLM连通性测试异常: {str(e)}",
        )


@router.post("/test-connectivity/embedding", response_model=ConnectivityTestResponse)
async def test_embedding_connectivity():
    """测试Embedding服务连通性"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if "ollama" in settings.embed.base_url.lower() or ":11434" in settings.embed.base_url:
                response = await client.post(
                    f"{settings.embed.base_url}/api/embeddings",
                    json={
                        "model": settings.embed.model,
                        "prompt": "Hello",
                    },
                )
            else:
                response = await client.post(
                    f"{settings.embed.base_url}/embeddings",
                    json={
                        "input": ["Hello"],
                        "model": settings.embed.model,
                    },
                )

            if response.status_code == 200:
                return ConnectivityTestResponse(
                    status="success",
                    component="embedding",
                    message=f"Embedding连通性测试成功 (模型: {settings.embed.model})",
                    details={"base_url": settings.embed.base_url, "model": settings.embed.model},
                )
            else:
                return ConnectivityTestResponse(
                    status="error",
                    component="embedding",
                    message=f"Embedding连通性测试失败: HTTP {response.status_code}",
                    details={"response": response.text[:200]},
                )

    except Exception as e:
        return ConnectivityTestResponse(
            status="error",
            component="embedding",
            message=f"Embedding连通性测试异常: {str(e)}",
        )


@router.post("/test-connectivity/es", response_model=ConnectivityTestResponse)
async def test_es_connectivity():
    """测试Elasticsearch连通性"""
    try:
        es_info = es_client.test_connection()
        return ConnectivityTestResponse(
            status="success",
            component="elasticsearch",
            message=f"ES连通性测试成功 (版本: {es_info.get('version', 'unknown')})",
            details={
                "host": settings.es.host,
                "port": settings.es.port,
                "version": es_info.get("version"),
                "cluster_name": es_info.get("cluster_name"),
            },
        )
    except Exception as e:
        return ConnectivityTestResponse(
            status="error",
            component="elasticsearch",
            message=f"ES连通性测试失败: {str(e)}",
            details={"host": settings.es.host, "port": settings.es.port},
        )
