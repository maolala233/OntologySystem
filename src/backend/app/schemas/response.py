# app/schemas/response.py - 响应数据模型
# 功能：定义API响应的数据结构，包括本体响应、问答响应等

from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from enum import Enum


class StatusEnum(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PROCESSING = "processing"


class OntologyResponse(BaseModel):
    """本体构建响应模型"""
    status: StatusEnum
    filename: Optional[str] = None
    ttl_content: Optional[str] = None
    log: str
    message: Optional[str] = None


class QAResponse(BaseModel):
    """问答响应模型"""
    status: StatusEnum
    answer: str
    context: str
    sources: str
    message: Optional[str] = None


class FileUploadResponse(BaseModel):
    """文件上传响应模型"""
    status: StatusEnum
    filename: str
    message: str


class ErrorResponse(BaseModel):
    """错误响应模型"""
    status: StatusEnum
    message: str
    error_code: Optional[str] = None