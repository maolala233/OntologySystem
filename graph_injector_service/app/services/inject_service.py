"""
ES注入服务
将构建的实体和关系注入到Elasticsearch中，支持RAGFlow的知识图谱格式
参考 project_ragflow_inject/test_upload_and_inject_graph.py
"""
import json
import logging
import xxhash
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.core.logging import logger
from app.core.config import settings
from app.core.exceptions import ESInjectionException
from app.infrastructure.es_client import es_client
from app.infrastructure.embedding_client import embedding_client
from app.infrastructure.ragflow_client import RAGFlowClient


class ESInjector:
    """
    ES注入服务
    将知识图谱的实体、关系和图结构注入到Elasticsearch中
    遵循RAGFlow的知识图谱数据格式
    """

    def __init__(self):
        self.logger = logging.getLogger("graph_injector.es_injector")

    def generate_chunk_id(self, content: str, kb_id: str) -> str:
        """
        生成chunk ID（使用xxhash）

        Args:
            content: 内容字符串
            kb_id: 知识库ID

        Returns:
            64位十六进制hash值
        """
        hasher = xxhash.xxh64()
        hasher.update((content + kb_id).encode("utf-8"))
        return hasher.hexdigest()

    async def inject_graph(self, entities: List[Dict[str, Any]],
                          relationships: List[Dict[str, Any]],
                          kb_id: str, tenant_id: str,
                          doc_id: str) -> Dict[str, Any]:
        """
        注入知识图谱数据到ES

        Args:
            entities: 实体列表
            relationships: 关系列表
            kb_id: 知识库ID
            tenant_id: 租户ID
            doc_id: 文档ID

        Returns:
            注入结果统计
        """
        self.logger.info(f"开始注入图谱到ES: kb_id={kb_id}, tenant_id={tenant_id}, "
                        f"实体数={len(entities)}, 关系数={len(relationships)}")

        index_name = es_client.get_index_name(tenant_id)

        if not es_client.index_exists(index_name):
            raise ESInjectionException(
                f"ES索引不存在: {index_name}。请确保知识库已创建且有文档被解析过"
            )

        result_stats = {
            "entities_created": 0,
            "entities_updated": 0,
            "relations_created": 0,
            "relations_updated": 0,
            "graph_created": False,
            "ty2ents_created": False,
            "errors": [],
        }

        self.logger.info("注入实体（生成embedding向量）...")
        entity_inject_results = await self._inject_entities(
            index_name, entities, kb_id, doc_id
        )
        result_stats["entities_created"] = entity_inject_results["created"]
        result_stats["entities_updated"] = entity_inject_results["updated"]
        result_stats["errors"].extend(entity_inject_results["errors"])

        self.logger.info("注入关系（生成embedding向量）...")
        relation_inject_results = await self._inject_relationships(
            index_name, relationships, kb_id, doc_id
        )
        result_stats["relations_created"] = relation_inject_results["created"]
        result_stats["relations_updated"] = relation_inject_results["updated"]
        result_stats["errors"].extend(relation_inject_results["errors"])

        self.logger.info("注入图谱结构...")
        graph_success = await self._inject_graph_structure(
            index_name, entities, relationships, kb_id, doc_id
        )
        result_stats["graph_created"] = graph_success
        if not graph_success:
            result_stats["errors"].append("图谱结构注入失败")

        self.logger.info("注入实体类型映射...")
        ty2ents_success = await self._inject_ty2ents(
            index_name, entities, kb_id
        )
        result_stats["ty2ents_created"] = ty2ents_success
        if not ty2ents_success:
            result_stats["errors"].append("实体类型映射注入失败")

        self.logger.info(f"图谱注入完成: 实体新增={result_stats['entities_created']}, "
                        f"实体更新={result_stats['entities_updated']}, "
                        f"关系新增={result_stats['relations_created']}, "
                        f"关系更新={result_stats['relations_updated']}")

        return result_stats

    async def _inject_entities(self, index_name: str, entities: List[Dict[str, Any]],
                              kb_id: str, doc_id: str) -> Dict[str, Any]:
        """
        注入实体到ES

        Args:
            index_name: ES索引名称
            entities: 实体列表
            kb_id: 知识库ID
            doc_id: 文档ID

        Returns:
            注入统计
        """
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        for entity in entities:
            try:
                ent_name = entity.get("name", entity.get("properties", {}).get("name", ""))
                ent_type = entity.get("type", "Entity")
                ent_props = entity.get("properties", {})
                ent_desc = ent_props.get("description", json.dumps(ent_props, ensure_ascii=False))

                embed_text = f"{ent_name}: {ent_desc}"
                embedding = await self._safe_get_embedding(embed_text)

                if not embedding:
                    self.logger.warning(f"跳过实体 {ent_name} - embedding生成失败")
                    stats["skipped"] += 1
                    continue

                entity_doc = {
                    "important_kwd": [ent_name],
                    "title_tks": ent_name,
                    "entity_kwd": ent_name,
                    "knowledge_graph_kwd": "entity",
                    "entity_type_kwd": ent_type,
                    "content_with_weight": json.dumps({
                        "entity_type": ent_type,
                        "description": ent_desc,
                        "source_id": [doc_id],
                        "properties": ent_props,
                    }, ensure_ascii=False),
                    "content_ltks": ent_desc,
                    "source_id": [doc_id],
                    "kb_id": kb_id,
                    "available_int": 1,
                    "q_1024_vec": embedding,
                }

                chunk_id = self.generate_chunk_id(
                    json.dumps(entity_doc["content_with_weight"]), kb_id
                )

                success = await self._upsert_document(
                    index_name, chunk_id, entity_doc,
                    query_field="entity_kwd", query_value=ent_name, kb_id=kb_id,
                    kg_type="entity"
                )

                if success:
                    stats["created"] += 1
                    self.logger.info(f"  新增实体: {ent_name} [{ent_type}]")
                else:
                    stats["updated"] += 1
                    self.logger.info(f"  更新实体: {ent_name} [{ent_type}]")

            except Exception as e:
                error_msg = f"实体注入失败 {entity.get('name', 'unknown')}: {str(e)}"
                self.logger.error(error_msg)
                stats["errors"].append(error_msg)

        return stats

    async def _inject_relationships(self, index_name: str, relationships: List[Dict[str, Any]],
                                   kb_id: str, doc_id: str) -> Dict[str, Any]:
        """
        注入关系到ES

        Args:
            index_name: ES索引名称
            relationships: 关系列表
            kb_id: 知识库ID
            doc_id: 文档ID

        Returns:
            注入统计
        """
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        for rel in relationships:
            try:
                from_ent = rel.get("source_name", "")
                to_ent = rel.get("target_name", "")
                rel_type = rel.get("type", "related_to")
                rel_props = rel.get("properties", {})
                rel_desc = rel_props.get("description", f"{from_ent} {rel_type} {to_ent}")
                rel_keywords = rel_props.get("keywords", [rel_type])

                embed_text = f"{from_ent}与{to_ent}的关系: {rel_desc}"
                embedding = await self._safe_get_embedding(embed_text)

                if not embedding:
                    self.logger.warning(f"跳过关系 {from_ent} -> {to_ent} - embedding生成失败")
                    stats["skipped"] += 1
                    continue

                relation_doc = {
                    "from_entity_kwd": from_ent,
                    "to_entity_kwd": to_ent,
                    "knowledge_graph_kwd": "relation",
                    "relation_type_kwd": rel_type,
                    "content_with_weight": json.dumps({
                        "description": rel_desc,
                        "keywords": rel_keywords,
                        "source_id": [doc_id],
                        "weight": 1.0,
                        "properties": rel_props,
                    }, ensure_ascii=False),
                    "content_ltks": rel_desc,
                    "important_kwd": rel_keywords,
                    "source_id": [doc_id],
                    "weight_int": 1,
                    "kb_id": kb_id,
                    "available_int": 1,
                    "q_1024_vec": embedding,
                }

                chunk_id = self.generate_chunk_id(
                    json.dumps(relation_doc["content_with_weight"]), kb_id
                )

                success = await self._upsert_document(
                    index_name, chunk_id, relation_doc,
                    query_fields=[("from_entity_kwd", from_ent), ("to_entity_kwd", to_ent)],
                    kb_id=kb_id, kg_type="relation"
                )

                if success:
                    stats["created"] += 1
                    self.logger.info(f"  新增关系: {from_ent} -> {to_ent}")
                else:
                    stats["updated"] += 1
                    self.logger.info(f"  更新关系: {from_ent} -> {to_ent}")

            except Exception as e:
                error_msg = f"关系注入失败 {rel.get('source_name', '?')} -> {rel.get('target_name', '?')}: {str(e)}"
                self.logger.error(error_msg)
                stats["errors"].append(error_msg)

        return stats

    async def _inject_graph_structure(self, index_name: str, entities: List[Dict[str, Any]],
                                     relationships: List[Dict[str, Any]],
                                     kb_id: str, doc_id: str) -> bool:
        """
        注入图谱整体结构到ES

        Args:
            index_name: ES索引名称
            entities: 实体列表
            relationships: 关系列表
            kb_id: 知识库ID
            doc_id: 文档ID

        Returns:
            是否成功
        """
        try:
            import networkx as nx

            graph = nx.Graph()
            for ent in entities:
                ent_name = ent.get("name", ent.get("properties", {}).get("name", ""))
                ent_type = ent.get("type", "Entity")
                graph.add_node(ent_name, entity_type=ent_type)

            for rel in relationships:
                from_ent = rel.get("source_name", "")
                to_ent = rel.get("target_name", "")
                if from_ent and to_ent:
                    pair = sorted([from_ent, to_ent])
                    graph.add_edge(pair[0], pair[1], weight=1, relation_type=rel.get("type", ""))

            graph_data = nx.node_link_data(graph, edges="edges")

            graph_doc = {
                "content_with_weight": json.dumps(graph_data, ensure_ascii=False, indent=2),
                "knowledge_graph_kwd": "graph",
                "kb_id": kb_id,
                "source_id": [doc_id],
                "available_int": 0,
                "removed_kwd": "N",
            }

            chunk_id = self.generate_chunk_id(graph_doc["content_with_weight"], kb_id)

            success = await self._upsert_document(
                index_name, chunk_id, graph_doc,
                query_field="knowledge_graph_kwd", query_value="graph",
                kb_id=kb_id, kg_type="graph"
            )

            if success:
                self.logger.info(f"  新增图谱结构: {graph.number_of_nodes()} 节点, "
                               f"{graph.number_of_edges()} 边")
            else:
                self.logger.info(f"  更新图谱结构: {graph.number_of_nodes()} 节点, "
                               f"{graph.number_of_edges()} 边")

            return True

        except ImportError:
            self.logger.error("networkx未安装，跳过图谱结构注入")
            return False
        except Exception as e:
            self.logger.error(f"图谱结构注入失败: {e}")
            return False

    async def _inject_ty2ents(self, index_name: str, entities: List[Dict[str, Any]],
                              kb_id: str) -> bool:
        """
        注入实体类型映射(ty2ents)到ES

        Args:
            index_name: ES索引名称
            entities: 实体列表
            kb_id: 知识库ID

        Returns:
            是否成功
        """
        try:
            ty2ents: Dict[str, List[str]] = {}
            for ent in entities:
                ty = ent.get("type", "Entity")
                ent_name = ent.get("name", ent.get("properties", {}).get("name", ""))
                if ty not in ty2ents:
                    ty2ents[ty] = []
                ty2ents[ty].append(ent_name)

            ty2ents_doc = {
                "content_with_weight": json.dumps(ty2ents, ensure_ascii=False),
                "kb_id": kb_id,
                "knowledge_graph_kwd": "ty2ents",
                "available_int": 0,
            }

            chunk_id = self.generate_chunk_id(ty2ents_doc["content_with_weight"], kb_id)

            success = await self._upsert_document(
                index_name, chunk_id, ty2ents_doc,
                query_field="knowledge_graph_kwd", query_value="ty2ents",
                kb_id=kb_id, kg_type="ty2ents"
            )

            if success:
                self.logger.info(f"  新增实体类型映射: {len(ty2ents)} 个类型")
            else:
                self.logger.info(f"  更新实体类型映射: {len(ty2ents)} 个类型")

            return True

        except Exception as e:
            self.logger.error(f"实体类型映射注入失败: {e}")
            return False

    async def _upsert_document(self, index_name: str, chunk_id: str, document: Dict[str, Any],
                               query_field: str = None, query_value: str = None,
                               query_fields: List[tuple] = None,
                               kb_id: str = None, kg_type: str = None) -> bool:
        """
        更新或插入文档

        Args:
            index_name: ES索引名称
            chunk_id: 文档ID
            document: 文档内容
            query_field: 查询字段名(单个)
            query_value: 查询值(单个)
            query_fields: 查询字段列表[(字段名, 值)]
            kb_id: 知识库ID
            kg_type: 知识图谱类型

        Returns:
            True表示新增，False表示更新
        """
        try:
            must_clauses = []

            if query_field and query_value:
                must_clauses.append({"term": {query_field: query_value}})
            elif query_fields:
                for field_name, field_value in query_fields:
                    must_clauses.append({"term": {field_name: field_value}})

            if kb_id:
                must_clauses.append({"term": {"kb_id": kb_id}})
            if kg_type:
                must_clauses.append({"term": {"knowledge_graph_kwd": kg_type}})

            query = {
                "query": {
                    "bool": {"must": must_clauses}
                },
                "size": 1,
            }

            existing = es_client.search(index_name, query)

            if existing["hits"]["total"]["value"] > 0:
                existing_id = existing["hits"]["hits"][0]["_id"]
                es_client.update_document(index_name, existing_id, document)
                return False
            else:
                es_client.index_document(index_name, chunk_id, document)
                return True

        except Exception as e:
            self.logger.warning(f"查询现有文档失败: {e}，直接插入")
            es_client.index_document(index_name, chunk_id, document)
            return True

    async def _safe_get_embedding(self, text: str) -> List[float]:
        """
        安全获取embedding，失败时返回空列表

        Args:
            text: 输入文本

        Returns:
            embedding向量
        """
        try:
            return await embedding_client.get_embedding(text)
        except Exception as e:
            self.logger.warning(f"Embedding获取失败: {e}")
            return []


class RAGFlowGraphInjector:
    """
    RAGFlow图谱注入协调器
    协调文档上传、解析和图谱注入的完整流程
    """

    def __init__(self):
        self.logger = logging.getLogger("graph_injector.ragflow_injector")
        self.es_injector = ESInjector()

    async def upload_and_inject(self, text_contents: List[str], kb_id: str, tenant_id: str,
                               api_key: str, doc_id: str,
                               entities: List[Dict[str, Any]],
                               relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        上传文档并注入图谱

        Args:
            text_contents: 文档内容列表
            kb_id: 知识库ID
            tenant_id: 租户ID
            api_key: RAGFlow API Key
            doc_id: 文档ID
            entities: 实体列表
            relationships: 关系列表

        Returns:
            注入结果
        """
        ragflow_client = RAGFlowClient(api_key)
        result = {
            "doc_id": doc_id,
            "parse_triggered": False,
            "injection": {},
        }

        self.logger.info(f"上传并注入图谱: kb_id={kb_id}, 文档数={len(text_contents)}")

        result["injection"] = await self.es_injector.inject_graph(
            entities, relationships, kb_id, tenant_id, doc_id
        )

        return result
