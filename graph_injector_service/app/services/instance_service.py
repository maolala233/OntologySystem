"""
Ontology实例构建服务
基于Palantir Ontology Schema从文档中提取Object实例/Link实例/Action实例
支持完整的图谱关系构建和溯源信息记录
领域无关设计，适用于任何文档类型
"""
import logging
import asyncio
import uuid
from typing import Dict, Any, List, Optional
from app.core.exceptions import InstanceBuildException
from app.infrastructure.llm_client import llm_client, LLMCallException
from app.utils.chunker import chunk_text

ONTOLOGY_INSTANCE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "object_type": {"type": "string", "description": "对象类型名称，必须匹配Schema中定义的object_type"},
                    "properties": {
                        "type": "object",
                        "description": "对象实例的属性值，键为属性名，值为属性值"
                    },
                    "source_location": {
                        "type": "string",
                        "description": "该对象实例在文档中的来源位置（如页码、段落、章节等）"
                    }
                },
                "required": ["object_type", "properties"]
            }
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "link_type": {"type": "string", "description": "链接类型名称，必须匹配Schema中定义的link_type"},
                    "source_object_name": {"type": "string", "description": "源对象的标识名称"},
                    "source_object_type": {"type": "string", "description": "源对象的类型"},
                    "target_object_name": {"type": "string", "description": "目标对象的标识名称"},
                    "target_object_type": {"type": "string", "description": "目标对象的类型"},
                    "properties": {
                        "type": "object",
                        "description": "链接的属性（可选）"
                    },
                    "source_location": {
                        "type": "string",
                        "description": "该链接关系在文档中的来源位置"
                    }
                },
                "required": ["link_type", "source_object_name", "source_object_type", "target_object_name", "target_object_type"]
            }
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string", "description": "动作类型名称，必须匹配Schema中定义的action_type"},
                    "target_object_name": {"type": "string", "description": "动作作用的对象实例标识名称"},
                    "target_object_type": {"type": "string", "description": "动作作用的对象类型"},
                    "parameters": {
                        "type": "object",
                        "description": "动作参数的值"
                    },
                    "source_location": {
                        "type": "string",
                        "description": "该动作在文档中的来源位置"
                    }
                },
                "required": ["action_type", "target_object_name", "target_object_type"]
            }
        }
    },
    "required": ["objects", "links"]
}

class InstanceBuilder:

    SYSTEM_PROMPT = """你是一个专业的本体实例提取专家。严格按照Ontology Schema定义从文档中提取对象实例、链接实例和动作实例。

=== 核心规则 ===
1. 只能使用下面列出的Object Types和Link Types
2. 每个对象实例必须包含该对象类型的标识属性（标注了"标识属性"的那个属性），其值用于唯一标识该实例
3. 链接的source_object_name和target_object_name必须与同chunk中提取的对象的标识属性值完全一致
4. 链接方向必须严格按照定义: [源对象类型] -> [目标对象类型]
5. 记录每个对象和链接在文档中的来源位置（如页码、章节、段落等）
6. 必须提取多种类型的对象实例，不要只提取一种类型
7. 所有属性值必须使用文档原文语言（中文文档用中文）
8. 属性名使用snake_case命名风格
9. 不要遗漏文档中的数值型信息（如费率、比例、金额、期限等），这些信息对查询非常重要
10. 如果文档中提到了对某个对象可以执行的操作（如购买、赎回、调整等），也要提取为Action实例

=== 输出JSON格式 ===
{{
  "objects": [
    {{"object_type": "XXX", "properties": {{"标识属性名": "实例名称", "其他属性": "值"}}, "source_location": "第X页"}},
  ],
  "links": [
    {{"link_type": "XXX", "source_object_name": "源对象标识值", "source_object_type": "SourceType", "target_object_name": "目标对象标识值", "target_object_type": "TargetType", "properties": {{}}, "source_location": "第X页"}},
  ],
  "actions": [
    {{"action_type": "XXX", "target_object_name": "作用对象标识值", "target_object_type": "TargetType", "parameters": {{"param_name": "param_value"}}, "source_location": "第X页"}}
  ]
}}

=== 重要 ===
1. 链接中source_object_name/target_object_name必须与对应对象的标识属性值完全一致
2. 尽可能从文档中提取更多类型的对象和关系
3. 数值型属性（费率、比例、金额等）必须提取，不能遗漏

{ontology_description}

只输出JSON，不要任何解释。"""

    def __init__(self):
        self.logger = logging.getLogger("graph_injector.instance_builder")

    async def build_instances(self, text_content: str, ontology: Dict[str, Any],
                             chunk_size: int = 8000, overlap_percentage: int = 15,
                             additional_instructions: Optional[str] = None) -> Dict[str, Any]:
        object_types = ontology.get('object_types', ontology.get('entity_types', []))
        link_types = ontology.get('link_types', ontology.get('relation_types', []))
        action_types = ontology.get('action_types', [])
        
        self.logger.info(f"开始构建Ontology实例，文本长度: {len(text_content)}, "
                        f"对象类型数: {len(object_types)}, 链接类型数: {len(link_types)}")

        chunks = chunk_text(text_content, chunk_size, overlap_percentage)

        if not chunks:
            raise InstanceBuildException("无法从空文本中提取实例")

        self.logger.info(f"文本被分为 {len(chunks)} 个chunks")

        ontology_description = self._build_ontology_description(object_types, link_types, action_types)

        all_objects: List[Dict[str, Any]] = []
        all_links: List[Dict[str, Any]] = []
        all_actions: List[Dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        
        for i, chunk in enumerate(chunks):
            self.logger.info(f"从chunk {i+1}/{len(chunks)} 提取Ontology实例")

            try:
                chunk_result = await self._extract_from_chunk(
                    chunk["text"], ontology_description, additional_instructions
                )
                if chunk_result:
                    chunk_objects = chunk_result.get("objects", [])
                    chunk_links = chunk_result.get("links", chunk_result.get("relationships", []))
                    chunk_actions = chunk_result.get("actions", [])
                    
                    for obj in chunk_objects:
                        obj["source_location"] = obj.get("source_location") or f"chunk_{i+1}"
                        all_objects.append(obj)
                    
                    for link in chunk_links:
                        link["source_location"] = link.get("source_location") or f"chunk_{i+1}"
                        all_links.append(link)
                    
                    for action in chunk_actions:
                        action["source_location"] = action.get("source_location") or f"chunk_{i+1}"
                        all_actions.append(action)
                    
                    success_count += 1
                    self.logger.info(f"Chunk {i+1} 提取成功: {len(chunk_objects)}个对象, {len(chunk_links)}个链接, {len(chunk_actions)}个动作")
                else:
                    failure_count += 1
                    self.logger.warning(f"Chunk {i+1} 返回空结果")
            except LLMCallException as e:
                failure_count += 1
                self.logger.warning(f"Chunk {i+1} LLM调用失败: {e.message}")
                await asyncio.sleep(1)
            except Exception as e:
                failure_count += 1
                self.logger.warning(f"Chunk {i+1} 实例提取失败: {e}", exc_info=True)
                await asyncio.sleep(0.5)

        nodes, edges, action_instances = self._build_graph(all_objects, all_links, all_actions, object_types, link_types)

        final_result = {
            "nodes": nodes,
            "edges": edges,
            "action_instances": action_instances,
            "relationships": edges,
            "metadata": {
                "chunk_stats": {
                    "total_chunks": len(chunks),
                    "successful_chunks": success_count,
                    "failed_chunks": failure_count,
                    "success_rate": success_count / len(chunks) if chunks else 0
                },
                "ontology_stats": {
                    "object_types": len(object_types),
                    "link_types": len(link_types),
                    "action_types": len(action_types),
                    "total_objects": len(nodes),
                    "total_links": len(edges),
                    "total_actions": len(action_instances)
                }
            }
        }
        
        self.logger.info(f"Ontology实例构建完成: 对象={len(nodes)}, 链接={len(edges)}, 动作={len(action_instances)}")
        return final_result

    def _build_ontology_description(self, object_types: List[Dict], link_types: List[Dict], action_types: List[Dict]) -> str:
        """构建Ontology Schema的可读描述（精简版，减少LLM token消耗）"""
        description = "=== 对象类型 ===\n"
        for ot in object_types:
            name = ot.get('name', '')
            props = ot.get('properties', ot.get('attributes', []))
            pk = ot.get('primary_key', '')
            prop_names = [p.get('name', '') for p in props]
            
            name_prop = self._find_name_prop(name, props, pk)
            
            description += f"- {name}"
            if name_prop:
                description += f"（标识属性: {name_prop}）"
            description += f": {', '.join(prop_names)}\n"

        description += "\n=== 链接类型 ===\n"
        for lt in link_types:
            name = lt.get('name', '')
            source = lt.get('source_object_type', lt.get('source_types', []))
            target = lt.get('target_object_type', lt.get('target_types', []))
            if isinstance(source, list):
                source = ", ".join(source)
            if isinstance(target, list):
                target = ", ".join(target)
            description += f"- {name}: [{source}] -> [{target}]\n"

        if action_types:
            description += "\n=== 动作类型 ===\n"
            for at in action_types:
                name = at.get('name', '')
                target = at.get('target_object_type', '')
                params = at.get('parameters', [])
                param_names = [p.get('name', '') for p in params]
                description += f"- {name}: 作用于[{target}]"
                if param_names:
                    description += f", 参数: {', '.join(param_names)}"
                description += "\n"

        return description

    @staticmethod
    def _find_name_prop(type_name: str, props: List[Dict], pk: str) -> str:
        """智能识别对象类型的标识名称属性"""
        prop_names = [p.get('name', '') for p in props]
        
        for p in props:
            pn = p.get('name', '')
            if pn in ('name', f'{type_name.lower()}_name', 'product_name', 'cooperator_name',
                      'asset_name', 'category_name', 'risk_name', 'type_name',
                      'org_name', 'institution_name', 'investor_name', 'channel_name',
                      'fee_name', 'document_name', 'contract_name', 'policy_name',
                      'event_name', 'instrument_name', 'manager_name', 'regulator_name',
                      'agent_name', 'target_name'):
                return pn
        
        if pk and pk in prop_names:
            return pk
        
        for pn in prop_names:
            if 'name' in pn.lower() or '名称' in pn or '标题' in pn:
                return pn
        
        if prop_names:
            return prop_names[0]
        
        return ''

    async def _extract_from_chunk(self, chunk_text: str, ontology_description: str,
                                  additional_instructions: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """从单个chunk提取Ontology实例"""
        system_prompt = self.SYSTEM_PROMPT.format(ontology_description=ontology_description)

        user_prompt = f"请从以下文档中提取Ontology对象实例、链接实例和动作实例:\n\n{chunk_text}"

        if additional_instructions:
            user_prompt += f"\n\n额外指令:\n{additional_instructions}"

        user_prompt += "\n\n输出JSON格式(objects、links和actions三个列表)。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await llm_client.extract_json(messages, temperature=0.1, json_schema=ONTOLOGY_INSTANCE_JSON_SCHEMA, max_retries=2)
        except LLMCallException as e:
            self.logger.warning(f"LLM调用最终失败: {e.message[:200]}")
            return {"objects": [], "links": [], "actions": []}

        if "objects" not in result:
            result["objects"] = result.get("nodes", [])
        if "links" not in result:
            result["links"] = result.get("relationships", [])
        if "actions" not in result:
            result["actions"] = []

        return result

    def _build_graph(self, all_objects: List[Dict], all_links: List[Dict], all_actions: List[Dict],
                     object_types: List[Dict], link_types: List[Dict]) -> tuple:
        """
        构建完整的知识图谱，包含节点、边和动作实例
        
        将对象实例和链接实例转换为图谱的nodes和edges格式
        使用模糊匹配策略关联链接的source/target对象
        """
        valid_object_type_names = {ot.get('name', '') for ot in object_types}

        object_type_pk_map: Dict[str, str] = {}
        object_type_name_prop_map: Dict[str, str] = {}
        for ot in object_types:
            ot_name = ot.get('name', '')
            props = ot.get('properties', [])
            pk = ot.get('primary_key', '')
            if pk:
                object_type_pk_map[ot_name] = pk
            name_prop = self._find_name_prop(ot_name, props, pk)
            if name_prop:
                object_type_name_prop_map[ot_name] = name_prop

        link_type_map = {}
        for lt in link_types:
            link_type_map[lt.get('name', '')] = {
                'source_object_type': lt.get('source_object_type', ''),
                'target_object_type': lt.get('target_object_type', ''),
            }

        def extract_object_name(obj_type: str, props: Dict) -> str:
            """从对象属性中智能提取名称标识"""
            name_prop = object_type_name_prop_map.get(obj_type, '')
            if name_prop and name_prop in props and props[name_prop]:
                return str(props[name_prop])
            
            pk = object_type_pk_map.get(obj_type, '')
            if pk and pk in props and props[pk]:
                return str(props[pk])
            
            for key, value in props.items():
                if value and isinstance(value, str) and len(value) > 2:
                    if 'name' in key.lower() or '名称' in key:
                        return str(value)
            
            for key, value in props.items():
                if value and isinstance(value, str) and len(value) > 1:
                    return str(value)
            
            return ''

        object_registry: Dict[str, Dict[str, Any]] = {}
        
        for obj in all_objects:
            obj_type = obj.get('object_type', obj.get('type', ''))
            props = obj.get('properties', {})
            
            if obj_type not in valid_object_type_names:
                continue
            
            name = extract_object_name(obj_type, props)
            if not name:
                continue
            
            obj_key = f"{obj_type}::{name}"
            if obj_key not in object_registry:
                object_registry[obj_key] = {
                    "id": str(uuid.uuid4()),
                    "type": obj_type,
                    "name": name,
                    "properties": props,
                    "source_location": obj.get('source_location')
                }

        valid_node_ids = set()
        node_id_map: Dict[str, str] = {}
        node_type_map: Dict[str, str] = {}
        node_name_map: Dict[str, str] = {}
        
        nodes = []
        for obj_key, node in object_registry.items():
            nodes.append(node)
            valid_node_ids.add(node["id"])
            node_id_map[obj_key] = node["id"]
            node_type_map[node["id"]] = node["type"]
            node_name_map[node["name"]] = node["id"]

        def find_node_id(obj_type: str, obj_name: str) -> str:
            """查找节点ID，支持精确匹配和模糊匹配"""
            exact_key = f"{obj_type}::{obj_name}"
            if exact_key in node_id_map:
                return node_id_map[exact_key]
            
            if obj_name in node_name_map:
                return node_name_map[obj_name]
            
            for key, nid in node_id_map.items():
                if key.startswith(f"{obj_type}::"):
                    registered_name = key.split("::", 1)[1]
                    if obj_name in registered_name or registered_name in obj_name:
                        return nid
            
            obj_name_clean = obj_name.replace(" ", "").strip()
            for key, nid in node_id_map.items():
                if key.startswith(f"{obj_type}::"):
                    registered_name = key.split("::", 1)[1].replace(" ", "").strip()
                    if obj_name_clean == registered_name:
                        return nid
            
            return ''

        edges = []
        for link in all_links:
            link_type = link.get('link_type', link.get('type', ''))
            
            if link_type not in link_type_map:
                continue
            
            source_name = link.get('source_object_name', '')
            source_type = link.get('source_object_type', '')
            target_name = link.get('target_object_name', '')
            target_type = link.get('target_object_type', '')
            
            source_id = find_node_id(source_type, source_name)
            target_id = find_node_id(target_type, target_name)
            
            if not source_id or not target_id:
                continue
            
            if source_id not in valid_node_ids or target_id not in valid_node_ids:
                continue
            
            lt_def = link_type_map.get(link_type, {})
            actual_source_type = node_type_map.get(source_id, '')
            actual_target_type = node_type_map.get(target_id, '')
            
            expected_source = lt_def.get('source_object_type', '')
            expected_target = lt_def.get('target_object_type', '')
            
            if expected_source and actual_source_type != expected_source:
                continue
            if expected_target and actual_target_type != expected_target:
                continue
            
            edge = {
                "id": str(uuid.uuid4()),
                "type": link_type,
                "source_id": source_id,
                "target_id": target_id,
                "source_object_type": source_type,
                "target_object_type": target_type,
                "properties": link.get('properties', {}),
                "source_location": link.get('source_location')
            }
            edges.append(edge)

        action_instances = []
        for action in all_actions:
            action_type = action.get('action_type', action.get('type', ''))
            target_name = action.get('target_object_name', '')
            target_type = action.get('target_object_type', '')
            
            target_id = find_node_id(target_type, target_name)
            
            action_instance = {
                "id": str(uuid.uuid4()),
                "action_type": action_type,
                "target_object_id": target_id if target_id else None,
                "target_object_name": target_name,
                "target_object_type": target_type,
                "parameters": action.get('parameters', {}),
                "source_location": action.get('source_location')
            }
            action_instances.append(action_instance)

        return nodes, edges, action_instances
