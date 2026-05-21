"""
Embedding客户端封装
支持vLLM和Ollama两种服务生成向量
"""
import logging
from typing import List, Optional
import httpx
from app.core.config import settings
from app.core.exceptions import EmbeddingException


class EmbeddingClient:
    """
    文本Embedding客户端
    自动检测并使用OpenAI兼容格式(vLLM)或Ollama格式
    """

    def __init__(self):
        self.base_url: str = settings.embed.base_url.rstrip("/")
        self.model: str = settings.embed.model
        self.timeout: int = settings.embed.timeout
        self.dim: int = settings.embed.dim
        self.logger = logging.getLogger("graph_injector.embedding_client")
        self._is_ollama: Optional[bool] = None

    def _detect_api_type(self) -> str:
        """
        检测API类型 (vLLM/OpenAI兼容 或 Ollama)

        Returns:
            'openai' 或 'ollama'
        """
        if self._is_ollama is not None:
            return "ollama" if self._is_ollama else "openai"

        if "ollama" in self.base_url.lower() or ":11434" in self.base_url:
            self._is_ollama = True
            return "ollama"
        else:
            self._is_ollama = False
            return "openai"

    async def get_embedding(self, text: str) -> List[float]:
        """
        获取文本的embedding向量

        Args:
            text: 输入文本

        Returns:
            embedding向量列表
        """
        if not text or not text.strip():
            raise EmbeddingException("输入文本不能为空")

        api_type = self._detect_api_type()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if api_type == "ollama":
                    return await self._get_embedding_ollama(client, text)
                else:
                    return await self._get_embedding_openai(client, text)
        except EmbeddingException:
            raise
        except httpx.TimeoutException:
            raise EmbeddingException(f"Embedding请求超时(>{self.timeout}s)")
        except Exception as e:
            raise EmbeddingException(f"Embedding调用异常: {str(e)}")

    async def get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        批量获取文本的embedding向量

        Args:
            texts: 输入文本列表

        Returns:
            embedding向量列表的列表
        """
        embeddings = []
        for text in texts:
            try:
                emb = await self.get_embedding(text)
                embeddings.append(emb)
            except Exception as e:
                self.logger.warning(f"文本embedding失败: {e}")
                embeddings.append([0.0] * self.dim)  # 返回零向量作为降级处理
        return embeddings

    async def _get_embedding_openai(self, client: httpx.AsyncClient, text: str) -> List[float]:
        """使用OpenAI兼容格式(vLLM)获取embedding"""
        body = {
            "input": [text],
            "model": self.model,
        }

        response = await client.post(
            f"{self.base_url}/embeddings",
            json=body,
        )

        if response.status_code != 200:
            raise EmbeddingException(f"Embedding调用失败: HTTP {response.status_code} - {response.text}")

        result = response.json()
        embedding = result.get("data", [{}])[0].get("embedding", [])
        return embedding

    async def _get_embedding_ollama(self, client: httpx.AsyncClient, text: str) -> List[float]:
        """使用Ollama格式获取embedding"""
        body = {
            "model": self.model,
            "prompt": text,
        }

        response = await client.post(
            f"{self.base_url}/api/embeddings",
            json=body,
        )

        if response.status_code != 200:
            raise EmbeddingException(f"Ollama embedding失败: HTTP {response.status_code} - {response.text}")

        result = response.json()
        embedding = result.get("embedding", [])
        return embedding


# 单例Embedding客户端
embedding_client = EmbeddingClient()
