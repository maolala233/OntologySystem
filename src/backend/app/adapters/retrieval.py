# app/adapters/retrieval.py - GraphRAG 检索与问答（docs/design/07 §4）
# 里程碑：M5 实现。封装 semantica AgentContext：向量检索 + 图 BFS 扩展 + proximity 融合；
# 现有 services/rag_engine.py 双路实现迁入本模块作为后端之一；Text2Cypher 保留（只读白名单）。

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievedSource(BaseModel):
    """问答引用（[n] 标注的数据载体，char 定位供前端高亮）。"""

    doc_file: str
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    ref_type: str = "vector_chunk"  # vector_chunk | graph_edge | graph_node
    score: float = 0.0


class QAResult(BaseModel):
    answer: str
    sources: list[RetrievedSource] = Field(default_factory=list)
    reasoning_path: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class RetrievalQuery(BaseModel):
    project_id: int
    question: str
    top_k: int = 12
    use_graph: bool = True
    max_expansion_hops: int = 2
    knowledge_domain: str | None = None  # 向量过滤表达式（权限隔离强制 project_id）


def retrieve(query: RetrievalQuery) -> list[RetrievedSource]:
    """TODO(M5): 双路召回 —— Milvus（hybrid + 项目/知识域过滤表达式）+
    命中实体 BFS 邻域扩展，proximity score 融合排序。"""
    raise NotImplementedError("M5 实现（docs/design/07 §4）")


def query_with_reasoning(query: RetrievalQuery) -> QAResult:
    """TODO(M5): retrieve 子图喂 LLM 生成接地答案（AgentContext.query_with_reasoning 语义），
    流式 token 由 qa 路由层包装为 SSE（03 §15）。"""
    raise NotImplementedError("M5 实现")


__all__ = ["RetrievedSource", "QAResult", "RetrievalQuery", "retrieve", "query_with_reasoning"]
