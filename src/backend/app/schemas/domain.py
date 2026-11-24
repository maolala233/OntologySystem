"""
知识域相关 Schema 定义
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class KnowledgeDomainBase(BaseModel):
    """知识域基础 Schema（长度与数据库列一致：name 100 / description 500）"""
    name: str = Field(..., min_length=1, max_length=100, description="知识域名称")
    description: Optional[str] = Field(None, max_length=500, description="知识域描述")


class KnowledgeDomainCreate(KnowledgeDomainBase):
    """创建知识域请求 Schema"""
    pass


class KnowledgeDomainUpdate(BaseModel):
    """更新知识域请求 Schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="知识域名称")
    description: Optional[str] = Field(None, max_length=500, description="知识域描述")


class KnowledgeDomainResponse(KnowledgeDomainBase):
    """知识域响应 Schema"""
    id: int
    created_at: datetime
    updated_at: datetime
    # 项目总数（含未发布），与删除接口的校验口径一致（Project.domain_id）
    project_count: int = 0

    class Config:
        from_attributes = True