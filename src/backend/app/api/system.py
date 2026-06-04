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


@router.post("/test-connectivity/vl")
def test_vl_connectivity(
    config: Dict[str, Any], 
    current_user: User = Depends(get_current_user)
):
    """测试VL视觉模型连通性及视觉能力"""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can test connectivity")
    
    try:
        vl_base_url = config.get("vl_base_url", "")
        vl_api_key = config.get("vl_api_key", "")
        vl_model = config.get("vl_model", "")

        if not vl_base_url or not vl_model:
            return {"status": "error", "message": "请先配置 VL 模型地址和模型名称"}

        import base64
        import io
        import time

        ollama_base = vl_base_url.rstrip("/")
        if "/v1" in ollama_base:
            ollama_base = ollama_base.replace("/v1", "")
        is_ollama = "11434" in vl_base_url or "ollama" in vl_base_url.lower()

        if is_ollama:
            try:
                ps_resp = requests.get(f"{ollama_base}/api/ps", timeout=5)
                if ps_resp.status_code == 200:
                    running_models = ps_resp.json().get("models", [])
                    for m in running_models:
                        mname = m.get("name", m.get("model", ""))
                        if mname and mname != vl_model:
                            try:
                                requests.post(
                                    f"{ollama_base}/api/generate",
                                    json={"model": mname, "keep_alive": 0},
                                    timeout=10,
                                )
                            except Exception:
                                pass
                    if running_models:
                        time.sleep(2)
            except Exception:
                pass

        headers = {
            "Content-Type": "application/json"
        }
        if vl_api_key:
            headers["Authorization"] = f"Bearer {vl_api_key}"

        url = vl_base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        text_payload = {
            "model": vl_model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5
        }
        if is_ollama:
            text_payload["keep_alive"] = "5m"

        try:
            text_resp = requests.post(url, json=text_payload, headers=headers, timeout=60)
        except requests.exceptions.Timeout:
            return {"status": "error", "message": f"VL 模型 ({vl_model}) 连接超时，请检查模型地址和模型名称是否正确"}
        except Exception as e:
            return {"status": "error", "message": f"VL 模型连接失败: {str(e)}"}

        if text_resp.status_code != 200:
            try:
                err = text_resp.json().get("error", {})
                err_msg = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                err_msg = text_resp.text[:200]
            return {"status": "error", "message": f"VL 模型 ({vl_model}) 文本请求失败 (HTTP {text_resp.status_code}): {err_msg}。请检查模型名称是否正确"}

        from PIL import Image as PILImage
        img = PILImage.new("RGB", (64, 64), color=(100, 150, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")

        vision_payload = {
            "model": vl_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in one word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                ]
            }],
            "max_tokens": 20
        }
        if is_ollama:
            vision_payload["keep_alive"] = "5m"

        try:
            vision_resp = requests.post(url, json=vision_payload, headers=headers, timeout=60)
        except requests.exceptions.Timeout:
            if is_ollama:
                try:
                    requests.post(f"{ollama_base}/api/generate", json={"model": vl_model, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass
            return {"status": "error", "message": f"VL 模型 ({vl_model}) 视觉请求超时，模型可能不支持图片输入"}
        except Exception as e:
            if is_ollama:
                try:
                    requests.post(f"{ollama_base}/api/generate", json={"model": vl_model, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass
            return {"status": "error", "message": f"VL 视觉请求失败: {str(e)}"}

        if vision_resp.status_code == 200:
            if is_ollama:
                try:
                    requests.post(f"{ollama_base}/api/generate", json={"model": vl_model, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass
            return {"status": "success", "message": f"VL 模型 ({vl_model}) 连通性及视觉能力测试成功"}
        else:
            try:
                err = vision_resp.json().get("error", {})
                err_msg = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                err_msg = vision_resp.text[:200]

            if is_ollama:
                try:
                    requests.post(f"{ollama_base}/api/generate", json={"model": vl_model, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass

            if "unexpectedly stopped" in err_msg.lower():
                return {
                    "status": "error",
                    "message": f"VL 模型 ({vl_model}) 不支持视觉能力，处理图片时崩溃。该模型可能是纯文本模型，请使用支持视觉的模型（如 qwen3-vl、gemma3、llava 等）"
                }
            elif "resource" in err_msg.lower():
                return {
                    "status": "error",
                    "message": f"VL 模型 ({vl_model}) 加载失败（GPU 内存不足）: {err_msg}。建议：1) 关闭其他占用 GPU 的模型；2) 使用更小的 VL 模型；3) 增加 GPU 显存"
                }
            else:
                return {"status": "error", "message": f"VL 视觉能力测试失败 (HTTP {vision_resp.status_code}): {err_msg}。该模型可能不支持图片输入，请确认使用的是视觉模型"}

    except Exception as e:
        return {"status": "error", "message": f"VL 模型连通性测试失败: {str(e)}"}


@router.get("/vl-status")
def get_vl_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取VL视觉模型配置状态"""
    config = db.query(SystemConfig).filter(SystemConfig.key == "vl_config").first()
    if not config or not config.value:
        return {"configured": False, "model": "", "base_url": ""}
    vl_model = config.value.get("vl_model", "")
    vl_base_url = config.value.get("vl_base_url", "")
    return {
        "configured": bool(vl_model and vl_base_url),
        "model": vl_model,
        "base_url": vl_base_url,
    }