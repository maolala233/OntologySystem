"""
知识域相关 Schema 定义
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class KnowledgeDomainBase(BaseModel):
    """知识域基础 Schema"""
    name: str = Field(..., description="知识域名称")
    description: Optional[str] = Field(None, description="知识域描述")


class KnowledgeDomainCreate(KnowledgeDomainBase):
    """创建知识域请求 Schema"""
    pass


class KnowledgeDomainUpdate(BaseModel):
    """更新知识域请求 Schema"""
    name: Optional[str] = Field(None, description="知识域名称")
    description: Optional[str] = Field(None, description="知识域描述")


class KnowledgeDomainResponse(KnowledgeDomainBase):
    """知识域响应 Schema"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True