# app/services/extractor.py - 本体抽取器服务（模块一重构版）
#
# 核心改造：
# 1. 两阶段引擎: extract_schema() → 骨架提取（仅 Class + ObjectProperty）
#                extract_instances() → 约束下的实例提取（严格遵守 Schema）
# 2. 确定性 ID:  废弃 LLM 随机英文名，使用 MD5(label + category) 保证跨轮次唯一
# 3. 防御性校验: extract_instances() 中对不符合 Schema 约束的连线直接丢弃
# 4. 保留向量库同步（build_ontology 兼容旧接口，内部转为两阶段调用）

import os
import json
import re
import time
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set

from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace, XSD, URIRef

from app.infrastructure.llm_client import LLMClient
from app.infrastructure.vector_client import VectorStoreManager
from app.core.logging import logger


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def make_deterministic_id(label: str, category: str) -> str:
    """
    用 MD5(label + category) 生成确定性 6 位十六进制前缀 + 语义 slug。
    同一概念、同一类别，在多次提取中永远返回同一 ID。
    """
    raw = f"{label.strip()}::{category.strip()}"
    hex_prefix = hashlib.md5(raw.encode("utf-8")).hexdigest()[:6]
    # 去除特殊字符，保留字母数字下划线
    slug = re.sub(r"[^\w]", "_", label.strip())[:30]
    return f"{hex_prefix}_{slug}"


def safe_id(raw: str) -> str:
    """清理字符串，使其可以作为 URI 片段（兼容旧逻辑 fallback）。"""
    s = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw.strip())
    if s and s[0].isdigit():
        s = "n" + s
    return s or "unknown"


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
            logger.warning(f"获取流式配置失败，使用默认值: {e}")
            return True

    def _call_llm(self, system_prompt: str, user_prompt: str) -> Optional[dict]:
        """调用 LLM 并处理异常，返回解析后的 dict 或 None。"""
        streaming = self._get_streaming_config()
        try:
            return self.llm_client.call_llm(system_prompt, user_prompt, stream=streaming)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return None

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
    ) -> Dict[str, Any]:
        """
        第一阶段：从文本中提取本体骨架 (Schema)。
        
        约束：
        - 绝对禁止提取实例 (NamedIndividual)。
        - 仅允许提取: OWL Class、Object Property (类与类的关系)、Data Property (属性字段)。
        - 所有 ID 使用确定性算法生成（MD5 prefix + label slug）。
        
        返回格式（dict）:
        {
            "classes": [{"id": ..., "label": ..., "sub_class_of": ..., "data_properties": [...]}],
            "object_properties": [{"id": ..., "label": ..., "domain": ..., "range": ...}],
        }
        """
        logger.info(f"[API1-SchemaExtraction] 开始骨架提取，意图: {user_intent or '通用'}")

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
   - 错误示例: 提取「张三」「产品A」「订单001」→ 这些是实例，禁止！
   - 正确示例: 提取「人员」「产品」「订单」→ 这些是类，允许！
2. 【ID 不由你决定】: 你输出的 id 字段用于辅助识别，最终 ID 将由系统重新计算，请直接用英文语义名（如 "Product", "hasName"）。
3. 【Label 必须中文】: 所有 label 字段必须是简洁中文（非英文）。
4. 【Data Property】: 在 classes 的 data_properties 字段中列出该类应有的属性名称列表（字符串数组）。
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

        # 聚合结构：用 ID 做去重（后面会重算确定性ID）
        all_classes: Dict[str, dict] = {}
        all_obj_props: Dict[str, dict] = {}

        for i, chunk in enumerate(chunks):
            logger.info(f"[SchemaExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = self._call_llm(system_prompt, user_prompt)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    time.sleep(request_interval)
                continue

            # 处理 classes
            for cls in data.get("classes", []):
                raw_label = cls.get("label", "").strip()
                if not raw_label:
                    continue
                det_id = make_deterministic_id(raw_label, "Class")
                if det_id not in all_classes:
                    all_classes[det_id] = {
                        "id": det_id,
                        "label": raw_label,
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
                det_id = make_deterministic_id(raw_label, "ObjectProperty")
                if det_id not in all_obj_props:
                    all_obj_props[det_id] = {
                        "id": det_id,
                        "label": raw_label,
                        "domain": raw_domain,   # 临时存原始文本，后续二次解析
                        "range": raw_range,
                    }

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

        logger.info(
            f"[SchemaExtraction] 完成：{len(result['classes'])} 个类，"
            f"{len(result['object_properties'])} 个关系"
        )
        return result

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
        """
        logger.info("[API2-InstanceExtraction] 开始实例提取")

        classes: List[dict] = schema_graph.get("classes", [])
        obj_props: List[dict] = schema_graph.get("object_properties", [])

        if not classes:
            return {"instances": [], "discarded_edges_count": 0}

        # ── 构建约束描述给 LLM ──
        class_list_str = "\n".join(
            f"  - 类 ID: {c['id']} | 中文名: {c['label']} | 属性字段: {', '.join(c.get('data_properties', []) or [])}"
            for c in classes
        )
        op_list_str = "\n".join(
            f"  - 关系 ID: {op['id']} | 名称: {op['label']} | 起点类: {op['domain']} → 终点类: {op['range']}"
            for op in obj_props
        ) or "  （当前 Schema 无 ObjectProperty）"

        domain_code_clause = ""
        if product_code:
            domain_code_clause = (
                f"\n【🔴 知识域隔离】所有实例 ID 必须以 `_{product_code}` 结尾，"
                f"例如: `张三_HR` → `{make_deterministic_id('张三', 'Instance')}_{product_code}`。\n"
            )

        system_prompt = f"""你是一位精通 OWL2 DL 的本体工程师，正在执行「实例提取」任务。

【已审核的类 Schema（你只能实例化这些类）】:
{class_list_str}

【已审核的关系 Schema（连线只能使用这些关系，且必须符合 domain→range）】:
{op_list_str}
{domain_code_clause}
【严格约束】:
1. 【仅实例化已定义的类】: type 字段的值必须是上方某个「类 ID」。绝对不能创建 Schema 以外的类型。
2. 【连线必须符合 domain→range】: object_props 中使用的关系 ID 必须是上方已定义的，且起点/终点类型必须匹配。
3. 【禁止重新定义类】: 不要输出 classes 或 object_properties 字段。
4. 【ID 使用语义英文名】: 你输出的 id 用于辅助，系统将重新计算确定性 ID。
5. 【Label 必须中文】: label 字段必须是中文。
6. 【不遗漏】: 文本中出现的所有符合上述类定义的实体都必须提取，不得只举例代表。
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
        # 为实例 type → class_id 的快速校验建立标签索引
        class_label_to_id: Dict[str, str] = {c["label"]: c["id"] for c in classes}

        all_instances: Dict[str, dict] = {}  # det_id → instance dict
        discarded_count = 0

        for i, chunk in enumerate(chunks):
            logger.info(f"[InstanceExtraction] 处理分块 {i+1}/{total_chunks}")
            user_prompt = user_prompt_template.format(chunk=chunk)
            data = self._call_llm(system_prompt, user_prompt)

            if not data:
                logger.warning(f"分块 {i+1}/{total_chunks} LLM 返回空，跳过")
                if i < total_chunks - 1:
                    time.sleep(request_interval)
                continue

            raw_instances = data.get("instances", [])

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
                raw_obj_props = inst.get("object_props", {})
                for op_id, targets in raw_obj_props.items():
                    targets_list = targets if isinstance(targets, list) else [targets]

                    if op_id not in op_constraints:
                        logger.warning(
                            f"[InstanceExtraction] 连线关系 '{op_id}' 不在 Schema 中，已丢弃"
                        )
                        discarded_count += len(targets_list)
                        continue

                    _, expected_range = op_constraints[op_id]
                    valid_targets = []
                    for t_raw in targets_list:
                        # target 是实例 id 或 label，暂存原始值（第二步图构建时按 label 解析）
                        valid_targets.append(t_raw)

                    if valid_targets:
                        valid_obj_props[op_id] = valid_targets

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

            if i < total_chunks - 1:
                time.sleep(request_interval)

        result = {
            "instances": list(all_instances.values()),
            "discarded_edges_count": discarded_count,
        }
        logger.info(
            f"[InstanceExtraction] 完成: {len(result['instances'])} 个实例，"
            f"{discarded_count} 条不合规连线已丢弃"
        )
        return result

    # ──────────────────────────────────────────
    # 图数据转换工具：Schema / Instance → GraphData
    # ──────────────────────────────────────────

    @staticmethod
    def schema_to_graph_data(schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Schema（classes + object_properties）转换为前端可渲染的 {nodes, edges}。
        节点类型统一为 'owl:Class'。
        """
        nodes = []
        edges = []
        processed = set()

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
                    "properties": {dp: "" for dp in cls.get("data_properties", [])},
                },
            })
            processed.add(cid)

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
            return f"❌ TTL 解析失败: {e}"

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
    ) -> Tuple[str, str]:
        """
        兼容旧的一步式调用接口，内部转换为两阶段执行，
        并将最终图数据序列化为 TTL 文件。
        """
        logger.info(f"build_ontology (两阶段兼容模式) 调用 chunk_size={chunk_size}")

        if progress:
            progress(0.0, desc="阶段1：骨架提取...")

        # Phase 1: Schema
        schema = self.extract_schema(
            text,
            user_intent=scenario_desc,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
        )

        if progress:
            progress(0.4, desc="阶段2：实例提取...")

        # Phase 2: Instances
        inst_result = self.extract_instances(
            text,
            schema_graph=schema,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            request_interval=request_interval,
            product_code=product_code,
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
            f"- 类定义: {count_cls} 个\n"
            f"- 实例: {count_inst} 个\n"
            f"- 丢弃不合规连线: {inst_result['discarded_edges_count']} 条"
        )

        if progress:
            progress(1.0, desc="完成！")

        return filename, msg