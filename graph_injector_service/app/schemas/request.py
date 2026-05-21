"""
Pydantic Schema定义 - 请求模型
"""
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class SchemaBuildRequest(BaseModel):
    """
    Schema构建请求
    用于从文档内容中提取本体结构(Schema)
    """
    text_content: str = Field(..., min_length=1, description="文档文本内容")
    chunk_size: int = Field(default=1000, ge=100, le=10000, description="每个chunk的字符长度")
    overlap_percentage: int = Field(default=10, ge=0, le=50, description="chunk之间的重叠百分比")
    additional_instructions: Optional[str] = Field(default=None, description="额外的构建指令")

    @field_validator("overlap_percentage")
    @classmethod
    def validate_overlap(cls, v):
        if v < 0 or v > 50:
            raise ValueError("重叠百分比必须在0-50之间")
        return v


class InstanceBuildRequest(BaseModel):
    """
    实例构建请求
    基于给定的Schema从文档中提取实体和关系实例
    """
    text_content: str = Field(..., min_length=1, description="文档文本内容")
    schema_json: dict = Field(..., description="Schema骨架JSON(包含entity_types和relation_types)")
    chunk_size: int = Field(default=1000, ge=100, le=10000, description="每个chunk的字符长度")
    overlap_percentage: int = Field(default=10, ge=0, le=50, description="chunk之间的重叠百分比")
    additional_instructions: Optional[str] = Field(default=None, description="额外的构建指令")


class InjectRequest(BaseModel):
    """
    完整的注入请求
    包含文档上传、分块、Schema构建/使用、实例构建和ES注入全流程
    """
    text_content: Optional[str] = Field(default="", description="直接提供的文本内容")
    chunk_size: int = Field(default=1000, ge=100, le=10000, description="每个chunk的字符长度")
    overlap_percentage: int = Field(default=10, ge=0, le=50, description="chunk之间的重叠百分比")
    use_existing_schema: bool = Field(default=False, description="是否使用已有的Schema文件")
    existing_schema_path: Optional[str] = Field(default=None, description="已有Schema JSON文件的路径")
    ragflow_kb_id: str = Field(..., min_length=1, description="RAGFlow知识库ID")
    ragflow_tenant_id: str = Field(..., min_length=1, description="RAGFlow租户ID")
    ragflow_api_key: str = Field(..., min_length=1, description="RAGFlow API Key")
    additional_instructions: Optional[str] = Field(default=None, description="额外的指令")

    @field_validator("existing_schema_path")
    @classmethod
    def validate_schema_path(cls, v, info):
        values = info.data
        if values.get("use_existing_schema") and not v:
            raise ValueError("当use_existing_schema为true时，必须提供existing_schema_path")
        return v


class InjectTaskRequest(BaseModel):
    """
    异步注入任务请求（用于批量注入）
    """
    text_contents: List[str] = Field(..., min_length=1, description="文档文本内容列表")
    chunk_size: int = Field(default=1000, ge=100, le=10000, description="每个chunk的字符长度")
    overlap_percentage: int = Field(default=10, ge=0, le=50, description="chunk之间的重叠百分比")
    use_existing_schema: bool = Field(default=False, description="是否使用已有的Schema文件")
    existing_schema_path: Optional[str] = Field(default=None, description="已有Schema JSON文件的路径")
    ragflow_kb_id: str = Field(..., min_length=1, description="RAGFlow知识库ID")
    ragflow_tenant_id: str = Field(..., min_length=1, description="RAGFlow租户ID")
    ragflow_api_key: str = Field(..., min_length=1, description="RAGFlow API Key")
    additional_instructions: Optional[str] = Field(default=None, description="额外的指令")
