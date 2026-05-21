"""
Ontology Schema构建服务
利用LLM从文档中自动构建符合Palantir Ontology核心概念的本体结构
包含Object Type/Property/Link Type/Action Type的完整定义
领域无关设计，适用于任何文档类型
"""
import json
import re
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
from app.core.config import settings
from app.core.exceptions import SchemaBuildException
from app.infrastructure.llm_client import llm_client, ONTOLOGY_JSON_SCHEMA
from app.utils.chunker import chunk_text


class SchemaBuilder:

    SYSTEM_PROMPT = """你是一个专业的本体建模专家，擅长从文档中自动构建符合Palantir Ontology核心概念的本体结构。

请从文档中提取以下本体信息：

## 1. Object Types（对象类型）
文档中出现的所有实体或事件的schema定义。
每个Object Type需要：
- name: 类型名称（英文PascalCase命名，如FinancialProduct、RiskEvent）
- description: 类型描述（必须使用中文，与文档语言一致）
- primary_key: 唯一标识该对象实例的字段名
- properties: 属性列表，每个属性包含：
  - name: 属性名称（snake_case命名，如registration_code）
  - description: 属性描述（必须使用中文）
  - data_type: 数据类型（string/number/boolean/date/datetime/array/object）

## 2. Link Types（链接类型）
文档中两个对象类型之间的关系。
每个Link Type需要：
- name: 链接名称（PascalCase命名，如ManagedBy、InvestsIn）
- description: 链接描述（必须使用中文）
- source_object_type: 源对象类型
- target_object_type: 目标对象类型
- cardinality: 关系基数（one-to-one/one-to-many/many-to-one/many-to-many）

## 3. Action Types（动作类型）
文档中描述的对对象可执行的操作。
每个Action Type需要：
- name: 动作名称（PascalCase命名）
- description: 动作描述（必须使用中文）
- target_object_type: 作用的对象类型
- parameters: 参数列表（可选），每个参数包含name、data_type

## 关键规则
1. 所有description字段必须使用中文（与文档原文语言一致）
2. 充分挖掘文档中所有实体类型，包括但不限于：核心业务对象、参与者角色、费用/费率结构、时间/期限、文件/合同、风险因素、监管要求等
3. 每个对象类型的属性要尽可能完整，覆盖文档中提到的所有字段信息（如费率、比例、期限、金额等数值型属性不要遗漏）
4. 链接类型要覆盖文档中所有明确的关系
5. Action Type要与文档中描述的业务操作对应（如购买、赎回、调整、报告等）
6. 属性name统一使用snake_case命名风格

请只输出JSON格式，不要输出其他内容。"""

    def __init__(self):
        self.logger = logging.getLogger("graph_injector.schema_builder")

    async def build_schema(self, text_content: str, chunk_size: int = 1000,
                          overlap_percentage: int = 10,
                          additional_instructions: Optional[str] = None) -> Dict[str, Any]:
        self.logger.info(f"开始构建Ontology Schema，文本长度: {len(text_content)}")

        chunks = chunk_text(text_content, chunk_size, overlap_percentage)

        if not chunks:
            raise SchemaBuildException("无法从空文本中提取Ontology Schema")

        self.logger.info(f"文本被分为 {len(chunks)} 个chunks")

        all_schemas: List[Dict[str, Any]] = []

        for i, chunk in enumerate(chunks):
            self.logger.info(f"从chunk {i+1}/{len(chunks)} 提取Ontology Schema")

            try:
                chunk_schema = await self._extract_ontology_from_chunk(
                    chunk["text"], additional_instructions
                )
                if chunk_schema:
                    all_schemas.append(chunk_schema)
            except Exception as e:
                self.logger.warning(f"Chunk {i+1} Ontology Schema提取失败: {e}")
                continue

        if not all_schemas:
            raise SchemaBuildException("未能从文档中提取到有效的Ontology Schema")

        merged_ontology = self._merge_ontology_schemas(all_schemas)
        self.logger.info(f"Ontology Schema合并完成: {len(merged_ontology.get('object_types', []))} 个对象类型, "
                        f"{len(merged_ontology.get('link_types', []))} 个链接类型, "
                        f"{len(merged_ontology.get('action_types', []))} 个动作类型")

        return merged_ontology

    async def _extract_ontology_from_chunk(self, chunk_text: str,
                                         additional_instructions: Optional[str] = None) -> Optional[Dict[str, Any]]:
        user_prompt = f"请从以下文档中构建Palantir风格的Ontology Schema结构:\n\n{chunk_text}"

        if additional_instructions:
            user_prompt += f"\n\n额外指令:\n{additional_instructions}"

        user_prompt += "\n\n请严格按照JSON Schema定义的格式输出，只输出JSON对象，不要输出其他任何内容。"

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        schema = await llm_client.extract_json(messages, temperature=0.1, json_schema=ONTOLOGY_JSON_SCHEMA)

        self._validate_ontology_structure(schema)
        return schema

    def _validate_ontology_structure(self, schema: Dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise SchemaBuildException(f"LLM返回的不是有效的JSON对象，类型: {type(schema).__name__}")

        # 兼容LLM可能返回的不同key命名风格
        key_mappings = {
            'ObjectTypes': 'object_types',
            'LinkTypes': 'link_types',
            'ActionTypes': 'action_types',
            'objectTypes': 'object_types',
            'linkTypes': 'link_types',
            'actionTypes': 'action_types',
        }
        for old_key, new_key in key_mappings.items():
            if old_key in schema and new_key not in schema:
                schema[new_key] = schema.pop(old_key)

        if "object_types" not in schema:
            schema["object_types"] = []
        if "link_types" not in schema:
            schema["link_types"] = []
        if "action_types" not in schema:
            schema["action_types"] = []

        if not isinstance(schema.get("object_types"), list):
            schema["object_types"] = []
        if not isinstance(schema.get("link_types"), list):
            schema["link_types"] = []
        if not isinstance(schema.get("action_types"), list):
            schema["action_types"] = []

        for ot in schema.get("object_types", []):
            if not isinstance(ot, dict):
                continue
            if "name" not in ot:
                ot["name"] = "Unknown"
            if "description" not in ot:
                ot["description"] = ""
            if "properties" not in ot:
                ot["properties"] = []
            for prop in ot.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                if "data_type" not in prop and "type" in prop:
                    prop["data_type"] = prop.pop("type")
                if "data_type" not in prop:
                    prop["data_type"] = "string"

        for lt in schema.get("link_types", []):
            if not isinstance(lt, dict):
                continue
            if "name" not in lt:
                lt["name"] = "related_to"
            if "description" not in lt:
                lt["description"] = ""
            if "source_object_type" not in lt:
                lt["source_object_type"] = ""
            if "target_object_type" not in lt:
                lt["target_object_type"] = ""

        for at in schema.get("action_types", []):
            if not isinstance(at, dict):
                continue
            if "name" not in at:
                at["name"] = "update"
            if "description" not in at:
                at["description"] = ""
            if "target_object_type" not in at:
                at["target_object_type"] = ""
            if "parameters" not in at:
                at["parameters"] = []

    @staticmethod
    def _to_snake_case(name: str) -> str:
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    @staticmethod
    def _names_are_similar(name1: str, name2: str) -> bool:
        """判断两个类型名称是否语义相似（可能是同一概念的不同命名）"""
        n1 = SchemaBuilder._to_snake_case(name1).replace('_', '')
        n2 = SchemaBuilder._to_snake_case(name2).replace('_', '')
        if n1 == n2:
            return True
        known_aliases = {
            frozenset({'product', 'financialproduct'}),
            frozenset({'regulator', 'regulatorybody'}),
            frozenset({'contract', 'legaldocument'}),
            frozenset({'saleschannel', 'salesinstitution'}),
            frozenset({'salesagent', 'salesinstitution'}),
            frozenset({'investmentasset', 'investmenttarget'}),
            frozenset({'investmentasset', 'investableinstrument'}),
            frozenset({'riskfactor', 'risktype'}),
        }
        for alias_group in known_aliases:
            if n1 in alias_group and n2 in alias_group:
                return True
        return False

    def _merge_ontology_schemas(self, schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并多个chunk提取的Ontology Schema，支持语义去重"""
        object_type_map: Dict[str, Dict[str, Any]] = {}
        link_type_map: Dict[str, Dict[str, Any]] = {}
        action_type_map: Dict[str, Dict[str, Any]] = {}

        def find_existing_ot_key(name: str) -> Optional[str]:
            if name in object_type_map:
                return name
            for existing_name in object_type_map:
                if self._names_are_similar(name, existing_name):
                    return existing_name
            return None

        def find_existing_lt_key(name: str, source: str, target: str) -> Optional[str]:
            if name in link_type_map:
                return name
            for existing_name, existing_lt in link_type_map.items():
                if (self._names_are_similar(name, existing_name) and
                    existing_lt.get('source_object_type') == source and
                    existing_lt.get('target_object_type') == target):
                    return existing_name
            return None

        def find_existing_at_key(name: str, target: str) -> Optional[str]:
            if name in action_type_map:
                return name
            for existing_name, existing_at in action_type_map.items():
                if (self._names_are_similar(name, existing_name) and
                    existing_at.get('target_object_type') == target):
                    return existing_name
            return None

        for schema in schemas:
            for ot in schema.get("object_types", []):
                name = ot.get("name", "")
                existing_key = find_existing_ot_key(name)
                if existing_key is None:
                    object_type_map[name] = {
                        "name": name,
                        "description": ot.get("description", ""),
                        "primary_key": ot.get("primary_key", "id"),
                        "properties": ot.get("properties", []),
                    }
                else:
                    existing = object_type_map[existing_key]
                    if not existing["description"] and ot.get("description"):
                        existing["description"] = ot["description"]
                    if not existing.get("primary_key") and ot.get("primary_key"):
                        existing["primary_key"] = ot["primary_key"]
                    existing["properties"] = self._merge_properties(
                        existing.get("properties", []), ot.get("properties", [])
                    )

            for lt in schema.get("link_types", []):
                name = lt.get("name", "")
                source = lt.get("source_object_type", "")
                target = lt.get("target_object_type", "")
                existing_key = find_existing_lt_key(name, source, target)
                if existing_key is None:
                    link_type_map[name] = {
                        "name": name,
                        "description": lt.get("description", ""),
                        "source_object_type": source,
                        "target_object_type": target,
                        "cardinality": lt.get("cardinality", "many-to-many"),
                    }
                else:
                    existing = link_type_map[existing_key]
                    if not existing["description"] and lt.get("description"):
                        existing["description"] = lt["description"]

            for at in schema.get("action_types", []):
                name = at.get("name", "")
                target_ot = at.get("target_object_type", "")
                existing_key = find_existing_at_key(name, target_ot)
                if existing_key is None:
                    action_type_map[name] = {
                        "name": name,
                        "description": at.get("description", ""),
                        "target_object_type": target_ot,
                        "parameters": at.get("parameters", []),
                    }
                else:
                    existing = action_type_map[existing_key]
                    if not existing["description"] and at.get("description"):
                        existing["description"] = at["description"]
                    existing["parameters"] = self._merge_action_parameters(
                        existing.get("parameters", []), at.get("parameters", [])
                    )

        merged = {
            "object_types": list(object_type_map.values()),
            "link_types": list(link_type_map.values()),
            "action_types": list(action_type_map.values()),
        }

        return merged

    @staticmethod
    def _merge_properties(existing_props: List[Dict[str, Any]],
                         new_props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged = []
        seen_snake_names = set()

        for prop in existing_props:
            name = prop.get("name", "")
            if not name:
                continue
            snake_name = SchemaBuilder._to_snake_case(name)
            if snake_name not in seen_snake_names:
                seen_snake_names.add(snake_name)
                if name != snake_name:
                    prop = dict(prop)
                    prop["name"] = snake_name
                merged.append(prop)

        for prop in new_props:
            name = prop.get("name", "")
            if not name:
                continue
            snake_name = SchemaBuilder._to_snake_case(name)
            if snake_name in seen_snake_names:
                continue
            seen_snake_names.add(snake_name)
            if name != snake_name:
                prop = dict(prop)
                prop["name"] = snake_name
            merged.append(prop)

        return merged

    @staticmethod
    def _merge_action_parameters(existing_params: List[Dict[str, Any]],
                                new_params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged = []
        seen_snake_names = set()

        for param in existing_params:
            name = param.get("name", "")
            if not name:
                continue
            snake_name = SchemaBuilder._to_snake_case(name)
            if snake_name not in seen_snake_names:
                seen_snake_names.add(snake_name)
                if name != snake_name:
                    param = dict(param)
                    param["name"] = snake_name
                merged.append(param)

        for param in new_params:
            name = param.get("name", "")
            if not name:
                continue
            snake_name = SchemaBuilder._to_snake_case(name)
            if snake_name in seen_snake_names:
                continue
            seen_snake_names.add(snake_name)
            if name != snake_name:
                param = dict(param)
                param["name"] = snake_name
            merged.append(param)

        return merged

    def save_ontology(self, ontology: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        save_dir = Path(output_dir or settings.schema_output_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ontology_{timestamp}.json"
        filepath = save_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(ontology, f, ensure_ascii=False, indent=2)

        self.logger.info(f"Ontology Schema已保存到: {filepath}")
        return str(filepath)

    def save_schema(self, schema: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        return self.save_ontology(schema, output_dir)

    def load_ontology(self, schema_path: str) -> Dict[str, Any]:
        path = Path(schema_path)
        if not path.exists():
            raise SchemaBuildException(f"Ontology Schema文件不存在: {schema_path}")

        with open(path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        self._validate_ontology_structure(schema)
        self.logger.info(f"Ontology Schema已加载: {schema_path}")
        return schema

    def load_schema(self, schema_path: str) -> Dict[str, Any]:
        return self.load_ontology(schema_path)
