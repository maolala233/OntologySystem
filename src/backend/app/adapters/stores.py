# app/adapters/stores.py - 三类存储统一接口封装（docs/design/02 §1/§5，O1-B5 实装）
# 写入一律由 outbox 消费端（worker-graph / worker-rdf）调用，API 进程禁止直写。
# 项目级全量事件（graph_rebuilt/publication.created）由 graph_tasks/rdf_tasks 处理
# （需要查 Project.graph_data）；本模块只做实体/关系级增量与向量化/RDF 重建封装。

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
    """实体/关系级 outbox 事件 → Neo4j 增量 MERGE（O1-B5 实装，幂等）。

    - entity.upsert：payload.node = 画布节点 dict（必带 id）
    - relation.upsert：payload.edge = 画布边 dict（source/target 节点须已存在）
    - project.deleted：按 project_id 精确 DETACH DELETE
    MERGE 键 {id, project_id}（02 §5），重放不产生重复。返回成功处理的事件数；
    任一事件失败抛 RuntimeError，由调用方置 outbox failed 留待重放。
    """
    from app.infrastructure.neo4j_client import neo4j_client

    handled = 0
    for ev in events or []:
        et = ev.event_type
        payload = ev.payload or {}
        if et == "project.deleted":
            if not neo4j_client.delete_project_data(ev.aggregate_id):
                raise RuntimeError("Neo4j delete_project_data 返回失败")
        elif et == "entity.upsert":
            node = payload.get("node")
            if not isinstance(node, dict) or not node.get("id"):
                raise RuntimeError("entity.upsert payload 缺 node.id")
            pid = int(payload.get("project_id") or ev.aggregate_id)
            if not neo4j_client.upsert_node(pid, node, payload.get("domain")):
                raise RuntimeError("Neo4j upsert_node 返回失败")
        elif et == "relation.upsert":
            edge = payload.get("edge")
            if not isinstance(edge, dict) or not edge.get("source"):
                raise RuntimeError("relation.upsert payload 缺 edge.source")
            pid = int(payload.get("project_id") or ev.aggregate_id)
            if not neo4j_client.upsert_edge(pid, edge, payload.get("domain")):
                raise RuntimeError("Neo4j upsert_edge 返回失败")
        else:
            raise RuntimeError(f"sync_to_neo4j 不支持事件: {et}")
        handled += 1
    return handled


def sync_to_milvus(records: list[VectorRecord]) -> int:
    """向量记录 upsert（O1-B5 实装）：VectorStoreManager 插入 + content_hash 去重。

    幂等语义：Milvus 侧按 (text, metadata) 内容哈希查重跳过，重复重放安全；
    VectorRecord.pk（uri_hash）随 metadata 入库供溯源对账。Milvus 未启用时
    返回 0（降级不报错——向量化缺失由 QA 检索侧的 IDF 兜底）。
    向量化失败抛异常，调用方置 outbox failed 按 1/5/15min 退避重放。
    """
    from app.infrastructure.vector_client import VectorStoreManager

    if not records:
        return 0
    mgr = VectorStoreManager()
    if not getattr(mgr, "is_enabled", False):
        return 0
    texts = [r.text for r in records]
    metadatas = [{**r.metadata, "pk": r.pk} for r in records]
    mgr.insert_data(texts, metadatas)
    return len(records)


def rebuild_rdf_named_graphs(project_id: int) -> bool:
    """四类命名图整体替换（O1-B5 实装，worker-rdf 专用）。

    委托 oxigraph_sink.write_project_graphs（tbox/abox/prov/pub 先 clear 再 add），
    自持 DB 会话，供 rdf_tasks 与运维重放调用。
    """
    from app.infrastructure.database import SessionLocal
    from app.services.oxigraph_sink import write_project_graphs

    db = SessionLocal()
    try:
        res = write_project_graphs(db, project_id)
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "oxigraph write failed"))
        return True
    finally:
        db.close()


__all__ = [
    "GRAPH_TBOX", "GRAPH_ABOX", "GRAPH_PROV", "GRAPH_PUB",
    "StoreBackend", "VectorRecord", "OutboxEvent",
    "sync_to_neo4j", "sync_to_milvus", "rebuild_rdf_named_graphs",
]
