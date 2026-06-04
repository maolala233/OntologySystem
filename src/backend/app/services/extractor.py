# app/services/extractor.py - 本体抽取器服务（模块一重构版）
#
# 核心改造：
# 1. 两阶段引擎：extract_schema() → 骨架提取（仅 Class + ObjectProperty）
#                extract_instances() → 约束下的实例提取（严格遵守 Schema）
# 2. 确定性 ID:  废弃 LLM 随机英文名，使用 MD5(label + category) 保证跨轮次唯一
# 3. 防御性校验：extract_instances() 中对不符合 Schema 约束的连线直接丢弃
# 4. 保留向量库同步（build_ontology 兼容旧接口，内部转为两阶段调用）
# 5. 支持进度回调和任务取消

import os
import json
import re
import time
import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as ConcurrentTimeoutError
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set, Callable

from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace, XSD, URIRef

from app.infrastructure.llm_client import LLMClient
from app.infrastructure.vector_client import VectorStoreManager
from app.infrastructure.task_manager import task_manager, TaskCancelledError
from app.core.logging import logger


SCHEMA_EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "object_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "对象类型名称，必须使用中文命名，如'理财产品'、'风险事件'"},
                    "label": {"type": "string", "description": "中文标签"},
                    "description": {"type": "string", "description": "对象类型描述，必须使用中文"},
                    "primary_key": {"type": "string", "description": "主键字段名"},
                    "sub_class_of": {"type": ["string", "null"], "description": "父类名称"},
                    "properties": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "属性名称，必须使用中文命名，如'登记编码'、'风险评级'"},
                                "label": {"type": "string", "description": "中文属性名"},
                                "description": {"type": "string", "description": "属性描述，必须使用中文"},
                                "data_type": {"type": "string", "enum": ["string", "number", "boolean", "date", "datetime", "array", "object"]}
                            },
                            "required": ["name", "label", "description", "data_type"]
                        }
                    }
                },
                "required": ["name", "label", "description", "properties"]
            }
        },
        "link_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "链接类型名称，必须使用中文命名，如'购买'、'管理'"},
                    "label": {"type": "string", "description": "中文标签"},
                    "description": {"type": "string", "description": "链接类型描述，必须使用中文"},
                    "source_object_type": {"type": "string", "description": "源对象类型名称"},
                    "target_object_type": {"type": "string", "description": "目标对象类型名称"},
                    "cardinality": {"type": "string", "enum": ["one-to-one", "one-to-many", "many-to-one", "many-to-many"]}
                },
                "required": ["name", "label", "description", "source_object_type", "target_object_type"]
            }
        },
        "action_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "动作类型名称，必须使用中文命名，如'购买产品'、'赎回产品'"},
                    "label": {"type": "string", "description": "中文标签"},
                    "description": {"type": "string", "description": "动作描述，必须使用中文"},
                    "target_object_type": {"type": "string", "description": "作用的对象类型"},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "参数名称，必须使用中文命名，如'金额'、'日期'"},
                                "data_type": {"type": "string", "description": "参数类型"}
                            },
                            "required": ["name", "data_type"]
                        }
                    }
                },
                "required": ["name", "label", "description", "target_object_type"]
            }
        }
    },
    "required": ["object_types", "link_types"]
}

INSTANCE_EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "instances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "英文语义名"},
                    "type": {"type": "string", "description": "所属类的id或中文名"},
                    "label": {"type": "string", "description": "中文标签"},
                    "object_props": {
                        "type": "object",
                        "description": "对象属性，键为关系id，值为目标实例label列表"
                    },
                    "data_props": {
                        "type": "object",
                        "description": "数据属性，键为属性中文名，值为属性值"
                    }
                },
                "required": ["id", "type", "label"]
            }
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "link_type": {"type": "string", "description": "关系id"},
                    "source_label": {"type": "string", "description": "源实例label"},
                    "source_type": {"type": "string", "description": "源实例类id"},
                    "target_label": {"type": "string", "description": "目标实例label"},
                    "target_type": {"type": "string", "description": "目标实例类id"}
                },
                "required": ["link_type", "source_label", "source_type", "target_label", "target_type"]
            }
        },
        "action_instances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string", "description": "动作类型name"},
                    "label": {"type": "string", "description": "动作实例中文标签"},
                    "target_instance_label": {"type": "string", "description": "目标实例label"},
                    "target_type": {"type": "string", "description": "目标类型"},
                    "parameters": {
                        "type": "object",
                        "description": "动作参数"
                    }
                },
                "required": ["action_type", "label", "target_instance_label", "target_type"]
            }
        }
    },
    "required": ["instances", "action_instances"]
}


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def make_deterministic_id(label: str, category: str) -> str:
    """
    用 MD5(label + category) 生成确定性 ID。
    同一概念、同一类别，在多次提取中永远返回同一 ID。
    
    格式：{category_prefix}_{8 位哈希}
    例如：C_a1b2c3d4, I_f5e6d7c8, OP_12345678
    
    ★ 修复：确保即使 label 为空或包含空白也能生成有效的 ID，不会产生多余下划线
    """
    # 清理 label：去除首尾空白，如果为空则使用特殊标记
    cleaned_label = label.strip() if label else ""
    if not cleaned_label:
        cleaned_label = "_empty_label_"
    
    raw = f"{cleaned_label}::{category.strip()}"
    hex_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    
    # 类别前缀映射
    prefix_map = {
        "Class": "C",
        "ObjectType": "C",
        "Instance": "I",
        "ObjectProperty": "OP",
        "LinkType": "OP",
        "DataProperty": "DP",
        "ActionType": "AT",
    }
    prefix = prefix_map.get(category, "Node")
    
    # 确保返回的 ID 格式正确，不包含多余下划线
    return f"{prefix}_{hex_hash}"


def safe_id(raw: str) -> str:
    return make_deterministic_id(raw, "Unknown")


def _to_snake_case(name: str) -> str:
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def names_are_similar(name1: str, name2: str) -> bool:
    n1 = _to_snake_case(name1).replace('_', '')
    n2 = _to_snake_case(name2).replace('_', '')
    if n1 == n2:
        return True
    if name1.strip() == name2.strip():
        return True
    return False


def _normalize_schema_response(data: dict) -> dict:
    """
    将 LLM 返回的 Schema 数据归一化为 Palantir Ontology 格式。
    如果 LLM 返回旧格式（classes/object_properties），自动转换为
    新格式（object_types/link_types/action_types）。
    """
    if not isinstance(data, dict):
        return data

    if "object_types" in data or "link_types" in data:
        return data

    object_types = []
    for cls in data.get("classes", []):
        raw_name = (cls.get("id") or cls.get("name") or "").strip()
        raw_label = (cls.get("label") or "").strip()
        if not raw_name and raw_label:
            raw_name = raw_label
        props = []
        for dp in cls.get("data_properties", []):
            if isinstance(dp, dict):
                props.append({
                    "name": dp.get("name", ""),
                    "label": dp.get("name", ""),
                    "description": dp.get("description", ""),
                    "data_type": dp.get("data_type", "string"),
                })
            elif isinstance(dp, str):
                props.append({"name": dp, "label": dp, "description": "", "data_type": "string"})
        ot = {
            "name": raw_name,
            "label": raw_label,
            "description": cls.get("description", raw_label),
            "properties": props,
        }
        if cls.get("primary_key"):
            ot["primary_key"] = cls["primary_key"]
        if cls.get("sub_class_of"):
            ot["sub_class_of"] = cls["sub_class_of"]
        object_types.append(ot)

    link_types = []
    for op in data.get("object_properties", []):
        raw_name = (op.get("id") or op.get("name") or "").strip()
        raw_label = (op.get("label") or "").strip()
        if not raw_name and raw_label:
            raw_name = raw_label
        lt = {
            "name": raw_name,
            "label": raw_label,
            "description": op.get("description", ""),
            "source_object_type": op.get("domain", ""),
            "target_object_type": op.get("range", ""),
        }
        if op.get("cardinality"):
            lt["cardinality"] = op["cardinality"]
        link_types.append(lt)

    result = {"object_types": object_types, "link_types": link_types}
    if data.get("action_types"):
        result["action_types"] = data["action_types"]
    else:
        result["action_types"] = []

    return result


def _normalize_schema_graph(schema_graph: Dict[str, Any]) -> Tuple[List[dict], List[dict], List[dict]]:
    """
    将 schema_graph 归一化为 (object_types, link_types, action_types) 三元组。
    支持新旧两种格式：
    - 新格式：object_types / link_types / action_types
    - 旧格式：classes / object_properties
    """
    if "object_types" in schema_graph:
        object_types = schema_graph.get("object_types", [])
        link_types = schema_graph.get("link_types", [])
        action_types = schema_graph.get("action_types", [])
        return object_types, link_types, action_types

    classes = schema_graph.get("classes", [])
    obj_props = schema_graph.get("object_properties", [])

    object_types = []
    for cls in classes:
        cid = cls.get("id", "")
        label = cls.get("label", "")
        raw_name = cls.get("name", cid)
        props = []
        for dp in cls.get("data_properties", []) or []:
            if isinstance(dp, dict):
                props.append({
                    "name": dp.get("name", ""),
                    "label": dp.get("label", "") or dp.get("name", ""),
                    "description": dp.get("description", ""),
                    "data_type": dp.get("data_type", "string"),
                })
            elif isinstance(dp, str):
                props.append({"name": dp, "label": dp, "description": "", "data_type": "string"})
        prop_defs = cls.get("property_definitions", []) or []
        if prop_defs and not props:
            for pd in prop_defs:
                props.append({
                    "name": pd.get("name", ""),
                    "label": pd.get("label", "") or pd.get("name", ""),
                    "description": pd.get("description", ""),
                    "data_type": pd.get("data_type", "string"),
                })
        ot = {
            "id": cid,
            "name": raw_name or cid,
            "label": label,
            "description": cls.get("description", label),
            "properties": props,
        }
        if cls.get("primary_key"):
            ot["primary_key"] = cls["primary_key"]
        if cls.get("sub_class_of"):
            ot["sub_class_of"] = cls["sub_class_of"]
        if cls.get("parent_classes"):
            ot["sub_class_of"] = cls["parent_classes"][0] if isinstance(cls["parent_classes"], list) else cls["parent_classes"]
        object_types.append(ot)

    link_types = []
    for op in obj_props:
        opid = op.get("id", "")
        label = op.get("label", "")
        raw_name = op.get("name", opid)
        lt = {
            "id": opid,
            "name": raw_name or opid,
            "label": label,
            "description": op.get("description", ""),
            "source_object_type": op.get("domain", op.get("source_object_type", "")),
            "target_object_type": op.get("range", op.get("target_object_type", "")),
        }
        if op.get("cardinality"):
            lt["cardinality"] = op["cardinality"]
        link_types.append(lt)

    action_types = schema_graph.get("action_types", [])

    return object_types, link_types, action_types


# ─────────────────────────────────────────────────────────────
# 核心抽取器
# ─────────────────────────────────────────────────────────────

class OntologyExtractor:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        collection_name: Optional[str] = None,
    ):
        self.llm_client = LLMClient(api_key, base_url, model)
        self.EX = Namespace("http://www.example.org/auto_ontology#")
        self.vector_manager = VectorStoreManager(collection_name=collection_name)

    # ──────────────────────────────────────────
    # 文本切分工具（带血缘信息）
    # ──────────────────────────────────────────

    def _chunk_text(
        self, 
        text: str, 
        chunk_size: int = 15000, 
        overlap: int = 10,
        filename: str = "unknown",
        start_chunk_index: int = 0
    ) -> List[Dict[str, Any]]:
        """
        切分文本，每个切片携带血缘信息（filename, chunk_index）。
        
        返回格式：List[Dict]，每个 Dict 包含：
        - text: 切片文本内容
        - filename: 所属文件名
        - chunk_index: 切片索引（从 start_chunk_index 开始）
        """
        try:
            chunk_size = int(chunk_size)
        except (ValueError, TypeError):
            chunk_size = 15000
        try:
            overlap = int(overlap)
        except (ValueError, TypeError):
            overlap = 10
        if overlap > 50:
            overlap = 50
        overlap_chars = int(chunk_size * overlap / 100)
        
        raw_chunks = self._recursive_split(
            text, chunk_size, overlap_chars,
            separators=["\n\n", "\n", "。 ", "！ ", "？ ", ". ", " ", ""]
        )
        
        # 为每个切片添加血缘信息
        return [
            {
                "text": chunk,
                "filename": filename,
                "chunk_index": start_chunk_index + i,
            }
            for i, chunk in enumerate(raw_chunks)
        ]

    def _chunk_documents(
        self,
        documents: List[Dict[str, Any]],
        chunk_size: int = 15000,
        overlap: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        对多个文档进行切分，保持每个文档的血缘信息。
        
        参数:
        - documents: 文档列表，每个文档包含 {"text": str, "filename": str}
        
        返回：所有切片的列表，每个切片携带完整的血缘信息
        """
        all_chunks = []
        global_chunk_index = 0
        
        logger.info(f"[_chunk_documents] 开始切分 {len(documents)} 个文档")
        
        for doc_idx, doc in enumerate(documents):
            text = doc.get("text", "")
            filename = doc.get("filename", f"doc_{doc_idx}")
            
            logger.info(f"[_chunk_documents] 处理文档 {doc_idx+1}/{len(documents)}: filename={filename}, text_length={len(text)}")
            
            chunks = self._chunk_text(
                text=text,
                chunk_size=chunk_size,
                overlap=overlap,
                filename=filename,
                start_chunk_index=global_chunk_index,
            )
            
            logger.info(f"[_chunk_documents] 文档 {doc_idx+1} 切分为 {len(chunks)} 个切片")
            
            # 验证每个切片的血缘信息
            for chunk in chunks:
                logger.debug(f"[_chunk_documents] 切片：filename={chunk.get('filename')}, chunk_index={chunk.get('chunk_index')}")
            
            all_chunks.extend(chunks)
            global_chunk_index += len(chunks)
        
        logger.info(f"[_chunk_documents] 完成：共 {len(all_chunks)} 个切片")
        
        return all_chunks

    def _sub_chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 10) -> List[str]:
        try:
            chunk_size = int(chunk_size)
        except (ValueError, TypeError):
            chunk_size = 800
        try:
            overlap = int(overlap)
        except (ValueError, TypeError):
            overlap = 10
        overlap_chars = int(chunk_size * overlap / 100) if overlap <= 50 else overlap
        return self._recursive_split(
            text, chunk_size, overlap_chars,
            separators=["\n\n", "\n", "。 ", "！ ", "？ ", ". ", " ", ""]
        )

    def _recursive_split(
        self, text: str, chunk_size: int, overlap: int, separators: List[str]
    ) -> List[str]:
        try:
            chunk_size = int(chunk_size)
        except (ValueError, TypeError):
            chunk_size = 15000
        try:
            overlap = int(overlap)
        except (ValueError, TypeError):
            overlap = 10

        if len(text) <= chunk_size:
            return [text]

        separator = ""
        new_separators: List[str] = []
        for i, s in enumerate(separators):
            if s == "":
                separator = s
                break
            if s in text:
                separator = s
                new_separators = separators[i + 1:]
                break

        if separator == "" or not new_separators:
            return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size - overlap)]

        splits = text.split(separator)
        final_chunks: List[str] = []
        current_chunk = ""

        for split in splits:
            if len(split) > chunk_size:
                if current_chunk:
                    final_chunks.append(current_chunk)
                    current_chunk = ""
                recursive_splits = self._recursive_split(split, chunk_size, overlap, new_separators)
                final_chunks.extend(recursive_splits[:-1])
                current_chunk = recursive_splits[-1]
            elif current_chunk and len(current_chunk) + len(separator) + len(split) > chunk_size:
                final_chunks.append(current_chunk)
                overlap_text = current_chunk[-overlap:] if overlap > 0 else ""
                current_chunk = overlap_text + (separator if overlap_text else "") + split
            else:
                current_chunk = (current_chunk + separator + split) if current_chunk else split

        if current_chunk:
            final_chunks.append(current_chunk)

        return final_chunks

    # ──────────────────────────────────────────
    # LLM 调用配置
    # ──────────────────────────────────────────

    def _get_streaming_config(self) -> bool:
        try:
            from app.infrastructure.database import SessionLocal, SystemConfig
            db = SessionLocal()
            try:
                config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
                if config and config.value:
                    val = config.value.get("streaming_enabled", True)
                    return val
                return True
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"获取流式配置失败，使用默认值：{e}")
            return True

    def _get_timeout_config(self) -> int:
        try:
            from app.infrastructure.database import SessionLocal, SystemConfig
            db = SessionLocal()
            try:
                config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
                if config and config.value:
                    timeout_val = config.value.get("llm_timeout", 300)
                    try:
                        return int(timeout_val) if timeout_val else 300
                    except (ValueError, TypeError):
                        return 300
                return 300
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"获取超时配置失败，使用默认值 300 秒：{e}")
            return 300

    def _call_llm(self, system_prompt: str, user_prompt: str, task_id: Optional[str] = None, timeout: Optional[int] = None, json_schema: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        """
        同步调用 LLM 并处理异常，返回解析后的 dict 或 None。
        使用线程池执行 LLM 调用，支持超时和取消检查。
        
        参数:
        - task_id: 任务 ID，用于在调用间隙检查取消标志
        - timeout: LLM 调用超时时间（秒），如果未提供则从数据库配置读取
        - json_schema: JSON Schema 定义，用于约束输出格式（优先使用）
        """
        streaming = self._get_streaming_config()
        # 如果未提供 timeout，从数据库配置读取
        if timeout is None:
            timeout = self._get_timeout_config()
        try:
            # 如果提供了 task_id，在调用前快速检查是否已取消
            if task_id and task_manager.is_cancelled(task_id):
                raise TaskCancelledError("Task cancelled before LLM call")
            
            # 使用线程池执行 LLM 调用，支持超时
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                # 注意：call_llm 的参数顺序是 (system_prompt, user_prompt, max_retries, stream, timeout, task_id, json_schema)
                # 必须使用关键字参数传入 stream、timeout、task_id 和 json_schema，避免位置参数混淆
                future = executor.submit(
                    self.llm_client.call_llm,
                    system_prompt,
                    user_prompt,
                    3,  # max_retries
                    stream=streaming,
                    timeout=timeout,
                    task_id=task_id,  # 传递 task_id 以支持取消检查
                    json_schema=json_schema,  # 传递 json_schema 以约束输出格式
                )
                result = future.result(timeout=timeout + 10)  # 额外 10 秒缓冲
            except ConcurrentTimeoutError:
                logger.error(f"LLM 调用超时（timeout={timeout}s）")
                return None
            finally:
                executor.shutdown(wait=False)
            
            # 调用后再次检查取消标志
            if task_id and task_manager.is_cancelled(task_id):
                raise TaskCancelledError("Task cancelled after LLM call")
            
            return result
        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            return None

    # ──────────────────────────────────────────
    # 术语规范化 / 同义词轻量归一
    # ──────────────────────────────────────────

    def _normalize_term(self, label: str, user_intent: Optional[str] = None) -> str:
        """
        对 LLM 返回的中文术语做轻量规范化，用于实现 Map-Reduce 风格的同义词归一。
        目标示例：将「负责的人」「负责人员」规范为「负责人」等。
        """
        if not label:
            return label

        normalized = label.strip()
        # 去除常见助词 / 冗余尾缀
        for suffix in ["的人", "的人员", "人员", "的人士", "的情况", "的记录"]:
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        # 去除无意义的"的"
        if normalized.endswith("的") and len(normalized) > 1:
            normalized = normalized[:-1]

        # 将全角空格等统一为半角并裁剪
        normalized = normalized.replace("\u3000", " ").strip()

        # 预留基于 user_intent 的后续规则（当前仅占位，不做细化分支）
        return normalized or label.strip()

    # ──────────────────────────────────────────
    # ★ API 1：骨架提取 (Schema Extraction)
    # ──────────────────────────────────────────

    def extract_schema(
        self,
        text: str,
        user_intent: Optional[str] = None,
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        第一阶段：从文本中提取本体骨架 (Schema)，输出 Palantir Ontology 风格。

        返回格式（dict）:
        {
            "object_types": [{"name": ..., "label": ..., "description": ..., "properties": [...]}],
            "link_types": [{"name": ..., "label": ..., "source_object_type": ..., "target_object_type": ...}],
            "action_types": [{"name": ..., "label": ..., "target_object_type": ..., "parameters": [...]}],
        }
        """
        logger.info(f"[API1-SchemaExtraction] 开始骨架提取，意图：{user_intent or '通用'}")

        def report_progress(progress: float, message: str = ""):
            if progress_callback:
                progress_callback(progress, message)
            if task_id:
                task_manager.update_progress(task_id, progress=progress, message=message)

        def check_cancelled():
            if task_id:
                task_manager.check_cancelled(task_id)

        report_progress(0.0, "开始骨架提取...")

        intent_instruction = ""
        if user_intent:
            intent_instruction = (
                f"\n【⚡ 用户意图约束】: 用户关注领域为「{user_intent}」。"
                f"请严格聚焦该领域，提取与之直接相关的对象类型和关系，忽略无关领域的概念。\n"
            )

        system_prompt = f"""你是一位专业的本体建模专家，擅长从文档中自动构建符合Palantir Ontology核心概念的本体结构。

请从文档中提取以下本体信息：

## 1. Object Types（对象类型）
文档中出现的所有实体或事件的schema定义。
每个Object Type需要：
- name: 类型名称（使用中文命名，如"理财产品"、"风险事件"）
- label: 中文标签
- description: 类型描述（必须使用中文，与文档语言一致）
- primary_key: 唯一标识该对象实例的字段名
- sub_class_of: 父类名称（如有继承关系）
- properties: 属性列表，每个属性包含：
  - name: 属性名称（使用中文命名，如"登记编码"、"风险评级"）
  - description: 属性描述（必须使用中文）
  - data_type: 数据类型（string/number/boolean/date/datetime/array/object）

## 2. Link Types（链接类型）
文档中两个对象类型之间的关系。
每个Link Type需要：
- name: 链接名称（使用中文命名，如"购买"、"管理"）
- label: 中文标签
- description: 链接描述（必须使用中文）
- source_object_type: 源对象类型
- target_object_type: 目标对象类型
- cardinality: 关系基数（one-to-one/one-to-many/many-to-one/many-to-many）

## 3. Action Types（动作类型）
文档中描述的对对象可执行的操作。
每个Action Type需要：
- name: 动作名称（使用中文命名，如"购买产品"、"赎回产品"）
- label: 中文标签
- description: 动作描述（必须使用中文）
- target_object_type: 作用的对象类型
- parameters: 参数列表（可选），每个参数包含name、data_type

## 关键规则
1. 所有description字段必须使用中文（与文档原文语言一致）
2. 充分挖掘文档中所有实体类型，包括但不限于：核心业务对象、参与者角色、费用/费率结构、时间/期限、文件/合同、风险因素、监管要求等
3. 每个对象类型的属性要尽可能完整，覆盖文档中提到的所有字段信息（如费率、比例、期限、金额等数值型属性不要遗漏）
4. 链接类型要覆盖文档中所有明确的关系
5. Action Type要与文档中描述的业务操作对应（如购买、赎回、调整、报告等）
6. 属性name统一使用中文命名风格，保持与文档语言一致
7. 如果文本中存在类的继承/层级关系，必须在 sub_class_of 字段中明确指出父类
8. 鼓励抽取隐含关系：如果文本中存在明确描述或强烈暗示的关系，即使没有出现"关系名称"，也请提取为Link Type
{intent_instruction}

【严格约束 - 违反将导致任务失败】：
1. 【禁止提取实例】: 绝对不允许提取任何具体实例。只提取抽象的「对象类型」和「属性」。
2. 【name必须中文】: 所有 name 字段必须使用中文，禁止使用英文！例如：用"理财产品"而非"FinancialProduct"，用"登记编码"而非"registration_code"，用"购买"而非"PurchasedBy"，用"购买产品"而非"PurchaseProduct"。这是强制要求，违反将导致输出被丢弃。
3. 【label必须中文】: 所有 label 字段必须是简洁中文。
4. 【description必须中文】: 所有 description 字段必须使用中文详细描述。
5. 【source_object_type/target_object_type必须中文】: 链接类型中的源和目标对象类型必须使用中文对象类型名称，如"理财产品"、"投资者"等。
"""

        user_prompt_template = """【当前文本片段】:
"{chunk}"

【输出 JSON 格式（严格遵守，不输出任何注释）】:
{{
  "object_types": [
    {{
      "name": "理财产品",
      "label": "理财产品",
      "description": "理财产品，指金融机构接受投资者委托...",
      "primary_key": "登记编码",
      "sub_class_of": null,
      "properties": [
        {{"name": "登记编码", "description": "理财产品登记编码", "data_type": "string"}},
        {{"name": "产品名称", "description": "产品名称", "data_type": "string"}},
        {{"name": "风险评级", "description": "风险评级", "data_type": "string"}}
      ]
    }}
  ],
  "link_types": [
    {{
      "name": "购买",
      "label": "购买",
      "description": "投资者购买理财产品",
      "source_object_type": "投资者",
      "target_object_type": "理财产品",
      "cardinality": "many-to-many"
    }}
  ],
  "action_types": [
    {{
      "name": "购买产品",
      "label": "购买产品",
      "description": "投资者通过销售渠道购买理财产品",
      "target_object_type": "理财产品",
      "parameters": [
        {{"name": "投资金额", "data_type": "number"}}
      ]
    }}
  ]
}}

【约束提醒】: 不得包含 instances 字段。只输出 object_types、link_types 和 action_types。
"""

        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        total_chunks = len(chunks)

        all_object_types: Dict[str, dict] = {}
        all_link_types: Dict[str, dict] = {}
        all_action_types: Dict[str, dict] = {}

        for i, chunk in enumerate(chunks):
            check_cancelled()

            logger.info(f"[SchemaExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = self._call_llm(system_prompt, user_prompt, task_id=task_id, json_schema=SCHEMA_EXTRACTION_JSON_SCHEMA)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        time.sleep(0.1)
                continue

            data = _normalize_schema_response(data)

            for ot in data.get("object_types", []):
                raw_name = (ot.get("name") or "").strip()
                raw_label = (ot.get("label") or "").strip()
                if not raw_name:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "ObjectType")
                raw_props = ot.get("properties", [])
                props = []
                for p in raw_props:
                    if isinstance(p, dict):
                        props.append({
                            "name": p.get("name", ""),
                            "label": p.get("label", p.get("name", "")),
                            "description": p.get("description", ""),
                            "data_type": p.get("data_type", "string"),
                        })
                    elif isinstance(p, str):
                        props.append({"name": p, "label": p, "description": "", "data_type": "string"})
                if det_id not in all_object_types:
                    all_object_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": ot.get("description", ""),
                        "primary_key": ot.get("primary_key"),
                        "sub_class_of": None,
                        "properties": props,
                    }
                else:
                    existing_props = {p["name"]: p for p in all_object_types[det_id].get("properties", [])}
                    for p in props:
                        if p["name"] not in existing_props:
                            existing_props[p["name"]] = p
                    all_object_types[det_id]["properties"] = list(existing_props.values())

                if ot.get("sub_class_of"):
                    all_object_types[det_id]["_raw_sub_class_of"] = ot["sub_class_of"]

            for lt in data.get("link_types", []):
                raw_name = (lt.get("name") or "").strip()
                raw_label = (lt.get("label") or "").strip()
                raw_source = (lt.get("source_object_type") or "").strip()
                raw_target = (lt.get("target_object_type") or "").strip()
                if not raw_name or not raw_source or not raw_target:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "LinkType")
                if det_id not in all_link_types:
                    all_link_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": lt.get("description", ""),
                        "source_object_type": raw_source,
                        "target_object_type": raw_target,
                        "cardinality": lt.get("cardinality"),
                    }

            for at in data.get("action_types", []):
                raw_name = (at.get("name") or "").strip()
                raw_label = (at.get("label") or "").strip()
                raw_target = (at.get("target_object_type") or "").strip()
                if not raw_name or not raw_target:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "ActionType")
                raw_params = at.get("parameters", [])
                params = []
                for pm in raw_params:
                    if isinstance(pm, dict):
                        params.append({"name": pm.get("name", ""), "data_type": pm.get("data_type", "string")})
                    elif isinstance(pm, str):
                        params.append({"name": pm, "data_type": "string"})
                if det_id not in all_action_types:
                    all_action_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": at.get("description", ""),
                        "target_object_type": raw_target,
                        "parameters": params,
                    }
                else:
                    existing_params = {p["name"]: p for p in all_action_types[det_id].get("parameters", [])}
                    for p in params:
                        if p["name"] not in existing_params:
                            existing_params[p["name"]] = p
                    all_action_types[det_id]["parameters"] = list(existing_params.values())

            progress = (i + 1) / total_chunks * 0.9
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                time.sleep(request_interval)

        # ── 二次处理：将 source_object_type/target_object_type/sub_class_of 映射为类名 ──
        name_to_det_id: Dict[str, str] = {
            v["name"]: k for k, v in all_object_types.items()
        }
        label_to_det_id: Dict[str, str] = {
            v["label"]: k for k, v in all_object_types.items()
        }
        det_id_to_name: Dict[str, str] = {
            k: v["name"] for k, v in all_object_types.items()
        }

        def resolve_to_det_id(raw: str) -> Optional[str]:
            if not raw:
                return None
            if raw in all_object_types:
                return raw
            if raw in name_to_det_id:
                return name_to_det_id[raw]
            if raw in label_to_det_id:
                return label_to_det_id[raw]
            raw_lower = raw.lower()
            for n, did in name_to_det_id.items():
                if n.lower() == raw_lower:
                    return did
            for lbl, did in label_to_det_id.items():
                if raw in lbl or lbl in raw:
                    return did
            return None

        def resolve_to_class_name(raw: str) -> Optional[str]:
            det_id = resolve_to_det_id(raw)
            if det_id and det_id in det_id_to_name:
                return det_id_to_name[det_id]
            if raw in name_to_det_id:
                return raw
            return None

        # 修正 sub_class_of（使用类名）
        for det_id, ot_data in all_object_types.items():
            raw_sco = ot_data.pop("_raw_sub_class_of", None)
            if raw_sco:
                resolved = resolve_to_class_name(raw_sco)
                resolved_det_id = resolve_to_det_id(raw_sco)
                if resolved and resolved_det_id and resolved_det_id != det_id:
                    ot_data["sub_class_of"] = resolved
                    logger.info(f"[SchemaExtraction] 子类关系解析成功：{ot_data['label']} → {resolved}")
                else:
                    logger.warning(f"[SchemaExtraction] 子类关系无法解析：{ot_data['label']} 的父类 '{raw_sco}' 未找到")

        # 修正 source_object_type / target_object_type（使用类名）
        valid_link_types: Dict[str, dict] = {}
        for det_id, lt_data in all_link_types.items():
            source_resolved = resolve_to_class_name(lt_data["source_object_type"])
            target_resolved = resolve_to_class_name(lt_data["target_object_type"])
            if source_resolved and target_resolved:
                lt_data["source_object_type"] = source_resolved
                lt_data["target_object_type"] = target_resolved
                valid_link_types[det_id] = lt_data
                logger.info(f"[SchemaExtraction] LinkType '{lt_data['label']}' 解析成功：{source_resolved} → {target_resolved}")
            else:
                logger.warning(
                    f"[SchemaExtraction] LinkType '{lt_data['label']}' "
                    f"source/target 无法解析，已丢弃 "
                    f"(source={lt_data['source_object_type']}→{source_resolved}, target={lt_data['target_object_type']}→{target_resolved})"
                )

        # 修正 action_types 的 target_object_type（使用类名）
        valid_action_types: Dict[str, dict] = {}
        for det_id, at_data in all_action_types.items():
            target_resolved = resolve_to_class_name(at_data["target_object_type"])
            if target_resolved:
                at_data["target_object_type"] = target_resolved
                valid_action_types[det_id] = at_data
            else:
                logger.warning(
                    f"[SchemaExtraction] ActionType '{at_data['label']}' "
                    f"target_object_type 无法解析，已丢弃 "
                    f"(target={at_data['target_object_type']}→{target_resolved})"
                )

        # 语义去重
        dedup_object_types: Dict[str, dict] = {}
        for det_id, ot_data in all_object_types.items():
            merged = False
            for existing_id, existing_data in dedup_object_types.items():
                if names_are_similar(ot_data["label"], existing_data["label"]) or names_are_similar(ot_data["name"], existing_data["name"]):
                    for p in ot_data.get("properties", []):
                        existing_names = {ep["name"] for ep in existing_data.get("properties", [])}
                        if p["name"] not in existing_names:
                            existing_data.setdefault("properties", []).append(p)
                    merged = True
                    logger.info(f"[SchemaExtraction] 语义去重：'{ot_data['label']}' 合并到 '{existing_data['label']}'")
                    break
            if not merged:
                dedup_object_types[det_id] = ot_data
        all_object_types = dedup_object_types

        result = {
            "object_types": list(all_object_types.values()),
            "link_types": list(valid_link_types.values()),
            "action_types": list(valid_action_types.values()),
            "metadata": {
                "total_chunks": total_chunks,
                "successful_chunks": sum(1 for i in range(total_chunks)),
                "failed_chunks": 0,
                "success_rate": 1.0,
                "total_object_types": len(all_object_types),
                "total_link_types": len(valid_link_types),
                "total_action_types": len(valid_action_types),
            },
        }

        report_progress(1.0, f"骨架提取完成：{len(result['object_types'])} 个对象类型，{len(result['link_types'])} 个链接类型，{len(result['action_types'])} 个动作类型")
        logger.info(
            f"[SchemaExtraction] 完成：{len(result['object_types'])} 个对象类型，"
            f"{len(result['link_types'])} 个链接类型，{len(result['action_types'])} 个动作类型"
        )
        return result

    def extract_schema_only(
        self,
        text: str,
        user_intent: Optional[str] = None,
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        对外暴露的「Schema Only」方法。
        语义等价于 extract_schema，仅负责抽取骨架（Class/ObjectProperty/DataProperty），
        不做任何实例提取，方便在路由层与实例提取 API 做清晰区分。
        """
        return self.extract_schema(
            text=text,
            user_intent=user_intent,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            task_id=task_id,
            progress_callback=progress_callback,
        )

    # ──────────────────────────────────────────
    # ★ API 2：强约束实例提取 (Instance Extraction)
    # ──────────────────────────────────────────

    def extract_instances(
        self,
        text: str,
        schema_graph: Dict[str, Any],
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,  # ★ 新增：支持传入文档数组 [{"text": str, "filename": str}]
    ) -> Dict[str, Any]:
        """
        第二阶段：在 Schema 约束下提取实例 (NamedIndividual)。
        
        核心机制：
        1. 将 schema_graph 转为 Prompt 约束注入；
        2. 模型仅能实例化 schema_graph.classes 中已定义的类；
        3. 生成的 ObjectProperty 连线必须符合 domain/range 约束，否则后端丢弃。
        4. 实例 ID 同样使用确定性算法。
        
        返回格式（dict）:
        {
            "instances": [...],
            "discarded_edges_count": 0,
        }
        
        参数:
        - task_id: 任务 ID，用于支持取消操作
        - progress_callback: 进度回调函数，签名：callback(progress: float, message: str)
        """
        logger.info("=" * 80)
        logger.info("[API2-InstanceExtraction] 开始实例提取")
        logger.info(f"[InstanceExtraction] 输入参数：product_code={product_code}, task_id={task_id}")
        logger.info(f"[InstanceExtraction] Schema 包含 {len(schema_graph.get('object_types', schema_graph.get('classes', [])))} 个对象类型，{len(schema_graph.get('link_types', schema_graph.get('object_properties', [])))} 个链接类型")
        logger.info(f"[InstanceExtraction] Schema 详情：object_types={schema_graph.get('object_types', schema_graph.get('classes', []))[:3]}... (仅显示前 3 个)")
        logger.info(f"[InstanceExtraction] Schema 详情：link_types={schema_graph.get('link_types', schema_graph.get('object_properties', []))[:3]}... (仅显示前 3 个)")
        logger.info(f"[InstanceExtraction] 输入文本长度：{len(text)} 字符")
        
        # 进度回调辅助函数
        def report_progress(progress: float, message: str = ""):
            if progress_callback:
                progress_callback(progress, message)
            if task_id:
                task_manager.update_progress(task_id, progress=progress, message=message)
        
        # 检查取消
        def check_cancelled():
            if task_id:
                task_manager.check_cancelled(task_id)
        
        report_progress(0.0, "开始实例提取...")

        object_types, link_types, action_types = _normalize_schema_graph(schema_graph)
        classes = object_types
        obj_props = link_types

        if not classes:
            return {"instances": [], "discarded_edges_count": 0}

        class_id_to_info: Dict[str, dict] = {c.get("id", c.get("name", "")): c for c in classes}

        class_to_subclasses: Dict[str, List[str]] = {c.get("id", c.get("name", "")): [] for c in classes}
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            parent_classes = c.get('sub_class_of', None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_id in parent_classes:
                    if parent_id in class_to_subclasses:
                        class_to_subclasses[parent_id].append(cid)

        class_list_items = []
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            label = c.get("label", "")
            props_list = c.get("properties", [])
            prop_names_list = []
            for p in props_list:
                if isinstance(p, dict):
                    prop_names_list.append(p.get("label", "") or p.get("name", ""))
                elif isinstance(p, str):
                    prop_names_list.append(p)
            props_str = ','.join(prop_names_list) if prop_names_list else ''
            class_list_items.append(
                f"  {cid}|{label}|{props_str}"
            )
        class_list_str = "\n".join(class_list_items)

        op_list_str = "\n".join(
            f"  {op.get('name', op.get('id', ''))}|{op.get('label', '')}|{op.get('source_object_type', op.get('domain', ''))}->{op.get('target_object_type', op.get('range', ''))}"
            for op in obj_props
        ) or "  （无LinkType）"

        action_list_str = "\n".join(
            f"  {at.get('name', at.get('id', ''))}|{at.get('label', '')}|{at.get('target_object_type', '')}"
            for at in action_types
        ) or "  （无ActionType）"

        domain_code_clause = ""
        if product_code:
            domain_code_clause = (
                f"\n【🔴 知识域隔离】所有实例 ID 必须以 `_{product_code}` 结尾，"
                f"例如：`张三_HR` → `{make_deterministic_id('张三', 'Instance')}_{product_code}`。\n"
            )

        # 知识域上下文注入
        domain_context = ""
        if product_code:
            domain_context = f"\n【📚 知识域上下文】当前提取任务属于【{product_code}】知识域。请确保提取的实例与该知识域相关。\n"

        system_prompt = f"""你是本体工程师，执行实例提取任务。根据文本提取符合Schema的实例。

【类 Schema】(格式: id|中文名|属性列表):
{class_list_str}

【关系 Schema】(格式: id|名称|源类->目标类):
{op_list_str}

【动作类型 Schema】(格式: id|名称|目标类):
{action_list_str}
{domain_code_clause}{domain_context}
【约束】:
1. 仅实例化文本中明确提到的实体，type必须是类ID。禁止创建文本未提及的实例。
2. object_props的关系ID必须已定义，且domain/range匹配。文本值放data_props，实体引用放object_props。
3. Label必须中文且具体（禁止用类名作label，禁止模糊代称如"本产品"）。
4. data_props键名用中文label，仅输出文档中明确提到的属性值。文档未提及的属性不要输出（不要输出空字符串key）。
5. 优先将实例分配给最具体的子类。
6. 必须提取动作实例（action_instances），action_type须匹配Schema。
7. 禁止输出classes/object_properties字段。
8. 【🔴 关键】只提取文本中明确提到的实例和动作！不要凭空创造、不要推测、不要重复。每个chunk最多提取15个实例和5个动作实例。
"""

        user_prompt_template = """【文本片段】:
"{chunk}"

输出JSON（仅action_instances/instances/links，按此顺序）:
{{
  "action_instances": [
    {{
      "action_type": "购买产品",
      "label": "张三购买理财A",
      "target_instance_label": "理财A",
      "target_type": "理财产品",
      "parameters": {{ "金额": "10万元" }}
    }}
  ],
  "instances": [
    {{
      "id": "ZhangSan",
      "type": "Employee",
      "label": "张三",
      "object_props": {{ "worksIn": ["DeptA"] }},
      "data_props": {{ "工号": "001", "职级": "P6", "部门": "技术部", "描述": "负责系统维护的技术人员" }}
    }}
  ],
  "links": [
    {{
      "link_type": "worksIn",
      "source_label": "张三",
      "source_type": "Employee",
      "target_label": "DeptA",
      "target_type": "Department"
    }}
  ]
}}

提醒: 仔细扫描文本，把与实例相关的所有信息都填入data_props对应属性；links必须声明每条关系含源/目标类型。"""

        # ★ 关键修复：如果传入了 documents 数组，使用它来保持文件名溯源信息
        # 否则 fallback 到旧的 _chunk_text 方法
        if documents and len(documents) > 0:
            chunks = self._chunk_documents(documents, chunk_size=chunk_size, overlap=chunk_overlap)
            logger.info(f"[InstanceExtraction] 使用 documents 数组进行切分：{len(documents)} 个文档")
        else:
            chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
            logger.info(f"[InstanceExtraction] 使用 text 进行切分：{len(chunks)} 个切片")
        
        total_chunks = len(chunks)

        # 构建防御性校验索引
        valid_class_ids: Set[str] = {c.get("id", c.get("name", "")) for c in classes}
        valid_class_ids.update({c.get("name", "") for c in classes if c.get("name")})
        class_label_to_id: Dict[str, str] = {c.get("label", ""): c.get("id", c.get("name", "")) for c in classes}
        class_name_to_id: Dict[str, str] = {c.get("name", ""): c.get("id", c.get("name", "")) for c in classes}

        def _resolve_class_ref(ref: str) -> str:
            if not ref:
                return ref
            if ref in valid_class_ids:
                return ref
            if ref in class_name_to_id:
                return class_name_to_id[ref]
            if ref in class_label_to_id:
                return class_label_to_id[ref]
            return ref

        op_constraints: Dict[str, Tuple[str, str]] = {}
        for op in obj_props:
            op_id = op.get("id", op.get("name", ""))
            src = _resolve_class_ref(op.get("source_object_type", op.get("domain", "")))
            tgt = _resolve_class_ref(op.get("target_object_type", op.get("range", "")))
            op_constraints[op_id] = (src, tgt)
        op_label_to_id: Dict[str, str] = {op.get("label", ""): op.get("id", op.get("name", "")) for op in obj_props}

        # 构建类到所有祖先类的映射（含自身），用于 Domain/Range 继承校验
        # 值集合中同时包含内部ID、英文名和中文标签，确保跨格式匹配
        class_to_ancestors: Dict[str, Set[str]] = {}
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            name = c.get("name", "")
            label = c.get("label", "")
            ancestors = {cid}
            if name and name != cid:
                ancestors.add(name)
            if label:
                ancestors.add(label)
            class_to_ancestors[cid] = ancestors
            if name and name != cid:
                class_to_ancestors[name] = ancestors
            if label and label != cid and label != name:
                class_to_ancestors[label] = ancestors

        for c in classes:
            cid = c.get("id", c.get("name", ""))
            name = c.get("name", "")
            label = c.get("label", "")
            parent_classes = c.get('sub_class_of', None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_ref in parent_classes:
                    parent_id = _resolve_class_ref(parent_ref)
                    if parent_id in class_to_ancestors:
                        parent_ancestors = class_to_ancestors[parent_id]
                        class_to_ancestors[cid].update(parent_ancestors)
                        if name and name != cid:
                            class_to_ancestors[name] = class_to_ancestors[cid]
                        if label and label != cid and label != name:
                            class_to_ancestors[label] = class_to_ancestors[cid]

        all_instances: Dict[str, dict] = {}  # det_id → instance dict
        all_action_instances: List[dict] = []
        discarded_count = 0

        for i, chunk in enumerate(chunks):
            # 检查取消
            check_cancelled()
            
            # chunk 现在是一个字典，包含 text, filename, chunk_index
            if isinstance(chunk, dict):
                chunk_text = chunk.get("text", "")
                chunk_filename = chunk.get("filename", "unknown")
                chunk_index = chunk.get("chunk_index", i)
            else:
                # 兼容旧格式（纯字符串）
                chunk_text = chunk
                chunk_filename = "unknown"
                chunk_index = i
            
            logger.info(f"[InstanceExtraction] 处理分块 {i+1}/{total_chunks} (file={chunk_filename}, index={chunk_index})")
            if chunk_filename and chunk_filename != "unknown":
                user_prompt = f"""【文档来源】: {chunk_filename}\n""" + user_prompt_template.format(chunk=chunk_text)
            else:
                user_prompt = user_prompt_template.format(chunk=chunk_text)
            data = self._call_llm(system_prompt, user_prompt, task_id=task_id, json_schema=INSTANCE_EXTRACTION_JSON_SCHEMA)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    # 在等待期间也定期检查取消标志
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        time.sleep(0.1)
                continue

            raw_instances = []
            raw_links = []
            raw_action_instances = []
            if isinstance(data, list):
                raw_instances = data
                logger.info(f"分块 {i+1}/{total_chunks} LLM 返回了列表格式，已自动适配")
            elif isinstance(data, dict):
                raw_instances = data.get("instances", [])
                raw_links = data.get("links", data.get("relationships", []))
                raw_action_instances = data.get("action_instances", [])
            else:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回格式异常 (type: {type(data)})，跳过")
                continue

            for inst in raw_instances:
                inst["_source_file"] = chunk_filename
                inst["_source_chunk_index"] = chunk_index
                inst["_source_quote"] = chunk_text[:200] if chunk_text else ""
                if product_code:
                    inst["_domain"] = product_code
                
                logger.debug(f"[InstanceExtraction] 实例 '{inst.get('label')}' 溯源：file={chunk_filename}, chunk={chunk_index}")
                raw_label = inst.get("label", "").strip()
                raw_type = inst.get("type", "").strip()

                if not raw_label:
                    continue

                resolved_type = None
                if raw_type in valid_class_ids:
                    resolved_type = raw_type
                elif raw_type in class_label_to_id:
                    resolved_type = class_label_to_id[raw_type]
                elif raw_type in class_name_to_id:
                    resolved_type = class_name_to_id[raw_type]
                else:
                    logger.warning(
                        f"[InstanceExtraction] 实例 '{raw_label}' 的 type '{raw_type}' "
                        f"不在 Schema 中，已丢弃"
                    )
                    discarded_count += 1
                    continue

                # ── 生成确定性 ID ──
                det_id = make_deterministic_id(raw_label, "Instance")
                if product_code:
                    det_id = f"{det_id}_{product_code}"

                # ── 防御性校验 object_props 连线 ──
                valid_obj_props: Dict[str, List[str]] = {}
                
                # ★ 获取该类允许的数据属性列表（包含继承属性）
                valid_data_props_keys = set()
                if resolved_type:
                    target_cls = next((c for c in classes if c.get("id", c.get("name", "")) == resolved_type), None)
                    if target_cls:
                        for p in target_cls.get("properties", []):
                            if isinstance(p, dict):
                                valid_data_props_keys.add(p.get("name", ""))
                        inherited_props = set(target_cls.get("inherited_properties", []) or [])
                        valid_data_props_keys = valid_data_props_keys | inherited_props

                raw_obj_props = inst.get("object_props", {})
                if not isinstance(raw_obj_props, dict):
                    raw_obj_props = {}
                
                # 确保 data_props 初始化且为 dict
                if "data_props" not in inst or not isinstance(inst["data_props"], dict):
                    inst["data_props"] = {}

                for op_key, targets in raw_obj_props.items():
                    targets_list = targets if isinstance(targets, list) else [targets]
                    
                    # ── 纠错逻辑：检查这是否其实是一个 Data Property (属性) ──
                    if op_key in valid_data_props_keys:
                        # 这是一个属性，模型把它放错位置了 -> 挪到 data_props
                        val_str = ", ".join([str(t) for t in targets_list]) # 简单的列表转字符串
                        inst["data_props"][op_key] = val_str
                        logger.info(f"[AutoFix] 将误入 object_props 的属性 '{op_key}' 移动至 data_props")
                        continue
                    # ──────────────────────────────────────────────────

                    # 下面是正常的 Object Property 校验逻辑
                    resolved_op_id = op_key
                    if resolved_op_id not in op_constraints:
                        if resolved_op_id in op_label_to_id:
                            resolved_op_id = op_label_to_id[resolved_op_id]
                        else:
                            logger.warning(
                                f"[EdgeDiscard] 实例 '{raw_label}' (Type: {resolved_type}) 试图通过关系 '{op_key}' 连接，"
                                f"但被拦截。原因：关系未在 Schema 中定义（ID/Label 均未匹配）。"
                            )
                            discarded_count += len(targets_list)
                            continue

                    expected_domain, expected_range = op_constraints[resolved_op_id]

                    # 校验起点实例的父类是否符合 ObjectProperty 的 domain
                    # 支持继承：如果实例的 type 是 domain 类的子类，也允许通过
                    instance_ancestors = class_to_ancestors.get(resolved_type, {resolved_type})
                    if not resolved_type or expected_domain not in instance_ancestors:
                        logger.warning(
                            f"[EdgeDiscard] 实例 '{raw_label}' (Type: {resolved_type}) 试图通过关系 '{resolved_op_id}' 连接，"
                            f"但被拦截。原因：Domain 不匹配（期望：{expected_domain}, 实际：{resolved_type}，"
                            f"祖先类：{instance_ancestors}）。"
                        )
                        discarded_count += len(targets_list)
                        continue

                    valid_targets = []
                    for t_raw in targets_list:
                        # 如果目标是 dict（LLM 返回了完整对象），提取其 label 或 id 作为引用
                        if isinstance(t_raw, dict):
                            t_raw = t_raw.get("label") or t_raw.get("id") or t_raw.get("name", "")
                            if not t_raw:
                                continue
                        # 确保 t_raw 是字符串
                        t_raw = str(t_raw)
                        
                        if t_raw in all_instances:
                            valid_targets.append(t_raw)
                        else:
                            found = False
                            for existing_id, existing_inst in all_instances.items():
                                if names_are_similar(t_raw, existing_inst.get("label", "")):
                                    valid_targets.append(existing_id)
                                    found = True
                                    logger.info(f"[InstanceExtraction] 模糊匹配：'{t_raw}' -> '{existing_inst['label']}' (id={existing_id})")
                                    break
                            if not found:
                                valid_targets.append(t_raw)

                    if valid_targets:
                        valid_obj_props[resolved_op_id] = valid_targets

                if det_id not in all_instances:
                    all_instances[det_id] = {
                        "id": det_id,
                        "type": resolved_type,
                        "label": raw_label,
                        "object_props": valid_obj_props,
                        "data_props": inst.get("data_props", {}),
                        "_source_file": inst.get("_source_file", ""),
                        "_source_chunk_index": inst.get("_source_chunk_index", 0),
                        "_source_quote": inst.get("_source_quote", ""),
                    }
                else:
                    # 合并同一实例的属性
                    existing = all_instances[det_id]
                    for op_id, targets in valid_obj_props.items():
                        if op_id in existing["object_props"]:
                            # 去重合并：将可能包含 dict 的目标转为 JSON 字符串再比较
                            existing_targets = existing["object_props"][op_id]
                            seen = set()
                            merged = []
                            for t in existing_targets + targets:
                                # 将 dict 转为可哈希的 JSON 字符串用于去重
                                key = json.dumps(t, sort_keys=True) if isinstance(t, dict) else t
                                if key not in seen:
                                    seen.add(key)
                                    merged.append(t)
                            existing["object_props"][op_id] = merged
                        else:
                            existing["object_props"][op_id] = targets
                    existing["data_props"].update(inst.get("data_props", {}))

            # ── 处理 links 数组：补充和修正实例间的关系 ──
            # links 提供了更精确的 source/target 类型信息，可以修正 object_props 中可能放错的关系
            for link in raw_links:
                link_type = link.get("link_type", link.get("type", ""))
                source_label = link.get("source_label", link.get("source_object_name", ""))
                source_type_raw = link.get("source_type", link.get("source_object_type", ""))
                target_label = link.get("target_label", link.get("target_object_name", ""))
                target_type_raw = link.get("target_type", link.get("target_object_type", ""))

                if not link_type or not source_label or not target_label:
                    continue

                # 解析 link_type
                resolved_op_id = link_type
                if resolved_op_id not in op_constraints:
                    if resolved_op_id in op_label_to_id:
                        resolved_op_id = op_label_to_id[resolved_op_id]
                    else:
                        continue

                # 解析 source_type
                resolved_source_type = None
                if source_type_raw in valid_class_ids:
                    resolved_source_type = source_type_raw
                elif source_type_raw in class_label_to_id:
                    resolved_source_type = class_label_to_id[source_type_raw]

                # 解析 target_type
                resolved_target_type = None
                if target_type_raw in valid_class_ids:
                    resolved_target_type = target_type_raw
                elif target_type_raw in class_label_to_id:
                    resolved_target_type = class_label_to_id[target_type_raw]

                # 查找 source 实例
                source_det_id = make_deterministic_id(source_label.strip(), "Instance")
                if product_code:
                    source_det_id = f"{source_det_id}_{product_code}"
                if source_det_id not in all_instances:
                    for eid, einst in all_instances.items():
                        if names_are_similar(source_label, einst.get("label", "")):
                            source_det_id = eid
                            break

                # 查找 target 实例
                target_det_id = make_deterministic_id(target_label.strip(), "Instance")
                if product_code:
                    target_det_id = f"{target_det_id}_{product_code}"
                if target_det_id not in all_instances:
                    for eid, einst in all_instances.items():
                        if names_are_similar(target_label, einst.get("label", "")):
                            target_det_id = eid
                            break

                if source_det_id not in all_instances or target_det_id not in all_instances:
                    continue

                # 校验 Domain/Range（支持继承）
                expected_domain, expected_range = op_constraints[resolved_op_id]
                actual_source_type = all_instances[source_det_id].get("type", "")
                actual_target_type = all_instances[target_det_id].get("type", "")

                source_ancestors = class_to_ancestors.get(actual_source_type, {actual_source_type})
                target_ancestors = class_to_ancestors.get(actual_target_type, {actual_target_type})

                if expected_domain not in source_ancestors:
                    logger.debug(
                        f"[LinkDiscard] Link Domain 不匹配：关系 '{resolved_op_id}'，"
                        f"source '{source_label}'(type={actual_source_type})，"
                        f"期望 domain={expected_domain}"
                    )
                    continue
                if expected_range not in target_ancestors:
                    logger.debug(
                        f"[LinkDiscard] Link Range 不匹配：关系 '{resolved_op_id}'，"
                        f"target '{target_label}'(type={actual_target_type})，"
                        f"期望 range={expected_range}"
                    )
                    continue

                # 将 link 补充到 source 实例的 object_props 中
                source_inst = all_instances[source_det_id]
                if resolved_op_id not in source_inst["object_props"]:
                    source_inst["object_props"][resolved_op_id] = [target_det_id]
                elif target_det_id not in source_inst["object_props"][resolved_op_id]:
                    source_inst["object_props"][resolved_op_id].append(target_det_id)

                logger.debug(f"[LinkProcess] Link 补充：'{source_label}' --[{resolved_op_id}]--> '{target_label}'")

            # ── 处理 action_instances 数组 ──
            for ai in raw_action_instances:
                action_type_name = ai.get("action_type", "").strip()
                ai_label = ai.get("label", "").strip()
                target_instance_label = ai.get("target_instance_label", "").strip()
                target_type = ai.get("target_type", "").strip()

                if not action_type_name or not ai_label or not target_instance_label:
                    logger.warning(f"[ActionInstanceDiscard] 动作实例缺少必填字段，已丢弃: {ai}")
                    continue

                # ─ Action Type 模糊匹配（支持简称、同义词、包含关系）──
                valid_action_type = False
                matched_at_name = None
                
                # 策略1：精确匹配（name 或 label）
                for at in action_types:
                    at_name = at.get("name", "")
                    at_label = at.get("label", "")
                    if action_type_name == at_name or action_type_name == at_label:
                        valid_action_type = True
                        matched_at_name = at_name
                        break
                
                # 策略2：包含关系匹配（Schema名称包含LLM返回的名称，或反之）
                if not valid_action_type:
                    for at in action_types:
                        at_name = at.get("name", "")
                        at_label = at.get("label", "")
                        # 检查双向包含关系
                        if (action_type_name in at_name or at_name in action_type_name or
                            action_type_name in at_label or at_label in action_type_name):
                            valid_action_type = True
                            matched_at_name = at_name
                            logger.info(f"[ActionTypeFuzzyMatch] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' 通过包含关系匹配到 Schema '{at_name}'")
                            break
                
                # 策略3：常见同义词映射
                if not valid_action_type:
                    synonym_map = {
                        "申购": ["申购产品", "购买产品"],
                        "赎回": ["赎回产品"],
                        "撤单": ["撤销申请"],
                        "撤单申请": ["撤销申请"],
                        "调整业绩比较基准": ["变更业绩比较基准"],
                        "调整费用": ["调整收费标准", "调整费用"],
                        "延缓支付": ["延期清算"],
                        "提前终止": ["提前终止产品", "终止产品"],
                        "分配收益": ["分红"],
                        "收取强制赎回费": ["巨额赎回处理"],
                        "调整投资组合": ["调整投资范围"],
                        "进行估值": ["暂停估值", "纠正估值错误"],
                        "增设份额类别": ["调整收费标准"],
                        "拒绝申购": ["拒绝申请"],
                        "拒绝赎回": ["拒绝申请"],
                        "拒绝认购": ["拒绝申请"],
                        "发布重大事项公告": ["发布产品信息", "发布披露"],
                    }
                    for synonym, targets in synonym_map.items():
                        if action_type_name == synonym or synonym in action_type_name or action_type_name in synonym:
                            for target in targets:
                                for at in action_types:
                                    if at.get("name") == target or at.get("label") == target:
                                        valid_action_type = True
                                        matched_at_name = target
                                        logger.info(f"[ActionTypeSynonym] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' 通过同义词匹配到 Schema '{target}'")
                                        break
                                if valid_action_type:
                                    break
                        if valid_action_type:
                            break
                
                if valid_action_type and matched_at_name:
                    action_type_name = matched_at_name
                if not valid_action_type:
                    logger.warning(
                        f"[ActionInstanceDiscard] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' "
                        f"不在 Schema 中，已丢弃"
                    )
                    continue

                resolved_target_type = _resolve_class_ref(target_type)
                if resolved_target_type not in valid_class_ids:
                    logger.warning(
                        f"[ActionInstanceDiscard] 动作实例 '{ai_label}' 的 target_type '{target_type}' "
                        f"不在 Schema 中，已丢弃"
                    )
                    continue

                ai_data = {
                    "action_type": action_type_name,
                    "label": ai_label,
                    "target_instance_label": target_instance_label,
                    "target_type": resolved_target_type,
                    "source_quote": ai.get("source_quote", ""),
                    "parameters": ai.get("parameters", {}),
                    "_source_file": chunk_filename,
                    "_source_chunk_index": chunk_index,
                }
                if ai.get("id"):
                    ai_data["id"] = ai["id"]

                all_action_instances.append(ai_data)
                logger.debug(f"[ActionInstanceProcess] 动作实例：'{ai_label}' (type={action_type_name}, target={target_instance_label}, file={chunk_filename}, chunk={chunk_index})")

            # 更新进度
            progress = (i + 1) / total_chunks * 0.9  # 预留 10% 给后续处理
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                time.sleep(request_interval)

        result = {
            "instances": list(all_instances.values()),
            "action_instances": all_action_instances,
            "discarded_edges_count": discarded_count,
            "metadata": {
                "total_chunks": total_chunks,
                "successful_chunks": sum(1 for i in range(total_chunks)),
                "failed_chunks": 0,
                "success_rate": 1.0,
                "total_instances": len(all_instances),
                "total_action_instances": len(all_action_instances),
                "total_edges": sum(len(inst.get("object_props", {})) for inst in all_instances.values()),
                "discarded_edges_count": discarded_count,
            },
        }
        report_progress(1.0, f"实例提取完成：{len(result['instances'])} 个实例，{len(all_action_instances)} 个动作实例")
        logger.info(
            f"[InstanceExtraction] 完成：{len(result['instances'])} 个实例，"
            f"{len(all_action_instances)} 个动作实例，"
            f"{discarded_count} 条不合规连线已丢弃"
        )
        
        # ★ 详细日志：打印提取结果（前 5 个实例）
        logger.info("=" * 80)
        logger.info("[InstanceExtraction] ★ 提取结果详情（前 5 个实例）:")
        for i, inst in enumerate(result["instances"][:5]):
            logger.info(f"  [{i+1}] id={inst['id']}, type={inst['type']}, label={inst['label']}")
            logger.info(f"      data_props={inst.get('data_props', {})}")
            logger.info(f"      object_props={inst.get('object_props', {})}")
            logger.info(f"      溯源：file={inst.get('_source_file')}, chunk={inst.get('_source_chunk_index')}, quote={inst.get('_source_quote', '')[:50]}...")
        
        if len(result["instances"]) > 5:
            logger.info(f"  ... 还有 {len(result['instances']) - 5} 个实例")
        
        logger.info(f"[InstanceExtraction] 丢弃的边数：{discarded_count}")
        logger.info("=" * 80)
        
        return result

    def extract_instances_with_constraints(
        self,
        text: str,
        schema_graph: Dict[str, Any],
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,  # ★ 新增：支持传入文档数组
    ) -> Dict[str, Any]:
        """
        对外暴露的「带 Schema 约束的实例提取」方法。
        语义等价于 extract_instances，但命名上强调强约束规则，便于路由层对齐 API 设计。
        
        ★ 关键修复：支持传入 documents 数组，保持原始文件名溯源信息
        """
        return self.extract_instances(
            text=text,
            schema_graph=schema_graph,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            product_code=product_code,
            task_id=task_id,
            progress_callback=progress_callback,
            documents=documents,  # ★ 传递 documents 数组
        )

    # ──────────────────────────────────────────
    # 图数据转换工具：Schema / Instance → GraphData
    # ──────────────────────────────────────────

    @staticmethod
    def schema_to_graph_data(schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Schema（object_types + link_types + action_types）转换为前端可渲染的 {nodes, edges}。
        同时兼容旧格式（classes + object_properties）。
        节点 ID 优先使用类名（name），兼容内部 det_id。
        """
        nodes = []
        edges = []
        processed = set()

        object_types, link_types, action_types = _normalize_schema_graph(schema)

        name_to_node_id: Dict[str, str] = {}

        for ot in object_types:
            raw_id = ot.get("id", "")
            name = ot.get("name", "")
            cid = name if name else raw_id
            if cid in processed:
                continue
            if name:
                name_to_node_id[name] = cid
            if raw_id and raw_id != cid:
                name_to_node_id[raw_id] = cid
            label = ot.get("label", "")
            if label and label not in name_to_node_id:
                name_to_node_id[label] = cid
            props_with_type = {}
            prop_defs = ot.get("properties", [])
            for p in prop_defs:
                if isinstance(p, dict):
                    pname = p.get("name", "")
                    props_with_type[pname] = p.get("data_type", "")
            node_data = {
                "label": label,
                "type": "owl:Class",
                "properties": props_with_type,
                "description": ot.get("description", ""),
            }
            if raw_id and raw_id != cid:
                node_data["raw_id"] = raw_id
            if prop_defs:
                node_data["property_definitions"] = prop_defs
            if ot.get("primary_key"):
                node_data["primary_key"] = ot["primary_key"]
            nodes.append({
                "id": cid,
                "type": "custom",
                "position": {"x": 0, "y": 0},
                "data": node_data,
            })
            processed.add(cid)

        def _resolve_node_id(ref: str) -> Optional[str]:
            if not ref:
                return None
            if ref in processed:
                return ref
            return name_to_node_id.get(ref)

        for ot in object_types:
            raw_id = ot.get("id", "")
            name = ot.get("name", "")
            cid = name if name else raw_id
            parent_classes = ot.get("sub_class_of", None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_ref in parent_classes:
                    parent_id = _resolve_node_id(parent_ref)
                    if parent_id and parent_id != cid:
                        edges.append({
                            "id": f"e_subclass_{cid}_{parent_id}",
                            "source": cid,
                            "target": parent_id,
                            "label": "subClassOf",
                            "type": "custom",
                            "data": {"label": "subClassOf", "relation": "subclass_of"},
                        })

        for lt in link_types:
            src_ref = lt.get("source_object_type", lt.get("domain", ""))
            tgt_ref = lt.get("target_object_type", lt.get("range", ""))
            src = _resolve_node_id(src_ref)
            tgt = _resolve_node_id(tgt_ref)
            lt_id = lt.get("id", lt.get("name", ""))
            if src and tgt:
                edge_data = {"label": lt.get("label", ""), "prop_id": lt_id, "relation": "object_property"}
                if lt.get("cardinality"):
                    edge_data["cardinality"] = lt["cardinality"]
                if lt.get("description"):
                    edge_data["description"] = lt["description"]
                edges.append({
                    "id": f"e_{src}_{tgt}_{lt_id}",
                    "source": src,
                    "target": tgt,
                    "label": lt.get("label", ""),
                    "type": "custom",
                    "data": edge_data,
                })

        for at in action_types:
            at_id = at.get("id", at.get("name", ""))
            target_ref = at.get("target_object_type", "")
            target_ot = _resolve_node_id(target_ref)
            if at_id not in processed:
                params_info = {}
                for pm in at.get("parameters", []):
                    if isinstance(pm, dict):
                        params_info[pm.get("name", "")] = pm.get("data_type", "")
                nodes.append({
                    "id": at_id,
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": at.get("label", ""),
                        "type": "owl:ActionType",
                        "raw_id": at_id if at_id.startswith("AT_") else at.get("id", ""),
                        "description": at.get("description", ""),
                        "parameters": at.get("parameters", []),
                        "properties": params_info,
                    },
                })
                processed.add(at_id)
            if target_ot and target_ot in processed:
                edges.append({
                    "id": f"e_action_{at_id}_{target_ot}",
                    "source": at_id,
                    "target": target_ot,
                    "label": at.get("label", ""),
                    "type": "custom",
                    "data": {"label": at.get("label", ""), "relation": "action", "prop_id": at_id},
                })

        # 边去重：相同source+target+relation的边只保留一条
        seen_edges = set()
        deduped_edges = []
        for e in edges:
            e_key = (e.get("source", ""), e.get("target", ""),
                     e.get("data", {}).get("relation", ""), e.get("label", ""))
            if e_key not in seen_edges:
                seen_edges.add(e_key)
                deduped_edges.append(e)
        if len(deduped_edges) < len(edges):
            logger.info(f"[merge_instances_to_graph_data] 边去重: {len(edges)} → {len(deduped_edges)}")

        return {"nodes": nodes, "edges": deduped_edges}

    @staticmethod
    def merge_instances_to_graph_data(
        schema_graph_data: Dict[str, Any],
        instances: List[dict],
        action_instances: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        """
        将实例合并到已有的 Schema GraphData 中，生成完整图。
        - 类节点保持蓝色（owl:Class）
        - 实例节点标记为 owl:NamedIndividual
        - instance_of 连线（虚线）+ ObjectProperty 连线（实线）
        """
        import copy
        nodes = copy.deepcopy(schema_graph_data.get("nodes", []))
        edges = copy.deepcopy(schema_graph_data.get("edges", []))
        existing_ids = {n["id"] for n in nodes}

        # 方便通过 label 查找实例 det_id
        label_to_inst_id: Dict[str, str] = {}

        # 实例去重：检测并合并相似实例
        # 问题：LLM 可能在多个 chunk 中提取出同一实体的不同版本（如"量子安全平台 1.0"、"量子安全平台 2.0"等）
        # 解决方案：对同一 label 前缀的实例进行分组，只保留最完整的一个
        
        def get_instance_prefix(label: str) -> str:
            """提取实例 label 的前缀（去除版本号等后缀）"""
            # 匹配常见版本号模式：1.0, 2.0, V1, V2 等
            import re
            # 移除末尾的版本号模式
            prefix = re.sub(r'[\s_]?[vV]?[\d\.]+$', '', label.strip())
            return prefix.strip()
        
        # 按 label 前缀和 type 分组实例
        from collections import defaultdict
        inst_groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for inst in instances:
            prefix = get_instance_prefix(inst["label"])
            type_key = inst.get("type", "")
            inst_groups[(prefix, type_key)].append(inst)
        
        # 对每组实例进行去重：保留属性最完整的那个
        deduplicated_instances: List[dict] = []
        for (prefix, type_key), group in inst_groups.items():
            if len(group) == 1:
                deduplicated_instances.append(group[0])
            else:
                # 选择属性最丰富的实例
                def count_props(inst):
                    obj_count = len(inst.get("object_props", {}))
                    data_count = len(inst.get("data_props", {}))
                    return obj_count + data_count
                
                # 按属性数量排序，保留最丰富的那个
                best_inst = max(group, key=count_props)
                
                # 合并所有实例的属性到最佳实例
                for inst in group:
                    if inst["id"] != best_inst["id"]:
                        # 合并 object_props
                        for op_id, targets in inst.get("object_props", {}).items():
                            if op_id not in best_inst["object_props"]:
                                best_inst["object_props"][op_id] = []
                            for t in targets:
                                if t not in best_inst["object_props"][op_id]:
                                    best_inst["object_props"][op_id].append(t)
                        # 合并 data_props
                        for dp_name, value in inst.get("data_props", {}).items():
                            if dp_name not in best_inst["data_props"]:
                                best_inst["data_props"][dp_name] = value
                
                deduplicated_instances.append(best_inst)
        
        logger.info(f"[merge_instances_to_graph_data] 实例去重：{len(instances)} -> {len(deduplicated_instances)}")
        
        # 使用去重后的实例列表
        instances = deduplicated_instances

        # 构建 class_id → class_label 映射，用于实例节点展示
        class_id_to_label: Dict[str, str] = {}
        class_id_to_prop_defs: Dict[str, List[Dict]] = {}
        det_id_to_node_id: Dict[str, str] = {}
        # 构建类节点label集合，用于过滤与类同名的空实例
        class_labels: Set[str] = set()
        for n in nodes:
            n_type = n.get("data", {}).get("type", "")
            if n_type in ("owl:Class", "owl:ActionType"):
                class_id_to_label[n["id"]] = n["data"].get("label", "")
                class_id_to_prop_defs[n["id"]] = n["data"].get("property_definitions", [])
                n_label = n["data"].get("label", "")
                if n_label:
                    class_labels.add(n_label)
                raw_id = n.get("data", {}).get("raw_id", "")
                if raw_id and raw_id != n["id"]:
                    det_id_to_node_id[raw_id] = n["id"]

        # 构建 op_id → op_label 映射，用于边的可读标签
        op_id_to_label: Dict[str, str] = {}
        for e in edges:
            eid = e.get("data", {}).get("prop_id", "")
            elabel = e.get("data", {}).get("label", "")
            if eid and elabel:
                op_id_to_label[eid] = elabel
                # 同时用边ID和label本身作为key，增加匹配率
                edge_id = e.get("id", "")
                if edge_id:
                    op_id_to_label[edge_id] = elabel
        
        def _extract_readable_op_label(op_id: str) -> str:
            """从op_id中提取可读的关系名称。
            处理 e_node_xxx_object_property_关联目录 等格式，提取最后的中文部分。
            """
            if not op_id:
                return op_id
            # 如果已经是纯中文/可读名称，直接返回
            if not any(c in op_id for c in '_-'):
                return op_id
            # 尝试提取 _object_property_ 后面的部分
            if '_object_property_' in op_id:
                parts = op_id.split('_object_property_')
                if len(parts) > 1 and parts[-1]:
                    return parts[-1]
            # 尝试提取最后一个下划线后的部分（如果是中文）
            last_part = op_id.rsplit('_', 1)[-1] if '_' in op_id else op_id
            if last_part and any('\u4e00' <= c <= '\u9fff' for c in last_part):
                return last_part
            return op_id
        
        for inst in instances:
            iid = inst["id"]
            if iid not in existing_ids:
                inst_label = inst.get("label", "")
                node_properties = dict(inst.get("data_props", {}))
                # 过滤空字符串属性值，减少无意义输出
                node_properties = {k: v for k, v in node_properties.items() if v is not None and v != ""}
                
                # 过滤与类/动作类型同名的实例（LLM误将类名/动作类型名当作实例）
                # 条件：实例label与某个类/动作类型label相同，且实例没有有意义的data_props
                # （排除仅有"动作类型"、"目标实例"等模板属性的实例）
                if inst_label in class_labels:
                    meaningful_props = {k: v for k, v in node_properties.items()
                                       if k not in ("动作类型", "目标实例", "目标类型") and v}
                    if not meaningful_props and not inst.get("object_props"):
                        logger.info(f"[merge_instances_to_graph_data] 跳过与类/动作类型同名的空实例: '{inst_label}'")
                        continue
                
                inst_type = inst.get("type", "")
                resolved_type_node = det_id_to_node_id.get(inst_type, inst_type)
                if resolved_type_node not in class_id_to_label and inst_type in class_id_to_label:
                    resolved_type_node = inst_type
                prop_defs = class_id_to_prop_defs.get(resolved_type_node, [])
                
                if prop_defs:
                    name_to_label = {}
                    for pd in prop_defs:
                        if isinstance(pd, dict):
                            pname = pd.get("name", "")
                            plabel = pd.get("label", "")
                            if pname and plabel and pname != plabel:
                                name_to_label[pname] = plabel
                    if name_to_label:
                        renamed = {}
                        for k, v in node_properties.items():
                            if k in name_to_label:
                                renamed[name_to_label[k]] = v
                            else:
                                renamed[k] = v
                        node_properties = renamed
                
                class_label = class_id_to_label.get(resolved_type_node, "")
                
                # 构建实例描述
                desc_parts = [f"{inst['label']}是一个{class_label or '实例'}"]
                prop_desc_parts = []
                for k, v in node_properties.items():
                    if k in ("description", "_source_file", "_source_quote", "_source_chunk_index"):
                        continue
                    if v is None or v == "":
                        continue
                    prop_desc_parts.append(f"{k}: {v}")
                if prop_desc_parts:
                    desc_parts.append("，属性包括" + "，".join(prop_desc_parts))
                inst_description = "".join(desc_parts)
                
                inst_node_data = {
                    "label": inst["label"],
                    "type": "owl:NamedIndividual",
                    "class_label": class_label,
                    "properties": node_properties,
                    "source_document": inst.get("_source_file", ""),
                    "description": inst_description,
                }
                if inst.get("_source_quote"):
                    inst_node_data["source_quote"] = inst["_source_quote"]
                if inst.get("_source_chunk_index") is not None:
                    inst_node_data["source_chunk_index"] = inst["_source_chunk_index"]
                if inst.get("_domain"):
                    inst_node_data["domain"] = inst["_domain"]
                if prop_defs:
                    inst_node_data["property_definitions"] = prop_defs
                nodes.append({
                    "id": iid,
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": inst_node_data,
                })
                existing_ids.add(iid)
            label_to_inst_id[inst["label"]] = iid
            label_to_inst_id[iid] = iid  # self-map

            # rdf:type 连线（实例 → 类，虚线）
            type_class = inst.get("type")
            resolved_type_node = det_id_to_node_id.get(type_class, type_class)
            if resolved_type_node and resolved_type_node in existing_ids:
                edges.append({
                    "id": f"e_{iid}_type_{resolved_type_node}",
                    "source": iid,
                    "target": resolved_type_node,
                    "label": "type",
                    "type": "custom",
                    "style": {"strokeDasharray": "5,5"},
                    "data": {"label": "type", "relation": "type"},
                })

        # 实例间的 ObjectProperty 连线（实线）
        for inst in instances:
            src_id = inst["id"]
            for op_id, targets in inst.get("object_props", {}).items():
                # 使用可读的关系标签：优先映射表，回退到提取可读名称
                op_label = op_id_to_label.get(op_id, _extract_readable_op_label(op_id))
                for t_raw in targets:
                    # 先按确定性 ID 查，再按 label 查
                    tgt_id = label_to_inst_id.get(t_raw)
                    if tgt_id and tgt_id in existing_ids:
                        edges.append({
                            "id": f"e_{src_id}_{tgt_id}_{op_id}",
                            "source": src_id,
                            "target": tgt_id,
                            "label": op_label,
                            "type": "custom",
                            "data": {"label": op_label, "prop_id": op_id, "relation": "object_property"},
                        })

        # Action Type 实例处理
        if action_instances:
            action_type_to_node_id: Dict[str, str] = {}
            for n in nodes:
                n_type = n.get("data", {}).get("type", "")
                if n_type in ("owl:Class", "owl:ActionType"):
                    raw_id = n.get("data", {}).get("raw_id", "")
                    node_id = n["id"]
                    node_label = n.get("data", {}).get("label", "")
                    is_action = raw_id.startswith("AT_") or node_id.startswith("AT_") or n_type == "owl:ActionType"
                    if is_action:
                        action_type_to_node_id[node_id] = node_id
                        if raw_id:
                            action_type_to_node_id[raw_id] = node_id
                        if node_label:
                            action_type_to_node_id[node_label] = node_id
                        for pd in n.get("data", {}).get("property_definitions", []):
                            if isinstance(pd, dict):
                                pd_name = pd.get("name", "")
                                if pd_name:
                                    action_type_to_node_id[pd_name] = node_id

            logger.info(f"[merge_instances_to_graph_data] Action Type 映射表: {list(action_type_to_node_id.keys())}")
            logger.info(f"[merge_instances_to_graph_data] 待处理动作实例: {len(action_instances)} 个")

            for ai in action_instances:
                action_type = ai.get("action_type", "")
                ai_label = ai.get("label", "")
                target_label = ai.get("target_instance_label", "")
                target_type = ai.get("target_type", "")
                source_quote = ai.get("source_quote", "")

                # 将target_type解析为可读的类名
                resolved_target_type = det_id_to_node_id.get(target_type, target_type)
                target_type_label = class_id_to_label.get(resolved_target_type, target_type)
                if target_type_label == resolved_target_type and target_type != resolved_target_type:
                    # det_id_to_node_id解析成功但class_id_to_label中没有，尝试用原始值
                    target_type_label = class_id_to_label.get(target_type, target_type)

                ai_id = f"action_{make_deterministic_id(ai_label, 'ActionInstance')}"

                if ai_id not in existing_ids:
                    ai_props = {
                        "动作类型": action_type,
                        "目标实例": target_label,
                        "目标类型": target_type_label,
                    }
                    # 保留 parameters 信息
                    params = ai.get("parameters", {})
                    if params and isinstance(params, dict):
                        for pk, pv in params.items():
                            if pk and pv:
                                ai_props[pk] = pv

                    action_class_id = action_type_to_node_id.get(action_type, "")
                    if not action_class_id:
                        for key, val in action_type_to_node_id.items():
                            if action_type in key or key in action_type:
                                action_class_id = val
                                logger.info(f"[merge_instances_to_graph_data] 动作实例 '{ai_label}' 的 action_type '{action_type}' 通过包含关系匹配到节点 '{val}'")
                                break

                    # 构建动作实例描述
                    # 获取动作类型的label（更友好的描述）
                    action_type_label = action_type
                    if action_class_id:
                        action_class_node = next((n for n in nodes if n["id"] == action_class_id), None)
                        if action_class_node:
                            action_type_label = action_class_node.get("data", {}).get("label", action_type)
                    ai_desc_parts = [f"{ai_label}（{action_type_label}）"]
                    if target_label:
                        ai_desc_parts.append(f"，目标：{target_label}")
                    # 添加parameters信息
                    param_parts = []
                    for k, v in ai_props.items():
                        if k in ("description", "_source_file", "_source_quote", "_source_chunk_index",
                                 "动作类型", "目标实例", "目标类型"):
                            continue
                        if v is None or v == "":
                            continue
                        param_parts.append(f"{k}: {v}")
                    if param_parts:
                        ai_desc_parts.append("，参数：" + "，".join(param_parts))
                    ai_description = "".join(ai_desc_parts)

                    ai_node_data = {
                        "label": ai_label,
                        "type": "owl:NamedIndividual",
                        "class_label": action_type,
                        "properties": ai_props,
                        "source_document": ai.get("_source_file", ""),
                        "_is_action_instance": True,
                        "description": ai_description,
                    }
                    if source_quote:
                        ai_node_data["source_quote"] = source_quote
                    if ai.get("_source_chunk_index") is not None:
                        ai_node_data["source_chunk_index"] = ai["_source_chunk_index"]
                    if action_class_id:
                        action_class_node = next((n for n in nodes if n["id"] == action_class_id), None)
                        if action_class_node:
                            ai_node_data["raw_id"] = action_class_node.get("data", {}).get("raw_id", "")
                            ai_node_data["class_label"] = action_class_node.get("data", {}).get("label", action_type)
                    nodes.append({
                        "id": ai_id,
                        "type": "custom",
                        "position": {"x": 0, "y": 0},
                        "data": ai_node_data,
                    })
                    existing_ids.add(ai_id)

                    if action_class_id and action_class_id in existing_ids:
                        edges.append({
                            "id": f"e_{ai_id}_type_{action_class_id}",
                            "source": ai_id,
                            "target": action_class_id,
                            "label": "type",
                            "type": "custom",
                            "style": {"strokeDasharray": "5,5"},
                            "data": {"label": "type", "relation": "type"},
                        })

                    tgt_inst_id = label_to_inst_id.get(target_label)
                    if tgt_inst_id and tgt_inst_id in existing_ids:
                        edges.append({
                            "id": f"e_action_{ai_id}_{tgt_inst_id}",
                            "source": ai_id,
                            "target": tgt_inst_id,
                            "label": action_type,
                            "type": "custom",
                            "data": {"label": action_type, "relation": "action"},
                        })

        return {"nodes": nodes, "edges": edges}

# ──────────────────────────────────────────
    # 向量库同步
    # ──────────────────────────────────────────

    def sync_ttl_to_vector_store(
        self, 
        ttl_file_path: str, 
        progress=None, 
        delete_old: bool = True,
        project_id: Optional[int] = None,
        domain: Optional[str] = None,
        collection_name: Optional[str] = None,
    ) -> str:
        """
        同步 TTL 文件到向量库，支持知识域隔离和溯源信息。
        
        ★ 工业级 GraphRAG 增强入库版：
        1. 严格过滤：100% 只入库 NamedIndividual (业务实例)，彻底屏蔽 Schema 噪音。
        2. 语义拼接：实体名 + 原句，解决向量检索中的“代词指代不明”问题。
        3. 容错提取：兼容带下划线和不带下划线的溯源字段。
        """
        import json
        vector_manager = self.vector_manager
        
        filename = os.path.basename(ttl_file_path)
        if delete_old:
            if project_id:
                vector_manager.delete_by_expr(f'project_id == {project_id}')
            else:
                vector_manager.delete_by_expr(f'metadata like "%\\"source_file\\": \\"{filename}\\"%"')

        g = Graph()
        try:
            g.parse(ttl_file_path, format="turtle")
        except Exception as e:
            return f"❌ TTL 解析失败：{e}"

        def get_local(uri):
            u = str(uri)
            return u.split("#")[-1] if "#" in u else u.split("/")[-1]

        knowledge_texts = []
        knowledge_metas =[]
        count = 0

        # ──────────────────────────────────────────
        # ★ 核心防线：只获取真正的业务实例，彻底抛弃 Schema 噪音
        # ──────────────────────────────────────────
        instances = list(g.subjects(RDF.type, OWL.NamedIndividual))
        total = len(instances)  # ★ 修复：定义 total 变量用于进度计算
        
        for subj in instances:
            subj_id = get_local(subj)
            
            # 1. 提取当前实例的真实业务类名 (Type)
            subj_type = "未知类型"
            for t in g.objects(subj, RDF.type):
                t_id = get_local(t)
                if t_id != "NamedIndividual":
                    # 尝试获取类型的中文 label
                    t_labels = list(g.objects(t, RDFS.label))
                    subj_type = str(t_labels[0]) if t_labels else t_id
                    break

            props = {}
            relations = {}
            source_file = "未知文件"
            source_quote = ""
            inst_domain = domain or "通用域"
            chunk_index = 0

            # 2. 遍历该实例的所有谓词和宾语
            for p, o in g.predicate_objects(subj):
                p_id = get_local(p)
                val = str(o)

                # 跳过 rdf:type 这种内部元数据
                if p == RDF.type:
                    continue
                
                # ★ 兼容性提取：捕获我们埋入的溯源字段 (带不带下划线都认)
                if p_id in ["_source_file", "source_file"]:
                    source_file = val
                elif p_id in ["_source_quote", "source_quote"]:
                    source_quote = val
                elif p_id in ["_domain", "domain"]:
                    inst_domain = val
                elif p_id in ["_source_chunk_index", "chunk_index"]:
                    try:
                        chunk_index = int(val)
                    except (ValueError, TypeError):
                        pass
                
                # 提取正常的业务属性和关系
                elif p == RDFS.label:
                    props["label"] = val
                elif isinstance(o, URIRef):
                    # 这是一个关系边 (ObjectProperty)
                    o_id = get_local(o)
                    o_labels = list(g.objects(o, RDFS.label))
                    o_label = str(o_labels[0]) if o_labels else o_id
                    
                    # 取出关系的中文 label
                    p_labels = list(g.objects(p, RDFS.label))
                    p_label = str(p_labels[0]) if p_labels else p_id
                    
                    if p_label not in relations:
                        relations[p_label] = []
                    if o_label not in relations[p_label]:
                        relations[p_label].append(o_label)
                else:
                    # 这是一个数据属性 (DataProperty)
                    if p_id in props:
                        if isinstance(props[p_id], list):
                            if val not in props[p_id]: props[p_id].append(val)
                        elif props[p_id] != val:
                            props[p_id] = [props[p_id], val]
                    else:
                        props[p_id] = val

            # 获取实例展示名称
            subj_label = props.get("label", subj_id)

            # ──────────────────────────────────────────
            # ★ 终极优化：构建具有极高"向量召回率"的 Embedding 文本
            # ──────────────────────────────────────────
            # 问题：如果原句是 "本产品的限额是1万"，单独向量化会因为没有主语而搜不到。
            # 解决：在原句前面强行拼接实体名称和类型！
            
            embed_text = ""
            if source_quote and len(source_quote) > 5:
                # 语义融合拼装
                embed_text = f"【{subj_type}：{subj_label}】的原文记载：{source_quote}"
            else:
                # 如果没有原句，使用属性和关系拼接
                prop_parts =[f"{k}是{v}" for k, v in props.items() if k != "label"]
                rel_parts =[f"{k}{'、'.join(v)}" for k, v in relations.items()]
                prop_desc = "，".join(prop_parts)
                rel_desc = "，".join(rel_parts)
                
                embed_text = f"实体【{subj_label}】是一个{subj_type}。"
                if prop_desc: embed_text += f" 其属性有：{prop_desc}。"
                if rel_desc: embed_text += f" 关联信息：{rel_desc}。"

            # ──────────────────────────────────────────
            # ★ 构建严谨的 Metadata
            # ──────────────────────────────────────────
            meta = {
                "source": "ttl_instance",
                "project_id": project_id,               # 标量：租户隔离
                "domain": inst_domain,                  # 标量：领域隔离
                "source_file": source_file,             # 溯源：文件名
                "chunk_index": chunk_index,             # 溯源：切片序号
                "source_quote": source_quote,           # 溯源：原句高亮
                
                "subject": subj_label,
                "subject_type": subj_type,
                "properties": json.dumps(props, ensure_ascii=False),
                "relations": json.dumps(relations, ensure_ascii=False)
            }

            knowledge_texts.append(embed_text)
            knowledge_metas.append(meta)
            count += 1

            # 批量插入 Milvus (参数严格对应 vector_manager)
            if len(knowledge_texts) >= 100:
                vector_manager.insert_data(knowledge_texts, knowledge_metas)
                knowledge_texts =[]
                knowledge_metas =[]
                if progress:
                    progress(count / total, desc=f"同步中... {count}/{total}")

        # 插入剩余数据
        if knowledge_texts:
            vector_manager.insert_data(knowledge_texts, knowledge_metas)
            if progress:
                progress(1.0, desc=f"同步完成：{count} 条记录")

        logger.info(f"[TTL 同步] 完美写入完成：共处理 {count} 个纯业务实例 (project_id={project_id}, domain={domain})")
        
        return f"✅ 同步完成：成功写入 {count} 个纯业务实例到库 {vector_manager.collection_name}"
    # ──────────────────────────────────────────
    # 兼容旧接口（内部转两阶段调用）
    # ──────────────────────────────────────────

    def build_ontology(
        self,
        text: str,
        scenario_desc: str,
        entities_df,
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        progress=None,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        兼容旧的一步式调用接口，内部转换为两阶段执行，
        并将最终图数据序列化为 TTL 文件。
        """
        logger.info(f"build_ontology (两阶段兼容模式) 调用 chunk_size={chunk_size}")

        if progress:
            progress(0.0, desc="阶段 1：骨架提取...")

        # Phase 1: Schema
        schema = self.extract_schema(
            text,
            user_intent=scenario_desc,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            task_id=task_id,
            progress_callback=lambda p, m: progress(p, desc=m) if progress else None,
        )

        if progress:
            progress(0.4, desc="阶段 2：实例提取...")

        # Phase 2: Instances
        inst_result = self.extract_instances(
            text,
            schema_graph=schema,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            product_code=product_code,
            task_id=task_id,
            progress_callback=lambda p, m: progress(p, desc=m) if progress else None,
        )

        if progress:
            progress(0.9, desc="序列化 TTL...")

        # 构建 RDF Graph 并序列化为 TTL
        master_g = Graph()
        master_g.bind("ex", self.EX)
        master_g.bind("owl", OWL)
        master_g.bind("rdfs", RDFS)
        master_g.bind("xsd", XSD)

        onto_uri = URIRef("http://www.example.org/auto_ontology")
        master_g.add((onto_uri, RDF.type, OWL.Ontology))
        master_g.add((onto_uri, OWL.versionInfo, Literal("2.0")))

        def generate_safe_uri_id(original_name: str, prefix: str = "prop") -> str:
            """
            生成一个安全的 URI ID，使用 MD5 哈希保证唯一性。
            
            参数:
            - original_name: 原始名称（可能是中文）
            - prefix: 前缀，用于区分类型
            
            返回:
            - 安全的 ASCII ID，格式：{prefix}_{8 位 MD5 哈希}
            """
            import re
            if not original_name:
                return f"{prefix}_empty"
            
            # 始终使用 MD5 哈希保证唯一性（区分同音词如"使用"和"实用"）
            md5_hash = hashlib.md5(original_name.encode('utf-8')).hexdigest()
            return f"{prefix}_{md5_hash[:8]}"

        def get_uri(id_str: str) -> URIRef:
            """将 ID 字符串转换为安全 URI。
            
            对于类/实例/ObjectProperty，已经是 ASCII 确定性 ID（如 C_xxx），直接使用。
            对于 data property 键（可能是中文），使用 MD5 哈希生成安全 URI。
            """
            import re
            # 如果是纯 ASCII 且包含字母数字（如 C_xxx, OP_xxx, I_xxx），直接使用
            if re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', id_str):
                return self.EX[id_str]
            # 否则用 MD5 生成安全 URI
            safe_id = generate_safe_uri_id(id_str, prefix="DP")
            return self.EX[safe_id]

        def get_dp_uri(prop_name: str) -> URIRef:
            """DataProperty URI 生成：中文属性名使用 MD5 哈希。"""
            import re
            # 纯 ASCII 合法 ID（如 OP_xxx, DP_xxx）直接使用
            if re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', prop_name):
                return self.EX[prop_name]
            # 含中文的属性名：用 MD5 哈希生成唯一 ID
            safe_id = generate_safe_uri_id(prop_name, prefix="DP")
            return self.EX[safe_id]

        # 写入类相关内容（包括 DataProperty 声明）
        object_types, link_types, action_types = _normalize_schema_graph(schema)
        for ot in object_types:
            uri = self.EX[ot.get("id", ot.get("name", ""))]
            master_g.add((uri, RDF.type, OWL.Class))
            master_g.add((uri, RDFS.label, Literal(ot.get("label", ""), lang="zh")))
            if ot.get("description"):
                master_g.add((uri, RDFS.comment, Literal(ot["description"], lang="zh")))
            if ot.get("sub_class_of"):
                parent_id = ot["sub_class_of"]
                parent_uri = self.EX[parent_id]
                master_g.add((uri, RDFS.subClassOf, parent_uri))
            prop_defs = ot.get("properties", [])
            for pd in prop_defs:
                if not isinstance(pd, dict):
                    continue
                dp_name = pd.get("name", "")
                dp_uri = get_dp_uri(dp_name)
                master_g.add((dp_uri, RDF.type, OWL.DatatypeProperty))
                dp_label = pd.get("label", dp_name)
                master_g.add((dp_uri, RDFS.label, Literal(dp_label, lang="zh")))
                master_g.add((dp_uri, RDFS.domain, uri))
                if pd.get("description"):
                    master_g.add((dp_uri, RDFS.comment, Literal(pd["description"], lang="zh")))
                if pd.get("data_type"):
                    xsd_type_map = {
                        "string": XSD.string, "number": XSD.decimal,
                        "boolean": XSD.boolean, "date": XSD.date,
                        "datetime": XSD.dateTime, "array": XSD.string,
                        "object": XSD.string,
                    }
                    xsd_type = xsd_type_map.get(pd["data_type"], XSD.string)
                    master_g.add((dp_uri, RDFS.range, xsd_type))

        for lt in link_types:
            lt_id = lt.get("id", lt.get("name", ""))
            op_uri = self.EX[lt_id]
            master_g.add((op_uri, RDF.type, OWL.ObjectProperty))
            master_g.add((op_uri, RDFS.label, Literal(lt.get("label", ""), lang="zh")))
            src_id = lt.get("source_object_type", lt.get("domain", ""))
            tgt_id = lt.get("target_object_type", lt.get("range", ""))
            master_g.add((op_uri, RDFS.domain, self.EX[src_id]))
            master_g.add((op_uri, RDFS.range, self.EX[tgt_id]))
            if lt.get("description"):
                master_g.add((op_uri, RDFS.comment, Literal(lt["description"], lang="zh")))

        for inst in inst_result.get("instances", []):
            inst_uri = self.EX[inst["id"]]  # 实例 ID 已是 I_xxx 格式
            master_g.add((inst_uri, RDF.type, OWL.NamedIndividual))
            master_g.add((inst_uri, RDFS.label, Literal(inst["label"], lang="zh")))
            master_g.add((inst_uri, RDF.type, self.EX[inst["type"]]))
            for pid, targets in inst.get("object_props", {}).items():
                for t in (targets if isinstance(targets, list) else [targets]):
                    # op ID 已是 OP_xxx, 目标实例 ID 已是 I_xxx
                    master_g.add((inst_uri, self.EX[pid], self.EX[t]))
            for pid, val in inst.get("data_props", {}).items():
                dp_uri = get_dp_uri(pid)
                master_g.add((dp_uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((dp_uri, RDFS.label, Literal(pid, lang="zh")))
                master_g.add((
                    inst_uri, dp_uri,
                    Literal(val, lang="zh") if isinstance(val, str) else Literal(val)
                ))
            
            # ★ 关键修复：将溯源信息作为隐藏属性写入 TTL，确保向量入库时能透传原始文件名和 chunk_index
            if "_source_file" in inst:
                source_file_uri = self.EX["_source_file"]
                master_g.add((source_file_uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((source_file_uri, RDFS.label, Literal("源文件", lang="zh")))
                master_g.add((inst_uri, source_file_uri, Literal(inst["_source_file"], lang="zh")))
            
            if "_source_chunk_index" in inst:
                chunk_index_uri = self.EX["_source_chunk_index"]
                master_g.add((chunk_index_uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((chunk_index_uri, RDFS.label, Literal("切片索引", lang="zh")))
                master_g.add((inst_uri, chunk_index_uri, Literal(str(inst["_source_chunk_index"]), datatype=XSD.integer)))
            
            if "_source_quote" in inst:
                source_quote_uri = self.EX["_source_quote"]
                master_g.add((source_quote_uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((source_quote_uri, RDFS.label, Literal("原文引用", lang="zh")))
                master_g.add((inst_uri, source_quote_uri, Literal(inst["_source_quote"], lang="zh")))
            
            if "_domain" in inst:
                domain_uri = self.EX["_domain"]
                master_g.add((domain_uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((domain_uri, RDFS.label, Literal("知识域", lang="zh")))
                master_g.add((inst_uri, domain_uri, Literal(inst["_domain"], lang="zh")))


        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("TTL", exist_ok=True)
        filename = os.path.join("TTL", f"ontology_{timestamp}.ttl")
        master_g.serialize(filename, format="turtle")

        count_cls = len(list(master_g.subjects(RDF.type, OWL.Class)))
        count_inst = len(list(master_g.subjects(RDF.type, OWL.NamedIndividual)))
        msg = (
            f"✅ 两阶段构建成功！\n"
            f"- 类定义：{count_cls} 个\n"
            f"- 实例：{count_inst} 个\n"
            f"- 丢弃不合规连线：{inst_result['discarded_edges_count']} 条"
        )

        if progress:
            progress(1.0, desc="完成！")

        return filename, msg

    # ──────────────────────────────────────────
    # ★ 异步方法：生产级非阻塞接口
    # ──────────────────────────────────────────

    async def _async_call_llm(self, system_prompt: str, user_prompt: str,
                               task_id: Optional[str] = None, timeout: Optional[int] = None,
                               json_schema: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        streaming = self._get_streaming_config()
        if timeout is None:
            timeout = self._get_timeout_config()
        try:
            if task_id and task_manager.is_cancelled(task_id):
                raise TaskCancelledError("Task cancelled before LLM call")
            result = await self.llm_client.async_call_llm(
                system_prompt, user_prompt,
                max_retries=3,
                stream=streaming,
                timeout=timeout,
                task_id=task_id,
                json_schema=json_schema,
            )
            if task_id and task_manager.is_cancelled(task_id):
                raise TaskCancelledError("Task cancelled after LLM call")
            return result
        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error(f"异步 LLM 调用失败：{e}")
            return None

    async def async_extract_schema(
        self,
        text: str,
        user_intent: Optional[str] = None,
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        logger.info(f"[AsyncAPI1-SchemaExtraction] 开始骨架提取，意图：{user_intent or '通用'}")

        def report_progress(progress: float, message: str = ""):
            if progress_callback:
                progress_callback(progress, message)
            if task_id:
                task_manager.update_progress(task_id, progress=progress, message=message)

        def check_cancelled():
            if task_id:
                task_manager.check_cancelled(task_id)

        report_progress(0.0, "开始骨架提取...")

        intent_instruction = ""
        if user_intent:
            intent_instruction = (
                f"\n【⚡ 用户意图约束】: 用户关注领域为「{user_intent}」。"
                f"请严格聚焦该领域，提取与之直接相关的对象类型和关系，忽略无关领域的概念。\n"
            )

        system_prompt = f"""你是一位专业的本体建模专家，擅长从文档中自动构建符合Palantir Ontology核心概念的本体结构。

请从文档中提取以下本体信息：

## 1. Object Types（对象类型）
文档中出现的所有实体或事件的schema定义。
每个Object Type需要：
- name: 类型名称（使用中文命名，如"理财产品"、"风险事件"）
- label: 中文标签
- description: 类型描述（必须使用中文，与文档语言一致）
- primary_key: 唯一标识该对象实例的字段名
- sub_class_of: 父类名称（如有继承关系）
- properties: 属性列表，每个属性包含：
  - name: 属性名称（使用中文命名，如"登记编码"、"风险评级"）
  - label: 中文属性名
  - description: 属性描述（必须使用中文）
  - data_type: 数据类型（string/number/boolean/date/datetime/array/object）

## 2. Link Types（链接类型）
文档中两个对象类型之间的关系。
每个Link Type需要：
- name: 链接名称（使用中文命名，如"购买"、"管理"）
- label: 中文标签
- description: 链接描述（必须使用中文）
- source_object_type: 源对象类型
- target_object_type: 目标对象类型
- cardinality: 关系基数（one-to-one/one-to-many/many-to-one/many-to-many）

## 3. Action Types（动作类型）
文档中描述的对对象可执行的操作。
每个Action Type需要：
- name: 动作名称（使用中文命名，如"购买产品"、"赎回产品"）
- label: 中文标签
- description: 动作描述（必须使用中文）
- target_object_type: 作用的对象类型
- parameters: 参数列表（可选），每个参数包含name、data_type

## 关键规则
1. 所有description字段必须使用中文（与文档原文语言一致）
2. 充分挖掘文档中所有实体类型，包括但不限于：核心业务对象、参与者角色、费用/费率结构、时间/期限、文件/合同、风险因素、监管要求等
3. 每个对象类型的属性要尽可能完整，覆盖文档中提到的所有字段信息（如费率、比例、期限、金额等数值型属性不要遗漏）
4. 链接类型要覆盖文档中所有明确的关系
5. Action Type要与文档中描述的业务操作对应（如购买、赎回、调整、报告等）
6. 属性name统一使用中文命名风格，保持与文档语言一致
7. 如果文本中存在类的继承/层级关系，必须在 sub_class_of 字段中明确指出父类
8. 鼓励抽取隐含关系：如果文本中存在明确描述或强烈暗示的关系，即使没有出现"关系名称"，也请提取为Link Type
{intent_instruction}

【严格约束 - 违反将导致任务失败】：
1. 【禁止提取实例】: 绝对不允许提取任何具体实例。只提取抽象的「对象类型」和「属性」。
2. 【name必须中文】: 所有 name 字段必须使用中文，禁止使用英文！例如：用"理财产品"而非"FinancialProduct"，用"登记编码"而非"registration_code"，用"购买"而非"PurchasedBy"，用"购买产品"而非"PurchaseProduct"。这是强制要求，违反将导致输出被丢弃。
3. 【label必须中文】: 所有 label 字段必须是简洁中文。
4. 【description必须中文】: 所有 description 字段必须使用中文详细描述。
5. 【source_object_type/target_object_type必须中文】: 链接类型中的源和目标对象类型必须使用中文对象类型名称，如"理财产品"、"投资者"等。
"""

        user_prompt_template = """【当前文本片段】:
"{chunk}"

【输出 JSON 格式（严格遵守，不输出任何注释）】:
{{
  "object_types": [
    {{
      "name": "理财产品",
      "label": "理财产品",
      "description": "理财产品，指金融机构接受投资者委托...",
      "primary_key": "登记编码",
      "sub_class_of": null,
      "properties": [
        {{"name": "登记编码", "label": "登记编码", "description": "理财产品登记编码", "data_type": "string"}},
        {{"name": "产品名称", "label": "产品名称", "description": "产品名称", "data_type": "string"}},
        {{"name": "风险评级", "label": "风险评级", "description": "风险评级", "data_type": "string"}}
      ]
    }}
  ],
  "link_types": [
    {{
      "name": "购买",
      "label": "购买",
      "description": "投资者购买理财产品",
      "source_object_type": "投资者",
      "target_object_type": "理财产品",
      "cardinality": "many-to-many"
    }}
  ],
  "action_types": [
    {{
      "name": "购买产品",
      "label": "购买产品",
      "description": "投资者通过销售渠道购买理财产品",
      "target_object_type": "理财产品",
      "parameters": [
        {{"name": "投资金额", "data_type": "number"}}
      ]
    }}
  ]
}}

【约束提醒】: 不得包含 instances 字段。只输出 object_types、link_types 和 action_types。
"""

        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        total_chunks = len(chunks)

        all_object_types: Dict[str, dict] = {}
        all_link_types: Dict[str, dict] = {}
        all_action_types: Dict[str, dict] = {}

        for i, chunk in enumerate(chunks):
            check_cancelled()
            logger.info(f"[AsyncSchemaExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = await self._async_call_llm(system_prompt, user_prompt, task_id=task_id, json_schema=SCHEMA_EXTRACTION_JSON_SCHEMA)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        await asyncio.sleep(0.1)
                continue

            data = _normalize_schema_response(data)

            for ot in data.get("object_types", []):
                raw_name = (ot.get("name") or "").strip()
                raw_label = (ot.get("label") or "").strip()
                if not raw_name:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "ObjectType")
                raw_props = ot.get("properties", [])
                props = []
                for p in raw_props:
                    if isinstance(p, dict):
                        props.append({
                            "name": p.get("name", ""),
                            "label": p.get("label", p.get("name", "")),
                            "description": p.get("description", ""),
                            "data_type": p.get("data_type", "string"),
                        })
                    elif isinstance(p, str):
                        props.append({"name": p, "label": p, "description": "", "data_type": "string"})
                if det_id not in all_object_types:
                    all_object_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": ot.get("description", ""),
                        "primary_key": ot.get("primary_key"),
                        "sub_class_of": None,
                        "properties": props,
                    }
                else:
                    existing_props = {p["name"]: p for p in all_object_types[det_id].get("properties", [])}
                    for p in props:
                        if p["name"] not in existing_props:
                            existing_props[p["name"]] = p
                    all_object_types[det_id]["properties"] = list(existing_props.values())

                if ot.get("sub_class_of"):
                    all_object_types[det_id]["_raw_sub_class_of"] = ot["sub_class_of"]

            for lt in data.get("link_types", []):
                raw_name = (lt.get("name") or "").strip()
                raw_label = (lt.get("label") or "").strip()
                raw_source = (lt.get("source_object_type") or "").strip()
                raw_target = (lt.get("target_object_type") or "").strip()
                if not raw_name or not raw_source or not raw_target:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "LinkType")
                if det_id not in all_link_types:
                    all_link_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": lt.get("description", ""),
                        "source_object_type": raw_source,
                        "target_object_type": raw_target,
                        "cardinality": lt.get("cardinality"),
                    }

            for at in data.get("action_types", []):
                raw_name = (at.get("name") or "").strip()
                raw_label = (at.get("label") or "").strip()
                raw_target = (at.get("target_object_type") or "").strip()
                if not raw_name or not raw_target:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(raw_name, "ActionType")
                raw_params = at.get("parameters", [])
                params = []
                for pm in raw_params:
                    if isinstance(pm, dict):
                        params.append({"name": pm.get("name", ""), "data_type": pm.get("data_type", "string")})
                    elif isinstance(pm, str):
                        params.append({"name": pm, "data_type": "string"})
                if det_id not in all_action_types:
                    all_action_types[det_id] = {
                        "id": det_id,
                        "name": raw_name,
                        "label": norm_label,
                        "description": at.get("description", ""),
                        "target_object_type": raw_target,
                        "parameters": params,
                    }
                else:
                    existing_params = {p["name"]: p for p in all_action_types[det_id].get("parameters", [])}
                    for p in params:
                        if p["name"] not in existing_params:
                            existing_params[p["name"]] = p
                    all_action_types[det_id]["parameters"] = list(existing_params.values())

            progress = (i + 1) / total_chunks * 0.9
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                await asyncio.sleep(request_interval)

        name_to_det_id: Dict[str, str] = {v["name"]: k for k, v in all_object_types.items()}
        label_to_det_id: Dict[str, str] = {v["label"]: k for k, v in all_object_types.items()}
        det_id_to_name: Dict[str, str] = {k: v["name"] for k, v in all_object_types.items()}

        def resolve_to_det_id(raw: str) -> Optional[str]:
            if not raw:
                return None
            if raw in all_object_types:
                return raw
            if raw in name_to_det_id:
                return name_to_det_id[raw]
            if raw in label_to_det_id:
                return label_to_det_id[raw]
            raw_lower = raw.lower()
            for n, did in name_to_det_id.items():
                if n.lower() == raw_lower:
                    return did
            for lbl, did in label_to_det_id.items():
                if raw in lbl or lbl in raw:
                    return did
            return None

        def resolve_to_class_name(raw: str) -> Optional[str]:
            det_id = resolve_to_det_id(raw)
            if det_id and det_id in det_id_to_name:
                return det_id_to_name[det_id]
            if raw in name_to_det_id:
                return raw
            return None

        for det_id, ot_data in all_object_types.items():
            raw_sco = ot_data.pop("_raw_sub_class_of", None)
            if raw_sco:
                resolved = resolve_to_class_name(raw_sco)
                resolved_det_id = resolve_to_det_id(raw_sco)
                if resolved and resolved_det_id and resolved_det_id != det_id:
                    ot_data["sub_class_of"] = resolved
                else:
                    logger.warning(f"[AsyncSchemaExtraction] 子类关系无法解析：{ot_data['label']} 的父类 '{raw_sco}' 未找到")

        valid_link_types: Dict[str, dict] = {}
        for det_id, lt_data in all_link_types.items():
            source_resolved = resolve_to_class_name(lt_data["source_object_type"])
            target_resolved = resolve_to_class_name(lt_data["target_object_type"])
            if source_resolved and target_resolved:
                lt_data["source_object_type"] = source_resolved
                lt_data["target_object_type"] = target_resolved
                valid_link_types[det_id] = lt_data
            else:
                logger.warning(
                    f"[AsyncSchemaExtraction] LinkType '{lt_data['label']}' "
                    f"source/target 无法解析，已丢弃"
                )

        valid_action_types: Dict[str, dict] = {}
        for det_id, at_data in all_action_types.items():
            target_resolved = resolve_to_class_name(at_data["target_object_type"])
            if target_resolved:
                at_data["target_object_type"] = target_resolved
                valid_action_types[det_id] = at_data
            else:
                logger.warning(
                    f"[AsyncSchemaExtraction] ActionType '{at_data['label']}' "
                    f"target_object_type 无法解析，已丢弃"
                )

        dedup_object_types: Dict[str, dict] = {}
        for det_id, ot_data in all_object_types.items():
            merged = False
            for existing_id, existing_data in dedup_object_types.items():
                if names_are_similar(ot_data["label"], existing_data["label"]) or names_are_similar(ot_data["name"], existing_data["name"]):
                    for p in ot_data.get("properties", []):
                        existing_names = {ep["name"] for ep in existing_data.get("properties", [])}
                        if p["name"] not in existing_names:
                            existing_data.setdefault("properties", []).append(p)
                    merged = True
                    break
            if not merged:
                dedup_object_types[det_id] = ot_data
        all_object_types = dedup_object_types

        result = {
            "object_types": list(all_object_types.values()),
            "link_types": list(valid_link_types.values()),
            "action_types": list(valid_action_types.values()),
            "metadata": {
                "total_chunks": total_chunks,
                "successful_chunks": total_chunks,
                "failed_chunks": 0,
                "success_rate": 1.0,
                "total_object_types": len(all_object_types),
                "total_link_types": len(valid_link_types),
                "total_action_types": len(valid_action_types),
            },
        }

        report_progress(1.0, f"骨架提取完成：{len(result['object_types'])} 个对象类型，{len(result['link_types'])} 个链接类型，{len(result['action_types'])} 个动作类型")
        return result

    async def async_extract_schema_only(
        self,
        text: str,
        user_intent: Optional[str] = None,
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        return await self.async_extract_schema(
            text=text,
            user_intent=user_intent,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            task_id=task_id,
            progress_callback=progress_callback,
        )

    async def async_extract_instances(
        self,
        text: str,
        schema_graph: Dict[str, Any],
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        logger.info("=" * 80)
        logger.info(f"[AsyncAPI2-InstanceExtraction] 开始实例提取")
        logger.info(f"[AsyncInstanceExtraction] Schema 包含 {len(schema_graph.get('object_types', schema_graph.get('classes', [])))} 个对象类型，{len(schema_graph.get('link_types', schema_graph.get('object_properties', [])))} 个链接类型")

        def report_progress(progress: float, message: str = ""):
            if progress_callback:
                progress_callback(progress, message)
            if task_id:
                task_manager.update_progress(task_id, progress=progress, message=message)

        def check_cancelled():
            if task_id:
                task_manager.check_cancelled(task_id)

        report_progress(0.0, "开始实例提取...")

        object_types, link_types, action_types = _normalize_schema_graph(schema_graph)
        classes = object_types
        obj_props = link_types

        if not classes:
            return {"instances": [], "discarded_edges_count": 0}

        class_id_to_info: Dict[str, dict] = {c.get("id", c.get("name", "")): c for c in classes}
        class_to_subclasses: Dict[str, List[str]] = {c.get("id", c.get("name", "")): [] for c in classes}
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            parent_classes = c.get('sub_class_of', None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_id in parent_classes:
                    if parent_id in class_to_subclasses:
                        class_to_subclasses[parent_id].append(cid)

        class_list_items = []
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            label = c.get("label", "")
            props_list = c.get("properties", [])
            prop_names_list = []
            for p in props_list:
                if isinstance(p, dict):
                    prop_names_list.append(p.get("label", "") or p.get("name", ""))
                elif isinstance(p, str):
                    prop_names_list.append(p)
            props_str = ','.join(prop_names_list) if prop_names_list else ''
            class_list_items.append(
                f"  {cid}|{label}|{props_str}"
            )
        class_list_str = "\n".join(class_list_items)

        op_list_str = "\n".join(
            f"  {op.get('name', op.get('id', ''))}|{op.get('label', '')}|{op.get('source_object_type', op.get('domain', ''))}->{op.get('target_object_type', op.get('range', ''))}"
            for op in obj_props
        ) or "  （无LinkType）"

        action_list_str = "\n".join(
            f"  {at.get('name', at.get('id', ''))}|{at.get('label', '')}|{at.get('target_object_type', '')}"
            for at in action_types
        ) or "  （无ActionType）"

        domain_code_clause = ""
        if product_code:
            domain_code_clause = (
                f"\n【🔴 知识域隔离】所有实例 ID 必须以 `_{product_code}` 结尾，"
                f"例如：`张三_HR` → `{make_deterministic_id('张三', 'Instance')}_{product_code}`。\n"
            )

        domain_context = ""
        if product_code:
            domain_context = f"\n【📚 知识域上下文】当前提取任务属于【{product_code}】知识域。请确保提取的实例与该知识域相关。\n"

        system_prompt = f"""你是本体工程师，执行实例提取任务。根据文本提取符合Schema的实例。

【类 Schema】(格式: id|中文名|属性列表):
{class_list_str}

【关系 Schema】(格式: id|名称|源类->目标类):
{op_list_str}

【动作类型 Schema】(格式: id|名称|目标类):
{action_list_str}
{domain_code_clause}{domain_context}
【约束】:
1. 仅实例化文本中明确提到的实体，type必须是类ID。禁止创建文本未提及的实例。
2. object_props的关系ID必须已定义，且domain/range匹配。文本值放data_props，实体引用放object_props。
3. Label必须中文且具体（禁止用类名作label，禁止模糊代称如"本产品"）。
4. data_props键名用中文label，仅输出文档中明确提到的属性值。文档未提及的属性不要输出（不要输出空字符串key）。
5. 优先将实例分配给最具体的子类。
6. 必须提取动作实例（action_instances），action_type须匹配Schema。
7. 禁止输出classes/object_properties字段。
8. 【🔴 关键】只提取文本中明确提到的实例和动作！不要凭空创造、不要推测、不要重复。每个chunk最多提取15个实例和5个动作实例。
"""

        user_prompt_template = """【文本片段】:
"{chunk}"

输出JSON（仅action_instances/instances/links，按此顺序）:
{{
  "action_instances": [
    {{
      "action_type": "购买产品",
      "label": "张三购买理财A",
      "target_instance_label": "理财A",
      "target_type": "理财产品",
      "parameters": {{ "金额": "10万元" }}
    }}
  ],
  "instances": [
    {{
      "id": "ZhangSan",
      "type": "Employee",
      "label": "张三",
      "object_props": {{ "worksIn": ["DeptA"] }},
      "data_props": {{ "工号": "001", "职级": "P6", "部门": "技术部", "描述": "负责系统维护的技术人员" }}
    }}
  ],
  "links": [
    {{
      "link_type": "worksIn",
      "source_label": "张三",
      "source_type": "Employee",
      "target_label": "DeptA",
      "target_type": "Department"
    }}
  ]
}}

提醒: 仔细扫描文本，把与实例相关的所有信息都填入data_props对应属性；links必须声明每条关系含源/目标类型。"""

        if documents and len(documents) > 0:
            chunks = self._chunk_documents(documents, chunk_size=chunk_size, overlap=chunk_overlap)
        else:
            chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)

        total_chunks = len(chunks)

        valid_class_ids: Set[str] = {c.get("id", c.get("name", "")) for c in classes}
        valid_class_ids.update({c.get("name", "") for c in classes if c.get("name")})
        class_label_to_id: Dict[str, str] = {c.get("label", ""): c.get("id", c.get("name", "")) for c in classes}
        class_name_to_id: Dict[str, str] = {c.get("name", ""): c.get("id", c.get("name", "")) for c in classes}

        def _resolve_class_ref(ref: str) -> str:
            if not ref:
                return ref
            if ref in valid_class_ids:
                return ref
            if ref in class_name_to_id:
                return class_name_to_id[ref]
            if ref in class_label_to_id:
                return class_label_to_id[ref]
            return ref

        op_constraints: Dict[str, Tuple[str, str]] = {}
        for op in obj_props:
            op_id = op.get("id", op.get("name", ""))
            src = _resolve_class_ref(op.get("source_object_type", op.get("domain", "")))
            tgt = _resolve_class_ref(op.get("target_object_type", op.get("range", "")))
            op_constraints[op_id] = (src, tgt)
        op_label_to_id: Dict[str, str] = {op.get("label", ""): op.get("id", op.get("name", "")) for op in obj_props}

        class_to_ancestors: Dict[str, Set[str]] = {}
        for c in classes:
            cid = c.get("id", c.get("name", ""))
            name = c.get("name", "")
            label = c.get("label", "")
            ancestors = {cid}
            if name and name != cid:
                ancestors.add(name)
            if label:
                ancestors.add(label)
            class_to_ancestors[cid] = ancestors
            if name and name != cid:
                class_to_ancestors[name] = ancestors
            if label and label != cid and label != name:
                class_to_ancestors[label] = ancestors

        for c in classes:
            cid = c.get("id", c.get("name", ""))
            name = c.get("name", "")
            label = c.get("label", "")
            parent_classes = c.get('sub_class_of', None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_ref in parent_classes:
                    parent_id = _resolve_class_ref(parent_ref)
                    if parent_id in class_to_ancestors:
                        parent_ancestors = class_to_ancestors[parent_id]
                        class_to_ancestors[cid].update(parent_ancestors)
                        if name and name != cid:
                            class_to_ancestors[name] = class_to_ancestors[cid]
                        if label and label != cid and label != name:
                            class_to_ancestors[label] = class_to_ancestors[cid]

        all_instances: Dict[str, dict] = {}
        all_action_instances: List[dict] = []
        discarded_count = 0

        for i, chunk in enumerate(chunks):
            check_cancelled()

            if isinstance(chunk, dict):
                chunk_text = chunk.get("text", "")
                chunk_filename = chunk.get("filename", "unknown")
                chunk_index = chunk.get("chunk_index", i)
            else:
                chunk_text = chunk
                chunk_filename = "unknown"
                chunk_index = i

            logger.info(f"[AsyncInstanceExtraction] 处理分块 {i+1}/{total_chunks} (file={chunk_filename})")
            if chunk_filename and chunk_filename != "unknown":
                user_prompt = f"""【文档来源】: {chunk_filename}\n""" + user_prompt_template.format(chunk=chunk_text)
            else:
                user_prompt = user_prompt_template.format(chunk=chunk_text)
            data = await self._async_call_llm(system_prompt, user_prompt, task_id=task_id, json_schema=INSTANCE_EXTRACTION_JSON_SCHEMA)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        await asyncio.sleep(0.1)
                continue

            raw_instances = []
            raw_links = []
            raw_action_instances = []
            if isinstance(data, list):
                raw_instances = data
            elif isinstance(data, dict):
                raw_instances = data.get("instances", [])
                raw_links = data.get("links", data.get("relationships", []))
                raw_action_instances = data.get("action_instances", [])
            else:
                continue

            for inst in raw_instances:
                inst["_source_file"] = chunk_filename
                inst["_source_chunk_index"] = chunk_index
                inst["_source_quote"] = chunk_text[:200] if chunk_text else ""
                if product_code:
                    inst["_domain"] = product_code

                raw_label = inst.get("label", "").strip()
                raw_type = inst.get("type", "").strip()

                if not raw_label:
                    continue

                resolved_type = None
                if raw_type in valid_class_ids:
                    resolved_type = raw_type
                elif raw_type in class_label_to_id:
                    resolved_type = class_label_to_id[raw_type]
                elif raw_type in class_name_to_id:
                    resolved_type = class_name_to_id[raw_type]
                else:
                    discarded_count += 1
                    continue

                det_id = make_deterministic_id(raw_label, "Instance")
                if product_code:
                    det_id = f"{det_id}_{product_code}"

                valid_obj_props: Dict[str, List[str]] = {}

                valid_data_props_keys = set()
                if resolved_type:
                    target_cls = next((c for c in classes if c.get("id", c.get("name", "")) == resolved_type), None)
                    if target_cls:
                        for p in target_cls.get("properties", []):
                            if isinstance(p, dict):
                                valid_data_props_keys.add(p.get("name", ""))
                        inherited_props = set(target_cls.get("inherited_properties", []) or [])
                        valid_data_props_keys = valid_data_props_keys | inherited_props

                raw_obj_props = inst.get("object_props", {})
                if not isinstance(raw_obj_props, dict):
                    raw_obj_props = {}

                if "data_props" not in inst or not isinstance(inst["data_props"], dict):
                    inst["data_props"] = {}

                for op_key, targets in raw_obj_props.items():
                    targets_list = targets if isinstance(targets, list) else [targets]

                    if op_key in valid_data_props_keys:
                        val_str = ", ".join([str(t) for t in targets_list])
                        inst["data_props"][op_key] = val_str
                        continue

                    resolved_op_id = op_key
                    if resolved_op_id not in op_constraints:
                        if resolved_op_id in op_label_to_id:
                            resolved_op_id = op_label_to_id[resolved_op_id]
                        else:
                            discarded_count += len(targets_list)
                            continue

                    expected_domain, expected_range = op_constraints[resolved_op_id]
                    instance_ancestors = class_to_ancestors.get(resolved_type, {resolved_type})
                    if not resolved_type or expected_domain not in instance_ancestors:
                        discarded_count += len(targets_list)
                        continue

                    valid_targets = []
                    for t_raw in targets_list:
                        if isinstance(t_raw, dict):
                            t_raw = t_raw.get("label") or t_raw.get("id") or t_raw.get("name", "")
                            if not t_raw:
                                continue
                        t_raw = str(t_raw)
                        if t_raw in all_instances:
                            valid_targets.append(t_raw)
                        else:
                            found = False
                            for existing_id, existing_inst in all_instances.items():
                                if names_are_similar(t_raw, existing_inst.get("label", "")):
                                    valid_targets.append(existing_id)
                                    found = True
                                    break
                            if not found:
                                valid_targets.append(t_raw)

                    if valid_targets:
                        valid_obj_props[resolved_op_id] = valid_targets

                if det_id not in all_instances:
                    all_instances[det_id] = {
                        "id": det_id,
                        "type": resolved_type,
                        "label": raw_label,
                        "object_props": valid_obj_props,
                        "data_props": inst.get("data_props", {}),
                        "_source_file": inst.get("_source_file", ""),
                        "_source_chunk_index": inst.get("_source_chunk_index", 0),
                        "_source_quote": inst.get("_source_quote", ""),
                    }
                else:
                    existing = all_instances[det_id]
                    for op_id, targets in valid_obj_props.items():
                        if op_id in existing["object_props"]:
                            existing_targets = existing["object_props"][op_id]
                            seen = set()
                            merged = []
                            for t in existing_targets + targets:
                                key = json.dumps(t, sort_keys=True) if isinstance(t, dict) else t
                                if key not in seen:
                                    seen.add(key)
                                    merged.append(t)
                            existing["object_props"][op_id] = merged
                        else:
                            existing["object_props"][op_id] = targets
                    existing["data_props"].update(inst.get("data_props", {}))

            for link in raw_links:
                link_type = link.get("link_type", link.get("type", ""))
                source_label = link.get("source_label", link.get("source_object_name", ""))
                source_type_raw = link.get("source_type", link.get("source_object_type", ""))
                target_label = link.get("target_label", link.get("target_object_name", ""))
                target_type_raw = link.get("target_type", link.get("target_object_type", ""))

                if not link_type or not source_label or not target_label:
                    continue

                resolved_op_id = link_type
                if resolved_op_id not in op_constraints:
                    if resolved_op_id in op_label_to_id:
                        resolved_op_id = op_label_to_id[resolved_op_id]
                    else:
                        continue

                resolved_source_type = None
                if source_type_raw in valid_class_ids:
                    resolved_source_type = source_type_raw
                elif source_type_raw in class_label_to_id:
                    resolved_source_type = class_label_to_id[source_type_raw]

                resolved_target_type = None
                if target_type_raw in valid_class_ids:
                    resolved_target_type = target_type_raw
                elif target_type_raw in class_label_to_id:
                    resolved_target_type = class_label_to_id[target_type_raw]

                source_det_id = make_deterministic_id(source_label.strip(), "Instance")
                if product_code:
                    source_det_id = f"{source_det_id}_{product_code}"
                if source_det_id not in all_instances:
                    for eid, einst in all_instances.items():
                        if names_are_similar(source_label, einst.get("label", "")):
                            source_det_id = eid
                            break

                target_det_id = make_deterministic_id(target_label.strip(), "Instance")
                if product_code:
                    target_det_id = f"{target_det_id}_{product_code}"
                if target_det_id not in all_instances:
                    for eid, einst in all_instances.items():
                        if names_are_similar(target_label, einst.get("label", "")):
                            target_det_id = eid
                            break

                if source_det_id not in all_instances or target_det_id not in all_instances:
                    continue

                expected_domain, expected_range = op_constraints[resolved_op_id]
                actual_source_type = all_instances[source_det_id].get("type", "")
                actual_target_type = all_instances[target_det_id].get("type", "")

                source_ancestors = class_to_ancestors.get(actual_source_type, {actual_source_type})
                target_ancestors = class_to_ancestors.get(actual_target_type, {actual_target_type})

                if expected_domain not in source_ancestors:
                    continue
                if expected_range not in target_ancestors:
                    continue

                source_inst = all_instances[source_det_id]
                if resolved_op_id not in source_inst["object_props"]:
                    source_inst["object_props"][resolved_op_id] = [target_det_id]
                elif target_det_id not in source_inst["object_props"][resolved_op_id]:
                    source_inst["object_props"][resolved_op_id].append(target_det_id)

            for ai in raw_action_instances:
                action_type_name = ai.get("action_type", "").strip()
                ai_label = ai.get("label", "").strip()
                target_instance_label = ai.get("target_instance_label", "").strip()
                target_type = ai.get("target_type", "").strip()

                if not action_type_name or not ai_label or not target_instance_label:
                    logger.warning(f"[ActionInstanceDiscard] 动作实例缺少必填字段，已丢弃: {ai}")
                    continue

                # ─ Action Type 模糊匹配（支持简称、同义词、包含关系）──
                valid_action_type = False
                matched_at_name = None
                
                # 策略1：精确匹配（name 或 label）
                for at in action_types:
                    at_name = at.get("name", "")
                    at_label = at.get("label", "")
                    if action_type_name == at_name or action_type_name == at_label:
                        valid_action_type = True
                        matched_at_name = at_name
                        break
                
                # 策略2：包含关系匹配（Schema名称包含LLM返回的名称，或反之）
                if not valid_action_type:
                    for at in action_types:
                        at_name = at.get("name", "")
                        at_label = at.get("label", "")
                        if (action_type_name in at_name or at_name in action_type_name or
                            action_type_name in at_label or at_label in action_type_name):
                            valid_action_type = True
                            matched_at_name = at_name
                            logger.info(f"[ActionTypeFuzzyMatch] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' 通过包含关系匹配到 Schema '{at_name}'")
                            break
                
                # 策略3：常见同义词映射
                if not valid_action_type:
                    synonym_map = {
                        "申购": ["申购产品", "购买产品"],
                        "赎回": ["赎回产品"],
                        "撤单": ["撤销申请"],
                        "撤单申请": ["撤销申请"],
                        "调整业绩比较基准": ["变更业绩比较基准"],
                        "调整费用": ["调整收费标准", "调整费用"],
                        "延缓支付": ["延期清算"],
                        "提前终止": ["提前终止产品", "终止产品"],
                        "分配收益": ["分红"],
                        "收取强制赎回费": ["巨额赎回处理"],
                        "调整投资组合": ["调整投资范围"],
                        "进行估值": ["暂停估值", "纠正估值错误"],
                        "增设份额类别": ["调整收费标准"],
                        "拒绝申购": ["拒绝申请"],
                        "拒绝赎回": ["拒绝申请"],
                        "拒绝认购": ["拒绝申请"],
                        "发布重大事项公告": ["发布产品信息", "发布披露"],
                    }
                    for synonym, targets in synonym_map.items():
                        if action_type_name == synonym or synonym in action_type_name or action_type_name in synonym:
                            for target in targets:
                                for at in action_types:
                                    if at.get("name") == target or at.get("label") == target:
                                        valid_action_type = True
                                        matched_at_name = target
                                        logger.info(f"[ActionTypeSynonym] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' 通过同义词匹配到 Schema '{target}'")
                                        break
                                if valid_action_type:
                                    break
                        if valid_action_type:
                            break
                
                if valid_action_type and matched_at_name:
                    action_type_name = matched_at_name
                if not valid_action_type:
                    logger.warning(
                        f"[ActionInstanceDiscard] 动作实例 '{ai_label}' 的 action_type '{action_type_name}' "
                        f"不在 Schema 中，已丢弃"
                    )
                    continue

                resolved_target_type = _resolve_class_ref(target_type)
                if resolved_target_type not in valid_class_ids:
                    logger.warning(
                        f"[ActionInstanceDiscard] 动作实例 '{ai_label}' 的 target_type '{target_type}' "
                        f"不在 Schema 中，已丢弃"
                    )
                    continue

                ai_data = {
                    "action_type": action_type_name,
                    "label": ai_label,
                    "target_instance_label": target_instance_label,
                    "target_type": resolved_target_type,
                    "source_quote": ai.get("source_quote", ""),
                    "parameters": ai.get("parameters", {}),
                    "_source_file": chunk_filename,
                    "_source_chunk_index": chunk_index,
                }
                if ai.get("id"):
                    ai_data["id"] = ai["id"]

                all_action_instances.append(ai_data)
                logger.debug(f"[ActionInstanceProcess] 动作实例：'{ai_label}' (type={action_type_name}, target={target_instance_label}, file={chunk_filename}, chunk={chunk_index})")

            progress = (i + 1) / total_chunks * 0.9
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                await asyncio.sleep(request_interval)

        result = {
            "instances": list(all_instances.values()),
            "action_instances": all_action_instances,
            "discarded_edges_count": discarded_count,
            "metadata": {
                "total_chunks": total_chunks,
                "successful_chunks": total_chunks,
                "failed_chunks": 0,
                "success_rate": 1.0,
                "total_instances": len(all_instances),
                "total_action_instances": len(all_action_instances),
                "total_edges": sum(len(inst.get("object_props", {})) for inst in all_instances.values()),
                "discarded_edges_count": discarded_count,
            },
        }
        report_progress(1.0, f"实例提取完成：{len(result['instances'])} 个实例，{len(all_action_instances)} 个动作实例")
        logger.info(f"[AsyncInstanceExtraction] 完成：{len(result['instances'])} 个实例，{len(all_action_instances)} 个动作实例，{discarded_count} 条不合规连线已丢弃")
        return result

    async def async_extract_instances_with_constraints(
        self,
        text: str,
        schema_graph: Dict[str, Any],
        chunk_size: int = 15000,
        chunk_overlap: int = 10,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return await self.async_extract_instances(
            text=text,
            schema_graph=schema_graph,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            product_code=product_code,
            task_id=task_id,
            progress_callback=progress_callback,
            documents=documents,
        )