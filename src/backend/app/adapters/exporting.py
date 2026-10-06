# app/adapters/exporting.py - 多格式导出（docs/design/03 §14）
# 里程碑：M4-R1 同步 RDF 序列化已实现（rdflib，即 semantica 导出底座）；
# 大产物异步（Celery）落 MinIO ontology-exports/ 留待后续分期。

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


# 同步直下格式（兼容现有下载交互；其余格式异步产物待后续分期）
SYNC_FORMATS = {ExportFormat.TURTLE, ExportFormat.NTRIPLES, ExportFormat.RDFXML,
                ExportFormat.JSONLD, ExportFormat.TRIG, ExportFormat.OWL,
                ExportFormat.JSON}

# ExportFormat → rdflib 序列化格式 / 媒体类型 / 扩展名
_RDF_SERIALIZATION = {
    ExportFormat.TURTLE: ("turtle", "text/turtle; charset=utf-8", "ttl"),
    ExportFormat.NTRIPLES: ("nt", "application/n-triples; charset=utf-8", "nt"),
    ExportFormat.RDFXML: ("xml", "application/rdf+xml; charset=utf-8", "rdf"),
    ExportFormat.JSONLD: ("json-ld", "application/ld+json; charset=utf-8", "jsonld"),
    ExportFormat.TRIG: ("trig", "application/trig; charset=utf-8", "trig"),
    ExportFormat.OWL: ("xml", "application/rdf+xml; charset=utf-8", "owl"),
}

# 平台支持的 RDF 导入/导出格式 → rdflib 解析格式（导入侧格式嗅探用）
# 注意：sniff_rdf_format 返回值即 rdflib 解析格式，无需再映射。


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


def export_rdf(turtle_text: str, fmt: ExportFormat) -> tuple[bytes, str, str]:
    """Turtle 文本 → 指定 RDF 序列化（semantica 导出底座 = rdflib）。

    返回 (content, media_type, extension)。trig 需要 ConjunctiveGraph 承载多命名图。
    """
    if fmt not in _RDF_SERIALIZATION:
        raise ValueError(f"不支持的同步 RDF 导出格式: {fmt}")
    from rdflib import ConjunctiveGraph, Graph

    g: Graph = ConjunctiveGraph() if fmt == ExportFormat.TRIG else Graph()
    g.parse(data=turtle_text, format="turtle")
    serialization, media_type, ext = _RDF_SERIALIZATION[fmt]
    data = g.serialize(format=serialization)
    if isinstance(data, bytes):
        return data, media_type, ext
    return data.encode("utf-8"), media_type, ext


def sniff_rdf_format(filename: str, content: str) -> str | None:
    """导入侧格式嗅探：先按扩展名，再按内容特征。返回 rdflib 解析格式或 None。

    平台导出的 6 种 RDF 格式（exporting.ExportFormat）均可回读。
    """
    from os.path import splitext

    ext = splitext(filename or "")[1].lower().lstrip(".")
    # 注意：.json 是平台 JSON（entities/relationships）专用扩展，由调用方分流，不做 JSON-LD 猜测
    by_ext = {"ttl": "turtle", "nt": "nt", "ntriples": "nt", "n3": "n3",
              "rdf": "xml", "owl": "xml", "xml": "xml",
              "jsonld": "json-ld", "trig": "trig"}
    if ext in by_ext:
        return by_ext[ext]
    head = (content or "").lstrip()[:512]
    if head.startswith("<?xml") or head.startswith("<rdf:RDF"):
        return "xml"
    if head.startswith("{") or head.startswith("["):
        return "json-ld"
    return None  # turtle/nt 由调用方默认


def run_export(request: ExportRequest) -> ExportResult:
    """同步格式直接生成；异步格式（表格/图/报告）投 Celery 落 MinIO，后续分期。"""
    if request.format not in SYNC_FORMATS:
        raise NotImplementedError(f"格式 {request.format} 的异步导出待后续分期（docs/design/03 §14）")
    raise ValueError("同步导出由 API 层组合 turtle 生成 + export_rdf 完成，不走 run_export")


__all__ = ["ExportFormat", "SYNC_FORMATS", "ExportRequest",
           "ExportResult", "export_rdf", "sniff_rdf_format", "run_export"]
