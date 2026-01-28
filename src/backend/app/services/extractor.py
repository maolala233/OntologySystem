# app/services/extractor.py - 本体抽取器服务
# 功能：核心服务类，负责从文本中抽取知识并构建本体，同步到向量库

import os
import json
import re
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from rdflib import Graph, Literal, RDF, RDFS, OWL, Namespace, XSD, URIRef
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.vector_client import VectorStoreManager
from app.core.exceptions import ExtractionException
from app.core.logging import logger


class OntologyExtractor:
    def __init__(self, api_key: str, base_url: str, model: str, collection_name: Optional[str] = None):
        self.llm_client = LLMClient(api_key, base_url, model)
        self.EX = Namespace("http://www.example.org/auto_ontology#")
        # 初始化向量库
        self.vector_manager = VectorStoreManager(collection_name=collection_name)

    def _chunk_text(self, text: str, chunk_size: int = 15000, overlap: int = 500) -> List[str]:
        """
        改进的文本切分逻辑，尽量在段落或句子边界切分。
        """
        return self._recursive_split(text, chunk_size, overlap, separators=["\n\n", "\n", "。 ", "！ ", "？ ", ". ", " ", ""])

    def _sub_chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
        """
        用于向量库入库的细粒度切分。
        """
        return self._recursive_split(text, chunk_size, overlap, separators=["\n\n", "\n", "。 ", "！ ", "？ ", ". ", " ", ""])

    def _recursive_split(self, text: str, chunk_size: int, overlap: int, separators: List[str]) -> List[str]:
        """
        严格递归切分逻辑，确保任何分块都不会超过 chunk_size。
        """
        if len(text) <= chunk_size:
            return [text]
        
        # 寻找当前层级可用的分隔符
        separator = ""
        new_separators = []
        for i, s in enumerate(separators):
            if s == "":
                separator = s
                break
            if s in text:
                separator = s
                new_separators = separators[i+1:]
                break
        
        # 如果找不到分隔符，或者分隔符是空字符串，直接按长度硬切
        if separator == "" or not new_separators:
            return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size - overlap)]

        # 按分隔符切分
        splits = text.split(separator)
        final_chunks = []
        current_chunk = ""
        
        for split in splits:
            # 如果单个 split 就超过了 chunk_size，递归进一步切分
            if len(split) > chunk_size:
                if current_chunk:
                    final_chunks.append(current_chunk)
                    current_chunk = ""
                
                # 递归切分超长部分
                recursive_splits = self._recursive_split(split, chunk_size, overlap, new_separators)
                final_chunks.extend(recursive_splits[:-1])
                current_chunk = recursive_splits[-1]
            
            # 正常合并逻辑
            elif current_chunk and len(current_chunk) + len(separator) + len(split) > chunk_size:
                final_chunks.append(current_chunk)
                overlap_text = current_chunk[-(overlap):] if overlap > 0 else ""
                current_chunk = overlap_text + (separator if overlap_text else "") + split
            else:
                if current_chunk:
                    current_chunk += separator + split
                else:
                    current_chunk = split
        
        if current_chunk:
            final_chunks.append(current_chunk)
            
        return final_chunks

    def _construct_prompt_from_table(self, scenario_desc: str, entities_df) -> str:
        rule_text = f"【核心本体定义文档】:\n{scenario_desc if scenario_desc else '无'}\n\n"
        rule_text += "【用户补充的提取重点】:\n"
        valid_rows = 0
        if entities_df is not None and not entities_df.empty:
            for _, row in entities_df.iterrows():
                try:
                    cls_name = str(row.iloc[0]).strip()
                    attrs = str(row.iloc[1]).strip()
                    rels = str(row.iloc[2]).strip()
                except:
                    cls_name = str(row.get("主体 (Class)", "")).strip()
                    attrs = str(row.get("属性 (DataProp)", "")).strip()
                    rels = str(row.get("关系 (ObjectProp)", "")).strip()
                if not cls_name: continue
                valid_rows += 1
                rule_text += f"- 重点关注类: {cls_name} | 建议属性: {attrs} | 建议关系: {rels}\n"

        if valid_rows == 0:
            rule_text += "  (表格为空，请完全依据【核心本体定义文档】中的逻辑体系进行构建)\n"
        return rule_text

    def _analyze_global_schema(self, sample_text: str, scenario_desc: str) -> str:
        """
        对长文档进行预分析，提取全局 Schema 框架。
        """
        system_prompt = "你是一位本体建模专家。请分析提供的文本片段，提取出最核心的类(Classes)和关系(Properties)框架，以便后续详细提取时保持一致性。"
        user_prompt = f"场景描述: {scenario_desc}\n文本片段: {sample_text}\n请以 JSON 格式输出核心类和关系列表。"
        try:
            data = self.llm_client.call_llm(system_prompt, user_prompt)
            schema_str = json.dumps(data, ensure_ascii=False)
            return f"\n【全局 Schema 参考】: {schema_str}\n"
        except Exception as e:
            logger.warning(f"全局 Schema 分析失败: {e}")
            return ""

    def sync_ttl_to_vector_store(self, ttl_file_path: str, progress=None, delete_old: bool = True) -> str:
        """
        将现有的 TTL 文件内容同步到 Milvus 向量库中。
        """
        filename = os.path.basename(ttl_file_path)
        
        # 如果开启了覆盖模式，先删除该文件旧的同步记录
        if delete_old:
            # 由于 metadata 是 VARCHAR 存储的 JSON 字符串，使用 like 操作符匹配文件名
            self.vector_manager.delete_by_expr(f'metadata like "%\\"source_file\\": \\"{filename}\\"%"')

        g = Graph()
        try:
            g.parse(ttl_file_path, format="turtle")
        except Exception as e:
            return f"❌ TTL 解析失败: {e}"
        
        # 1. 预提取所有 label
        labels = {}
        for s, p, o in g.triples((None, RDFS.label, None)):
            labels[s] = str(o)
        
        knowledge_texts = []
        knowledge_metas = []
        
        def get_local(uri):
            u = str(uri)
            return u.split("#")[-1] if "#" in u else u.split("/")[-1]

        triples = list(g)
        total = len(triples)
        count = 0
        
        for i, (s, p, o) in enumerate(triples):
            # 过滤掉本体元数据定义
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
                "predicate": p_id
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
                if progress: progress(i/total, desc=f"同步中... {i}/{total}")

        if knowledge_texts:
            self.vector_manager.insert_data(knowledge_texts, knowledge_metas)
            
        return f"✅ 同步完成：共处理 {count} 条三元组到库 {self.vector_manager.collection_name}"

    def build_ontology(self, 
                      text: str, 
                      scenario_desc: str, 
                      entities_df, 
                      chunk_size: int = 15000, 
                      chunk_overlap: int = 500, 
                      request_interval: int = 2, 
                      progress=None, 
                      product_code: Optional[str] = None) -> tuple[str, str]:
        master_g = Graph()
        master_g.bind("ex", self.EX)
        master_g.bind("owl", OWL)
        master_g.bind("rdfs", RDFS)
        master_g.bind("xsd", XSD)
        master_g.bind("dc", Namespace("http://purl.org/dc/elements/1.1/"))

        onto_uri = URIRef("http://www.example.org/auto_ontology")
        master_g.add((onto_uri, RDF.type, OWL.Ontology))
        master_g.add((onto_uri, OWL.versionInfo, Literal("1.0")))

        # 针对超长文本的优化：如果文本过长，先进行全局 Schema 预分析
        global_schema_context = ""
        if len(text) > chunk_size * 2:
            if progress: progress(0, desc="正在进行全局 Schema 预分析...")
            global_schema_context = self._analyze_global_schema(text[:chunk_size*2], scenario_desc)

        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        total_chunks = len(chunks)
        
        uri_cache = {}
        def get_uri(id_str):
            s = str(id_str).strip()
            safe = re.sub(r'[^a-zA-Z0-9_-]', '_', s)
            if safe and safe[0].isdigit(): safe = "n" + safe
            if safe not in uri_cache: uri_cache[safe] = self.EX[safe]
            return uri_cache[safe]

        for i, chunk_text in enumerate(chunks):
            if progress: progress((i + 1) / total_chunks, desc=f"AI 处理中 {i + 1}/{total_chunks}...")
            
            # 1. 原始文本切片入库
            sub_chunks = list(self._sub_chunk_text(chunk_text))
            sub_metas = [{"source": "raw_text", "chunk_id": f"{i}_{j}", "date": datetime.now().strftime("%Y-%m-%d")} for j in range(len(sub_chunks))]
            self.vector_manager.insert_data(sub_chunks, sub_metas)

            # 2. LLM 提取
            user_rules_text = self._construct_prompt_from_table(scenario_desc, entities_df)
            real_time_date = datetime.now().strftime("%Y-%m-%d")
            
            # 构建知识域代码相关的提示
            domain_code_instruction = ""
            if product_code:
                domain_code_instruction = f"""
            
            【🔴 强制知识域隔离规范】
            当前知识域代码: {product_code}
            
            所有实例的 ID 必须遵循格式: {{实体类型}}_{{具体名称}}_{product_code}
            
            正确示例:
            - Entity_Name_{product_code}
            - Topic_AI_{product_code}
            - Department_HR_{product_code}
            - Product_ABC_{product_code}
            
            错误示例（绝对禁止）:
            - Entity_Name  ❌ (缺少知识域代码)
            - Topic_AI  ❌ (缺少知识域代码)
            
            这是为了防止不同知识域/主题/领域间的实例冲突，确保合并后的知识库中每个知识域的实例完全隔离。
            """
            
            system_prompt = f"""你是一位精通 OWL2 DL 标准的本体架构师，正在执行全量知识构建任务。
            【核心指令】
            1. 全量提取：提取文本中出现的所有相关实体。
            2. 禁止举例：严禁只提取一个例子代表一类。
            3. 宁可多提：只要符合类定义，就必须提取。
            4. ID 必须英文且唯一：所有 "id" 必须使用英文。{domain_code_instruction if product_code else "**重要：为了防止不同知识域间的实体冲突，请务必在实例 ID 中包含知识域标识或缩写（例如：Entity_Name_DOMAIN_A 而非简单的 Entity_Name）**。"}
            5. Label 必须中文：所有 "label" 字段必须是中文。
            """
            user_prompt = f"""
            {user_rules_text}
            {global_schema_context}
            【当前日期】: "{real_time_date}"
            【处理进度】: 第 {i + 1} / {total_chunks} 个片段
            【当前文本】: "{chunk_text}"
            【输出 JSON 格式】:
            {{
                "metadata": {{ "iri": "http://example.org/onto", "version": "1.0" }},
                "classes": [ {{ "id": "C", "label": "类", "subClassOf": "Parent" }} ],
                "object_properties": [ {{ "id": "op", "label": "关系", "domain": "C", "range": "C" }} ],
                "datatype_properties": [ {{ "id": "dp", "label": "属性", "domain": "C", "range": "string" }} ],
                "instances": [ 
                    {{ 
                        "id": "i1", "type": "C", "label": "实例1", 
                        "object_props": {{ "op_id": ["target_id"] }},
                        "data_props": {{ "dp_id": "value" }},
                        "annotations": {{ "source": "...", "extractionDate": "{real_time_date}" }} 
                    }} 
                ]
            }}
            
            【重要约束】:
            - 必须始终输出有效的JSON格式
            - 不得省略任何数组，即使为空也要输出[]
            - 不得截断输出，必须完整返回所有字段
            - 确保所有对象和数组正确闭合
            - 确保JSON结构完整，包含所有必要的大括号和方括号
            """
            # 使用非流式调用以获得更完整的响应
            data = self.llm_client.call_llm(system_prompt, user_prompt, stream=False)
            # 确保数据结构完整性
            if not data:
                logger.warning(f"第 {i + 1} 个片段的LLM返回数据为空，跳过此片段")
                continue
            # 确保必需字段存在，如果不存在则创建空数组
            if "classes" not in data:
                data["classes"] = []
            if "instances" not in data:
                data["instances"] = []
            if "object_properties" not in data:
                data["object_properties"] = []
            if "datatype_properties" not in data:
                data["datatype_properties"] = []

            # 3. 知识三元组与属性入库
            knowledge_texts = []
            knowledge_metas = []
            for inst in data.get("instances", []):
                s_id = inst["id"]
                s_uri_local = str(get_uri(s_id)).split("#")[-1]
                s_label = inst.get("label", s_id)
                s_type = inst.get("type", "Unknown")
                
                # 存储类型关系
                knowledge_texts.append(f"{s_label} 是 {s_type} 类型")
                knowledge_metas.append({
                    "source": "triple", 
                    "subject": s_label, 
                    "subject_id": s_uri_local,
                    "predicate": "type", 
                    "object": s_type
                })
                
                # 存储对象属性 (关系)
                for p, targets in inst.get("object_props", {}).items():
                    targets = targets if isinstance(targets, list) else [targets]
                    for t in targets:
                        t_uri_local = str(get_uri(t)).split("#")[-1]
                        knowledge_texts.append(f"{s_label} 的 {p} 是 {t}")
                        knowledge_metas.append({
                            "source": "triple", 
                            "subject": s_label, 
                            "subject_id": s_uri_local,
                            "predicate": p, 
                            "object": t,
                            "object_id": t_uri_local
                        })
                
                # 存储数据属性 (属性规则)
                for p, val in inst.get("data_props", {}).items():
                    knowledge_texts.append(f"{s_label} 的 {p} 属性值为 {val}")
                    knowledge_metas.append({
                        "source": "attribute", 
                        "subject": s_label, 
                        "subject_id": s_uri_local,
                        "predicate": p, 
                        "object": str(val)
                    })
                
                # 存储注释 (来源溯源等)
                for ann_id, ann_val in inst.get("annotations", {}).items():
                    knowledge_texts.append(f"{s_label} 的 {ann_id} 信息为 {ann_val}")
                    knowledge_metas.append({
                        "source": "annotation", 
                        "subject": s_label, 
                        "subject_id": s_uri_local,
                        "predicate": ann_id, 
                        "object": str(ann_val)
                    })

            if knowledge_texts:
                self.vector_manager.insert_data(knowledge_texts, knowledge_metas)

            # 4. Graph 合并
            for cls in data.get("classes", []):
                uri = get_uri(cls.get("id"))
                master_g.add((uri, RDF.type, OWL.Class))
                master_g.add((uri, RDFS.label, Literal(cls.get("label", cls["id"]), lang="zh")))
                if cls.get("subClassOf"):
                    parents = cls["subClassOf"] if isinstance(cls["subClassOf"], list) else [cls["subClassOf"]]
                    for p in parents:
                        if p and p not in ["Thing", "owl:Thing"]: master_g.add((uri, RDFS.subClassOf, get_uri(p)))

            for prop in data.get("object_properties", []):
                uri = get_uri(prop.get("id"))
                master_g.add((uri, RDF.type, OWL.ObjectProperty))
                master_g.add((uri, RDFS.label, Literal(prop.get("label", prop["id"]), lang="zh")))
                if prop.get("domain"): master_g.add((uri, RDFS.domain, get_uri(prop["domain"])))
                if prop.get("range"): master_g.add((uri, RDFS.range, get_uri(prop["range"])))

            for prop in data.get("datatype_properties", []):
                uri = get_uri(prop.get("id"))
                master_g.add((uri, RDF.type, OWL.DatatypeProperty))
                master_g.add((uri, RDFS.label, Literal(prop.get("label", prop["id"]), lang="zh")))

            for inst in data.get("instances", []):
                uri = get_uri(inst.get("id"))
                master_g.add((uri, RDF.type, OWL.NamedIndividual))
                master_g.add((uri, RDFS.label, Literal(inst.get("label", inst["id"]), lang="zh")))
                if inst.get("type"): master_g.add((uri, RDF.type, get_uri(inst["type"])))
                for pid, targets in inst.get("object_props", {}).items():
                    targets = targets if isinstance(targets, list) else [targets]
                    for t in targets: master_g.add((uri, get_uri(pid), get_uri(t)))
                for pid, val in inst.get("data_props", {}).items():
                    master_g.add((uri, get_uri(pid), Literal(val, lang="zh") if isinstance(val, str) else Literal(val)))
                for ann_id, ann_val in inst.get("annotations", {}).items():
                    master_g.add((uri, get_uri(ann_id), Literal(ann_val, lang="zh")))

            if i < total_chunks - 1:
                time.sleep(request_interval) # 适当限流

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("TTL", exist_ok=True)
        filename = os.path.join("TTL", f"ontology_{timestamp}.ttl")
        master_g.serialize(filename, format="turtle")
        
        count_cls = len(list(master_g.subjects(RDF.type, OWL.Class)))
        count_inst = len(list(master_g.subjects(RDF.type, OWL.NamedIndividual)))
        msg = f"✅ 构建成功！\n- 处理分块: {total_chunks} 个\n- 类定义: {count_cls} 个\n- 实例: {count_inst} 个\n- 数据已同步至 Milvus"
        return filename, msg