# app/adapters/exporting.py - 多格式导出（docs/design/03 §14）
# 里程碑：M4 实现。18 种格式 = Semantica exporter + rdflib 补充；
# 大产物异步（Celery）落 MinIO ontology-exports/，turtle/json 保留同步直下。

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExportFormat(str, Enum):
    """18 种导出格式（README §4.3 / 03 §14）。"""

    # RDF(6)
    TURTLE = "turtle"
    NTRIPLES = "ntriples"
    RDFXML = "rdfxml"
    JSONLD = "jsonld"
    TRIG = "trig"
    OWL = "owl"
    # 图(5)
    GRAPHML = "graphml"
    GEXF = "gexf"
    DOT = "dot"
    NEO4J_CYPHER = "neo4j_cypher"
    ARANGO_AQL = "arango_aql"
    # 表格(4)
    CSV = "csv"
    TSV = "tsv"
    PARQUET = "parquet"
    XLSX = "xlsx"
    # 其他(3)
    JSON = "json"            # graph_data 兼容现有前端格式
    YAML = "yaml"
    HTML_REPORT = "html_report"  # 含溯源附录


SYNC_FORMATS = {ExportFormat.TURTLE, ExportFormat.JSON}  # 同步直下（兼容现有下载交互）


class ExportRequest(BaseModel):
    project_id: int
    format: ExportFormat
    scope: str = "full"  # full | schema_only | public_snapshot
    options: dict = Field(default_factory=dict)


class ExportResult(BaseModel):
    task_id: str | None = None
    storage_key: str | None = None
    filename: str | None = None
    content: bytes | None = None  # 仅同步格式


def run_export(request: ExportRequest) -> ExportResult:
    """TODO(M4): 同步格式直接生成；异步格式投 Celery 产物落 MinIO + 预签名 URL。"""
    raise NotImplementedError("M4 实现（docs/design/03 §14）")


__all__ = ["ExportFormat", "SYNC_FORMATS", "ExportRequest", "ExportResult", "run_export"]
