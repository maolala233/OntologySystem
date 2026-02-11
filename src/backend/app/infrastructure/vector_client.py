# app/infrastructure/vector_client.py - 向量库客户端
# 功能：封装Milvus向量库操作，提供向量存储、检索和管理功能

import json
import hashlib
import os
from typing import List, Dict, Optional
from openai import OpenAI
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
from app.core.config import settings

# 通过 settings 对象访问配置值
MILVUS_HOST = settings.MILVUS_HOST
MILVUS_PORT = settings.MILVUS_PORT
MILVUS_COLLECTION_NAME = settings.MILVUS_COLLECTION_NAME
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
EMBEDDING_API_KEY = settings.EMBEDDING_API_KEY
EMBEDDING_BASE_URL = settings.EMBEDDING_BASE_URL
EMBEDDING_DIM = settings.EMBEDDING_DIM
from app.core.logging import logger


class VectorStoreManager:
    def __init__(self, collection_name: str = None, create_if_missing: bool = True):
        self.milvus_host = MILVUS_HOST
        self.milvus_port = MILVUS_PORT
        self.collection_name = collection_name if collection_name else MILVUS_COLLECTION_NAME
        self.embedding_dim = EMBEDDING_DIM
        self.emb_model = EMBEDDING_MODEL

        # 判断是否为外部API
        is_external_api = EMBEDDING_BASE_URL and ('openai' in EMBEDDING_BASE_URL.lower() or 'api.' in EMBEDDING_BASE_URL.lower() or 
                                                  'http' in EMBEDDING_BASE_URL and 'localhost' not in EMBEDDING_BASE_URL and 
                                                  '127.0.0.1' not in EMBEDDING_BASE_URL and '.lan' not in EMBEDDING_BASE_URL)
        
        if is_external_api:
            # 对于外部API，可能需要代理
            try:
                import httpx
                from urllib.parse import urlparse
                
                # 获取环境中的代理设置
                http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
                https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
                
                proxy_to_use = https_proxy or http_proxy
                
                if proxy_to_use:
                    # 检查是否为SOCKS代理
                    if 'socks' in proxy_to_use.lower():
                        try:
                            from httpx_socks import SyncProxyTransport
                            parsed = urlparse(proxy_to_use)
                            if parsed.scheme.startswith('socks'):
                                proxy_transport = SyncProxyTransport.from_url(proxy_to_use)
                                http_client = httpx.Client(transport=proxy_transport)
                                self.client = OpenAI(
                                    api_key=EMBEDDING_API_KEY,
                                    base_url=EMBEDDING_BASE_URL,
                                    http_client=http_client
                                )
                            else:
                                # 对于非SOCKS代理，使用标准方式
                                http_client = httpx.Client(proxy=proxy_to_use)
                                self.client = OpenAI(
                                    api_key=EMBEDDING_API_KEY,
                                    base_url=EMBEDDING_BASE_URL,
                                    http_client=http_client
                                )
                        except ImportError:
                            # 如果没有安装httpx_socks，记录警告并直接连接
                            logger.warning("httpx_socks not installed for SOCKS proxy support. Install with: pip install httpx[socks]")
                            # 移除环境变量中的代理设置来创建客户端
                            original_env = {}
                            proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']
                            
                            # 临时移除代理环境变量
                            for var in proxy_vars:
                                if var in os.environ:
                                    original_env[var] = os.environ[var]
                                    del os.environ[var]
                            
                            try:
                                self.client = OpenAI(
                                    api_key=EMBEDDING_API_KEY,
                                    base_url=EMBEDDING_BASE_URL
                                )
                            finally:
                                # 恢复环境变量
                                for var in proxy_vars:
                                    if var in original_env:
                                        os.environ[var] = original_env[var]
                                    elif var in os.environ:
                                        del os.environ[var]
                        except Exception as e:
                            # 处理URL格式错误或其他与代理相关的异常
                            logger.warning(f"Failed to create proxy client for embedding: {e}. Falling back to no proxy.")
                            # 移除环境变量中的代理设置来创建客户端
                            original_env = {}
                            proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']
                            
                            # 临时移除代理环境变量
                            for var in proxy_vars:
                                if var in os.environ:
                                    original_env[var] = os.environ[var]
                                    del os.environ[var]
                            
                            try:
                                self.client = OpenAI(
                                    api_key=EMBEDDING_API_KEY,
                                    base_url=EMBEDDING_BASE_URL
                                )
                            finally:
                                # 恢复环境变量
                                for var in proxy_vars:
                                    if var in original_env:
                                        os.environ[var] = original_env[var]
                                    elif var in os.environ:
                                        del os.environ[var]
                    else:
                        # 对于HTTP代理，使用httpx的proxy参数
                        http_client = httpx.Client(proxy=proxy_to_use)
                        self.client = OpenAI(
                            api_key=EMBEDDING_API_KEY,
                            base_url=EMBEDDING_BASE_URL,
                            http_client=http_client
                        )
                else:
                    # 没有代理设置，直接创建客户端
                    self.client = OpenAI(
                        api_key=EMBEDDING_API_KEY,
                        base_url=EMBEDDING_BASE_URL
                    )
            except Exception as e:
                logger.warning(f"Embedding客户端代理配置失败: {e}，尝试直接连接")
                self.client = OpenAI(
                    api_key=EMBEDDING_API_KEY,
                    base_url=EMBEDDING_BASE_URL
                )
        else:
            # 对于内部服务，移除代理环境变量
            original_env = {}
            proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']
            
            # 临时移除代理环境变量
            for var in proxy_vars:
                if var in os.environ:
                    original_env[var] = os.environ[var]
                    del os.environ[var]
            
            try:
                self.client = OpenAI(
                    api_key=EMBEDDING_API_KEY,
                    base_url=EMBEDDING_BASE_URL
                )
            finally:
                # 恢复环境变量
                for var in proxy_vars:
                    if var in original_env:
                        os.environ[var] = original_env[var]
                    elif var in os.environ:
                        del os.environ[var]

        # 连接 Milvus
        self.is_enabled = False
        self.collection = None
        try:
            self._connect_milvus()
            if create_if_missing:
                self.collection = self._get_or_create_collection()
            else:
                # 仅尝试加载已存在的集合，不创建
                if utility.has_collection(self.collection_name):
                    self.collection = Collection(self.collection_name)
                    self.collection.load()
            
            # 只要连接成功，就认为可用（至少可以执行 list_collections）
            self.is_enabled = True
        except Exception as e:
            logger.warning(f"Milvus 初始化跳过 (非阻塞): {e}")

    def _connect_milvus(self):
        try:
            if not connections.has_connection("default"):
                connections.connect("default", host=self.milvus_host, port=self.milvus_port, timeout=5)
                logger.info(f"✅ Milvus connected: {self.milvus_host}:{self.milvus_port}")
        except Exception as e:
            raise Exception(f"连接失败: {e}")

    def list_collections(self) -> List[str]:
        """列出所有可用的 Milvus 集合"""
        if not self.is_enabled:
            return []
        try:
            return utility.list_collections()
        except Exception as e:
            logger.warning(f"获取集合列表失败: {e}")
            return []

    def _get_or_create_collection(self):
        try:
            if utility.has_collection(self.collection_name):
                col = Collection(self.collection_name)
                col.load()
                return col

            logger.info(f"创建新集合: {self.collection_name} (dim={self.embedding_dim})")
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="content_hash", dtype=DataType.VARCHAR, max_length=64) # 用于去重
            ]
            schema = CollectionSchema(fields, "Knowledge Graph RAG Collection")
            collection = Collection(self.collection_name, schema)

            index_params = {
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            collection.create_index(field_name="vector", index_params=index_params)
            collection.load()
            return collection
        except Exception as e:
            logger.warning(f"集合创建/加载失败: {e}")
            return None

    def get_embedding(self, text: str) -> List[float]:
        try:
            text = text.replace("\n", " ")
            resp = self.client.embeddings.create(input=[text], model=self.emb_model)
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding error: {e}")
            return [0.0] * self.embedding_dim

    def _get_content_hash(self, text: str, metadata: Dict) -> str:
        """生成内容哈希，用于去重"""
        content = f"{text}_{json.dumps(metadata, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()

    def insert_data(self, texts: List[str], metadatas: List[Dict]):
        if not self.is_enabled or not self.collection or not texts:
            return

        vectors = []
        valid_texts = []
        valid_metas = []
        hashes = []

        # 1. 批量获取向量并检查重复
        for t, m in zip(texts, metadatas):
            try:
                # 安全截断
                safe_text = t[:65000] if len(t) > 65000 else t
                meta_str = json.dumps(m, ensure_ascii=False)
                safe_meta = meta_str[:65000] if len(meta_str) > 65000 else meta_str
                
                c_hash = self._get_content_hash(safe_text, m)
                
                # 逻辑唯一性检查
                try:
                    existing = self.collection.query(expr=f'content_hash == "{c_hash}"', output_fields=["id"])
                except Exception as e:
                    # 如果报 collection not found，尝试重新加载
                    if "collection not found" in str(e).lower():
                        logger.info("Milvus 集合句柄失效，正在尝试重新连接...")
                        self.collection = self._get_or_create_collection()
                        existing = self.collection.query(expr=f'content_hash == "{c_hash}"', output_fields=["id"])
                    else:
                        raise e

                if existing:
                    continue

                vec = self.get_embedding(safe_text)
                if len(vec) == self.embedding_dim:
                    vectors.append(vec)
                    valid_texts.append(safe_text)
                    valid_metas.append(safe_meta)
                    hashes.append(c_hash)
            except Exception as e:
                logger.warning(f"数据处理失败: {e}")

        # 2. 分批次存入 Milvus
        if vectors:
            batch_size = 50
            for i in range(0, len(vectors), batch_size):
                b_vectors = vectors[i:i + batch_size]
                b_texts = valid_texts[i:i + batch_size]
                b_metas = valid_metas[i:i + batch_size]
                b_hashes = hashes[i:i + batch_size]
                
                try:
                    self.collection.insert([b_vectors, b_texts, b_metas, b_hashes])
                    logger.info(f"Milvus 写入成功: {len(b_vectors)} 条记录")
                except Exception as e:
                    logger.error(f"Milvus 写入失败: {e}")
            
            self.collection.flush()

    def delete_by_expr(self, expr: str):
        """根据表达式删除记录"""
        if not self.is_enabled or not self.collection:
            return
        try:
            self.collection.delete(expr)
            self.collection.flush()
            logger.info(f"Milvus 记录已删除: {expr}")
        except Exception as e:
            logger.error(f"Milvus 删除失败: {e}")

    def search(self, query_text: str, top_k: int = 5):
        if not self.is_enabled or not self.collection:
            return []
        
        # 确保 top_k 是整数类型
        try:
            top_k_int = int(top_k)
        except (ValueError, TypeError):
            logger.warning(f"top_k 参数 '{top_k}' 无法转换为整数，使用默认值 5")
            top_k_int = 5
        
        query_vec = self.get_embedding(query_text)
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}
        
        results = self.collection.search(
            data=[query_vec],
            anns_field="vector",
            param=search_params,
            limit=top_k_int,
            output_fields=["text", "metadata"]
        )
        
        hits = []
        for hit in results[0]:
            hits.append({
                "text": hit.entity.get("text"),
                "metadata": json.loads(hit.entity.get("metadata")),
                "distance": hit.distance
            })
        return hits










