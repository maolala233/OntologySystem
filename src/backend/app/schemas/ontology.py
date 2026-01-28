# app/schemas/ontology.py - 本体数据模型
# 功能：定义本体相关的Pydantic模型，包括类、属性、实例等结构

from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class ClassDefinition(BaseModel):
    """类定义"""
    id: str
    label: str
    sub_class_of: Optional[str] = None


class ObjectProperty(BaseModel):
    """对象属性定义"""
    id: str
    label: str
    domain: Optional[str] = None
    range: Optional[str] = None


class DatatypeProperty(BaseModel):
    """数据类型属性定义"""
    id: str
    label: str
    domain: Optional[str] = None
    range: Optional[str] = None


class Instance(BaseModel):
    """实例定义"""
    id: str
    type: str
    label: str
    object_props: Optional[Dict[str, List[str]]] = None
    data_props: Optional[Dict[str, Any]] = None
    annotations: Optional[Dict[str, str]] = None


class OntologyData(BaseModel):
    """本体数据模型"""
    metadata: Optional[Dict[str, Any]] = None
    classes: List[ClassDefinition]
    object_properties: List[ObjectProperty]
    datatype_properties: List[DatatypeProperty]
    instances: List[Instance]


class GraphNode(BaseModel):
    """图节点模型"""
    id: str
    label: str
    type: str


class GraphEdge(BaseModel):
    """图边模型"""
    source: str
    target: str
    label: str
    relation: str


class GraphData(BaseModel):
    """图数据模型"""
    nodes: List[GraphNode]
    edges: List[GraphEdge]