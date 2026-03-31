from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List
from app.infrastructure.database import get_db, SystemConfig, User
from app.schemas.ontology import SystemConfigUpdate, SystemConfigResponse
from app.api.auth import get_current_user
from app.infrastructure.neo4j_client import neo4j_client
import requests
import logging

router = APIRouter(prefix="/api/system", tags=["system"])

@router.get("/config/{key}", response_model=SystemConfigResponse)
def get_config(key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not config:
        # Return a default empty config if not found
        return {
            "id": 0,
            "key": key,
            "value": {},
            "updated_at": "2024-01-01T00:00:00"
        }
    return config

@router.put("/config/{key}", response_model=SystemConfigResponse)
def update_config(
    key: str, 
    config_update: SystemConfigUpdate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # Authorization: Only 'admin' can change system settings
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can modify system configuration")
    
    db_config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if db_config:
        db_config.value = config_update.value
    else:
        db_config = SystemConfig(key=key, value=config_update.value)
        db.add(db_config)
    
    db.commit()
    db.refresh(db_config)
    return db_config

# 新增：测试连通性API
@router.post("/test-connectivity/llm")
def test_llm_connectivity(
    config: Dict[str, Any], 
    current_user: User = Depends(get_current_user)
):
    """测试大模型连通性"""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can test connectivity")
    
    try:
        # 提取配置
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        model = config.get("model", "")
        
        # 简单测试：发送一个简单的请求到LLM API
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 根据不同的base_url判断API类型
        if "openai" in base_url.lower() or "azure" in base_url.lower():
            # OpenAI格式
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10
            }
            response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=10)
        elif "ollama" in base_url.lower():
            # Ollama格式
            payload = {
                "model": model,
                "prompt": "Hello",
                "stream": False
            }
            response = requests.post(f"{base_url}/api/generate", json=payload, timeout=10)
        else:
            # 通用格式
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10
            }
            response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return {"status": "success", "message": "大模型连通性测试成功"}
        else:
            return {"status": "error", "message": f"大模型连通性测试失败: HTTP {response.status_code}"}
            
    except Exception as e:
        return {"status": "error", "message": f"大模型连通性测试失败: {str(e)}"}

@router.post("/test-connectivity/neo4j")
def test_neo4j_connectivity(
    config: Dict[str, Any], 
    current_user: User = Depends(get_current_user)
):
    """测试Neo4j连通性"""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can test connectivity")
    
    try:
        # 提取配置
        uri = config.get("neo4j_uri", "")
        username = config.get("neo4j_username", "")
        password = config.get("neo4j_password", "")
        
        # 创建临时Neo4j客户端进行测试
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(username, password))
        driver.verify_connectivity()
        driver.close()
        
        return {"status": "success", "message": "Neo4j连通性测试成功"}
        
    except Exception as e:
        return {"status": "error", "message": f"Neo4j连通性测试失败: {str(e)}"}

@router.post("/test-connectivity/embedding")
def test_embedding_connectivity(
    config: Dict[str, Any], 
    current_user: User = Depends(get_current_user)
):
    """测试Embedding连通性"""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can test connectivity")
    
    try:
        # 提取配置
        base_url = config.get("embedding_base_url", "")
        model = config.get("embedding_model", "")
        api_key = config.get("embedding_api_key", "")  # 获取 API Key
        
        # 构建请求头
        headers = {
            "Content-Type": "application/json"
        }
        
        # 如果有 API Key，添加 Authorization 头
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        # 根据不同的base_url判断API类型
        if "ollama" in base_url.lower():
            # Ollama格式
            payload = {
                "model": model,
                "prompt": "Hello"
            }
            response = requests.post(f"{base_url}/api/embeddings", json=payload, timeout=10)
        else:
            # 通用格式
            payload = {
                "input": ["Hello"],
                "model": model
            }
            response = requests.post(f"{base_url}/embeddings", json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return {"status": "success", "message": "Embedding连通性测试成功"}
        else:
            return {"status": "error", "message": f"Embedding连通性测试失败: HTTP {response.status_code}"}
            
    except Exception as e:
        return {"status": "error", "message": f"Embedding连通性测试失败: {str(e)}"}

@router.post("/test-connectivity/milvus")
def test_milvus_connectivity(
    config: Dict[str, Any], 
    current_user: User = Depends(get_current_user)
):
    """测试Milvus连通性"""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can test connectivity")
    
    try:
        # 提取配置
        host = config.get("milvus_host", "")
        port = config.get("milvus_port", "")
        
        # 创建临时Milvus客户端进行测试
        from pymilvus import connections
        connections.connect(host=host, port=port, timeout=10)
        
        return {"status": "success", "message": "Milvus连通性测试成功"}
        
    except Exception as e:
        return {"status": "error", "message": f"Milvus连通性测试失败: {str(e)}"}