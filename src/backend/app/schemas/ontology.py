from datetime import timezone

from pydantic import BaseModel, field_serializer
from typing import Optional, Dict, List, Any
from datetime import datetime
from .domain import KnowledgeDomainResponse

class UserResponse(BaseModel):
    id: int
    username: str
    
    class Config:
        from_attributes = True

class SystemConfigBase(BaseModel):
    key: str
    value: Dict[str, Any]

class SystemConfigUpdate(BaseModel):
    value: Dict[str, Any]

class SystemConfigResponse(SystemConfigBase):
    id: int
    updated_at: datetime

    class Config:
        from_attributes = True

class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    graph_data: Optional[Dict[str, Any]] = None
    is_published: Optional[bool] = None
    domain_id: Optional[int] = None

def _utc_iso(v: datetime | None) -> str | None:
    """naive UTC 时间补 Z 标记：前端 new Date() 才能正确转本地显示
    （否则 UTC 值被当本地时间，差 8 小时——2026-11-06 用户实测）。"""
    if v is None:
        return None
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc).isoformat()
    return v.isoformat()


class ProjectResponse(ProjectBase):
    id: int
    # 允许 NULL：遗留孤儿项目（如归属人被删）会挂掉管理员 scope=all 全量列表
    owner_id: Optional[int] = None
    owner: Optional[UserResponse] = None
    domain_id: Optional[int] = None
    domain: Optional[KnowledgeDomainResponse] = None
    graph_data: Optional[Dict[str, Any]] = None
    ttl_content: Optional[str] = None
    is_published: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_time(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    class Config:
        from_attributes = True


class ProjectListItem(ProjectResponse):
    """项目列表行（2026-11 列表视图改版）：附生命周期状态与轻量统计，
    免去前端逐项目拉构建摘要（状态机：published > building > ready > draft）。"""
    status: str = "draft"  # draft | building | ready | published
    classes_total: int = 0
    instances_total: int = 0
