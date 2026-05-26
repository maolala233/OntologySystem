import asyncio
import json
import xxhash
from typing import Dict, Any, List
from app.core.logging import logger
from app.infrastructure.es_client import ESClient
from app.infrastructure.embedding_client import EmbeddingClient

OWL_TYPE_MAP = {
    "owl:Class": "类",
    "owl:NamedIndividual": "实例",
    "owl:DatatypeProperty": "数据属性",
    "owl:ObjectProperty": "对象属性",
    "owl:ActionType": "动作类型",
    "owl:ActionInstance": "动作实例",
}


def _friendly_type(owl_type: str) -> str:
    return OWL_TYPE_MAP.get(owl_type, owl_type)


def _build_entity_description(ent_name: str, ent_type: str, props: Dict[str, Any]) -> str:
    parts = [f"{ent_name}是一个{_friendly_type(ent_type)}"]
    if props:
        prop_parts = []
        for k, v in props.items():
            if k in ("description", "_source_file", "_source_quote", "_source_chunk_index"):
                continue
            if v is None or v == "":
                continue
            prop_parts.append(f"{k}: {v}")
        if prop_parts:
            parts.append("，属性包括" + "，".join(prop_parts))
    return "".join(parts)


def _build_relation_description(from_ent: str, rel_label: str, to_ent: str) -> str:
    return f"{from_ent} {rel_label} {to_ent}"


class GraphInjectService:
    def __init__(self, es_host: str, es_port: int, es_user: str, es_password: str,
                 es_use_ssl: bool = False,
                 embedding_base_url: str = "http://localhost:11434/v1",
                 embedding_model: str = "bge-m3:latest",
                 embedding_api_key: str = "",
                 embedding_dim: int = 1024):
        self.es_client = ESClient(
            host=es_host, port=es_port, user=es_user, password=es_password,
            use_ssl=es_use_ssl, verify_certs=False
        )
        self.embedding_client = EmbeddingClient(
            base_url=embedding_base_url, model=embedding_model,
            api_key=embedding_api_key, dim=embedding_dim
        )
        self.logger = logger

    def generate_chunk_id(self, content: str, kb_id: str) -> str:
        hasher = xxhash.xxh64()
        hasher.update((content + kb_id).encode("utf-8"))
        return hasher.hexdigest()

    async def inject_graph(self, nodes: List[Dict[str, Any]],
                           edges: List[Dict[str, Any]],
                           kb_id: str, tenant_id: str,
                           doc_id: str) -> Dict[str, Any]:
        self.logger.info(f"开始注入图谱到ES: kb_id={kb_id}, tenant_id={tenant_id}, "
                         f"节点数={len(nodes)}, 边数={len(edges)}")

        index_name = self.es_client.get_index_name(tenant_id)

        if not self.es_client.index_exists(index_name):
            return {
                "success": False,
                "error": f"ES索引不存在: {index_name}。请确保知识库已创建且有文档被解析过",
            }

        entities, relationships = self._convert_graph_data(nodes, edges)

        result_stats = {
            "entities_created": 0,
            "entities_updated": 0,
            "entities_skipped": 0,
            "relations_created": 0,
            "relations_updated": 0,
            "relations_skipped": 0,
            "graph_created": False,
            "ty2ents_created": False,
            "errors": [],
        }

        self.logger.info("注入实体（生成embedding向量）...")
        entity_result = await self._inject_entities(index_name, entities, kb_id, doc_id)
        result_stats["entities_created"] = entity_result["created"]
        result_stats["entities_updated"] = entity_result["updated"]
        result_stats["entities_skipped"] = entity_result["skipped"]
        result_stats["errors"].extend(entity_result["errors"])

        self.logger.info("注入关系（生成embedding向量）...")
        rel_result = await self._inject_relationships(index_name, relationships, kb_id, doc_id)
        result_stats["relations_created"] = rel_result["created"]
        result_stats["relations_updated"] = rel_result["updated"]
        result_stats["relations_skipped"] = rel_result["skipped"]
        result_stats["errors"].extend(rel_result["errors"])

        self.logger.info("注入图谱结构...")
        graph_ok = await self._inject_graph_structure(index_name, entities, relationships, kb_id, doc_id)
        result_stats["graph_created"] = graph_ok
        if not graph_ok:
            result_stats["errors"].append("图谱结构注入失败")

        self.logger.info("注入实体类型映射...")
        ty2ents_ok = await self._inject_ty2ents(index_name, entities, kb_id)
        result_stats["ty2ents_created"] = ty2ents_ok
        if not ty2ents_ok:
            result_stats["errors"].append("实体类型映射注入失败")

        self.logger.info(f"图谱注入完成: 实体新增={result_stats['entities_created']}, "
                         f"实体更新={result_stats['entities_updated']}, "
                         f"实体跳过={result_stats['entities_skipped']}, "
                         f"关系新增={result_stats['relations_created']}, "
                         f"关系更新={result_stats['relations_updated']}, "
                         f"关系跳过={result_stats['relations_skipped']}")

        result_stats["success"] = True
        return result_stats

    def _convert_graph_data(self, nodes: List[Dict[str, Any]],
                            edges: List[Dict[str, Any]]) -> tuple:
        entities = []
        relationships = []
        node_id_to_label: Dict[str, str] = {}

        for node in nodes:
            data = node.get("data", {})
            label = data.get("label", node.get("id", ""))
            node_type = data.get("type", "Class")
            props = data.get("properties", {})
            desc = props.get("description", "") or data.get("description", "")

            node_id_to_label[node.get("id", "")] = label

            entities.append({
                "name": label,
                "type": node_type,
                "properties": props,
                "description": desc,
            })

        for edge in edges:
            data = edge.get("data", {})
            source_id = edge.get("source", "")
            target_id = edge.get("target", "")
            relation = data.get("relation", "")
            rel_label = data.get("label", relation or "相关")
            source_name = node_id_to_label.get(source_id, source_id)
            target_name = node_id_to_label.get(target_id, target_id)

            relationships.append({
                "source_name": source_name,
                "target_name": target_name,
                "relation": relation,
                "label": rel_label,
                "properties": data.get("properties", {}),
            })

        return entities, relationships

    async def _inject_entities(self, index_name: str, entities: List[Dict[str, Any]],
                               kb_id: str, doc_id: str) -> Dict[str, Any]:
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        embed_texts = []
        prepared = []
        for entity in entities:
            ent_name = entity.get("name", "")
            ent_type = entity.get("type", "Entity")
            ent_props = entity.get("properties", {})
            friendly_type = _friendly_type(ent_type)

            ent_desc = _build_entity_description(ent_name, ent_type, ent_props)
            if entity.get("description") and entity["description"] not in ent_desc:
                ent_desc = entity["description"]

            embed_text = f"{ent_name}: {ent_desc}"
            embed_texts.append(embed_text)
            prepared.append({
                "entity": entity,
                "ent_name": ent_name,
                "ent_type": ent_type,
                "ent_props": ent_props,
                "friendly_type": friendly_type,
                "ent_desc": ent_desc,
            })

        embeddings = await asyncio.gather(
            *[self.embedding_client.get_embedding(t) for t in embed_texts],
            return_exceptions=True
        )

        for i, item in enumerate(prepared):
            try:
                embedding = embeddings[i]
                if isinstance(embedding, Exception):
                    error_msg = f"实体embedding失败 {item['ent_name']}: {str(embedding)}"
                    self.logger.error(error_msg)
                    stats["errors"].append(error_msg)
                    stats["skipped"] += 1
                    continue

                if not embedding:
                    self.logger.warning(f"跳过实体 {item['ent_name']} - embedding生成失败")
                    stats["skipped"] += 1
                    continue

                entity_doc = {
                    "important_kwd": [item["ent_name"]],
                    "title_tks": item["ent_name"],
                    "entity_kwd": item["ent_name"],
                    "knowledge_graph_kwd": "entity",
                    "entity_type_kwd": item["friendly_type"],
                    "content_with_weight": json.dumps({
                        "entity_type": item["friendly_type"],
                        "description": item["ent_desc"],
                        "source_id": [doc_id],
                        "properties": item["ent_props"],
                    }, ensure_ascii=False),
                    "content_ltks": item["ent_desc"],
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
                    query_field="entity_kwd", query_value=item["ent_name"],
                    kb_id=kb_id, kg_type="entity"
                )

                if success:
                    stats["created"] += 1
                    self.logger.info(f"  新增实体: {item['ent_name']} [{item['friendly_type']}]")
                else:
                    stats["updated"] += 1
                    self.logger.info(f"  更新实体: {item['ent_name']} [{item['friendly_type']}]")

            except Exception as e:
                error_msg = f"实体注入失败 {item.get('ent_name', 'unknown')}: {str(e)}"
                self.logger.error(error_msg)
                stats["errors"].append(error_msg)

        return stats

    async def _inject_relationships(self, index_name: str, relationships: List[Dict[str, Any]],
                                    kb_id: str, doc_id: str) -> Dict[str, Any]:
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        embed_texts = []
        prepared = []
        for rel in relationships:
            from_ent = rel.get("source_name", "")
            to_ent = rel.get("target_name", "")
            rel_label = rel.get("label", "相关")
            rel_props = rel.get("properties", {})

            rel_desc = _build_relation_description(from_ent, rel_label, to_ent)
            rel_keywords = [rel_label] if rel_label else ["相关"]

            embed_text = f"{from_ent}与{to_ent}的关系: {rel_desc}"
            embed_texts.append(embed_text)
            prepared.append({
                "from_ent": from_ent,
                "to_ent": to_ent,
                "rel_label": rel_label,
                "rel_props": rel_props,
                "rel_desc": rel_desc,
                "rel_keywords": rel_keywords,
            })

        embeddings = await asyncio.gather(
            *[self.embedding_client.get_embedding(t) for t in embed_texts],
            return_exceptions=True
        )

        for i, item in enumerate(prepared):
            try:
                embedding = embeddings[i]
                if isinstance(embedding, Exception):
                    error_msg = f"关系embedding失败 {item['from_ent']} -> {item['to_ent']}: {str(embedding)}"
                    self.logger.error(error_msg)
                    stats["errors"].append(error_msg)
                    stats["skipped"] += 1
                    continue

                if not embedding:
                    self.logger.warning(f"跳过关系 {item['from_ent']} -> {item['to_ent']} - embedding生成失败")
                    stats["skipped"] += 1
                    continue

                relation_doc = {
                    "from_entity_kwd": item["from_ent"],
                    "to_entity_kwd": item["to_ent"],
                    "knowledge_graph_kwd": "relation",
                    "relation_type_kwd": item["rel_label"],
                    "content_with_weight": json.dumps({
                        "description": item["rel_desc"],
                        "keywords": item["rel_keywords"],
                        "source_id": [doc_id],
                        "weight": 1.0,
                        "properties": item["rel_props"],
                    }, ensure_ascii=False),
                    "content_ltks": item["rel_desc"],
                    "important_kwd": item["rel_keywords"],
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
                    query_fields=[("from_entity_kwd", item["from_ent"]), ("to_entity_kwd", item["to_ent"]), ("relation_type_kwd", item["rel_label"])],
                    kb_id=kb_id, kg_type="relation"
                )

                if success:
                    stats["created"] += 1
                    self.logger.info(f"  新增关系: {item['from_ent']} -[{item['rel_label']}]-> {item['to_ent']}")
                else:
                    stats["updated"] += 1
                    self.logger.info(f"  更新关系: {item['from_ent']} -[{item['rel_label']}]-> {item['to_ent']}")

            except Exception as e:
                error_msg = f"关系注入失败 {item.get('from_ent', '?')} -> {item.get('to_ent', '?')}: {str(e)}"
                self.logger.error(error_msg)
                stats["errors"].append(error_msg)

        return stats

    async def _inject_graph_structure(self, index_name: str, entities: List[Dict[str, Any]],
                                      relationships: List[Dict[str, Any]],
                                      kb_id: str, doc_id: str) -> bool:
        try:
            graph_nodes = []
            for ent in entities:
                ent_name = ent.get("name", "")
                ent_type = ent.get("type", "Entity")
                graph_nodes.append({
                    "id": ent_name,
                    "entity_type": _friendly_type(ent_type),
                })

            graph_edges = []
            for rel in relationships:
                from_ent = rel.get("source_name", "")
                to_ent = rel.get("target_name", "")
                if from_ent and to_ent:
                    graph_edges.append({
                        "source": from_ent,
                        "target": to_ent,
                        "weight": 1,
                    })

            graph_data = {
                "nodes": graph_nodes,
                "edges": graph_edges,
            }

            graph_doc = {
                "content_with_weight": json.dumps(graph_data, ensure_ascii=False),
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
                self.logger.info(f"  新增图谱结构: {len(graph_nodes)} 节点, "
                                 f"{len(graph_edges)} 边")
            else:
                self.logger.info(f"  更新图谱结构: {len(graph_nodes)} 节点, "
                                 f"{len(graph_edges)} 边")

            return True

        except Exception as e:
            self.logger.error(f"图谱结构注入失败: {e}")
            return False

    async def _inject_ty2ents(self, index_name: str, entities: List[Dict[str, Any]],
                              kb_id: str) -> bool:
        try:
            ty2ents: Dict[str, List[str]] = {}
            for ent in entities:
                ty = _friendly_type(ent.get("type", "Entity"))
                ent_name = ent.get("name", "")
                if ty not in ty2ents:
                    ty2ents[ty] = []
                ty2ents[ty].append(ent_name)

            ty2ents_doc = {
                "content_with_weight": json.dumps(ty2ents, ensure_ascii=False),
                "kb_id": kb_id,
                "knowledge_graph_kwd": "ty2ents",
                "available_int": 0,
                "removed_kwd": "N",
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

    async def _upsert_document(self, index_name: str, chunk_id: str,
                               document: Dict[str, Any],
                               query_field: str = None, query_value: str = None,
                               query_fields: List[tuple] = None,
                               kb_id: str = None, kg_type: str = None) -> bool:
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

            existing = self.es_client.search(index_name, query)

            if existing["hits"]["total"]["value"] > 0:
                existing_id = existing["hits"]["hits"][0]["_id"]
                self.es_client.update_document(index_name, existing_id, document)
                return False
            else:
                self.es_client.index_document(index_name, chunk_id, document)
                return True

        except Exception as e:
            self.logger.warning(f"查询现有文档失败: {e}，直接插入")
            self.es_client.index_document(index_name, chunk_id, document)
            return True

    def close(self):
        self.es_client.close()
