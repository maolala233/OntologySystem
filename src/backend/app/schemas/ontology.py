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
    """项目详情行（含 graph_data）：用于单个项目详情接口，不能用于列表。"""
    status: str = "draft"  # draft | building | scheduled | ready | published
    classes_total: int = 0
    instances_total: int = 0
    # 已发布后内容又更新过（重新构建过）：前端据此显示「有新版本」标记，提示可重新发布
    stale_published: bool = False


class ProjectBrief(ProjectBase):
    """项目列表行轻量 DTO（/my 与 /my/paged）：不返回 graph_data / ttl_content。

    此前列表行继承 ProjectResponse，每个项目都携带完整图谱 JSON（7 个项目约 2MB），
    公网下拉列表要数秒。列表只需要元信息与统计，图谱由详情接口按需加载。
    """
    id: int
    owner_id: Optional[int] = None
    owner: Optional[UserResponse] = None
    domain_id: Optional[int] = None
    domain: Optional[KnowledgeDomainResponse] = None
    is_published: bool
    created_at: datetime
    updated_at: datetime
    status: str = "draft"  # draft | building | scheduled | ready | published
    classes_total: int = 0
    instances_total: int = 0
    nodes_total: int = 0
    # 已发布后内容又更新过（重新构建过）：前端据此显示「有新版本」标记，提示可重新发布
    stale_published: bool = False

    @field_serializer("created_at", "updated_at")
    def _ser_time(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    class Config:
        from_attributes = True
