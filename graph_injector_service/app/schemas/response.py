"""
Pydantic Schema定义 - 响应模型
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending/processing/completed/error")
    progress: int = Field(default=0, description="进度百分比(0-100)")
    message: str = Field(default="", description="状态描述")
    result: Optional[Dict[str, Any]] = Field(default=None, description="任务结果")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    created_at: Optional[str] = Field(default=None, description="创建时间")
    completed_at: Optional[str] = Field(default=None, description="完成时间")


class SchemaBuildResponse(BaseModel):
    """Schema构建响应"""
    status: str = Field(..., description="状态: success/error")
    schema_path: Optional[str] = Field(default=None, description="生成的Schema文件路径")
    schema_content: Optional[Dict[str, Any]] = Field(default=None, description="Schema内容")
    message: str = Field(default="", description="处理结果描述")
    entity_types: List[str] = Field(default=[], description="提取的实体类型列表")
    relation_types: List[str] = Field(default=[], description="提取的关系类型列表")


class InstanceBuildResponse(BaseModel):
    """实例构建响应"""
    status: str = Field(..., description="状态: success/error")
    entities: List[Dict[str, Any]] = Field(default=[], description="提取的实体列表")
    relationships: List[Dict[str, Any]] = Field(default=[], description="提取的关系列表")
    message: str = Field(default="", description="处理结果描述")
    stats: Optional[Dict[str, Any]] = Field(default=None, description="构建统计信息")


class EntityInjectResult(BaseModel):
    """实体注入结果"""
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")
    action: str = Field(..., description="操作类型: created/updated/skipped")
    error: Optional[str] = Field(default=None, description="错误信息")


class RelationInjectResult(BaseModel):
    """关系注入结果"""
    from_entity: str = Field(..., description="源实体名称")
    to_entity: str = Field(..., description="目标实体名称")
    action: str = Field(..., description="操作类型: created/updated/skipped")
    error: Optional[str] = Field(default=None, description="错误信息")


class InjectResponse(BaseModel):
    """注入任务响应"""
    status: str = Field(..., description="状态: success/error")
    task_id: Optional[str] = Field(default=None, description="任务ID（异步模式）")
    message: str = Field(default="", description="处理结果描述")
    summary: Optional[Dict[str, Any]] = Field(default=None, description="注入统计摘要")
    entities_injected: int = Field(default=0, description="注入的实体数量")
    relationships_injected: int = Field(default=0, description="注入的关系数量")
    errors: List[str] = Field(default=[], description="错误信息列表")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="服务状态: healthy/degraded/unhealthy")
    timestamp: str = Field(..., description="检查时间")
    version: str = Field(default="1.0.0", description="服务版本")
    components: Optional[Dict[str, str]] = Field(default=None, description="组件状态")


class ConnectivityTestResponse(BaseModel):
    """连通性测试响应"""
    status: str = Field(..., description="测试状态: success/error")
    component: str = Field(..., description="测试组件名称")
    message: str = Field(default="", description="测试结果描述")
    details: Optional[Dict[str, Any]] = Field(default=None, description="详细信息")
