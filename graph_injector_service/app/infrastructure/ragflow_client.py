"""
RAGFlow HTTP API客户端
用于与RAGFlow服务交互
"""
import logging
from typing import Optional, Dict, Any, List
import httpx
from app.core.config import settings


class RAGFlowClient:
    """
    RAGFlow HTTP API客户端
    封装RAGFlow的知识库管理、文档上传等API
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = settings.ragflow_api_host.rstrip("/")
        self.logger = logging.getLogger("graph_injector.ragflow_client")

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def get_kb_info(self, kb_id: str) -> Optional[Dict[str, Any]]:
        """
        获取知识库信息，包括tenant_id等

        Args:
            kb_id: 知识库ID

        Returns:
            知识库信息或None
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/datasets",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == 0:
                        datasets = data.get("data", [])
                        for ds in datasets:
                            if ds.get("id") == kb_id:
                                return ds
                return None
        except Exception as e:
            self.logger.warning(f"获取知识库信息失败: {e}")
            return None

    async def upload_document(self, kb_id: str, file_path: str, filename: str) -> Optional[List[Dict[str, Any]]]:
        """
        上传文档到知识库

        Args:
            kb_id: 知识库ID
            file_path: 文件路径
            filename: 文件名

        Returns:
            文档信息列表
        """
        try:
            import os
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"文件不存在: {file_path}")

            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, 'rb') as f:
                    response = await client.post(
                        f"{self.base_url}/api/v1/datasets/{kb_id}/documents",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        files={"file": (filename, f, "text/plain")},
                    )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        return result.get("data", [])
                    else:
                        self.logger.error(f"上传失败: {result.get('message')}")
                else:
                    self.logger.error(f"上传请求失败: HTTP {response.status_code} - {response.text}")
                return None
        except Exception as e:
            self.logger.error(f"文档上传异常: {e}")
            return None

    async def run_document_parse(self, kb_id: str, doc_id: str) -> bool:
        """
        触发文档解析

        Args:
            kb_id: 知识库ID
            doc_id: 文档ID

        Returns:
            是否成功触发
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/datasets/{kb_id}/documents/{doc_id}/run",
                    headers=self._get_headers(),
                    json={},
                )

                if response.status_code == 200:
                    result = response.json()
                    self.logger.info(f"触发解析: {result}")
                    return True
                return False
        except Exception as e:
            self.logger.error(f"触发文档解析异常: {e}")
            return False

    async def test_retrieval(self, kb_id: str, question: str) -> List[Dict[str, Any]]:
        """
        测试检索

        Args:
            kb_id: 知识库ID
            question: 问题

        Returns:
            检索结果列表
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/retrieval",
                    headers=self._get_headers(),
                    json={
                        "question": question,
                        "dataset_ids": [kb_id],
                        "page": 1,
                        "page_size": 10,
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        data = result.get("data", {})
                        return data.get("chunks", [])
                return []
        except Exception as e:
            self.logger.error(f"检索异常: {e}")
            return []
