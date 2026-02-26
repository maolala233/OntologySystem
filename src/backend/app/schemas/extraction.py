# app/schemas/extraction.py - 两阶段 LLM 抽取的 Pydantic 数据契约
# 模块一重构：定义前后端严格的 JSON Interface

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


# ─────────────────────────────────────────────
#  共用的图数据结构 (前端 G6 / React-Flow 通用)
# ─────────────────────────────────────────────

class NodeData(BaseModel):
    """节点数据"""
    label: str = Field(..., description="节点显示名称（中文）")
    type: str = Field(..., description="节点类型: 'owl:Class' 或 'owl:NamedIndividual' 或具体类名")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点的 DataProperty 键值对")


class GraphNode(BaseModel):
    """图节点"""
    id: str = Field(..., description="确定性 ID，由后端 MD5(label+type) 生成")
    type: str = Field("custom", description="前端渲染类型")
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    data: NodeData


class EdgeData(BaseModel):
    """边数据"""
    label: str = Field(..., description="关系名称")


class GraphEdge(BaseModel):
    """图中的一条边/关系"""
    id: str = Field(..., description="边的唯一 ID")
    source: str = Field(..., description="起点节点 ID")
    target: str = Field(..., description="终点节点 ID")
    label: str = Field(..., description="关系/属性名称（英文 ID）")
    data: EdgeData


class GraphData(BaseModel):
    """完整的图数据结构（节点 + 边）"""
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)


# ─────────────────────────────────────────────
#  API 1：骨架提取 (Schema Extraction)
# ─────────────────────────────────────────────

class SchemaExtractionRequest(BaseModel):
    """
    API 1 请求体：上传文档文本 + 可选的用户意图
    """
    text_content: str = Field(..., description="文档解析后的原始文本内容")
    user_intent: Optional[str] = Field(
        None,
        description="用户填写的领域关注点/意图（可选）。若填写则引导 LLM 聚焦该领域；否则进行通用提取。"
    )
    chunk_size: int = Field(15000, ge=1000, le=50000, description="LLM 单次处理的文本分块大小（字符数）")
    chunk_overlap: int = Field(500, ge=0, le=2000, description="相邻分块间的重叠字符数")
    request_interval: int = Field(2, ge=0, le=60, description="LLM 请求间隔（秒），防止速率限制")


class OntologyClass(BaseModel):
    """OWL Class 的规范化表示"""
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    sub_class_of: Optional[str] = Field(None, description="父类 ID（若存在继承关系）")
    data_properties: List[str] = Field(default_factory=list, description="该类应有的 DataProperty 名称列表")


class OntologyObjectProperty(BaseModel):
    """OWL ObjectProperty 的规范化表示"""
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    domain: str = Field(..., description="起点类 ID (Domain Class)")
    range: str = Field(..., description="终点类 ID (Range Class)")


class SchemaGraph(BaseModel):
    """
    骨架图：仅包含 Class 和 ObjectProperty，
    是前端 Step 2 (Schema Review) 阶段的核心数据结构。
    也作为 API 2 的输入约束传递到后端。
    """
    classes: List[OntologyClass] = Field(default_factory=list)
    object_properties: List[OntologyObjectProperty] = Field(default_factory=list)


class SchemaExtractionResponse(BaseModel):
    """API 1 响应体：返回仅含类和关系的骨架图"""
    schema_graph: SchemaGraph = Field(..., description="提取出的骨架图（仅类和关系）")
    graph_data: GraphData = Field(..., description="适配前端渲染的节点和边数据")
    message: str = Field(..., description="处理信息，如处理分块数量、类数量等")


# ─────────────────────────────────────────────
#  API 2：实例提取 (Instance Extraction)
# ─────────────────────────────────────────────

class InstanceExtractionRequest(BaseModel):
    """
    API 2 请求体：文档文本 + 用户审核后的完整 Schema
    """
    text_content: str = Field(..., description="文档解析后的原始文本内容（与 API 1 相同）")
    schema_graph: SchemaGraph = Field(
        ...,
        description="用户在前端 Step 2 审核并修改后的骨架图（约束模型必须严格遵守此 Schema）"
    )
    chunk_size: int = Field(15000, ge=1000, le=50000)
    chunk_overlap: int = Field(500, ge=0, le=2000)
    request_interval: int = Field(2, ge=0, le=60)
    product_code: Optional[str] = Field(None, description="知识域隔离代码（可选）")


class OntologyInstance(BaseModel):
    """OWL NamedIndividual 的规范化表示"""
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    type: str = Field(..., description="所属类的 ID，必须是 SchemaGraph.classes 中已定义的类")
    object_props: Dict[str, List[str]] = Field(default_factory=dict, description="对象属性: {prop_id: [target_ids]}")
    data_props: Dict[str, str] = Field(default_factory=dict, description="数据属性: {prop_id: 字面量值}")


class InstanceExtractionResponse(BaseModel):
    """API 2 响应体：返回完整图（骨架 + 实例）"""
    instances: List[OntologyInstance] = Field(default_factory=list, description="提取的实例列表")
    graph_data: GraphData = Field(..., description="完整图数据（Schema 类节点 + 实例节点 + 所有边）")
    discarded_edges_count: int = Field(0, description="被防御性校验丢弃的不合规连线数量")
    message: str = Field(..., description="处理信息摘要")


# ─────────────────────────────────────────────
#  保存 & 发布时的图数据提交
# ─────────────────────────────────────────────

class SaveGraphRequest(BaseModel):
    """保存草稿 / 更新本体时的请求体"""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="前端当前画布的节点数组")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="前端当前画布的边数组")
