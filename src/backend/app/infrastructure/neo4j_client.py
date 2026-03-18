# app/infrastructure/neo4j_client.py - Neo4j 图数据库客户端
# 功能：封装 Neo4j 操作，支持溯源信息存储到节点和边

from neo4j import GraphDatabase
import logging
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

    def text2cypher_query(self, project_id: int, question: str, schema: dict = None, llm_client=None, model: str = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Text2Cypher 查询：将自然语言问题转换为 Cypher 查询
        1. 使用 LLM 将问题转换为 Cypher
        2. 执行 Cypher 查询
        3. 返回结果和溯源信息
        
        返回：
        - results: 查询结果列表
        - references: 溯源信息列表
        """
        if not self.driver or not llm_client:
            return [], []
        
        # 1. 构建 Schema 描述
        schema_desc = ""
        if schema:
            classes = schema.get("classes", [])
            object_properties = schema.get("object_properties", [])
            schema_desc = "Schema 信息:\n"
            schema_desc += "类:\n"
            for cls in classes:
                schema_desc += f"  - {cls.get('label', cls.get('id'))}\n"
            schema_desc += "关系:\n"
            for op in object_properties:
                schema_desc += f"  - {op.get('label', op.get('id'))}: {op.get('domain')} -> {op.get('range')}\n"
        
        # 2. 调用 LLM 生成 Cypher
        system_prompt = """你是一位 Neo4j Cypher 查询专家。请根据提供的 Schema 和用户问题，生成正确的 Cypher 查询语句。

【要求】：
1. 只生成 Cypher 查询，不要输出其他内容。
2. 查询必须包含 project_id 过滤条件。
3. 查询结果应包含 source_quote 等溯源信息。
"""
        
        user_prompt = f"""{schema_desc}
用户问题：{question}

请生成 Cypher 查询语句：
"""
        
        try:
            llm_response = llm_client.call_llm(system_prompt, user_prompt, max_retries=3, stream=False)
            cypher_query = llm_response.get("content", "") if isinstance(llm_response, dict) else str(llm_response)
            
            # 清理 Cypher 查询，去除可能的 markdown 标记
            cypher_query = cypher_query.replace("```cypher", "").replace("```", "").strip()
            
            logger.info(f"Generated Cypher: {cypher_query}")
            
            # 3. 执行 Cypher 查询
            with self.driver.session() as session:
                result = session.run(cypher_query, project_id=project_id)
                records = [record.data() for record in result]
                
                # 4. 提取溯源信息
                references = []
                for record in records:
                    # 从记录中提取溯源信息
                    for key, value in record.items():
                        if isinstance(value, dict):
                            if "source_quote" in value:
                                references.append({
                                    "type": "graph_result",
                                    "file": value.get("source_file"),
                                    "chunk_index": value.get("source_chunk_index"),
                                    "quote": value.get("source_quote"),
                                    "domain": value.get("domain"),
                                })
                
                return records, references
                
        except Exception as e:
            logger.error(f"Text2Cypher query failed: {e}")
            # Fallback: 使用通用查询
            return self.query_with_provenance(project_id, question, schema)


neo4j_client = Neo4jClient()