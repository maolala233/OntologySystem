from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class NodeData(BaseModel):
    label: str = Field(..., description="节点显示名称（中文）")
    type: str = Field(..., description="节点类型: 'owl:Class' 或 'owl:NamedIndividual' 或具体类名")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点的 DataProperty 键值对")


class GraphNode(BaseModel):
    id: str = Field(..., description="确定性 ID，由后端 MD5(label+type) 生成")
    type: str = Field("custom", description="前端渲染类型")
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    data: NodeData


class EdgeData(BaseModel):
    label: str = Field(..., description="关系名称")


class GraphEdge(BaseModel):
    id: str = Field(..., description="边的唯一 ID")
    source: str = Field(..., description="起点节点 ID")
    target: str = Field(..., description="终点节点 ID")
    label: str = Field(..., description="关系/属性名称（英文 ID）")
    data: EdgeData


class GraphData(BaseModel):
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)


class SchemaExtractionRequest(BaseModel):
    text_content: str = Field(..., description="文档解析后的原始文本内容")
    user_intent: Optional[str] = Field(
        None,
        description="用户填写的领域关注点/意图（可选）。若填写则引导 LLM 聚焦该领域；否则进行通用提取。"
    )
    chunk_size: int = Field(15000, ge=1000, le=50000, description="LLM 单次处理的文本分块大小（字符数）")
    chunk_overlap: int = Field(10, ge=0, le=50, description="相邻分块间的重叠百分比(0-50)")
    request_interval: int = Field(2, ge=0, le=60, description="LLM 请求间隔（秒），防止速率限制")


class DataPropertyDef(BaseModel):
    name: str = Field(..., description="属性名称（中文）")
    description: Optional[str] = Field(None, description="属性描述")
    data_type: str = Field("string", description="数据类型: string/number/boolean/date/datetime/array/object")


class OntologyClass(BaseModel):
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    sub_class_of: Optional[str] = Field(None, description="父类 ID（若存在继承关系）")
    data_properties: List[str] = Field(default_factory=list, description="该类应有的 DataProperty 名称列表（向后兼容）")
    property_definitions: Optional[List[DataPropertyDef]] = Field(None, description="属性的详细定义（含数据类型和描述）")


class OntologyObjectProperty(BaseModel):
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    description: Optional[str] = Field(None, description="关系描述")
    domain: str = Field(..., description="起点类 ID (Domain Class)")
    range: str = Field(..., description="终点类 ID (Range Class)")
    cardinality: Optional[str] = Field(None, description="关系基数: one-to-one/one-to-many/many-to-one/many-to-many")


class SchemaGraph(BaseModel):
    classes: List[OntologyClass] = Field(default_factory=list)
    object_properties: List[OntologyObjectProperty] = Field(default_factory=list)


class ExtractionMetadata(BaseModel):
    total_chunks: int = Field(0, description="总分块数")
    successful_chunks: int = Field(0, description="成功处理分块数")
    failed_chunks: int = Field(0, description="失败分块数")
    success_rate: float = Field(0.0, description="成功率")
    total_classes: int = Field(0, description="提取的类数量")
    total_object_properties: int = Field(0, description="提取的对象属性数量")
    total_instances: int = Field(0, description="提取的实例数量")
    total_edges: int = Field(0, description="提取的边数量")
    discarded_edges_count: int = Field(0, description="被防御性校验丢弃的边数量")
    deduplication_stats: Optional[Dict[str, Any]] = Field(None, description="去重统计")


class SchemaExtractionResponse(BaseModel):
    schema_graph: SchemaGraph = Field(..., description="提取出的骨架图（仅类和关系）")
    graph_data: GraphData = Field(..., description="适配前端渲染的节点和边数据")
    message: str = Field(..., description="处理信息，如处理分块数量、类数量等")
    metadata: Optional[ExtractionMetadata] = Field(None, description="提取统计元数据")


class InstanceExtractionRequest(BaseModel):
    text_content: str = Field(..., description="文档解析后的原始文本内容（与 API 1 相同）")
    schema_graph: SchemaGraph = Field(
        ...,
        description="用户在前端 Step 2 审核并修改后的骨架图（约束模型必须严格遵守此 Schema）"
    )
    chunk_size: int = Field(15000, ge=1000, le=50000)
    chunk_overlap: int = Field(10, ge=0, le=50, description="相邻分块间的重叠百分比(0-50)")
    request_interval: int = Field(2, ge=0, le=60)
    product_code: Optional[str] = Field(None, description="知识域隔离代码（可选）")


class OntologyInstance(BaseModel):
    id: str = Field(..., description="确定性 ID")
    label: str = Field(..., description="中文标签")
    type: str = Field(..., description="所属类的 ID，必须是 SchemaGraph.classes 中已定义的类")
    object_props: Dict[str, List[str]] = Field(default_factory=dict, description="对象属性: {prop_id: [target_ids]}")
    data_props: Dict[str, str] = Field(default_factory=dict, description="数据属性: {prop_id: 字面量值}")


class InstanceExtractionResponse(BaseModel):
    instances: List[OntologyInstance] = Field(default_factory=list, description="提取的实例列表")
    graph_data: GraphData = Field(..., description="完整图数据（Schema 类节点 + 实例节点 + 所有边）")
    discarded_edges_count: int = Field(0, description="被防御性校验丢弃的不合规连线数量")
    message: str = Field(..., description="处理信息摘要")
    metadata: Optional[ExtractionMetadata] = Field(None, description="提取统计元数据")


class SaveGraphRequest(BaseModel):
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="前端当前画布的节点数组")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="前端当前画布的边数组")
