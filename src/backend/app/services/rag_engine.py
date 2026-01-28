# app/services/rag_engine.py - RAG引擎服务
# 功能：实现基于知识图谱的RAG查询，从TTL文件加载图谱并进行多跳检索

import networkx as nx
from rdflib import Graph, RDF, RDFS, OWL, URIRef
import json
from typing import Tuple, List, Dict, Any
from app.infrastructure.vector_client import VectorStoreManager
from app.infrastructure.llm_client import LLMClient
from app.core.exceptions import RAGException
from app.core.logging import logger


class GraphRAG:
    def __init__(self, vector_manager: VectorStoreManager, llm_client: LLMClient, model: str):
        self.vector_manager = vector_manager
        self.llm_client = llm_client
        self.model = model
        self.graph = nx.MultiDiGraph()

    def load_from_ttl(self, ttl_file: str, clear_graph: bool = True):
        """从 TTL 文件加载知识图谱到 NetworkX"""
        g = Graph()
        g.parse(ttl_file, format="turtle")
        
        if clear_graph:
            self.graph.clear()
        for s, p, o in g:
            # 提取本地名称
            s_id = str(s).split("#")[-1] if "#" in str(s) else str(s)
            p_id = str(p).split("#")[-1] if "#" in str(p) else str(p)
            
            # 处理对象：如果是 URI 则提取 ID，如果是 Literal 则保留原样
            if isinstance(o, URIRef):
                o_id = str(o).split("#")[-1] if "#" in str(o) else str(o)
            else:
                o_id = str(o)
            
            # 添加节点
            self.graph.add_node(s_id, label=s_id)
            self.graph.add_node(o_id, label=o_id)
            
            # 添加边
            self.graph.add_edge(s_id, o_id, key=p_id, relation=p_id)
            
            # 特殊处理：如果是 label 关系，额外建立一个从 label 到 ID 的双向联系（通过无向图扩散可达）
            if p == RDFS.label:
                self.graph.add_edge(o_id, s_id, key="isLabelOf", relation="isLabelOf")
        
        logger.info(f"Graph loaded: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")

    def query(self, question: str, k_hop: int = 2, top_k: int = 5) -> Tuple[str, str, List[Dict[str, Any]]]:
        """多跳 GraphRAG 检索与生成，支持溯源"""
        # 第一步：向量检索定位锚点实体
        hits = self.vector_manager.search(question, top_k=top_k)
        anchor_nodes = []
        sources = []
        
        for hit in hits:
            # 记录来源信息
            meta = hit.get('metadata', {})
            source_info = {
                "text": hit.get('text'),
                "source": meta.get('source', 'unknown'),
                "chunk_id": meta.get('chunk_id', 'unknown'),
                "subject": meta.get('subject', 'unknown')
            }
            sources.append(source_info)
            
            # 尝试从元数据中提取主体和客体作为图锚点
            subject_id = meta.get('subject_id')
            subject_label = meta.get('subject')
            obj_id = meta.get('object_id')
            obj_label = meta.get('object')

            # 优先使用 ID，其次使用 Label
            for node_candidate in [subject_id, subject_label, obj_id, obj_label]:
                if node_candidate and node_candidate in self.graph:
                    anchor_nodes.append(node_candidate)
        
        # 去重
        anchor_nodes = list(set(anchor_nodes))
        
        # 第二步：在 NetworkX 中进行 K-Hop 扩散
        # 优化：如果同时找到了多个锚点，优先保留 ID 中包含问题关键字的，或者更核心的实体
        refined_anchors = []
        for node in anchor_nodes:
            # 如果节点 ID 包含问题中的关键代码（如 AF222827），则该锚点权重极高
            if any(word in str(node) for word in question.split() if len(word) > 3):
                refined_anchors.insert(0, node)
            else:
                refined_anchors.append(node)
        
        if not refined_anchors:
            # 如果没找到图锚点，退化为普通 RAG
            context = "\n".join([h['text'] for h in hits])
            answer = self._generate_answer(question, context)
            return answer, context, sources

        # 使用优化后的锚点进行扩散
        subgraph_nodes = set(refined_anchors[:3]) # 最多取前 3 个核心锚点扩散
        for node in refined_anchors[:3]:
            # 获取 K-Hop 邻居
            neighbors = nx.single_source_shortest_path_length(self.graph.to_undirected(), node, cutoff=k_hop)
            subgraph_nodes.update(neighbors.keys())
        
        # 提取子图三元组
        subgraph_triples = []
        for u, v, data in self.graph.subgraph(subgraph_nodes).edges(data=True):
            subgraph_triples.append(f"{u} --[{data['relation']}]--> {v}")
        
        context = "\n".join(subgraph_triples)
        
        # 第三步：喂给 LLM 生成答案
        answer = self._generate_answer(question, context)
        return answer, context, sources

    def _generate_answer(self, question: str, context: str) -> str:
        system_prompt = "你是一个基于知识图谱的问答助手。请根据提供的图谱上下文回答用户问题。如果上下文不足以回答，请说明。请在回答中尽量引用上下文中的实体和关系。"
        user_prompt = f"【问题】: {question}\n\n【图谱上下文】:\n{context}\n\n请给出详细回答："
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.llm_client.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,
                    timeout=60.0 # 增加 60 秒超时控制
                )
                return response.choices[0].message.content
            except Exception as e:
                import traceback
                logger.warning(f"GraphRAG LLM 调用失败 (第 {attempt+1} 次):")
                logger.warning(traceback.format_exc())
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 * (attempt + 1))
                else:
                    raise RAGException(f"问答生成失败: {str(e)}。请检查模型服务连接或稍后重试。")