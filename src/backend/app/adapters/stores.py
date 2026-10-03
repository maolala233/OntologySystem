# app/adapters/stores.py - 三类存储统一接口封装（docs/design/02 §1/§5）
# 里程碑：M4 实现。封装 Semantica GraphStore/VectorStore/TripletStore 统一接口，
# 写入一律由 outbox 消费端（worker-graph / worker-rdf）调用，API 进程禁止直写。

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# Oxigraph 命名图划分（02 §5；RocksDB 独占锁 → worker-rdf concurrency=1 唯一写者）
GRAPH_TBOX = "urn:onto:{{project}}:tbox"
GRAPH_ABOX = "urn:onto:{{project}}:abox"
GRAPH_PROV = "urn:onto:{{project}}:prov"
GRAPH_PUB = "urn:onto:{{project}}:pub:{{version_no}}"  # 不可变发布快照


class StoreBackend(str, Enum):
    NEO4J = "neo4j"
    MILVUS = "milvus"
    OXIGRAPH = "oxigraph"


class VectorRecord(BaseModel):
    pk: str                # uri_hash（确定性 ID，幂等 upsert 键）
    text: str
    embedding: list[float] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)  # project_id/knowledge_domain/source_file/...


class OutboxEvent(BaseModel):
    """outbox_events 表投影（消费端幂等键 = aggregate_type+aggregate_id+event_type）。"""

    aggregate_type: str
    aggregate_id: int
    event_type: str        # entity.upsert / entity.merge / relation.upsert / chunk.vectorize / publication.created / ttl.rebuild
    payload: dict = Field(default_factory=dict)
    trace_id: str | None = None


def sync_to_neo4j(events: list[OutboxEvent]) -> int:
    """TODO(M4): MERGE 幂等写入（Resource {uri} 必带 project_id；删除按 uri 精确 DETACH DELETE）。"""
    raise NotImplementedError("M4 实现（docs/design/02 §5）")


def sync_to_milvus(records: list[VectorRecord]) -> int:
    """TODO(M4): upsert（pk 幂等）+ 向量化失败重试退避 1/5/15min。"""
    raise NotImplementedError("M4 实现")


def rebuild_rdf_named_graphs(project_id: int) -> bool:
    """TODO(M4): worker-rdf 专用 —— 四类命名图整体替换（tbox/abox/prov/pub）。"""
    raise NotImplementedError("M4 实现")


__all__ = [
    "GRAPH_TBOX", "GRAPH_ABOX", "GRAPH_PROV", "GRAPH_PUB",
    "StoreBackend", "VectorRecord", "OutboxEvent",
    "sync_to_neo4j", "sync_to_milvus", "rebuild_rdf_named_graphs",
]
