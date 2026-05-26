import asyncio
import logging
from typing import List
import httpx
from app.core.logging import logger


class EmbeddingClient:
    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 model: str = "bge-m3:latest",
                 api_key: str = "",
                 dim: int = 1024,
                 timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.timeout = timeout

    def _is_ollama(self) -> bool:
        return "ollama" in self.base_url.lower() or ":11434" in self.base_url

    async def get_embedding(self, text: str) -> List[float]:
        if not text or not text.strip():
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if self._is_ollama():
                    url = self.base_url.replace("/v1", "") + "/api/embeddings"
                    payload = {"model": self.model, "prompt": text}
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("embedding", [])
                else:
                    url = self.base_url + "/embeddings"
                    payload = {"input": [text], "model": self.model}
                    headers = {}
                    if self.api_key:
                        headers = {"Authorization": f"Bearer {self.api_key}"}
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("data", [{}])[0].get("embedding", [])
        except Exception as e:
            logger.warning(f"Embedding获取失败: {e}")
            return []

    async def get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        results = await asyncio.gather(
            *[self.get_embedding(text) for text in texts],
            return_exceptions=True
        )
        embeddings = []
        for r in results:
            if isinstance(r, Exception):
                embeddings.append([0.0] * self.dim)
            elif r:
                embeddings.append(r)
            else:
                embeddings.append([0.0] * self.dim)
        return embeddings
