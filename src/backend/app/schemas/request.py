# app/schemas/request.py - 请求数据模型
# 功能：定义API请求的数据结构，包括抽取请求、RAG查询请求等

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class ExtractionType(str, Enum):
    ONTOLOGY = "ontology"
    RAG = "rag"


class ExtractionRequest(BaseModel):
    """知识抽取请求模型"""
    text_content: str = Field(..., description="要处理的文本内容")
    scenario: str = Field("", description="场景描述")
    rules: List[Dict[str, str]] = Field([], description="提取规则列表")
    chunk_size: int = Field(15000, ge=1000, le=30000, description="LLM分块大小")
    chunk_overlap: int = Field(500, ge=0, le=2000, description="分块重叠大小")
    request_interval: int = Field(2, ge=0, le=60, description="请求间隔（秒）")
    product_code: Optional[str] = Field(None, description="知识域代码")
    api_key: Optional[str] = Field(None, description="API密钥")
    base_url: Optional[str] = Field(None, description="API基础URL")
    model: Optional[str] = Field(None, description="模型名称")
    milvus_collection: Optional[str] = Field(None, description="Milvus集合名称")


class RagQueryRequest(BaseModel):
    """RAG查询请求模型"""
    question: str = Field(..., description="查询问题")
    k_hop: int = Field(2, ge=1, le=5, description="扩散深度")
    milvus_collection: Optional[str] = Field(None, description="Milvus集合名称")
    api_key: Optional[str] = Field(None, description="API密钥")
    base_url: Optional[str] = Field(None, description="API基础URL")
    model: Optional[str] = Field(None, description="模型名称")


class FileUploadRequest(BaseModel):
    """文件上传请求模型"""
    filename: str = Field(..., description="文件名")
    content_type: str = Field(..., description="文件类型")
    size: int = Field(..., description="文件大小")
    scenario: str = Field("", description="场景描述")
    product_code: Optional[str] = Field(None, description="知识域代码")