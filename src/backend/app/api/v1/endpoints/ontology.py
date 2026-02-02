# app/api/v1/endpoints/ontology.py - 本体生成端点
# 功能：提供本体文件生成、保存和同步到向量库的API接口

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List, Dict, Any, Optional
import pandas as pd
import os
from datetime import datetime
from rdflib import Graph, RDF, OWL, RDFS, Namespace, URIRef, Literal
from app.schemas.request import ExtractionRequest
from app.schemas.response import OntologyResponse, ErrorResponse
from app.services.extractor import OntologyExtractor
from app.core.config import settings
from sqlalchemy.orm import Session
from app.infrastructure.database import get_db, Project, User  # 添加 User 导入
from app.api.auth import get_current_user  # 添加这行导入

# 通过 settings 对象访问配置值
VLLM_API_KEY = settings.VLLM_API_KEY
VLLM_BASE_URL = settings.VLLM_BASE_URL
VLLM_MODEL = settings.VLLM_MODEL
MILVUS_COLLECTION_NAME = settings.MILVUS_COLLECTION_NAME
from app.core.exceptions import ExtractionException
from app.core.logging import logger

router = APIRouter()


@router.post("/projects/{project_id}/update-ontology")
async def update_ontology(project_id: int, data: Dict[str, Any], db: Session = Depends(get_db)):
    """
    更新本体数据并重新生成TTL文件 - 修复属性保存和TTL更新问题
    """
    try:
        # 获取项目
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 获取节点和边数据
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        
        # 创建图对象 - 使用与OntologyExtractor相同的命名空间
        EX = Namespace("http://www.example.org/auto_ontology#")
        g = Graph()
        g.bind("ex", EX)
        g.bind("owl", OWL)
        g.bind("rdfs", RDFS)
        g.bind("xsd", Namespace("http://www.w3.org/2001/XMLSchema#"))
        
        # 添加本体声明
        onto_uri = URIRef("http://www.example.org/auto_ontology")
        g.add((onto_uri, RDF.type, OWL.Ontology))
        g.add((onto_uri, OWL.versionInfo, Literal("1.0")))
        
        # 关键修复：正确处理节点属性
        for node in nodes:
            node_id = node.get("id", f"node_{len(nodes)}")
            node_label = node.get("data", {}).get("label", "未知实体")
            node_type = node.get("data", {}).get("type", "Entity")
            
            uri = EX[node_id]
            
            # 添加类型
            if node_type == "Class":
                g.add((uri, RDF.type, OWL.Class))
            else:
                g.add((uri, RDF.type, OWL.NamedIndividual))
            
            # 添加标签（确保中文正确）
            g.add((uri, RDFS.label, Literal(node_label, lang="zh")))
            
            # 关键修复：正确处理properties属性
            properties = node.get("data", {}).get("properties", {})
            for prop_name, prop_value in properties.items():
                # 确保属性名和值都被正确处理
                prop_uri = EX[prop_name]
                g.add((prop_uri, RDF.type, OWL.DatatypeProperty))
                g.add((prop_uri, RDFS.label, Literal(prop_name, lang="zh")))
                # 确保属性值被正确添加（关键修复点）
                g.add((uri, prop_uri, Literal(str(prop_value), lang="zh")))
        
        # 添加边
        for edge in edges:
            source_id = edge.get("source")
            target_id = edge.get("target")
            relation = edge.get("data", {}).get("relation", "related_to")
            edge_label = edge.get("data", {}).get("label", "关联")
            
            if source_id and target_id:
                source_uri = EX[source_id]
                target_uri = EX[target_id]
                
                rel_uri = EX[relation]
                g.add((rel_uri, RDF.type, OWL.ObjectProperty))
                g.add((rel_uri, RDFS.label, Literal(edge_label, lang="zh")))
                g.add((source_uri, rel_uri, target_uri))
        
        # 生成TTL文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("TTL", exist_ok=True)
        filename = os.path.join("TTL", f"ontology_updated_{project_id}_{timestamp}.ttl")
        g.serialize(filename, format="turtle")
        
        # 读取TTL内容
        with open(filename, "r", encoding="utf-8") as f:
            ttl_content = f.read()
        
        # 关键修复：确保更新所有相关字段
        project.graph_data = {"nodes": nodes, "edges": edges}
        project.ttl_content = ttl_content
        project.updated_at = datetime.utcnow()
        
        # 提交数据库
        db.commit()
        
        return {
            "status": "success",
            "message": "本体已更新，TTL文件已重新生成（属性保存已修复）",
            "filename": filename
        }
        
    except Exception as e:
        logger.error(f"更新本体时发生错误: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新本体失败: {str(e)}")


@router.post("/sync-ttl")
async def sync_ttl_to_vector_store(ttl_file_path: str, delete_old: bool = True):
    """
    将TTL文件同步到向量库
    """
    try:
        backend = OntologyExtractor(VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL, MILVUS_COLLECTION_NAME)
        result = backend.sync_ttl_to_vector_store(ttl_file_path, delete_old=delete_old)
        
        return {"status": "success", "message": result}
    except Exception as e:
        logger.error(f"同步TTL到向量库时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"同步TTL到向量库失败: {str(e)}")


# 新增：获取首页统计数据的API端点
@router.get("/dashboard/stats")
async def get_dashboard_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    获取首页统计数据
    """
    try:
        # 获取当前用户的项目数量
        my_projects_count = db.query(Project).filter(Project.owner_id == current_user.id).count()
        
        # 获取已发布的本体数量（所有用户可见的）
        published_ontologies_count = db.query(Project).filter(Project.is_published == True).count()
        
        # 获取公共资产数量（这里假设公共资产就是已发布的本体）
        public_assets_count = published_ontologies_count
        
        # 获取总节点数（遍历所有项目，累加节点数量）
        total_nodes = 0
        projects = db.query(Project).all()
        for project in projects:
            if project.graph_data and isinstance(project.graph_data, dict) and 'nodes' in project.graph_data:
                total_nodes += len(project.graph_data['nodes'])
        
        return {
            "status": "success",
            "data": {
                "my_projects": my_projects_count,
                "published_ontologies": published_ontologies_count,
                "public_assets": public_assets_count,
                "total_nodes": total_nodes
            }
        }
        
    except Exception as e:
        logger.error(f"获取统计数据时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}")
