# app/services/rag_engine.py - 双路 RAG 引擎
# 功能：融合 Neo4j 图检索和 Milvus 向量检索，实现双路溯源问答

import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from app.infrastructure.neo4j_client import neo4j_client
from app.infrastructure.vector_client import VectorStoreManager
from app.infrastructure.llm_client import LLMClient

logger = logging.getLogger(__name__)


class DualPathRAGEngine:
    """
    双路 RAG 引擎：融合 Neo4j 图检索和 Milvus 向量检索
    
    工作流程：
    1. 路径 A：Neo4j 结构化图检索 - 获取精确的关系图谱路径
    2. 路径 B：Milvus 语义向量召回 - 获取丰富的文本切片
    3. 智能上下文融合 - 拼接两路结果
    4. 最终 LLM 生成 - 生成回答并标注引用
    """
    
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.neo4j_client = neo4j_client
        self.vector_store = VectorStoreManager()
        self.llm_client = LLMClient(api_key=api_key, base_url=base_url, model=model)
        logger.info(f"[DualPathRAGEngine] 初始化：model={model}, base_url={base_url}")
    
    def query(
        self,
        question: str,
        project_id: int,
        domains: Optional[List[str]] = None,
        top_k: int = 5,
        use_text2cypher: bool = True,
        use_advanced_text2cypher: bool = True,  # ★ 新增：是否使用 3 步大模型驱动的检索
        schema: Optional[Dict] = None,
        db_session=None,  # ★ 新增：数据库会话，用于从 SQLite 获取 schema
    ) -> Dict[str, Any]:
        """
        执行双路 RAG 查询
        
        参数:
        - question: 用户问题
        - project_id: 项目 ID
        - domains: 知识域列表（可选），用于过滤
        - top_k: 向量检索返回数量
        - use_text2cypher: 是否使用 Text2Cypher 查询
        - use_advanced_text2cypher: 是否使用 3 步大模型驱动的检索（默认 True）
        - schema: 本体 Schema（用于 Text2Cypher）
        - db_session: 数据库会话（可选，用于从 SQLite 获取 schema）
        
        返回:
        {
            "answer": "回答文本",
            "references": [...],
            "debug_info": {...}
        }
        """
        # ★ 关键修复：如果 schema 为 None，则从 SQLite 或 Neo4j 获取
        if schema is None:
            logger.info(f"[DualPathRAG] 正在获取项目 {project_id} 的 Schema")
            schema = neo4j_client.get_project_schema(project_id, db_session=db_session)
            logger.info(f"[DualPathRAG] Schema 获取完成：{len(schema.get('classes', []))} 类，{len(schema.get('object_properties', []))} 关系")
            
            # ★ 详细日志：打印 Schema 内容
            if schema.get('classes'):
                logger.info(f"[DualPathRAG] ★ Schema 类详情:")
                for cls in schema.get('classes', [])[:5]:
                    logger.info(f"  - {cls.get('label')}: {cls.get('data_properties', [])[:10]}")
            if schema.get('object_properties'):
                logger.info(f"[DualPathRAG] ★ Schema 关系详情:")
                for op in schema.get('object_properties', [])[:5]:
                    logger.info(f"  - {op.get('domain')} -[{op.get('label')}]-> {op.get('range')}")
            
            # ★ 修复：如果 Schema 为空，强制重新从 Neo4j 获取
            if not schema.get('classes') and not schema.get('object_properties'):
                logger.warning(f"[DualPathRAG] Schema 为空，尝试直接从 Neo4j 获取（不使用 db_session）")
                schema = neo4j_client.get_project_schema(project_id, db_session=None)
                logger.info(f"[DualPathRAG] 重新获取 Schema：{len(schema.get('classes', []))} 类，{len(schema.get('object_properties', []))} 关系")
                
                if schema.get('classes'):
                    logger.info(f"[DualPathRAG] ★ Schema 类详情:")
                    for cls in schema.get('classes', [])[:5]:
                        logger.info(f"  - {cls.get('label')}: {cls.get('data_properties', [])[:10]}")
        else:
            logger.info(f"[DualPathRAG] 使用传入的 Schema：{len(schema.get('classes', []))} 类，{len(schema.get('object_properties', []))} 关系")
        
        logger.info("=" * 80)
        logger.info("[DualPathRAG] 开始双路 RAG 查询")
        logger.info(f"[DualPathRAG] ★ 输入参数:")
        logger.info(f"  - question: {question}")
        logger.info(f"  - project_id: {project_id}")
        logger.info(f"  - domains: {domains}")
        logger.info(f"  - top_k: {top_k}")
        logger.info(f"  - use_text2cypher: {use_text2cypher}")
        logger.info(f"  - use_advanced_text2cypher: {use_advanced_text2cypher}")
        logger.info("=" * 80)
        
        # ========== 路径 A：Neo4j 图检索 ==========
        graph_facts = []
        graph_references = []
        
        if self.neo4j_client.driver:
            try:
                # ★ 使用 3 步大模型驱动的检索流程
                if use_text2cypher and use_advanced_text2cypher:
                    logger.info("[DualPathRAG] ★ 使用 3 步大模型驱动的 Text2Cypher 检索")
                    graph_results, graph_refs = self.neo4j_client.advanced_text2cypher_query(
                        project_id=project_id,
                        question=question,
                        schema=schema,
                        llm_client=self.llm_client,
                        vector_manager=self.vector_store,
                        use_fallback=True,
                        db_session=db_session,  # ★ 传递 db_session 用于从 SQLite 获取 schema
                    )
                    # 从结果中提取事实
                    for result in graph_results:
                        graph_facts.append({
                            "data": result,
                            "type": result.get("type", "graph_result"),
                        })
                    graph_references = graph_refs
                    
                elif use_text2cypher and schema:
                    # 使用旧版 Text2Cypher 查询
                    graph_results, graph_refs = self.neo4j_client.text2cypher_query(
                        project_id=project_id,
                        question=question,
                        schema=schema,
                        llm_client=self.llm_client,
                    )
                    # 从结果中提取事实
                    for result in graph_results:
                        graph_facts.append({
                            "data": result,
                            "type": "graph_result",
                        })
                    graph_references = graph_refs
                else:
                    # 使用通用查询
                    graph_facts, graph_refs = self.neo4j_client.query_with_provenance(
                        project_id=project_id,
                        question=question,
                        schema=schema,
                    )
                
                graph_references = graph_refs
                logger.info(f"Neo4j 检索结果：{len(graph_facts)} 条事实，{len(graph_references)} 条引用")
                
                # ★ 详细日志：打印 Neo4j 检索结果
                logger.info("[DualPathRAG] ★ Neo4j 检索结果详情:")
                for i, fact in enumerate(graph_facts[:5]):
                    logger.info(f"  [事实{i+1}] type={fact.get('type')}, data={fact}")
                if len(graph_facts) > 5:
                    logger.info(f"  ... 还有 {len(graph_facts) - 5} 条事实")
                
                logger.info("[DualPathRAG] ★ Neo4j 引用详情:")
                for i, ref in enumerate(graph_references[:5]):
                    logger.info(f"  [引用{i+1}] type={ref.get('type')}, file={ref.get('file')}, chunk={ref.get('chunk_index')}, quote={ref.get('quote', '')[:50]}...")
                if len(graph_references) > 5:
                    logger.info(f"  ... 还有 {len(graph_references) - 5} 条引用")
                
            except Exception as e:
                logger.error(f"Neo4j 检索失败：{e}")
        
        # ========== 路径 B：Milvus 向量检索 ==========
        vector_results = []
        vector_references = []
        
        if self.vector_store.is_enabled and self.vector_store.collection:
            try:
                # 构建过滤表达式
                filter_expr = f"project_id == {project_id}"
                if domains:
                    domain_expr = " or ".join([f'domain == "{d}"' for d in domains])
                    filter_expr = f"({filter_expr}) and ({domain_expr})"
                
                logger.info(f"Milvus 过滤表达式：{filter_expr}")
                
                # 向量召回
                vector_results = self.vector_store.search_with_expr(
                    query_text=question,
                    expr=filter_expr,
                    top_k=top_k,
                )
                
                # 提取引用信息
                for i, result in enumerate(vector_results):
                    ref = {
                        "type": "vector_chunk",
                        "file": result.get("source_file", ""),
                        "chunk_index": result.get("metadata", {}).get("chunk_index", i),
                        "quote": result.get("source_quote", result.get("text", "")),
                        "domain": result.get("domain", ""),
                        "chunk_text": result.get("metadata", {}).get("chunk_text", result.get("text", "")),
                    }
                    vector_references.append(ref)
                
                logger.info(f"Milvus 检索结果：{len(vector_results)} 条结果，{len(vector_references)} 条引用")
                
                # ★ 详细日志：打印 Milvus 检索结果
                logger.info("[DualPathRAG] ★ Milvus 检索结果详情:")
                for i, result in enumerate(vector_results[:5]):
                    logger.info(f"  [结果{i+1}] text={result.get('text', '')[:100]}..., distance={result.get('distance', 0):.4f}")
                    logger.info(f"            file={result.get('source_file')}, domain={result.get('domain')}, quote={result.get('source_quote', '')[:50]}...")
                if len(vector_results) > 5:
                    logger.info(f"  ... 还有 {len(vector_results) - 5} 条结果")
                
                logger.info("[DualPathRAG] ★ Milvus 引用详情:")
                for i, ref in enumerate(vector_references[:5]):
                    logger.info(f"  [引用{i+1}] type={ref.get('type')}, file={ref.get('file')}, chunk={ref.get('chunk_index')}, quote={ref.get('quote', '')[:50]}...")
                if len(vector_references) > 5:
                    logger.info(f"  ... 还有 {len(vector_references) - 5} 条引用")
                
            except Exception as e:
                logger.error(f"Milvus 检索失败：{e}")
        
        # ========== 智能上下文融合 ==========
        # ★ 优化：限制图谱事实数量，最多保留 20 条
        limited_graph_facts = graph_facts[:20]
        
        # 构建融合的上下文
        context_parts = []
        all_references = []
        ref_id_counter = 1
        
        # 1. 添加 Neo4j 的确凿事实（包含节点属性）
        if limited_graph_facts:
            context_parts.append("【图谱事实】")
            for fact in limited_graph_facts:
                # ★ 关键修复：advanced_text2cypher_query 返回的结果格式是 {"data": {...}, "type": "..."}
                # 需要从 data 中提取 properties
                fact_data = fact.get("data", {})
                fact_type = fact.get("type", "")
                
                # 如果 fact 本身就是结果（没有嵌套 data），直接使用
                if not fact_data and fact_type:
                    fact_data = fact
                
                if fact_type == "edge" or fact_data.get("type") == "edge":
                    subject = fact_data.get("subject", fact.get("subject", ""))
                    predicate = fact_data.get("predicate", fact.get("predicate", ""))
                    object_val = fact_data.get("object", fact.get("object", ""))
                    context_parts.append(f"- {subject} -> {predicate} -> {object_val}")
                    
                elif fact_type == "node" or fact_data.get("type") == "node":
                    # ★ 修复：从 data 中提取 properties
                    value = fact_data.get("value", fact.get("value", ""))
                    properties = fact_data.get("properties", fact.get("properties", {}))
                    
                    # ★ 修复：也检查 matched_props（来自 _build_entity_keyword_query 的特殊字段）
                    matched_props = fact_data.get("matched_props", [])
                    
                    node_info = value
                    if properties:
                        key_props = {k: v for k, v in properties.items() if k in ['产品编号', '产品类型', '投资周期', '募集方式', '风险等级', '业绩比较基准']}
                        if key_props:
                            prop_str = ", ".join([f"{k}: {v}" for k, v in key_props.items()])
                            node_info = f"{value} ({prop_str})"
                    
                    # ★ 修复：如果有 matched_props，添加到输出
                    if matched_props:
                        for prop in matched_props:
                            if isinstance(prop, dict):
                                key = prop.get("key", "")
                                val = prop.get("value", "")
                                if key and val:
                                    node_info = f"{node_info}, {key}: {val}"
                    
                    context_parts.append(f"- {node_info}")
                else:
                    # 其他类型，直接输出 data
                    context_parts.append(f"- {str(fact_data if fact_data else fact)}")
        
        # ★ 优化：限制向量结果数量，最多保留 5 条
        limited_vector_results = vector_results[:5]
        
        # 2. 添加 Milvus 的丰富文本
        if limited_vector_results:
            context_parts.append("\n【相关文档】")
            for i, result in enumerate(limited_vector_results):
                # ★ 优化：从 metadata 中提取文本，截断到 300 字符
                metadata = result.get("metadata", {})
                chunk_text = metadata.get("chunk_text", result.get("text", ""))[:300]
                
                # ★ 优化：只保留关键属性
                properties = metadata.get("properties", {})
                if properties and isinstance(properties, str):
                    try:
                        properties = json.loads(properties)
                    except:
                        pass
                
                if properties and isinstance(properties, dict):
                    key_props = {k: v for k, v in properties.items() if k in ['产品编号', '产品类型', '投资周期', '募集方式', '风险等级', '业绩比较基准']}
                    if key_props:
                        prop_str = ", ".join([f"{k}: {v}" for k, v in key_props.items()])
                        chunk_text = f"{chunk_text} | 属性：{prop_str}"
                
                context_parts.append(f"[{i+1}] {chunk_text}...")
        
        fused_context = "\n".join(context_parts)
        logger.info(f"融合上下文长度：{len(fused_context)} 字符 (优化后)")
        
        # ========== 最终 LLM 生成 ==========
        answer, references = self._generate_answer_with_references(
            question=question,
            context=fused_context,
            graph_references=graph_references,
            vector_references=vector_references,
        )
        
        logger.info(f"生成回答：{len(answer)} 字符，{len(references)} 条引用")
        
        # ★ 详细日志：打印最终返回结果
        logger.info("[DualPathRAG] ★ 最终返回结果:")
        logger.info(f"  answer: {answer[:200]}..." if len(answer) > 200 else f"  answer: {answer}")
        logger.info("  references:")
        for i, ref in enumerate(references[:5]):
            logger.info(f"    [{i+1}] id={ref.get('id')}, type={ref.get('type')}, file={ref.get('file')}, quote={ref.get('quote', '')[:50]}...")
        if len(references) > 5:
            logger.info(f"    ... 还有 {len(references) - 5} 条引用")
        logger.info("=" * 80)
        
        return {
            "answer": answer,
            "references": references,
            "debug_info": {
                "graph_facts_count": len(graph_facts),
                "vector_results_count": len(vector_results),
                "graph_references_count": len(graph_references),
                "vector_references_count": len(vector_references),
            }
        }
    
    def _generate_answer_with_references(
        self,
        question: str,
        context: str,
        graph_references: List[Dict],
        vector_references: List[Dict],
    ) -> Tuple[str, List[Dict]]:
        """
        使用 LLM 生成回答，并标注引用
        
        返回:
        - answer: 回答文本
        - references: 引用列表（已去重和排序）
        """
        # ★ 优化：限制引用总数，最多 10 条
        # 1. 合并所有引用，去重
        all_refs = []
        seen_quotes = set()
        ref_id = 1
        
        # 先添加图谱引用（确凿事实）- 限制最多 5 条
        graph_ref_count = 0
        for ref in graph_references:
            if graph_ref_count >= 5:
                break
            quote_key = ref.get("quote", "")[:50]  # 用前 50 个字符作为去重 key
            if quote_key and quote_key not in seen_quotes:
                seen_quotes.add(quote_key)
                all_refs.append({
                    "id": ref_id,
                    "domain": ref.get("domain", ""),
                    "file": ref.get("file", ""),
                    "chunk_index": ref.get("chunk_index", 0),
                    "quote": ref.get("quote", ""),
                    "type": ref.get("type", "graph_edge"),
                })
                ref_id += 1
                graph_ref_count += 1
        
        # 再添加向量引用（丰富文本）- 限制最多 5 条
        vector_ref_count = 0
        for ref in vector_references:
            if vector_ref_count >= 5:
                break
            quote_key = ref.get("quote", "")[:50]
            if quote_key and quote_key not in seen_quotes:
                seen_quotes.add(quote_key)
                all_refs.append({
                    "id": ref_id,
                    "domain": ref.get("domain", ""),
                    "file": ref.get("file", ""),
                    "chunk_index": ref.get("chunk_index", 0),
                    "quote": ref.get("quote", ""),
                    "type": ref.get("type", "vector_chunk"),
                })
                ref_id += 1
                vector_ref_count += 1
        
        # 2. 构建引用映射（用于在回答中标注）
        ref_mapping = {}
        for i, ref in enumerate(all_refs):
            ref_mapping[i + 1] = ref
        
        # 3. 调用 LLM 生成回答
        system_prompt = """你是一位专业的知识问答助手，基于提供的上下文信息回答用户问题。

【回答要求】：
1. 只根据提供的上下文回答，不要编造信息。
2. 在回答中使用 [1][2][3] 等标号标注引用来源。
3. 引用标号放在相关语句的末尾。
4. 如果上下文中没有相关信息，请如实告知用户。

【引用格式说明】：
- [1] 表示引用第 1 条参考信息
- [2] 表示引用第 2 条参考信息
- 以此类推
"""
        
        user_prompt = f"""【用户问题】
{question}

【提供的上下文】
{context}

【参考信息列表】
"""
        for i, ref in enumerate(all_refs):
            user_prompt += f"[{i+1}] 来源：{ref.get('file', '未知')}, 类型：{ref.get('type', 'unknown')}\n"
            user_prompt += f"    引用：{ref.get('quote', '')}\n\n"
        
        user_prompt += "\n请根据以上信息回答问题，并在适当位置标注引用标号："
        
        try:
            # ★ 使用 call_llm_text 方法，因为回答不需要 JSON 格式
            response = self.llm_client.call_llm_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_retries=3,
                stream=False,
                timeout=300.0,  # 5 分钟超时
            )
            
            answer = response.get("content", "") if isinstance(response, dict) else str(response)
            
            # 清理回答中的 markdown 标记
            answer = answer.replace("```", "").strip()
            
            logger.info(f"LLM 生成回答成功：{len(answer)} 字符")
            
        except Exception as e:
            logger.error(f"LLM 生成回答失败：{e}")
            # Fallback: 直接返回上下文
            answer = f"根据检索到的信息：\n{context}"
        
        return answer, all_refs
    
    def extract_references_from_answer(self, answer: str, all_references: List[Dict]) -> List[Dict]:
        """
        从回答中提取实际使用的引用
        分析回答中的 [1][2] 等标号，返回实际被引用的参考信息
        """
        import re
        
        used_refs = []
        # 匹配 [1][2] 等标号
        ref_pattern = re.compile(r'\[(\d+)\]')
        matches = ref_pattern.findall(answer)
        
        for match in matches:
            ref_id = int(match)
            if 1 <= ref_id <= len(all_references):
                used_refs.append(all_references[ref_id - 1])
        
        return used_refs


# 全局实例
rag_engine = DualPathRAGEngine()