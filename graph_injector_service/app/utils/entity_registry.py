"""
实体注册与关系解析工具
用于跨文档块的实体去重和关系解析
"""
import uuid
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ExtractedEntity:
    """表示一个提取的实体"""
    temp_id: str
    entity_type: str
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    source_location: Optional[str] = None
    chunk_id: Optional[int] = None


@dataclass
class ExtractedRelationship:
    """表示一个提取的关系"""
    temp_id: str
    relationship_type: str
    source_temp_id: str
    target_temp_id: str
    properties: Dict[str, Any] = field(default_factory=dict)
    source_location: Optional[str] = None
    chunk_id: Optional[int] = None


class EntityRegistry:
    """
    实体注册表
    基于实体类型+归一化名称的组合实现GUID去重
    """

    def __init__(self):
        self.entities: Dict[Tuple[str, str], str] = {}
        self.ai_id_mapping: Dict[str, str] = {}
        self.entity_details: Dict[str, ExtractedEntity] = {}
        self.logger = logging.getLogger("graph_injector.entity_registry")

    def _normalize_name(self, name: str) -> str:
        """
        归一化实体名称，用于去重
        """
        if not name:
            return name
        normalized = name.strip()
        normalized = normalized.title()
        if len(normalized) <= 4 and normalized.isalpha():
            normalized = normalized.upper()
        return normalized

    def register_entity(self, entity: ExtractedEntity) -> str:
        """
        注册实体并返回其GUID
        如果实体已存在(类型+归一化名称相同)，则返回现有GUID
        """
        if not entity.name:
            raise ValueError(f"实体 {entity.temp_id} 缺少必填属性 'name'")

        normalized_name = self._normalize_name(entity.name)
        entity_key = (entity.entity_type, normalized_name)

        if entity_key in self.entities:
            existing_guid = self.entities[entity_key]
            temp_key = f"chunk_{entity.chunk_id}_{entity.temp_id}"
            self.ai_id_mapping[temp_key] = existing_guid
            self.logger.debug(f"复用已有实体 {existing_guid} for {entity_key}")
            return existing_guid
        else:
            new_guid = str(uuid.uuid4())
            self.entities[entity_key] = new_guid
            temp_key = f"chunk_{entity.chunk_id}_{entity.temp_id}"
            self.ai_id_mapping[temp_key] = new_guid

            entity.name = normalized_name
            entity.properties["name"] = normalized_name
            entity.temp_id = new_guid
            self.entity_details[new_guid] = entity

            self.logger.debug(f"创建新实体 {new_guid} for {entity_key}")
            return new_guid

    def resolve_temp_id(self, chunk_id: int, temp_id: str) -> Optional[str]:
        """将临时ID解析为最终GUID"""
        temp_key = f"chunk_{chunk_id}_{temp_id}"
        return self.ai_id_mapping.get(temp_key)

    def _resolve_temp_id_any_chunk(self, temp_id: str) -> Optional[str]:
        """跨所有块解析临时ID"""
        for temp_key, guid in self.ai_id_mapping.items():
            if temp_key.endswith(f"_{temp_id}"):
                return guid
        return None

    def get_all_entities(self) -> List[Dict[str, Any]]:
        """获取所有实体(可序列化为JSON)"""
        entities = []
        for guid, entity in self.entity_details.items():
            entities.append({
                "id": guid,
                "type": entity.entity_type,
                "name": entity.name,
                "properties": entity.properties,
                "source_location": entity.source_location,
            })
        return entities

    def get_entity_count(self) -> int:
        return len(self.entity_details)

    def get_deduplication_stats(self) -> Dict[str, Any]:
        total = len(self.ai_id_mapping)
        unique = len(self.entity_details)
        return {
            "total_extracted": total,
            "unique_entities": unique,
            "duplicates_removed": total - unique,
            "deduplication_rate": (total - unique) / total if total > 0 else 0,
        }


class RelationshipResolver:
    """
    关系解析器
    使用EntityRegistry将临时ID映射为GUID
    """

    def __init__(self, entity_registry: EntityRegistry):
        self.entity_registry = entity_registry
        self.resolved_relationships: List[Dict[str, Any]] = []
        self.orphaned_relationships: List[ExtractedRelationship] = []
        self.logger = logging.getLogger("graph_injector.relationship_resolver")

    def resolve_relationships(self, all_relationships: List[ExtractedRelationship]) -> List[Dict[str, Any]]:
        """解析所有关系"""
        resolved = []
        orphaned = []

        for rel in all_relationships:
            source_guid = self.entity_registry._resolve_temp_id_any_chunk(rel.source_temp_id)
            target_guid = self.entity_registry._resolve_temp_id_any_chunk(rel.target_temp_id)

            if source_guid and target_guid:
                resolved_rel = {
                    "id": str(uuid.uuid4()),
                    "type": rel.relationship_type,
                    "source_id": source_guid,
                    "target_id": target_guid,
                    "source_name": self.entity_registry.entity_details.get(source_guid, ExtractedEntity("", "", "")).name,
                    "target_name": self.entity_registry.entity_details.get(target_guid, ExtractedEntity("", "", "")).name,
                    "properties": rel.properties,
                    "source_location": rel.source_location,
                }
                resolved.append(resolved_rel)
            else:
                orphaned.append(rel)
                self.logger.warning(f"孤立关系 {rel.temp_id}: source={rel.source_temp_id}, target={rel.target_temp_id}")

        self.resolved_relationships = resolved
        self.orphaned_relationships = orphaned
        self.logger.info(f"关系解析完成: 成功={len(resolved)}, 孤立={len(orphaned)}")
        return resolved

    def get_relationship_stats(self) -> Dict[str, Any]:
        total = len(self.resolved_relationships) + len(self.orphaned_relationships)
        return {
            "total_relationships": total,
            "resolved": len(self.resolved_relationships),
            "orphaned": len(self.orphaned_relationships),
            "resolution_rate": len(self.resolved_relationships) / total if total > 0 else 0,
        }


class EnhancedExtractionProcessor:
    """
    增强提取处理器
    协调EntityRegistry和RelationshipResolver完成实体去重和关系解析
    """

    def __init__(self):
        self.entity_registry = EntityRegistry()
        self.relationship_resolver = RelationshipResolver(self.entity_registry)
        self.all_relationships: List[ExtractedRelationship] = []
        self.logger = logging.getLogger("graph_injector.extraction_processor")

    def process_chunk_results(self, chunk_id: int, chunk_result: Dict[str, Any]) -> None:
        """
        处理单个chunk的提取结果

        Args:
            chunk_id: chunk索引
            chunk_result: 包含nodes和relationships的字典
        """
        nodes = chunk_result.get("nodes", [])
        for node_data in nodes:
            if "name" not in node_data.get("properties", {}):
                self.logger.warning(f"节点 {node_data.get('id')} 缺少 'name' 属性，跳过")
                continue

            entity = ExtractedEntity(
                temp_id=node_data["id"],
                entity_type=node_data.get("type", "Entity"),
                name=node_data["properties"]["name"],
                properties=node_data.get("properties", {}),
                source_location=node_data.get("source_location"),
                chunk_id=chunk_id,
            )
            self.entity_registry.register_entity(entity)

        relationships = chunk_result.get("relationships", [])
        for rel_data in relationships:
            relationship = ExtractedRelationship(
                temp_id=rel_data.get("id", str(uuid.uuid4())),
                relationship_type=rel_data.get("type", "related_to"),
                source_temp_id=rel_data.get("source_id", ""),
                target_temp_id=rel_data.get("target_id", ""),
                properties=rel_data.get("properties", {}),
                source_location=rel_data.get("source_location"),
                chunk_id=chunk_id,
            )
            self.all_relationships.append(relationship)

        self.logger.info(f"Chunk {chunk_id} 处理完成: "
                        f"实体注册={len(nodes)}, 关系暂存={len(relationships)}")

    def finalize_extraction(self) -> Dict[str, Any]:
        """
        完成所有提取，解析关系并返回最终结果

        Returns:
            包含nodes、relationships和metadata的字典
        """
        resolved_relationships = self.relationship_resolver.resolve_relationships(self.all_relationships)
        final_entities = self.entity_registry.get_all_entities()

        entity_stats = self.entity_registry.get_deduplication_stats()
        relationship_stats = self.relationship_resolver.get_relationship_stats()

        self.logger.info(f"提取完成: 唯一实体={len(final_entities)}, "
                        f"已解析关系={len(resolved_relationships)}")

        return {
            "nodes": final_entities,
            "relationships": resolved_relationships,
            "metadata": {
                "extraction_mode": "enhanced",
                "entity_stats": entity_stats,
                "relationship_stats": relationship_stats,
                "total_unique_entities": len(final_entities),
                "total_resolved_relationships": len(resolved_relationships),
            }
        }


def validate_name_properties(nodes: List[Dict[str, Any]]) -> List[str]:
    """验证所有节点是否具有必填的'name'属性"""
    errors = []
    for i, node in enumerate(nodes):
        node_id = node.get("id", f"node_{i}")
        properties = node.get("properties", {})
        if "name" not in properties:
            errors.append(f"节点 {node_id} (类型: {node.get('type')}) 缺少必填属性 'name'")
        elif not properties["name"]:
            errors.append(f"节点 {node_id} 的 'name' 属性为空")
    return errors
