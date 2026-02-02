from neo4j import GraphDatabase
import logging
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

    def sync_graph(self, project_id: int, graph_data: dict):
        """
        将本体数据同步到 Neo4j
        """
        if not self.driver:
            logger.error("Neo4j driver not initialized")
            return False

        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])

        with self.driver.session() as session:
            # 1. 清理该项目旧数据 (使用标签隔离)
            session.execute_write(self._delete_project_data, project_id)
            
            # 2. 创建节点
            for node in nodes:
                session.execute_write(self._create_node, project_id, node)
                
            # 3. 创建关系
            for edge in edges:
                session.execute_write(self._create_relationship, project_id, edge)
                
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
    def _create_node(tx, project_id, node_data):
        label = node_data.get("data", {}).get("type", "Entity")
        name = node_data.get("data", {}).get("label", "Unknown")
        node_id = node_data.get("id")
        properties = node_data.get("data", {}).get("properties", {})
        
        # 基础属性
        props = {
            "id": node_id,
            "name": name,
            "project_id": project_id
        }
        # 加入动态属性
        if isinstance(properties, dict):
            props.update(properties)
            
        # 构建 Cypher 语句，动态设置标签
        query = f"CREATE (n:`{label}` $props)"
        tx.run(query, props=props)

    @staticmethod
    def _create_relationship(tx, project_id, edge_data):
        source_id = edge_data.get("source")
        target_id = edge_data.get("target")
        rel_type = edge_data.get("data", {}).get("relation", "RELATED_TO").upper()
        
        query = (
            "MATCH (a), (b) "
            "WHERE a.id = $source_id AND a.project_id = $project_id "
            "AND b.id = $target_id AND b.project_id = $project_id "
            f"CREATE (a)-[r:`{rel_type}`]->(b)"
        )
        tx.run(query, source_id=source_id, target_id=target_id, project_id=project_id)

neo4j_client = Neo4jClient()