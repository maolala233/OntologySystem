"""
Elasticsearch客户端封装
负责ES连接管理和数据注入操作
"""
import logging
from typing import List, Dict, Any, Optional
from elasticsearch import Elasticsearch
from app.core.config import settings
from app.core.exceptions import ESInjectionException


class ESClient:
    """
    Elasticsearch客户端管理类
    封装ES连接、索引操作和数据注入功能
    """

    def __init__(self):
        self._client: Optional[Elasticsearch] = None
        self.logger = logging.getLogger("graph_injector.es_client")

    @property
    def client(self) -> Elasticsearch:
        """懒加载ES客户端"""
        if self._client is None:
            self._client = Elasticsearch(
                hosts=[settings.es.url],
                basic_auth=(settings.es.user, settings.es.password),
                verify_certs=settings.es.verify_certs,
                request_timeout=30,
                retry_on_timeout=True,
                max_retries=3,
            )
        return self._client

    def test_connection(self) -> Dict[str, Any]:
        """
        测试ES连接

        Returns:
            连接信息字典
        """
        try:
            info = self.client.info()
            return {
                "status": "success",
                "version": info.get("version", {}).get("number", "unknown"),
                "cluster_name": info.get("cluster_name", ""),
            }
        except Exception as e:
            raise ESInjectionException(f"ES连接失败: {str(e)}")

    def index_exists(self, index_name: str) -> bool:
        """
        检查索引是否存在

        Args:
            index_name: 索引名称

        Returns:
            是否存在
        """
        return self.client.indices.exists(index=index_name)

    def get_index_name(self, tenant_id: str) -> str:
        """
        根据租户ID生成索引名称

        Args:
            tenant_id: 租户ID

        Returns:
            完整的索引名称
        """
        return f"{settings.es_index_prefix}_{tenant_id}"

    def search(self, index_name: str, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行搜索查询

        Args:
            index_name: 索引名称
            query: 查询体

        Returns:
            搜索结果
        """
        return self.client.search(index=index_name, body=query)

    def index_document(self, index_name: str, doc_id: str, document: Dict[str, Any]) -> bool:
        """
        索引单个文档

        Args:
            index_name: 索引名称
            doc_id: 文档ID
            document: 文档内容

        Returns:
            是否成功
        """
        try:
            self.client.index(index=index_name, id=doc_id, document=document)
            return True
        except Exception as e:
            self.logger.error(f"索引文档失败: {e}")
            return False

    def update_document(self, index_name: str, doc_id: str, document: Dict[str, Any]) -> bool:
        """
        更新单个文档

        Args:
            index_name: 索引名称
            doc_id: 文档ID
            document: 更新的文档内容

        Returns:
            是否成功
        """
        try:
            self.client.update(index=index_name, id=doc_id, body={"doc": document})
            return True
        except Exception as e:
            self.logger.error(f"更新文档失败: {e}")
            return False

    def bulk_index(self, index_name: str, documents: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        批量索引文档

        Args:
            index_name: 索引名称
            documents: 文档列表，每个文档需要包含'_id'字段

        Returns:
            统计信息(success_count, error_count)
        """
        if not documents:
            return {"success": 0, "error": 0}

        actions = []
        for doc in documents:
            doc_id = doc.pop("_id", None)
            if doc_id:
                actions.append({"index": {"_index": index_name, "_id": doc_id}})
                actions.append(doc)

        if not actions:
            return {"success": 0, "error": 0}

        try:
            response = self.client.bulk(operations=actions, refresh=True)
            errors = response.get("errors", False)
            items = response.get("items", [])
            success_count = sum(1 for item in items if "index" in item and item["index"].get("status") in (200, 201))
            error_count = len(items) - success_count
            return {"success": success_count, "error": error_count}
        except Exception as e:
            self.logger.error(f"批量索引失败: {e}")
            return {"success": 0, "error": len(documents)}

    def close(self):
        """关闭ES连接"""
        if self._client:
            self._client.close()
            self._client = None


# 单例ES客户端
es_client = ESClient()
