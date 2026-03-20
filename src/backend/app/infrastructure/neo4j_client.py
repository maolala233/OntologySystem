# app/infrastructure/neo4j_client.py - Neo4j 图数据库客户端
# 功能：封装 Neo4j 操作，支持溯源信息存储到节点和边

from neo4j import GraphDatabase
import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from app.core.config import settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(self):
        self.uri = settings.NEO4J_URI
        self.username = settings.NEO4J_USERNAME
        self.password = settings.NEO4J_PASSWORD
        self.driver = None
        
        if self.uri and self.password:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
                self.driver.verify_connectivity()
                logger.info("Successfully connected to Neo4j")
            except Exception as e:
                logger.error(f"Failed to connect to Neo4j: {e}")

    def close(self):
        if self.driver:
            self.driver.close()

    def sync_graph(self, project_id: int, graph_data: dict, domain: str = None):
        """
        将本体数据同步到 Neo4j，支持溯源信息存储
        """
        if not self.driver:
            logger.error("Neo4j driver not initialized")
            return False

        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])

        with self.driver.session() as session:
            # 1. 清理该项目旧数据 (使用标签隔离)
            session.execute_write(self._delete_project_data, project_id)
            
            # 2. 创建节点 (包含溯源信息)
            for node in nodes:
                session.execute_write(self._create_node_with_provenance, project_id, node, domain)
                
            # 3. 创建关系 (包含溯源信息)
            for edge in edges:
                session.execute_write(self._create_relationship_with_provenance, project_id, edge, domain)
                
        return True

    def delete_project_data(self, project_id: int):
        """
        从 Neo4j 中删除特定项目的所有数据
        """
        if not self.driver:
            logger.error("Neo4j driver not initialized")
            return False

        with self.driver.session() as session:
            try:
                session.execute_write(self._delete_project_data, project_id)
                logger.info(f"Successfully deleted project {project_id} data from Neo4j")
                return True
            except Exception as e:
                logger.error(f"Failed to delete project {project_id} data from Neo4j: {str(e)}")
                return False

    @staticmethod
    def _delete_project_data(tx, project_id):
        tx.run("MATCH (n) WHERE n.project_id = $project_id DETACH DELETE n", project_id=project_id)

    @staticmethod
    def _create_node_with_provenance(tx, project_id, node_data, domain: str = None):
        """
        创建节点，包含溯源信息
        支持从 properties 中提取 _source_file, _source_chunk_index, _source_quote, _chunk_text, _domain
        """
        node_id = node_data.get("id")
        node_label = node_data.get("data", {}).get("label", "Unknown")
        node_type = node_data.get("data", {}).get("type", "Entity")
        properties = node_data.get("data", {}).get("properties", {}) or {}
        
        # 基础属性
        props = {
            "id": node_id,
            "label": node_label,
            "project_id": project_id,
        }
        
        # 如果有 domain 参数，优先使用
        if domain:
            props["domain"] = domain
        
        # 从 properties 中提取溯源信息 (移除 _ 前缀)
        source_file = properties.get("_source_file")
        source_chunk_index = properties.get("_source_chunk_index")
        source_quote = properties.get("_source_quote")
        chunk_text = properties.get("_chunk_text")
        node_domain = properties.get("_domain")
        
        if source_file:
            props["source_file"] = source_file
        if source_chunk_index is not None:
            props["source_chunk_index"] = int(source_chunk_index)
        if source_quote:
            props["source_quote"] = source_quote
        if chunk_text:
            props["chunk_text"] = chunk_text
        if node_domain and not domain:  # 只有在没有传入 domain 参数时才使用节点自身的 domain
            props["domain"] = node_domain
        
        # 添加业务属性 (排除 _ 开头的隐藏属性)
        for key, value in properties.items():
            if not key.startswith("_"):
                props[key] = value
        
        # 确定节点标签 (使用 node_type 或默认标签)
        # 如果是 owl:Class 或 owl:NamedIndividual，使用更具体的标签
        if node_type == "owl:Class":
            main_label = "Class"
        elif node_type == "owl:NamedIndividual":
            main_label = "NamedIndividual"
        else:
            main_label = "Entity"
        
        # 构建 Cypher 语句，动态设置标签
        # 使用 MERGE 避免重复创建
        query = f"""
        MERGE (n:`{main_label}` {{id: $props.id, project_id: $props.project_id}})
        SET n += $props
        """
        tx.run(query, props=props)

    @staticmethod
    def _create_relationship_with_provenance(tx, project_id, edge_data, domain: str = None):
        """
        创建关系，包含溯源信息
        支持从 data 中提取 _source_file, _source_chunk_index, _source_quote, _chunk_text, _domain
        """
        source_id = edge_data.get("source")
        target_id = edge_data.get("target")
        edge_label = edge_data.get("label", "RELATED_TO")
        edge_data_dict = edge_data.get("data", {}) or {}
        
        # 关系类型 (从 relation 或 label 获取)
        rel_type = edge_data_dict.get("relation", edge_label).upper()
        # 清理关系类型，使其成为有效的 Cypher 标识符
        rel_type = rel_type.replace(" ", "_").replace(":", "_").replace("-", "_").upper()
        if not rel_type or rel_type == "NONE":
            rel_type = "RELATED_TO"
        
        # 基础属性
        rel_props = {
            "source_id": source_id,
            "target_id": target_id,
            "project_id": project_id,
            "label": edge_label,
        }
        
        # 如果有 domain 参数，优先使用
        if domain:
            rel_props["domain"] = domain
        
        # 从 edge_data 中提取溯源信息 (移除 _ 前缀)
        source_file = edge_data_dict.get("_source_file")
        source_chunk_index = edge_data_dict.get("_source_chunk_index")
        source_quote = edge_data_dict.get("_source_quote")
        chunk_text = edge_data_dict.get("_chunk_text")
        edge_domain = edge_data_dict.get("_domain")
        
        if source_file:
            rel_props["source_file"] = source_file
        if source_chunk_index is not None:
            rel_props["source_chunk_index"] = int(source_chunk_index)
        if source_quote:
            rel_props["source_quote"] = source_quote
        if chunk_text:
            rel_props["chunk_text"] = chunk_text
        if edge_domain and not domain:  # 只有在没有传入 domain 参数时才使用边自身的 domain
            rel_props["domain"] = edge_domain
        
        # 构建 Cypher 语句创建关系
        query = f"""
        MATCH (a), (b)
        WHERE a.id = $source_id AND a.project_id = $project_id
        AND b.id = $target_id AND b.project_id = $project_id
        MERGE (a)-[r:`{rel_type}`]->(b)
        SET r.project_id = $project_id,
            r.source_id = $source_id,
            r.target_id = $target_id,
            r.label = $rel_props.label,
            r.domain = $rel_props.domain,
            r.source_file = $rel_props.source_file,
            r.source_chunk_index = $rel_props.source_chunk_index,
            r.source_quote = $rel_props.source_quote,
            r.chunk_text = $rel_props.chunk_text
        """
        tx.run(query, source_id=source_id, target_id=target_id, project_id=project_id, rel_props=rel_props)

    def query_graph(self, project_id: int, cypher: str, params: dict = None) -> List[Dict[str, Any]]:
        """
        执行 Cypher 查询，返回结果
        """
        if not self.driver:
            logger.error("Neo4j driver not initialized")
            return []
        
        if params is None:
            params = {}
        params["project_id"] = project_id
        
        with self.driver.session() as session:
            try:
                result = session.run(cypher, params)
                return [record.data() for record in result]
            except Exception as e:
                logger.error(f"Cypher query failed: {e}")
                return []

    def query_with_provenance(self, project_id: int, question: str, schema: dict = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        基于问题执行图检索，返回事实列表和溯源信息
        支持 Text2Cypher 风格的查询
        
        ★ 优化版：使用关键词匹配进行智能检索，而不是返回所有数据
        
        返回：
        - facts: 事实列表，每个事实包含 {subject, predicate, object, value}
        - references: 溯源信息列表，每个包含 {source_file, source_chunk_index, source_quote, type: "graph_edge"|"graph_node"}
        """
        if not self.driver:
            return [], []
        
        references = []
        facts = []
        
        with self.driver.session() as session:
            try:
                # ★ 1. 从问题中提取关键词，用于智能匹配
                # 提取可能的实体名称（如产品编号、产品名称等）
                import re
                
                # ★ 优化：尝试匹配更广泛的产品编号模式
                # 支持：AF253322, AF253322G, Z10172, 等格式
                product_code_patterns = [
                    r'[A-Z]{1,3}\d{5,7}[A-Z]?',  # AF253322G, AF253322
                    r'[A-Z]{1,2}\d{5,6}',         # Z10172
                    r'\d{5,7}',                    # 纯数字编号
                ]
                product_codes = []
                for pattern in product_code_patterns:
                    matches = re.findall(pattern, question)
                    product_codes.extend(matches)
                # 去重
                product_codes = list(set(product_codes))
                
                # ★ 优化：提取问题中的实体名称（引号内的内容、特定关键词后的内容）
                # 例如："G 类份额"的起点金额是多少 -> 提取 "G 类份额"
                quoted_entities = re.findall(r'"([^"]+)"', question)
                
                # 尝试匹配"XX 的 XX"格式，例如"G 类份额的起点金额" -> 提取 "G 类份额"
                entity_pattern = r'([^\s 的]+) 的 (起点金额 | 申购金额 | 认购金额 | 产品类型 | 风险等级 | 投资周期 | 业绩比较基准 | 收益率 | 募集方式)'
                entity_matches = re.findall(entity_pattern, question)
                extracted_entities = [match[0] for match in entity_matches]
                
                # 尝试匹配产品类型相关关键词
                product_keywords = ['产品类型', '投资周期', '投资期限', '募集方式', '风险等级', '业绩基准', '收益率', '起点金额', '申购金额', '认购金额']
                matched_keywords = [kw for kw in product_keywords if kw in question]
                
                logger.info(f"[Neo4j 检索] 从问题中提取：product_codes={product_codes}, quoted_entities={quoted_entities}, extracted_entities={extracted_entities}, keywords={matched_keywords}")
                
                # ★ 详细日志：打印 Neo4j 检索请求
                logger.info("[Neo4j 检索] ★ 检索请求详情:")
                logger.info(f"  - project_id: {project_id}")
                logger.info(f"  - question: {question}")
                logger.info(f"  - 提取的产品编号：{product_codes}")
                logger.info(f"  - 匹配的关键词：{matched_keywords}")
                
                # ★ 2. 根据提取的关键词构建智能查询
                # 如果有产品编号,优先查询该产品的信息
                if product_codes:
                    # 查询包含产品编号的节点
                    for code in product_codes:
                        # ★ 优化：只查询 NamedIndividual 类型的实例节点，减少无关结果
                        node_query = """
                        MATCH (n:NamedIndividual)
                        WHERE n.project_id = $project_id
                        AND (n.label CONTAINS $code OR n.id CONTAINS $code OR
                             toString(n.产品编号) = $code OR
                             toString(n.产品编号) CONTAINS $code)
                        RETURN n.id as id, n.label as label, 
                               n.source_file as source_file, 
                               n.source_chunk_index as source_chunk_index, 
                               n.source_quote as source_quote,
                               n.domain as domain,
                               n.产品编号 as 产品编号,
                               n.产品类型 as 产品类型,
                               n.投资周期 as 投资周期,
                               n.募集方式 as 募集方式,
                               n.风险等级 as 风险等级,
                               n.业绩比较基准 as 业绩比较基准
                        LIMIT 10
                        """
                        logger.info(f"[Neo4j 检索] ★ 执行节点查询 Cypher (product_code={code}):")
                        logger.info(f"  {node_query.strip()}")
                        
                        node_result = session.run(node_query, project_id=project_id, code=code)
                        nodes = [record.data() for record in node_result]
                        logger.info(f"[Neo4j 检索] 节点查询返回 {len(nodes)} 条结果 (已限制最多 10 条)")
                        
                        # ★ 详细日志：打印节点查询结果
                        for i, node in enumerate(nodes[:3]):
                            logger.info(f"[Neo4j 检索] ★ 节点结果 [{i+1}]:")
                            logger.info(f"    id={node.get('id')}, label={node.get('label')}")
                            props = {k: v for k, v in node.items() if k not in ['id', 'label', 'source_file', 'source_chunk_index', 'source_quote', 'domain'] and v is not None}
                            logger.info(f"    properties={props}")
                            logger.info(f"    source_file={node.get('source_file')}, chunk_index={node.get('source_chunk_index')}")
                        if len(nodes) > 3:
                            logger.info(f"    ... 还有 {len(nodes) - 3} 条节点结果")
                        
                        # 添加节点事实 - 优先使用属性值作为 quote
                        for node in nodes:
                            # 提取业务属性
                            properties = {k: v for k, v in node.items() if k not in ['id', 'label', 'source_file', 'source_chunk_index', 'source_quote', 'domain'] and v is not None}
                            
                            # ★ 优化：如果有业务属性，使用属性值构建 quote
                            quote_text = ""
                            if properties:
                                # 构建属性描述作为 quote
                                prop_descs = []
                                for k, v in properties.items():
                                    prop_descs.append(f"{k}: {v}")
                                quote_text = f"{node.get('label', code)} 的 {', '.join(prop_descs)}"
                            else:
                                quote_text = node.get('source_quote', node.get('label', code))
                            
                            facts.append({
                                "type": "node",
                                "subject": node.get("label") or node.get("id"),
                                "value": node.get("label") or node.get("id"),
                                "properties": properties,
                            })
                            
                            # ★ 优化：优先使用构建的 quote，如果没有 source_quote 则使用属性描述
                            references.append({
                                "type": "graph_node",
                                "file": node.get("source_file", "未知"),
                                "chunk_index": node.get("source_chunk_index", 0),
                                "quote": quote_text,
                                "domain": node.get("domain", ""),
                            })
                        
                        # ★ 优化：只查询与问题关键词相关的关系
                        # 如果问题包含"产品类型"、"风险等级"等关键词，只返回相关的关系
                        rel_query = """
                        MATCH (a:NamedIndividual)-[r]->(b)
                        WHERE a.project_id = $project_id AND b.project_id = $project_id
                        AND (a.label CONTAINS $code OR toString(a.产品编号) CONTAINS $code)
                        RETURN a.label as source_label, a.id as source_id,
                               type(r) as relation, b.label as target_label, b.id as target_id,
                               r.source_file as source_file, 
                               r.source_chunk_index as source_chunk_index,
                               r.source_quote as source_quote, 
                               r.domain as domain
                        LIMIT 20
                        """
                        logger.info(f"[Neo4j 检索] ★ 执行关系查询 Cypher (product_code={code}):")
                        logger.info(f"  {rel_query.strip()}")
                        
                        rel_result = session.run(rel_query, project_id=project_id, code=code)
                        relations = [record.data() for record in rel_result]
                        logger.info(f"[Neo4j 检索] 关系查询返回 {len(relations)} 条结果 (已限制最多 20 条)")
                        
                        # ★ 详细日志：打印关系查询结果
                        for i, rel in enumerate(relations[:3]):
                            logger.info(f"[Neo4j 检索] ★ 关系结果 [{i+1}]:")
                            logger.info(f"    {rel.get('source_label')} -[{rel.get('relation')}]-> {rel.get('target_label')}")
                            logger.info(f"    source_file={rel.get('source_file')}, chunk_index={rel.get('source_chunk_index')}")
                        if len(relations) > 3:
                            logger.info(f"    ... 还有 {len(relations) - 3} 条关系结果")
                        
                        # 添加关系事实 - 优先使用关系描述作为 quote
                        for rel in relations:
                            facts.append({
                                "type": "edge",
                                "subject": rel.get("source_label"),
                                "predicate": rel.get("relation"),
                                "object": rel.get("target_label"),
                            })
                            
                            # ★ 优化：构建关系描述作为 quote
                            quote_text = f"{rel.get('source_label')} {rel.get('relation')} {rel.get('target_label')}"
                            references.append({
                                "type": "graph_edge",
                                "file": rel.get("source_file", "未知"),
                                "chunk_index": rel.get("source_chunk_index", 0),
                                "quote": quote_text,
                                "domain": rel.get("domain", ""),
                            })
                
                # ★ 3. 如果没有匹配到产品编号,使用提取的实体名称查询
                if not facts and (quoted_entities or extracted_entities):
                    # 使用提取的实体名称查询
                    entities_to_search = quoted_entities + extracted_entities
                    logger.info(f"[Neo4j 检索] 使用实体名称查询：{entities_to_search}")
                    
                    for entity in entities_to_search[:3]:  # 限制最多 3 个实体
                        entity_query = """
                        MATCH (n:NamedIndividual)
                        WHERE n.project_id = $project_id
                        AND (n.label = $entity OR n.label CONTAINS $entity OR
                             n.份额名称 = $entity OR n.份额名称 CONTAINS $entity)
                        RETURN n.id as id, n.label as label,
                               n.source_file as source_file,
                               n.source_chunk_index as source_chunk_index,
                               n.source_quote as source_quote,
                               n.domain as domain,
                               n.产品编号 as 产品编号,
                               n.产品类型 as 产品类型,
                               n.份额名称 as 份额名称,
                               n.销售代码 as 销售代码,
                               n.业绩比较基准 as 业绩比较基准,
                               n.起点金额 as 起点金额
                        LIMIT 10
                        """
                        logger.info(f"[Neo4j 检索] ★ 执行实体查询 Cypher (entity={entity}):")
                        
                        try:
                            entity_result = session.run(entity_query, project_id=project_id, entity=entity)
                            nodes = [record.data() for record in entity_result]
                            logger.info(f"[Neo4j 检索] 实体查询返回 {len(nodes)} 条结果")
                            
                            for node in nodes:
                                properties = {k: v for k, v in node.items() if k not in ['id', 'label', 'source_file', 'source_chunk_index', 'source_quote', 'domain'] and v is not None}
                                
                                quote_text = ""
                                if properties:
                                    prop_descs = []
                                    for k, v in properties.items():
                                        prop_descs.append(f"{k}: {v}")
                                    quote_text = f"{node.get('label', entity)} 的 {', '.join(prop_descs)}"
                                else:
                                    quote_text = node.get('source_quote', node.get('label', entity))
                                
                                facts.append({
                                    "type": "node",
                                    "subject": node.get("label") or node.get("id"),
                                    "value": node.get("label") or node.get("id"),
                                    "properties": properties,
                                })
                                
                                references.append({
                                    "type": "graph_node",
                                    "file": node.get("source_file", "未知"),
                                    "chunk_index": node.get("source_chunk_index", 0),
                                    "quote": quote_text,
                                    "domain": node.get("domain", ""),
                                })
                        except Exception as e:
                            logger.warning(f"实体查询失败 (entity={entity}): {e}")
                
                # ★ 4. 如果没有匹配到产品编号,使用关键词匹配
                if not facts and matched_keywords:
                    # 构建关键词查询
                    keyword_conditions = []
                    params = {"project_id": project_id}
                    
                    for i, kw in enumerate(matched_keywords[:5]):  # 限制最多 5 个关键词
                        keyword_conditions.append(f"n.label CONTAINS $kw{i}")
                        params[f"kw{i}"] = kw
                    
                    if keyword_conditions:
                        keyword_query = f"""
                        MATCH (n)
                        WHERE n.project_id = $project_id
                        AND ({' OR '.join(keyword_conditions)})
                        RETURN n.id as id, n.label as label,
                               n.source_file as source_file,
                               n.source_chunk_index as source_chunk_index,
                               n.source_quote as source_quote,
                               n.domain as domain
                        LIMIT 20
                        """
                        logger.info(f"[Neo4j 检索] ★ 执行关键词查询 Cypher (keywords={matched_keywords[:3]}):")
                        logger.info(f"  {keyword_query.strip()}")
                        
                        node_result = session.run(keyword_query, params)
                        nodes = [record.data() for record in node_result]
                        logger.info(f"[Neo4j 检索] 关键词查询返回 {len(nodes)} 条结果")
                        
                        # ★ 详细日志：打印关键词查询结果
                        for i, node in enumerate(nodes[:3]):
                            logger.info(f"[Neo4j 检索] ★ 关键词结果 [{i+1}]:")
                            logger.info(f"    label={node.get('label')}, source_file={node.get('source_file')}")
                        if len(nodes) > 3:
                            logger.info(f"    ... 还有 {len(nodes) - 3} 条关键词结果")
                        
                        for node in nodes:
                            facts.append({
                                "type": "node",
                                "subject": node.get("label"),
                                "value": node.get("label"),
                            })
                            if node.get("source_quote"):
                                references.append({
                                    "type": "graph_node",
                                    "file": node.get("source_file"),
                                    "chunk_index": node.get("source_chunk_index"),
                                    "quote": node.get("source_quote"),
                                    "domain": node.get("domain"),
                                })
                
                # ★ 5. 如果还是没有结果，返回所有实例节点（作为 fallback）
                if not facts:
                    fallback_query = """
                    MATCH (n:NamedIndividual)
                    WHERE n.project_id = $project_id
                    RETURN n.id as id, n.label as label,
                           n.source_file as source_file,
                           n.source_chunk_index as source_chunk_index,
                           n.source_quote as source_quote,
                           n.domain as domain
                    LIMIT 20
                    """
                    logger.info(f"[Neo4j 检索] ★ 执行 Fallback 查询 Cypher:")
                    logger.info(f"  {fallback_query.strip()}")
                    
                    node_result = session.run(fallback_query, project_id=project_id)
                    nodes = [record.data() for record in node_result]
                    logger.info(f"[Neo4j 检索] Fallback 查询返回 {len(nodes)} 条结果")
                    
                    # ★ 详细日志：打印 Fallback 查询结果
                    for i, node in enumerate(nodes[:3]):
                        logger.info(f"[Neo4j 检索] ★ Fallback 结果 [{i+1}]:")
                        logger.info(f"    label={node.get('label')}, source_file={node.get('source_file')}")
                    if len(nodes) > 3:
                        logger.info(f"    ... 还有 {len(nodes) - 3} 条 Fallback 结果")
                    
                    for node in nodes:
                        facts.append({
                            "type": "node",
                            "subject": node.get("label"),
                            "value": node.get("label"),
                        })
                        if node.get("source_quote"):
                            references.append({
                                "type": "graph_node",
                                "file": node.get("source_file"),
                                "chunk_index": node.get("source_chunk_index"),
                                "quote": node.get("source_quote"),
                                "domain": node.get("domain"),
                            })
                
                logger.info(f"[Neo4j 检索] 返回 {len(facts)} 条事实，{len(references)} 条引用")
                return facts, references
                
            except Exception as e:
                logger.error(f"Graph query failed: {e}", exc_info=True)
                return [], []

    def get_project_schema(self, project_id: Any, db_session=None) -> Dict[str, Any]:
        """
        获取项目 Schema：优先从 SQLite 缓存读取，失败则从 Neo4j 实时提取。
        
        🌟 关键修复：Neo4j 中 project_id 可能是字符串或整数，需要兼容两种类型
        """
        schema_data = {
            "classes": [],
            "object_properties": [],
            "data_properties": []
        }
        
        # 🌟 修复 1：强制转换 project_id 为整数，确保与 Neo4j 中的存储类型一致
        try:
            target_project_id = int(project_id)
            logger.info(f"[Schema 获取] 开始处理项目 ID: {target_project_id}")
        except (ValueError, TypeError):
            target_project_id = project_id
            logger.warning(f"[Schema 获取] 项目 ID {project_id} 无法转换为整数，将使用原始值")

        # --- 第一阶段：SQLite 缓存尝试 ---
        if db_session:
            try:
                from app.infrastructure.database import Project # 确保路径准确
                db_project = db_session.query(Project).filter(Project.id == target_project_id).first()
                
                if db_project and db_project.graph_data:
                    # 处理 graph_data 可能是字符串的情况
                    graph_data = db_project.graph_data
                    if isinstance(graph_data, str):
                        graph_data = json.loads(graph_data)
                    
                    cached_schema = graph_data.get("schema", {})
                    if cached_schema and isinstance(cached_schema, dict) and cached_schema.get("classes"):
                        logger.info(f"[Schema 获取] ✓ 从 SQLite 缓存获取成功 (项目 {target_project_id})")
                        
                        # 标准化返回格式
                        for cls in cached_schema.get("classes", []):
                            schema_data["classes"].append({
                                "label": cls.get("label", cls.get("id", "Unknown")),
                                "data_properties": cls.get("data_properties", [])
                            })
                        for op in cached_schema.get("object_properties", []):
                            schema_data["object_properties"].append({
                                "label": op.get("label", op.get("id", "Unknown")),
                                "domain": op.get("domain", "Unknown"),
                                "range": op.get("range", "Unknown")
                            })
                        return schema_data
                logger.info(f"[Schema 获取] SQLite 中无缓存，准备转向 Neo4j 提取")
            except Exception as e:
                logger.error(f"[Schema 获取] SQLite 读取异常：{e}")

        # --- 第二阶段：Neo4j 实时提取 ---
        if not self.driver:
            logger.error("[Schema 获取] Neo4j 驱动未初始化")
            return schema_data

        with self.driver.session() as session:
            try:
                # 🌟 修复 2：使用 toString() 兼容整数和字符串类型的 project_id
                # 先统计项目中的节点数量用于调试
                count_query = """
                MATCH (n)
                WHERE n.project_id = $project_id OR n.project_id = toString($project_id)
                RETURN count(n) as count
                """
                count_result = session.run(count_query, project_id=target_project_id).single()
                node_count = count_result["count"] if count_result else 0
                logger.info(f"[Schema 获取] 项目 {target_project_id} 共有 {node_count} 个节点")
                
                if node_count == 0:
                    logger.warning(f"[Schema 获取] 项目 {target_project_id} 在 Neo4j 中没有数据")
                    return schema_data
                
                # 🌟 修复 3：简化 Labels 查询，不依赖 domain 字段
                # 逻辑：直接使用 labels(n)[0] 获取第一个标签
                # 注意：Neo4j 中节点可能有多个标签，我们取第一个业务标签
                labels_query = """
                MATCH (n)
                WHERE n.project_id = $project_id OR n.project_id = toString($project_id)
                WITH n, labels(n) as all_labels, keys(n) as node_keys
                WITH n, all_labels, node_keys,
                    CASE 
                        WHEN all_labels[0] IN ['NamedIndividual', 'Class', 'owl:NamedIndividual', 'owl:Class'] 
                        THEN all_labels[1] 
                        ELSE all_labels[0] 
                    END as biz_label
                RETURN 
                    CASE 
                        WHEN biz_label IS NULL OR biz_label IN ['NamedIndividual', 'Class', 'owl:NamedIndividual', 'owl:Class'] THEN 'Entity'
                        ELSE biz_label 
                    END as class_label, 
                    collect(DISTINCT node_keys) as all_keys
                """
                
                logger.info(f"[Schema 获取] 执行 Labels 查询...")
                labels_result = session.run(labels_query, project_id=target_project_id)
                
                # 用于去重（因为多个节点可能有相同的 class_label）
                class_map = {} 
                
                for record in labels_result:
                    label = record["class_label"]
                    all_keys = record["all_keys"]
                    
                    # 合并所有属性并去重
                    merged_keys = list(set(key for keys in all_keys for key in keys))
                    
                    # 🌟 修复 4：保留业务相关的属性，只过滤元数据
                    internal_keys = {
                        'id', 'project_id', 'source_file', 'source_chunk_index', 
                        'source_quote', 'chunk_text'
                    }
                    data_props = [k for k in merged_keys if k not in internal_keys]
                    
                    if label not in class_map:
                        class_map[label] = set()
                    class_map[label].update(data_props)

                # 构建最终 classes 列表
                for label, props in class_map.items():
                    schema_data["classes"].append({
                        "label": label,
                        "data_properties": sorted(list(props))
                    })
                    logger.info(f"[Schema 获取]   类：{label} -> 属性：{sorted(list(props))}")

                # 🌟 修复 5：针对关系的查询，同样不依赖 domain 字段
                # 注意：Neo4j 列表推导不支持 NOT IN，使用 FILTER 函数替代
                relationships_query = """
                MATCH (a)-[r]->(b)
                WHERE (a.project_id = $project_id OR a.project_id = toString($project_id))
                  AND (b.project_id = $project_id OR b.project_id = toString($project_id))
                WITH r, a, b, labels(a) as a_labels, labels(b) as b_labels
                WITH r, a, b, a_labels, b_labels,
                    CASE 
                        WHEN a_labels[0] IN ['NamedIndividual', 'Class', 'owl:NamedIndividual', 'owl:Class'] 
                        THEN a_labels[1] 
                        ELSE a_labels[0] 
                    END as start_label,
                    CASE 
                        WHEN b_labels[0] IN ['NamedIndividual', 'Class', 'owl:NamedIndividual', 'owl:Class'] 
                        THEN b_labels[1] 
                        ELSE b_labels[0] 
                    END as end_label
                RETURN DISTINCT 
                    type(r) as rel_type, 
                    CASE WHEN start_label IS NULL THEN 'Entity' ELSE start_label END as start_label,
                    CASE WHEN end_label IS NULL THEN 'Entity' ELSE end_label END as end_label
                """
                logger.info(f"[Schema 获取] 执行关系查询...")
                rel_result = session.run(relationships_query, project_id=target_project_id)
                
                for record in rel_result:
                    schema_data["object_properties"].append({
                        "label": record["rel_type"],
                        "domain": record["start_label"],
                        "range": record["end_label"]
                    })
                    logger.info(f"[Schema 获取]   关系：{record['start_label']} -[{record['rel_type']}]-> {record['end_label']}")

                logger.info(f"[Schema 获取] ✓ Neo4j 提取成功：{len(schema_data['classes'])} 类，{len(schema_data['object_properties'])} 关系")

            except Exception as e:
                logger.error(f"[Schema 获取] Neo4j 提取异常：{e}", exc_info=True)
                
        return schema_data
    
    def _build_schema_from_nodes_edges(self, nodes: List[dict], edges: List[dict]) -> Dict[str, Any]:
        """
        从 nodes 和 edges 数据结构中推断 schema
        """
        schema_data = {
            "classes": [],
            "object_properties": [],
            "data_properties": []
        }
        
        # 从节点中提取类（类型为 owl:Class 的节点）
        for node in nodes:
            node_type = node.get('data', {}).get('type', '')
            if node_type == 'owl:Class':
                node_id = str(node.get('id', ''))
                node_label = node.get('data', {}).get('label', node_id)
                
                # 从 properties 中提取数据属性
                properties = node.get('data', {}).get('properties', {})
                data_props = list(properties.keys()) if isinstance(properties, dict) else []
                
                schema_data["classes"].append({
                    "label": node_label,
                    "data_properties": data_props
                })
        
        # 从边中提取 ObjectProperty
        for edge in edges:
            edge_data = edge.get('data', {})
            relation = edge_data.get('relation', '')
            label = edge.get('label', '')
            
            # 排除 subClassOf 和 type 关系
            if relation in ('subclass_of', 'subClassOf') or label in ('subClassOf', 'subclass_of', 'rdf:type', 'type'):
                continue
            
            if relation or label:
                schema_data["object_properties"].append({
                    "label": label or relation,
                    "domain": edge.get('source', 'Unknown'),
                    "range": edge.get('target', 'Unknown')
                })
        
        return schema_data
    
    def _extract_entity_from_question_with_llm(
        self, 
        question: str, 
        schema: Dict[str, Any], 
        llm_client
    ) -> str:
        """
        ★ LLM 驱动的通用实体提取方法
        
        核心优势：
        1. 不依赖正则表达式，LLM 理解语义后提取
        2. 支持多种问法：
           - "G 类份额的起点金额是多少" -> "G 类份额"
           - "净值披露多长时间披露一次" -> "净值披露"
           - "这个产品有什么风险" -> 产品名称 (从上下文推断)
           - "能提前赎回吗" -> 产品名称 (从上下文推断)
        3. 支持产品编号自动识别
        
        参数:
        - question: 用户问题
        - schema: Schema 数据
        - llm_client: LLM 客户端
        
        返回:
        - extracted_entity: 提取的实体名称
        """
        # 构建 Schema 上下文
        schema_context = self.format_schema_for_llm(schema)
        
        # 提取 Schema 中的类名，用于指导 LLM 识别实体类型
        class_names = [cls.get("label", "") for cls in schema.get("classes", [])]
        
        # ★ 优化提示词：让 LLM 返回更具体的实体，而不是"当前产品"这种占位符
        entity_extraction_prompt = f"""你是一位图谱问答专家。请从用户问题中提取需要查询的核心实体名称。

【当前图谱包含的类】:
{', '.join(class_names[:20])}

【用户问题】: {question}

【提取规则】:
1. 如果问题包含"XX 的 XX"格式（如"G 类份额的起点金额"），提取"XX"（如"G 类份额"）
2. 如果问题询问某个主题的频率/时间/方式（如"净值披露多长时间披露一次"），提取该主题（如"净值披露"）
3. 如果问题包含产品编号（如 AF253322、Z10172 等），直接返回该编号
4. 如果问题使用代词（如"这个产品"、"它"），从问题所在的上下文中推断具体产品名称；如果无法推断，返回"*"表示查询所有产品
5. 如果问题询问通用概念（如"有什么风险"、"费用有哪些"），返回"*"表示查询所有相关内容
6. 如果无法确定具体实体，返回问题中最核心的关键词

【输出格式】:
只返回提取的实体名称，不要有任何解释。

请提取实体："""

        try:
            # 调用 LLM 提取实体
            system_prompt = "你是一位图谱问答专家，擅长从自然语言问题中提取核心实体。"
            
            llm_response = llm_client.call_llm_text(
                system_prompt=system_prompt,
                user_prompt=entity_extraction_prompt,
                max_retries=1,
                stream=False,
                timeout=30.0,
            )
            
            extracted_entity = llm_response.get("content", "").strip() if isinstance(llm_response, dict) else str(llm_response)
            
            # 清理可能的多余内容
            extracted_entity = extracted_entity.replace("```", "").strip()
            
            # 如果 LLM 提取失败或返回空，使用 fallback 策略
            if not extracted_entity or len(extracted_entity) > 100:
                logger.warning(f"[LLM 实体提取] 提取失败或结果异常：{extracted_entity[:50]}...，使用 fallback")
                return self._extract_entity_fallback(question)
            
            logger.info(f"[LLM 实体提取] 成功提取：'{question}' -> '{extracted_entity}'")
            return extracted_entity
            
        except Exception as e:
            logger.error(f"[LLM 实体提取] 失败：{e}，使用 fallback")
            return self._extract_entity_fallback(question)
    
    def _extract_entity_fallback(self, question: str) -> str:
        """
        Fallback 实体提取方法：使用正则表达式和关键词匹配
        """
        import re
        
        # 1. 尝试匹配"XX 的 XX"格式
        entity_pattern = r'([^\s 的"]+) 的 (起点金额 | 申购金额 | 认购金额 | 产品类型 | 风险等级 | 投资周期 | 业绩比较基准 | 收益率 | 募集方式 | 披露频率 | 披露时间 | 频率 | 时间 | 披露渠道)'
        entity_matches = re.findall(entity_pattern, question)
        if entity_matches:
            return entity_matches[0][0]
        
        # 2. 尝试提取常见实体关键词
        entity_keywords = ['净值披露', '信息披露', '定期报告', '临时报告', '产品管理人', '产品托管人', '销售机构']
        for kw in entity_keywords:
            if kw in question:
                return kw
        
        # 3. 尝试从频率问题中提取实体
        frequency_pattern = r'([^\s 的"]+?) (多长时间 | 多久 | 频率 | 一次)'
        freq_matches = re.findall(frequency_pattern, question)
        if freq_matches:
            return freq_matches[0]
        
        # 4. 尝试提取引号内的内容
        quoted_entities = re.findall(r'"([^"]+)"', question)
        if quoted_entities:
            return quoted_entities[0]
        
        # 5. 尝试提取产品编号
        product_code_patterns = [
            r'[A-Z]{1,3}\d{5,7}[A-Z]?',
            r'[A-Z]{1,2}\d{5,6}',
            r'\d{5,7}',
        ]
        for pattern in product_code_patterns:
            matches = re.findall(pattern, question)
            if matches:
                return matches[0]
        
        # ★ 新增：识别通用问题，返回"*"使用通用查询
        # 这些问题询问产品的通用特性，应该查询所有产品
        general_question_patterns = [
            (r'能.*吗', '*'),  # "能提前赎回吗"、"能转让吗"
            (r'可以.*吗', '*'),  # "可以提前赎回吗"
            (r'是否.*', '*'),  # "是否免收认购费"
            (r'有没有.*', '*'),  # "有没有风险"
            (r'.*风险.*', '*'),  # "有什么风险"
            (r'.*费用.*', '*'),  # "有哪些费用"
            (r'.*费.*', '*'),  # "认购费多少"
            (r'.*收益.*', '*'),  # "收益率多少"
            (r'.*期限.*', '*'),  # "投资期限多长"
            (r'.*赎回.*', '*'),  # "赎回规则"
            (r'.*认购.*', '*'),  # "认购起点"
            (r'.*申购.*', '*'),  # "申购条件"
        ]
        
        for pattern, result in general_question_patterns:
            if re.search(pattern, question):
                logger.info(f"[Fallback 实体提取] 识别为通用问题：'{question}' -> '*'")
                return "*"
        
        # 6. 返回整个问题作为最后 fallback
        return question
    
    def format_schema_for_llm(self, schema_data: Dict[str, Any]) -> str:
        """
        将 Schema 格式化为极简字符串给 LLM 看
        """
        schema_context = "【当前图谱结构字典】\n"
        
        # 1. 整理类和它们的属性
        for cls in schema_data.get("classes", []):
            props = ", ".join(cls.get("data_properties", []))
            schema_context += f"节点类型：{cls['label']} | 包含属性：[{props}]\n"
        
        # 2. 整理关系
        for op in schema_data.get("object_properties", []):
            schema_context += f"关系：({op['domain']}) -[{op['label']}]-> ({op['range']})\n"
        
        return schema_context
    
    def entity_alignment(self, user_entity: str, project_id: int, vector_manager=None, top_k: int = 1) -> str:
        """
        ★ 第二步：向量辅助的"实体对齐"（解决缩写和错别字）
        
        用户提问往往很口语化，比如问："安盈象的起点金额是多少？"
        但数据库里节点全名是"信银理财安盈象固收稳利十四个月封闭式"。
        
        使用 Milvus 向量检索找到最匹配的节点全名！
        
        参数:
        - user_entity: 用户问题中提取的实体名（如"安盈象"）
        - project_id: 项目 ID
        - vector_manager: 向量管理器实例
        - top_k: 返回最匹配的数量
        
        返回:
        - real_node_name: 数据库中真实的节点名称
        """
        if not vector_manager or not vector_manager.is_enabled:
            logger.warning(f"[实体对齐] 向量库不可用，返回原始实体：{user_entity}")
            return user_entity
        
        try:
            # 1. 去 Milvus 里搜这个词，拿回最匹配的 metadata.subject
            # 使用 search_with_expr 方法，因为它支持表达式过滤
            search_results = vector_manager.search_with_expr(
                user_entity, 
                expr=f"project_id == {project_id}",
                top_k=top_k
            )
            
            if search_results:
                # 🌟 改进 1：处理 JSON 字符串兼容性
                metadata = search_results[0].get("metadata", {})
                if isinstance(metadata, str):
                    import json
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                
                # 🌟 核心修复：必须提取 metadata 里的 subject！绝不能提取 text 或 source_quote！
                real_node_name = metadata.get("subject")
                
                # 🌟 修复：如果 subject 是一个长句子（包含"是一个"、"其属性有"等），说明向量库返回的是描述文本
                # 这时应该尝试从其他字段提取真正的实体名称
                if real_node_name and (len(real_node_name) > 50 or "是一个" in real_node_name or "其属性有" in real_node_name):
                    logger.warning(f"[实体对齐] subject 是长句子，尝试从其他字段提取：{real_node_name[:50]}...")
                    
                    # 尝试 1：从 label 字段提取
                    real_node_name = metadata.get("label")
                    
                    # 尝试 2：如果 label 也不可用，尝试从 chunk_text 中提取
                    if not real_node_name:
                        chunk_text = metadata.get("chunk_text", "")
                        if chunk_text:
                            # 尝试提取"实体【XXX】"格式
                            import re
                            match = re.search(r'实体【([^】]+)】', chunk_text)
                            if match:
                                real_node_name = match.group(1)
                            else:
                                # 取第一个句号前的内容作为实体名
                                real_node_name = chunk_text.split('。')[0][:30]
                    
                    # 尝试 3：如果 subject 本身就是"实体【XXX】是一个..."格式，直接提取 XXX
                    if not real_node_name and "实体【" in str(metadata.get("subject", "")):
                        import re
                        match = re.search(r'实体【([^】]+)】', str(metadata.get("subject", "")))
                        if match:
                            real_node_name = match.group(1)
                            logger.info(f"[实体对齐] 从 subject 中提取实体名称：{real_node_name}")
                
                # 🌟 新增尝试 4：如果还是没有结果，尝试从 source_quote 中提取
                if not real_node_name:
                    source_quote = metadata.get("source_quote", "")
                    if source_quote and "实体【" in source_quote:
                        import re
                        match = re.search(r'实体【([^】]+)】', source_quote)
                        if match:
                            real_node_name = match.group(1)
                            logger.info(f"[实体对齐] 从 source_quote 中提取实体名称：{real_node_name}")
                
                # 🌟 最终 fallback：使用用户原始实体
                if not real_node_name:
                    real_node_name = user_entity
                    logger.warning(f"[实体对齐] 所有提取方式都失败，使用用户原始实体：{user_entity}")
                
                # 🌟 置信度判断 - L2 距离 > 0.8 则放弃对齐
                distance = search_results[0].get("distance", 1.0)
                if distance > 0.8:
                    logger.info(f"[实体对齐] 距离={distance:.4f} > 0.8，放弃对齐，保留原词：{user_entity}")
                    return user_entity
                
                logger.info(f"[实体对齐] '{user_entity}' -> '{real_node_name}' (distance={distance:.4f})")
                return real_node_name
            else:
                logger.warning(f"[实体对齐] 未找到匹配实体，返回原始：{user_entity}")
                return user_entity
                
        except Exception as e:
            logger.error(f"[实体对齐] 失败：{e}")
            return user_entity
    
    def advanced_text2cypher_query(
        self, 
        project_id: int, 
        question: str, 
        schema: Dict[str, Any] = None,
        llm_client=None,
        vector_manager=None,
        use_fallback: bool = True,
        db_session=None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        ★ 简化版：LLM 直接根据 Schema 生成 Cypher 查询
        
        核心流程：
        1. 获取 Schema（类、关系、属性）
        2. 将 Schema + 用户问题一起给 LLM
        3. LLM 生成正确的 Cypher 查询
        4. 执行查询，返回结果
        
        参数:
        - project_id: 项目 ID
        - question: 用户问题
        - schema: Schema 数据（如果为 None，则自动获取）
        - llm_client: LLM 客户端
        - vector_manager: 向量管理器（可选，用于实体对齐）
        - use_fallback: 是否使用兜底策略
        - db_session: SQLAlchemy 数据库会话（可选，用于从 SQLite 获取 schema）
        
        返回:
        - results: 查询结果列表
        - references: 溯源信息列表
        """
        if not self.driver or not llm_client:
            logger.error("[Text2Cypher] Neo4j 驱动或 LLM 客户端不可用")
            return [], []
        
        # ========== 第一步：获取 Schema ==========
        if schema is None:
            logger.info("[Text2Cypher] 正在获取项目 Schema")
            schema = self.get_project_schema(project_id, db_session=db_session)
        
        # ========== 第二步：构建提示词，让 LLM 生成 Cypher ==========
        # 从 Schema 中提取所有真实存在的属性名
        available_props = []
        for cls in schema.get("classes", []):
            available_props.extend(cls.get("data_properties", []))
        available_props = list(set(available_props))
        
        # 从 Schema 中提取所有关系类型
        available_relations = [op.get("label", "") for op in schema.get("object_properties", [])]
        
        # 构建 Schema 描述
        schema_desc = self.format_schema_for_llm(schema)
        
        # 构建提示词
        text2cypher_prompt = f"""你是一位 Neo4j Cypher 查询专家。请根据提供的 Schema 和用户问题，生成正确的 Cypher 查询语句。

【图谱 Schema】:
{schema_desc}

【可用属性列表】:
{', '.join(available_props) if available_props else 'label, id'}

【可用关系类型】:
{', '.join(available_relations) if available_relations else 'RELATED_TO'}

【用户问题】: {question}

【生成要求】:
1. 只生成 Cypher 查询，不要输出其他内容
2. 查询必须包含 project_id 过滤：WHERE n.project_id = {project_id}
3. 查询结果应包含 source_quote 等溯源信息（如果存在）
4. 绝不能捏造 Schema 中不存在的属性名或关系名
5. 必须使用英文逗号 (,) 分隔字段，不能用中文逗号 (，)
6. 节点 Label 使用 NamedIndividual
7. 支持多字段 OR 匹配（如产品编号、份额名称等）

请生成 Cypher 查询："""

        system_prompt = "你是一位 Neo4j Cypher 查询专家，擅长根据 Schema 生成正确的 Cypher 查询。"
        
        try:
            # 调用 LLM 生成 Cypher
            llm_response = llm_client.call_llm_text(
                system_prompt=system_prompt,
                user_prompt=text2cypher_prompt,
                max_retries=1,
                stream=False,
                timeout=300.0,  # 增加到 5 分钟
            )
            cypher_query = llm_response.get("content", "") if isinstance(llm_response, dict) else str(llm_response)
            
            # 清理 Cypher 查询
            cypher_query = cypher_query.replace("```cypher", "").replace("```", "").strip()
            cypher_query = cypher_query.split(";")[0].strip()
            
            # 自动修复中文逗号
            if "，" in cypher_query:
                logger.warning("[Text2Cypher] 检测到中文逗号，正在自动修复...")
                cypher_query = cypher_query.replace("，", ",")
            
            logger.info(f"[Text2Cypher] 生成的 Cypher:\n{cypher_query}")
            
            # ========== 第三步：执行查询 ==========
            results = []
            references = []
            
            with self.driver.session() as session:
                try:
                    result = session.run(cypher_query, project_id=project_id)
                    records = [record.data() for record in result]
                    
                    if not records:
                        raise ValueError("查询无结果")
                    
                    # 转换结果为标准格式
                    for record in records:
                        self._convert_record_to_result(record, results, references, project_id)
                    
                    logger.info(f"[Text2Cypher] 查询成功，返回 {len(results)} 条结果")
                    return results, references
                    
                except Exception as e:
                    if not use_fallback:
                        raise e
                    
                    logger.warning(f"[Text2Cypher] 查询失败，使用兜底策略：{e}")
                    
                    # 兜底：查询所有 NamedIndividual 节点及其属性
                    fallback_cypher = f"""
                    MATCH (n:NamedIndividual)
                    WHERE n.project_id = {project_id}
                    RETURN n.label, n.id, n.产品编号，n.产品类型，n.份额名称，n.销售代码，n.起点金额，n.业绩比较基准，properties(n) as props
                    LIMIT 30
                    """
                    
                    logger.info(f"[Text2Cypher] 执行兜底查询：{fallback_cypher}")
                    result = session.run(fallback_cypher, project_id=project_id)
                    records = [record.data() for record in result]
                    
                    for record in records:
                        self._convert_record_to_result(record, results, references, project_id)
                    
                    logger.info(f"[Text2Cypher] 兜底查询返回 {len(results)} 条结果")
                    return results, references
                
        except Exception as e:
            logger.error(f"[Text2Cypher] 失败：{e}")
            # 最后的 fallback：使用通用查询
            return self.query_with_provenance(project_id, question, schema)
    
    def _convert_record_to_result(
        self, 
        record: Dict[str, Any], 
        results: List[Dict[str, Any]], 
        references: List[Dict[str, Any]],
        project_id: int
    ):
        """
        将 Neo4j 查询记录转换为标准结果格式
        """
        # 提取所有非元数据字段作为 properties
        properties = {}
        subject = ""
        
        for key, val in record.items():
            if val is None:
                continue
            
            # 处理键名
            clean_key = key.replace("n.", "") if key.startswith("n.") else key
            clean_key = clean_key.strip('`')
            
            if clean_key in ['id', 'label', 'source_file', 'source_chunk_index', 'source_quote', 'domain']:
                if clean_key == 'label':
                    subject = str(val)
                elif clean_key == 'id' and not subject:
                    subject = str(val)
            else:
                properties[clean_key] = val
        
        if subject or properties:
            results.append({
                "type": "node",
                "subject": subject or "Unknown",
                "value": subject or "Unknown",
                "properties": properties,
            })
            
            # 构建 quote
            if properties:
                prop_desc = ", ".join([f"{k}: {v}" for k, v in properties.items()])
                quote_text = f"{subject} 的 {prop_desc}"
            else:
                quote_text = subject
            
            references.append({
                "type": "graph_node",
                "file": record.get("source_file", "unknown"),
                "chunk_index": record.get("source_chunk_index", 0),
                "quote": quote_text,
                "domain": record.get("domain", ""),
            })
    
    def _query_all_entities(
        self, 
        project_id: int, 
        question: str, 
        schema: Dict[str, Any],
        llm_client=None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        ★ 通用查询策略：当实体为"*"时使用
        用于处理"这个产品有什么风险"、"费用有哪些"等通用问题
        
        参数:
        - project_id: 项目 ID
        - question: 用户问题
        - schema: Schema 数据
        - llm_client: LLM 客户端（可选）
        
        返回:
        - results: 查询结果列表
        - references: 溯源信息列表
        """
        results = []
        references = []
        
        if not self.driver:
            return results, references
        
        with self.driver.session() as session:
            try:
                # ★ 1. 分析问题意图，确定查询目标
                # 使用 LLM 分析问题，确定要查询的关系类型
                intent_relation = ""
                
                if llm_client:
                    intent_prompt = f"""你是一位图谱问答专家。请分析用户问题的意图，识别要查询的关系类型。

【用户问题】: {question}

【可用关系类型】:
{', '.join([op.get('label', '') for op in schema.get('object_properties', [])])}

【任务】:
1. 识别问题要查询的核心关系类型（如"涉及风险"、"产生费用"、"支持交易行为"等）
2. 如果问题不明确，返回最可能的关系类型

【输出格式】:
只返回关系类型名称，不要有任何解释。

请分析："""

                    try:
                        llm_response = llm_client.call_llm_text(
                            system_prompt="你是一位图谱问答专家，擅长分析自然语言问题的意图。",
                            user_prompt=intent_prompt,
                            max_retries=1,
                            stream=False,
                            timeout=30.0,
                        )
                        intent_relation = llm_response.get("content", "").strip() if isinstance(llm_response, dict) else str(llm_response)
                        intent_relation = intent_relation.replace("```", "").strip()
                        logger.info(f"[意图识别] 问题 '{question}' -> 关系类型 '{intent_relation}'")
                    except Exception as e:
                        logger.warning(f"[意图识别] 失败：{e}，使用兜底策略")
                        intent_relation = ""
                
                # ★ 2. 根据意图执行查询
                if intent_relation and intent_relation in [op.get('label', '') for op in schema.get('object_properties', [])]:
                    # 有明确意图，查询特定关系
                    cypher = f"""
                    MATCH (n:NamedIndividual)-[r:`{intent_relation}`]->(m:NamedIndividual)
                    WHERE n.project_id = {project_id} AND m.project_id = {project_id}
                    RETURN n.label AS source, m.label AS target, properties(n) AS props
                    LIMIT 20
                    """
                    logger.info(f"[通用查询] 执行意图查询：{cypher}")
                else:
                    # 意图不明确，查询所有关系
                    cypher = f"""
                    MATCH (n:NamedIndividual)-[r]-(m)
                    WHERE n.project_id = {project_id} AND m.project_id = {project_id}
                    RETURN n.label AS source, type(r) AS relation, m.label AS target, properties(n) AS props
                    LIMIT 30
                    """
                    logger.info(f"[通用查询] 执行全量查询：{cypher}")
                
                result = session.run(cypher)
                records = [record.data() for record in result]
                
                # ★ 3. 转换结果为标准格式
                for record in records:
                    source = record.get("source", "")
                    target = record.get("target", "")
                    relation = record.get("relation", "")
                    props = record.get("props", {})
                    
                    # 添加节点事实
                    if props:
                        results.append({
                            "type": "node",
                            "subject": source,
                            "value": source,
                            "properties": props,
                        })
                    
                    # 添加关系事实
                    if relation:
                        results.append({
                            "type": "edge",
                            "subject": source,
                            "predicate": relation,
                            "object": target,
                        })
                    
                    # 构建 quote
                    if props:
                        prop_desc = ", ".join([f"{k}: {v}" for k, v in props.items()])
                        quote_text = f"{source} 的 {prop_desc}"
                    else:
                        quote_text = f"{source} {relation} {target}" if relation else source
                    
                    references.append({
                        "type": "graph_node",
                        "file": "unknown",
                        "chunk_index": 0,
                        "quote": quote_text,
                        "domain": "",
                    })
                
                logger.info(f"[通用查询] 返回 {len(results)} 条结果")
                return results, references
                
            except Exception as e:
                logger.error(f"[通用查询] 失败：{e}", exc_info=True)
                return results, references


neo4j_client = Neo4jClient()
