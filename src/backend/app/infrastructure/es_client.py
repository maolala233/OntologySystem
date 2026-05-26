import logging
from typing import Dict, Any, List, Optional
from elasticsearch import Elasticsearch
from app.core.logging import logger


class ESClient:
    def __init__(self, host: str = "localhost", port: int = 9200,
                 user: str = "elastic", password: str = "",
                 use_ssl: bool = False, verify_certs: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_ssl = use_ssl
        self.verify_certs = verify_certs
        self._client: Optional[Elasticsearch] = None

    @property
    def url(self) -> str:
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"

    def _get_client(self) -> Elasticsearch:
        if self._client is None:
            kwargs = {
                "hosts": [self.url],
                "verify_certs": self.verify_certs,
                "request_timeout":30,
                "retry_on_timeout": True,
                "max_retries": 3,
            }
            if self.user:
                kwargs["basic_auth"] = (self.user, self.password)
            self._client = Elasticsearch(**kwargs)
        return self._client

    def test_connection(self) -> Dict[str, Any]:
        try:
            client = self._get_client()
            info = client.info()
            return {
                "status": "ok",
                "version": info.get("version", {}).get("number", "unknown"),
                "cluster_name": info.get("cluster_name", "unknown"),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def index_exists(self, index_name: str) -> bool:
        try:
            client = self._get_client()
            return client.indices.exists(index=index_name)
        except Exception:
            return False

    def get_index_name(self, tenant_id: str, prefix: str = "ragflow") -> str:
        return f"{prefix}_{tenant_id}"

    def search(self, index_name: str, query: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_client()
        return client.search(index=index_name, body=query)

    def index_document(self, index_name: str, doc_id: str, document: Dict[str, Any]) -> bool:
        try:
            client = self._get_client()
            client.index(index=index_name, id=doc_id, body=document, refresh=True)
            return True
        except Exception as e:
            logger.error(f"ES索引文档失败: {e}")
            return False

    def update_document(self, index_name: str, doc_id: str, document: Dict[str, Any]) -> bool:
        try:
            client = self._get_client()
            client.update(index=index_name, id=doc_id, body={"doc": document}, refresh=True)
            return True
        except Exception as e:
            logger.error(f"ES更新文档失败: {e}")
            return False

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
