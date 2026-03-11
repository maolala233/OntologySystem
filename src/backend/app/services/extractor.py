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


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def make_deterministic_id(label: str, category: str) -> str:
    """
    用 MD5(label + category) 生成确定性 ID。
    同一概念、同一类别，在多次提取中永远返回同一 ID。
    
    格式：{category_prefix}_{8 位哈希}
    例如：C_a1b2c3d4, I_f5e6d7c8, OP_12345678
    """
    raw = f"{label.strip()}::{category.strip()}"
    hex_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    
    # 类别前缀映射
    prefix_map = {
        "Class": "C",
        "Instance": "I",
        "ObjectProperty": "OP",
        "DataProperty": "DP",
    }
    prefix = prefix_map.get(category, "Node")
    
    return f"{prefix}_{hex_hash}"


def safe_id(raw: str) -> str:
    """
    安全的 ID 生成函数（兼容旧代码 fallback）。
    使用确定性哈希机制，不再使用正则替换中文。
    """
    return make_deterministic_id(raw, "Unknown")


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
    # 文本切分工具
    # ──────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 15000, overlap: int = 500) -> List[str]:
        try:
            chunk_size = int(chunk_size)
        except (ValueError, TypeError):
            chunk_size = 15000
        try:
            overlap = int(overlap)
        except (ValueError, TypeError):
            overlap = 500
        return self._recursive_split(
            text, chunk_size, overlap,
            separators=["\n\n", "\n", "。 ", "！ ", "？ ", ". ", " ", ""]
        )

    def _sub_chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
        try:
            chunk_size = int(chunk_size)
        except (ValueError, TypeError):
            chunk_size = 800
        try:
            overlap = int(overlap)
        except (ValueError, TypeError):
            overlap = 100
        return self._recursive_split(
            text, chunk_size, overlap,
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
            overlap = 500

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
            from app.infrastructure.database import get_db, SystemConfig
            db = next(get_db())
            config = db.query(SystemConfig).filter(SystemConfig.key == "llm_config").first()
            if config and config.value:
                db.expire(config)
                db.refresh(config)
                val = config.value.get("streaming_enabled", True)
                db.close()
                return val
            db.close()
            return True
        except Exception as e:
            logger.warning(f"获取流式配置失败，使用默认值：{e}")
            return True

    def _call_llm(self, system_prompt: str, user_prompt: str, task_id: Optional[str] = None, timeout: int = 300) -> Optional[dict]:
        """
        同步调用 LLM 并处理异常，返回解析后的 dict 或 None。
        使用线程池执行 LLM 调用，支持超时和取消检查。
        
        参数:
        - task_id: 任务 ID，用于在调用间隙检查取消标志
        - timeout: LLM 调用超时时间（秒），默认 300 秒
        """
        streaming = self._get_streaming_config()
        try:
            # 如果提供了 task_id，在调用前快速检查是否已取消
            if task_id and task_manager.is_cancelled(task_id):
                raise TaskCancelledError("Task cancelled before LLM call")
            
            # 使用线程池执行 LLM 调用，支持超时
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                # 注意：call_llm 的参数顺序是 (system_prompt, user_prompt, max_retries, stream, timeout, task_id)
                # 必须使用关键字参数传入 stream、timeout 和 task_id，避免位置参数混淆
                future = executor.submit(
                    self.llm_client.call_llm,
                    system_prompt,
                    user_prompt,
                    3,  # max_retries
                    stream=streaming,
                    timeout=timeout,
                    task_id=task_id  # 传递 task_id 以支持取消检查
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
        chunk_overlap: int = 500,
        request_interval: int = 2,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        第一阶段：从文本中提取本体骨架 (Schema)。
        
        约束：
        - 绝对禁止提取实例 (NamedIndividual)。
        - 仅允许提取：OWL Class、Object Property (类与类的关系)、Data Property (属性字段)。
        - 所有 ID 使用确定性算法生成（MD5 prefix + label slug）。
        
        返回格式（dict）:
        {
            "classes": [{"id": ..., "label": ..., "sub_class_of": ..., "data_properties": [...]}],
            "object_properties": [{"id": ..., "label": ..., "domain": ..., "range": ...}],
        }
        
        参数:
        - task_id: 任务 ID，用于支持取消操作
        - progress_callback: 进度回调函数，签名：callback(progress: float, message: str)
        """
        logger.info(f"[API1-SchemaExtraction] 开始骨架提取，意图：{user_intent or '通用'}")
        
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
        
        report_progress(0.0, "开始骨架提取...")

        # 意图聚焦前缀
        intent_instruction = ""
        if user_intent:
            intent_instruction = (
                f"\n【⚡ 用户意图约束】: 用户关注领域为「{user_intent}」。"
                f"请严格聚焦该领域，提取与之直接相关的类和关系，忽略无关领域的概念。\n"
            )

        system_prompt = f"""你是一位精通 OWL2 DL 标准的本体架构师，正在执行「骨架提取」任务。

【严格约束 - 违反将导致任务失败】：
1. 【禁止提取实例】: 绝对不允许提取任何具体实例 (NamedIndividual)。只提取抽象的「类」和「属性」。
   - 错误示例：提取「张三」「产品 A」「订单 001」→ 这些是实例，禁止！
   - 正确示例：提取「人员」「产品」「订单」→ 这些是类，允许！
2. 【ID 不由你决定】: 你输出的 id 字段用于辅助识别，最终 ID 将由系统重新计算，请直接用英文语义名（如 "Product", "hasName"）。
3. 【Label 必须中文】: 所有 label 字段必须是简洁中文（非英文）。
4. 【Data Property】: 在 classes 的 data_properties 字段中列出该类应有的属性名称列表（字符串数组）。
5. 【鼓励抽取隐含关系】: 如果文本中存在明确描述或强烈暗示的「系统 A 依赖于系统 B / A 调用 B / A 部署在 B 上 / A 与 B 对接」等关系，
   即使没有出现"关系名称"这个词，也请将其提取为类与类之间的 ObjectProperty，
   关系的 label 可用简洁中文动词短语（如「依赖于」「调用」「部署于」「对接」等）。
6. 【必须提取子类关系】: 如果文本中存在类的继承/层级关系（如"A 是 B 的一种"、"A 属于 B 类"、"A 是 B 的子类"、"A 包括 B"等），
   必须在 sub_class_of 字段中明确指出父类。这是构建本体层级的关键！
   - 示例：如果文本提到"量子密钥分发设备是一种量子设备"，则"量子密钥分发设备"的 sub_class_of 应该是"量子设备"的 id。
   - 示例：如果文本提到"系统包括认证系统和审计系统"，则"认证系统"和"审计系统"的 sub_class_of 应该是"系统"的 id。
{intent_instruction}
"""

        user_prompt_template = """【当前文本片段】:
"{chunk}"

【输出 JSON 格式（严格遵守，不输出任何注释）】:
{{
  "classes": [
    {{
      "id": "Product",
      "label": "产品",
      "sub_class_of": null,
      "data_properties": ["名称", "价格", "规格"]
    }}
  ],
  "object_properties": [
    {{
      "id": "belongsTo",
      "label": "属于",
      "domain": "Product",
      "range": "Category"
    }}
  ]
}}

【约束提醒】: 不得包含 instances 字段。只输出 classes 和 object_properties。
"""

        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        total_chunks = len(chunks)

        # 聚合结构：用规范化后的 Label 做 Reduce 去重
        all_classes: Dict[str, dict] = {}
        all_obj_props: Dict[str, dict] = {}

        for i, chunk in enumerate(chunks):
            # 检查取消
            check_cancelled()
            
            logger.info(f"[SchemaExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = self._call_llm(system_prompt, user_prompt, task_id=task_id, timeout=300)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    # 在等待期间也定期检查取消标志
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        time.sleep(0.1)
                continue

            # 处理 classes
            for cls in data.get("classes", []):
                raw_label = cls.get("label", "").strip()
                if not raw_label:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(norm_label, "Class")
                if det_id not in all_classes:
                    all_classes[det_id] = {
                        "id": det_id,
                        "label": norm_label,
                        "sub_class_of": None,
                        "data_properties": list(cls.get("data_properties", [])),
                    }
                else:
                    # 合并 data_properties（去重）
                    existing_dp = set(all_classes[det_id]["data_properties"])
                    for dp in cls.get("data_properties", []):
                        existing_dp.add(dp)
                    all_classes[det_id]["data_properties"] = list(existing_dp)

                # 处理父类关系（需在所有 classes 处理完后二次解析，这里先记录原始值）
                if cls.get("sub_class_of"):
                    all_classes[det_id]["_raw_sub_class_of"] = cls["sub_class_of"]

            # 处理 object_properties
            for op in data.get("object_properties", []):
                raw_label = op.get("label", "").strip()
                raw_domain = op.get("domain", "").strip()
                raw_range = op.get("range", "").strip()
                if not raw_label or not raw_domain or not raw_range:
                    continue
                norm_label = self._normalize_term(raw_label, user_intent=user_intent)
                det_id = make_deterministic_id(norm_label, "ObjectProperty")
                if det_id not in all_obj_props:
                    all_obj_props[det_id] = {
                        "id": det_id,
                        "label": norm_label,
                        "domain": raw_domain,   # 临时存原始文本，后续二次解析
                        "range": raw_range,
                    }

            # 更新进度
            progress = (i + 1) / total_chunks * 0.9  # 预留 10% 给后续处理
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                time.sleep(request_interval)

        # ── 二次处理：将 domain/range/sub_class_of 的原始文本映射为确定性 ID ──
        # 建立 label → det_id 的快速查找表
        label_to_det_id: Dict[str, str] = {
            v["label"]: k for k, v in all_classes.items()
        }
        # 也支持原始英文 id → det_id 的映射（LLM 可能输出英文名）
        raw_id_to_det_id: Dict[str, str] = {}
        for det_id, cls_data in all_classes.items():
            raw_id_to_det_id[cls_data.get("id", "")] = det_id

        def resolve_class_id(raw: str) -> Optional[str]:
            """尽力解析出一个 det_id，找不到则返回 None。"""
            if raw in all_classes:
                return raw  # 已经是 det_id
            if raw in label_to_det_id:
                return label_to_det_id[raw]
            if raw in raw_id_to_det_id:
                return raw_id_to_det_id[raw]
            return None

        # 修正 sub_class_of
        for det_id, cls_data in all_classes.items():
            raw_sco = cls_data.pop("_raw_sub_class_of", None)
            if raw_sco:
                resolved = resolve_class_id(raw_sco)
                cls_data["sub_class_of"] = resolved

        # 修正 domain / range
        valid_obj_props: Dict[str, dict] = {}
        for det_id, op_data in all_obj_props.items():
            domain_resolved = resolve_class_id(op_data["domain"])
            range_resolved = resolve_class_id(op_data["range"])
            if domain_resolved and range_resolved:
                op_data["domain"] = domain_resolved
                op_data["range"] = range_resolved
                valid_obj_props[det_id] = op_data
            else:
                logger.warning(
                    f"[SchemaExtraction] ObjectProperty '{op_data['label']}' "
                    f"domain/range 无法解析，已丢弃 "
                    f"(domain={op_data['domain']}, range={op_data['range']})"
                )

        result = {
            "classes": list(all_classes.values()),
            "object_properties": list(valid_obj_props.values()),
        }

        report_progress(1.0, f"骨架提取完成：{len(result['classes'])} 个类，{len(result['object_properties'])} 个关系")
        logger.info(
            f"[SchemaExtraction] 完成：{len(result['classes'])} 个类，"
            f"{len(result['object_properties'])} 个关系"
        )
        return result

    def extract_schema_only(
        self,
        text: str,
        user_intent: Optional[str] = None,
        chunk_size: int = 15000,
        chunk_overlap: int = 500,
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
        chunk_overlap: int = 500,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        第二阶段：在 Schema 约束下提取实例 (NamedIndividual)。
        
        核心机制：
        1. 将 schema_graph 转为 Prompt 约束规则注入；
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
        logger.info("[API2-InstanceExtraction] 开始实例提取")
        
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

        classes: List[dict] = schema_graph.get("classes", [])
        obj_props: List[dict] = schema_graph.get("object_properties", [])

        if not classes:
            return {"instances": [], "discarded_edges_count": 0}

        # ── 构建约束描述给 LLM ──
        # 首先建立 class_id → class_info 的映射，方便查找父类和子类
        class_id_to_info: Dict[str, dict] = {c['id']: c for c in classes}
        
        # 找出每个类的子类（谁是我的子类）
        class_to_subclasses: Dict[str, List[str]] = {c['id']: [] for c in classes}
        for c in classes:
            # 支持 parent_classes 和 sub_class_of 两种字段
            parent_classes = c.get('parent_classes', []) or c.get('sub_class_of', None)
            if parent_classes:
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_id in parent_classes:
                    if parent_id in class_to_subclasses:
                        class_to_subclasses[parent_id].append(c['id'])
        
        # 构建类列表字符串，包含子类信息
        class_list_items = []
        for c in classes:
            cid = c['id']
            label = c['label']
            props = ', '.join(c.get('data_properties', []) or []) or '无'
            
            # 获取该类的子类
            subclasses = class_to_subclasses.get(cid, [])
            subclass_info = ""
            if subclasses:
                subclass_labels = [class_id_to_info.get(sc, {}).get('label', sc) for sc in subclasses]
                subclass_info = f" | 子类：{', '.join(subclass_labels)}"
            
            class_list_items.append(
                f"  - 类 ID: {cid} | 中文名：{label} | 属性字段：{props}{subclass_info}"
            )
        class_list_str = "\n".join(class_list_items)
        
        op_list_str = "\n".join(
            f"  - 关系 ID: {op['id']} | 名称：{op['label']} | 起点类：{op['domain']} → 终点类：{op['range']}"
            for op in obj_props
        ) or "  （当前 Schema 无 ObjectProperty）"

        domain_code_clause = ""
        if product_code:
            domain_code_clause = (
                f"\n【🔴 知识域隔离】所有实例 ID 必须以 `_{product_code}` 结尾，"
                f"例如：`张三_HR` → `{make_deterministic_id('张三', 'Instance')}_{product_code}`。\n"
            )

        system_prompt = f"""你是一位精通 OWL2 DL 的本体工程师，正在执行「实例提取」任务。

【已审核的类 Schema（你只能实例化这些类）】:
{class_list_str}

【已审核的关系 Schema（连线只能使用这些关系，且必须符合 domain→range）】:
{op_list_str}
{domain_code_clause}
【子类继承说明】:
- 如果某个类有子类，你可以将实例分配给该类或其任意子类。
- 例如：如果"设备"有子类"量子设备"，而文档中提到"量子密钥分发设备"，则应该将其实例化为"量子设备"类（更具体的子类），而不是"设备"类（父类）。
- 优先将实例分配给最具体的子类（叶子类），而不是父类。

【严格约束】:
1. 【区分属性与关系】: 
   - 如果是文本值（如 "1.0 版", "高性能"），放入 data_props。
   - 如果是指向另一个**实体**（如指向 "算法 A"），放入 object_props。
   - 不要把 "平台名称"、"版本" 等属性放到 object_props 里！
2. 【仅实例化已定义的类】: type 字段的值必须是上方某个「类 ID」。绝对不能创建 Schema 以外的类型。
3. 【连线必须符合 domain→range】: object_props 中使用的关系 ID 必须是上方已定义的，且起点/终点类型必须匹配。
4. 【禁止重新定义类】: 不要输出 classes 或 object_properties 字段。
5. 【ID 使用语义英文名】: 你输出的 id 用于辅助，系统将重新计算确定性 ID。
6. 【Label 必须中文】: label 字段必须是中文。
7. 【不遗漏】: 文本中出现的所有符合上述类定义的实体都必须提取，不得只举例代表。
8. 【具体化命名原则】: 实例的 label 必须是文档中出现的最具体的专有名词或实体名称，例如"人行清算模拟系统""二代支付系统"等。
   绝对禁止直接照抄所属类的名称作为实例的 label（例如 type 为"清算系统"时，不允许实例 label 也叫"清算系统"）。
9. 【消除同名冗余】: 如果提取出的实例 label 与它的 type（类的 ID 对应的中文标签）完全一样，说明你提取错了，必须回到上下文中寻找更具体的限定词，
   例如不要提取出 label 为"监管机构"的实例，而应该提取出 label 为"国家金融监督管理总局"等更具体的机构名称。
10. 【属性防冗余】: 如果某个专有名词（如"二代支付系统""人行清算模拟系统"）已经作为实例的 label 出现，就不要再把同样的字符串重复放入 data_props 的"名称""系统名称"等字段中；
    data_props 应主要用于存放该实例的版本号、金额、日期、状态等真正的数据字段，而不是简单重复 label。
"""

        user_prompt_template = """【当前文本片段】:
"{chunk}"

【输出 JSON 格式（只输出 instances 数组，不得包含 classes/object_properties 字段）】:
{{
  "instances": [
    {{
      "id": "ZhangSan",
      "type": "Employee",
      "label": "张三",
      "object_props": {{
        "worksIn": ["DeptA"]
      }},
      "data_props": {{
        "工号": "001",
        "职级": "P6"
      }}
    }}
  ]
}}
"""

        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        total_chunks = len(chunks)

        # 构建防御性校验索引
        valid_class_ids: Set[str] = {c["id"] for c in classes}
        # op_constraints: {op_id: (domain_class_id, range_class_id)}
        op_constraints: Dict[str, Tuple[str, str]] = {
            op["id"]: (op["domain"], op["range"]) for op in obj_props
        }
        # 支持通过关系中文 label 反查 ObjectProperty ID，避免 LLM 用 label 代替 ID 时被误杀
        op_label_to_id: Dict[str, str] = {op["label"]: op["id"] for op in obj_props}
        # 为实例 type → class_id 的快速校验建立标签索引
        class_label_to_id: Dict[str, str] = {c["label"]: c["id"] for c in classes}

        all_instances: Dict[str, dict] = {}  # det_id → instance dict
        discarded_count = 0

        for i, chunk in enumerate(chunks):
            # 检查取消
            check_cancelled()
            
            logger.info(f"[InstanceExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = self._call_llm(system_prompt, user_prompt, task_id=task_id, timeout=300)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    # 在等待期间也定期检查取消标志
                    for _ in range(request_interval * 10):
                        check_cancelled()
                        time.sleep(0.1)
                continue

            raw_instances = []
            if isinstance(data, list):
                raw_instances = data
                logger.info(f"分块 {i+1}/{total_chunks} LLM 返回了列表格式，已自动适配")
            elif isinstance(data, dict):
                raw_instances = data.get("instances", [])
            else:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回格式异常 (type: {type(data)})，跳过")
                continue

            for inst in raw_instances:
                raw_label = inst.get("label", "").strip()
                raw_type = inst.get("type", "").strip()

                if not raw_label:
                    continue

                # ── 校验 type 必须在 Schema 中 ──
                resolved_type = None
                if raw_type in valid_class_ids:
                    resolved_type = raw_type
                elif raw_type in class_label_to_id:
                    resolved_type = class_label_to_id[raw_type]
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
                
                # 获取该类允许的数据属性列表 (用于纠错)
                valid_data_props_keys = set()
                if resolved_type:
                    # 找到 schema 中该类的定义
                    target_cls = next((c for c in classes if c["id"] == resolved_type), None)
                    if target_cls:
                        valid_data_props_keys = set(target_cls.get("data_properties", []))

                raw_obj_props = inst.get("object_props", {})
                
                # 确保 data_props 初始化
                if "data_props" not in inst:
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
                    if not resolved_type or resolved_type != expected_domain:
                        logger.warning(
                            f"[EdgeDiscard] 实例 '{raw_label}' (Type: {resolved_type}) 试图通过关系 '{resolved_op_id}' 连接，"
                            f"但被拦截。原因：Domain 不匹配（期望：{expected_domain}, 实际：{resolved_type}）。"
                        )
                        discarded_count += len(targets_list)
                        continue

                    valid_targets = []
                    for t_raw in targets_list:
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
                    }
                else:
                    # 合并同一实例的属性
                    existing = all_instances[det_id]
                    for op_id, targets in valid_obj_props.items():
                        if op_id in existing["object_props"]:
                            merged = list(set(existing["object_props"][op_id] + targets))
                            existing["object_props"][op_id] = merged
                        else:
                            existing["object_props"][op_id] = targets
                    existing["data_props"].update(inst.get("data_props", {}))

            # 更新进度
            progress = (i + 1) / total_chunks * 0.9  # 预留 10% 给后续处理
            report_progress(progress, f"处理分块 {i+1}/{total_chunks}")

            if i < total_chunks - 1:
                time.sleep(request_interval)

        result = {
            "instances": list(all_instances.values()),
            "discarded_edges_count": discarded_count,
        }
        report_progress(1.0, f"实例提取完成：{len(result['instances'])} 个实例")
        logger.info(
            f"[InstanceExtraction] 完成：{len(result['instances'])} 个实例，"
            f"{discarded_count} 条不合规连线已丢弃"
        )
        return result

    def extract_instances_with_constraints(
        self,
        text: str,
        schema_graph: Dict[str, Any],
        chunk_size: int = 15000,
        chunk_overlap: int = 500,
        request_interval: int = 2,
        product_code: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        对外暴露的「带 Schema 约束的实例提取」方法。
        语义等价于 extract_instances，但命名上强调强约束规则，便于路由层对齐 API 设计。
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
        )

    # ──────────────────────────────────────────
    # 图数据转换工具：Schema / Instance → GraphData
    # ──────────────────────────────────────────

    @staticmethod
    def schema_to_graph_data(schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Schema（classes + object_properties）转换为前端可渲染的 {nodes, edges}。
        节点类型统一为 'owl:Class'。
        同时处理 parent_classes/sub_class_of 关系，生成子类关系边。
        """
        nodes = []
        edges = []
        processed = set()

        # 首先处理所有类节点
        for cls in schema.get("classes", []):
            cid = cls["id"]
            if cid in processed:
                continue
            nodes.append({
                "id": cid,
                "type": "custom",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": cls["label"],
                    "type": "owl:Class",
                    "properties": {dp: "" for dp in cls.get("data_properties", []) or []},
                },
            })
            processed.add(cid)

        # 然后处理子类关系（parent_classes）
        for cls in schema.get("classes", []):
            cid = cls["id"]
            # 支持 parent_classes 和 sub_class_of 两种字段名
            parent_classes = cls.get("parent_classes", []) or cls.get("sub_class_of", None)
            if parent_classes:
                # parent_classes 可能是字符串或列表
                if isinstance(parent_classes, str):
                    parent_classes = [parent_classes]
                for parent_id in parent_classes:
                    if parent_id and parent_id in processed:
                        edges.append({
                            "id": f"e_subclass_{cid}_{parent_id}",
                            "source": cid,
                            "target": parent_id,
                            "label": "subClassOf",
                            "type": "custom",
                            "data": {"label": "subClassOf", "relation": "subclass_of"},
                        })

        # 处理 ObjectProperty 关系
        for op in schema.get("object_properties", []):
            src = op["domain"]
            tgt = op["range"]
            if src in processed and tgt in processed:
                edges.append({
                    "id": f"e_{src}_{tgt}_{op['id']}",
                    "source": src,
                    "target": tgt,
                    "label": op["label"],
                    "type": "custom",
                    "data": {"label": op["label"], "prop_id": op["id"]},
                })

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def merge_instances_to_graph_data(
        schema_graph_data: Dict[str, Any],
        instances: List[dict],
    ) -> Dict[str, Any]:
        """
        将实例合并到已有的 Schema GraphData 中，生成完整图。
        - 类节点保持蓝色（owl:Class）
        - 实例节点标记为 owl:NamedIndividual
        - instance_of 连线（虚线）+ ObjectProperty 连线（实线）
        """
        nodes = list(schema_graph_data.get("nodes", []))
        edges = list(schema_graph_data.get("edges", []))
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
        
        for inst in instances:
            iid = inst["id"]
            if iid not in existing_ids:
                nodes.append({
                    "id": iid,
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": inst["label"],
                        "type": "owl:NamedIndividual",
                        "properties": inst.get("data_props", {}),
                    },
                })
                existing_ids.add(iid)
            label_to_inst_id[inst["label"]] = iid
            label_to_inst_id[iid] = iid  # self-map

            # rdf:type 连线（实例 → 类，虚线）
            type_class = inst.get("type")
            if type_class and type_class in existing_ids:
                edges.append({
                    "id": f"e_{iid}_type_{type_class}",
                    "source": iid,
                    "target": type_class,
                    "label": "type",
                    "type": "custom",
                    "style": {"strokeDasharray": "5,5"},
                    "data": {"label": "type"},
                })

        # 实例间的 ObjectProperty 连线（实线）
        for inst in instances:
            src_id = inst["id"]
            for op_id, targets in inst.get("object_props", {}).items():
                for t_raw in targets:
                    # 先按确定性 ID 查，再按 label 查
                    tgt_id = label_to_inst_id.get(t_raw)
                    if tgt_id and tgt_id in existing_ids:
                        edges.append({
                            "id": f"e_{src_id}_{tgt_id}_{op_id}",
                            "source": src_id,
                            "target": tgt_id,
                            "label": op_id,
                            "type": "custom",
                            "data": {"label": op_id},
                        })

        return {"nodes": nodes, "edges": edges}

    # ──────────────────────────────────────────
    # 向量库同步
    # ──────────────────────────────────────────

    def sync_ttl_to_vector_store(
        self, ttl_file_path: str, progress=None, delete_old: bool = True
    ) -> str:
        filename = os.path.basename(ttl_file_path)
        if delete_old:
            self.vector_manager.delete_by_expr(
                f'metadata like "%\\"source_file\\": \\"{filename}\\"%"'
            )

        g = Graph()
        try:
            g.parse(ttl_file_path, format="turtle")
        except Exception as e:
            return f"❌ TTL 解析失败：{e}"

        labels: Dict[URIRef, str] = {}
        for s, p, o in g.triples((None, RDFS.label, None)):
            labels[s] = str(o)

        def get_local(uri):
            u = str(uri)
            return u.split("#")[-1] if "#" in u else u.split("/")[-1]

        knowledge_texts = []
        knowledge_metas = []
        triples = list(g)
        total = len(triples)
        count = 0

        for i, (s, p, o) in enumerate(triples):
            if p == RDF.type and o in [OWL.Ontology, OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty]:
                continue
            s_id = get_local(s)
            s_label = labels.get(s, s_id)
            p_id = get_local(p)
            p_label = labels.get(p, p_id)
            base_meta = {
                "source": "ttl_sync",
                "source_file": filename,
                "subject": s_label,
                "subject_id": s_id,
                "predicate": p_id,
            }
            if isinstance(o, URIRef):
                o_id = get_local(o)
                o_label = labels.get(o, o_id)
                text = f"{s_label} 的 {p_label} 是 {o_label}"
                meta = {**base_meta, "object": o_label, "object_id": o_id}
            else:
                o_val = str(o)
                text = f"{s_label} 的 {p_label} 属性值为 {o_val}"
                meta = {**base_meta, "object": o_val}

            knowledge_texts.append(text)
            knowledge_metas.append(meta)
            count += 1

            if len(knowledge_texts) >= 100:
                self.vector_manager.insert_data(knowledge_texts, knowledge_metas)
                knowledge_texts = []
                knowledge_metas = []
                if progress:
                    progress(i / total, desc=f"同步中... {i}/{total}")

        if knowledge_texts:
            self.vector_manager.insert_data(knowledge_texts, knowledge_metas)

        return f"✅ 同步完成：共处理 {count} 条三元组到库 {self.vector_manager.collection_name}"

    # ──────────────────────────────────────────
    # 兼容旧接口（内部转两阶段调用）
    # ──────────────────────────────────────────

    def build_ontology(
        self,
        text: str,
        scenario_desc: str,
        entities_df,
        chunk_size: int = 15000,
        chunk_overlap: int = 500,
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

        def get_uri(id_str: str) -> URIRef:
            return self.EX[safe_id(id_str)]

        for cls in schema.get("classes", []):
            uri = get_uri(cls["id"])
            master_g.add((uri, RDF.type, OWL.Class))
            master_g.add((uri, RDFS.label, Literal(cls["label"], lang="zh")))
            if cls.get("sub_class_of"):
                master_g.add((uri, RDFS.subClassOf, get_uri(cls["sub_class_of"])))

        for op in schema.get("object_properties", []):
            uri = get_uri(op["id"])
            master_g.add((uri, RDF.type, OWL.ObjectProperty))
            master_g.add((uri, RDFS.label, Literal(op["label"], lang="zh")))
            master_g.add((uri, RDFS.domain, get_uri(op["domain"])))
            master_g.add((uri, RDFS.range, get_uri(op["range"])))

        for inst in inst_result.get("instances", []):
            uri = get_uri(inst["id"])
            master_g.add((uri, RDF.type, OWL.NamedIndividual))
            master_g.add((uri, RDFS.label, Literal(inst["label"], lang="zh")))
            master_g.add((uri, RDF.type, get_uri(inst["type"])))
            for pid, targets in inst.get("object_props", {}).items():
                for t in (targets if isinstance(targets, list) else [targets]):
                    master_g.add((uri, get_uri(pid), get_uri(t)))
            for pid, val in inst.get("data_props", {}).items():
                master_g.add((
                    uri, get_uri(pid),
                    Literal(val, lang="zh") if isinstance(val, str) else Literal(val)
                ))

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