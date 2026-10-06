# app/adapters/retrieval.py - GraphRAG 检索与问答（docs/design/07 §4）
# M5 实装：向量召回（Milvus，项目/知识域过滤表达式）+ 图扩展（行表 relations BFS）
# + proximity 融合 + 溯源引用（provenance_records evidence/char 定位）
# + LLM 接地答案（非流式 QAResult / 流式 token 生成器）。
# 现有 services/rag_engine.py 双路实现保留为旧链路；本模块为 03 §15 新 QA 契约的后端。

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, ProvenanceRecord, Relation, UploadedDocument
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.vector_client import VectorStoreManager

logger = logging.getLogger(__name__)

# 单响应引用上限（07 §3.3：防超大响应；问答引用最多 10 条与旧链路一致）
MAX_SOURCES = 10
# 图事实在融合结果中的名额上限：结构化三元组仅作关系类问题的补充，
# 原文切片（向量/关键词）才是内容问答的主要证据来源
MAX_GRAPH_FACTS = 3


class RetrievedSource(BaseModel):
    """问答引用（[n] 标注的数据载体，char 定位供前端高亮）。"""

    doc_file: str
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    ref_type: str = "vector_chunk"  # vector_chunk | graph_edge | graph_node
    score: float = 0.0
    source_document_id: int | None = None  # 前端跳文档预览用


class QAResult(BaseModel):
    answer: str
    sources: list[RetrievedSource] = Field(default_factory=list)
    reasoning_path: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    model: str | None = None
    latency_ms: int = 0


class RetrievalQuery(BaseModel):
    project_id: int
    question: str
    top_k: int = 12
    use_graph: bool = True
    max_expansion_hops: int = 2
    knowledge_domain: str | None = None  # 向量过滤表达式（权限隔离强制 project_id）


def _milvus_expr(query: RetrievalQuery) -> str:
    """权限过滤表达式：**强制** project_id == 项目（07 §4 权限过滤），可选知识域。"""
    expr = f"project_id == {int(query.project_id)}"
    if query.knowledge_domain:
        # Milvus 表达式字符串只能用双引号；滤掉引号防注入
        domain = str(query.knowledge_domain).replace('"', "").replace("\\", "")
        if domain:
            expr += f' and domain == "{domain}"'
    return expr


def _match_entities(db: Session, query: RetrievalQuery) -> list[Entity]:
    """问题与候选实体匹配：label/别名 子串命中（≥2 字符），按 label 长度降序。"""
    q = query.question.strip()
    if not q:
        return []
    ents = (
        db.query(Entity)
        .filter(
            Entity.project_id == query.project_id,
            Entity.is_class_node.is_(False),
            Entity.status != "rejected",
        )
        .all()
    )
    ql = q.lower()
    matched: list[Entity] = []
    for e in ents:
        names = [e.label] + list(e.aliases or [])
        for name in names:
            n = (name or "").strip().lower()
            if len(n) >= 2 and n in ql:
                matched.append(e)
                break
    matched.sort(key=lambda x: len(x.label), reverse=True)
    return matched[:8]


def _graph_expand(db: Session, query: RetrievalQuery, seeds: list[Entity]) -> list[dict]:
    """图扩展：种子实体沿 relations 行表 BFS 1~2 跳，返回事实三元组。

    行表是事实源（02 §4），Neo4j 为投影——问答扩展走行表与 graph_view 同源。
    """
    if not seeds or query.max_expansion_hops < 1:
        return []
    hops = min(int(query.max_expansion_hops), 2)
    facts: list[dict] = []
    frontier_ids = [e.id for e in seeds]
    seen_edges: set[tuple[int, str, int]] = set()
    for hop in range(1, hops + 1):
        if not frontier_ids:
            break
        rows = (
            db.query(Relation, Entity)
            .join(Entity, Relation.subject_id == Entity.id)
            .filter(
                Relation.project_id == query.project_id,
                Relation.subject_id.in_(frontier_ids),
                Relation.status != "rejected",
            )
            .all()
        )
        rows += (
            db.query(Relation, Entity)
            .join(Entity, Relation.object_id == Entity.id)
            .filter(
                Relation.project_id == query.project_id,
                Relation.object_id.in_(frontier_ids),
                Relation.status != "rejected",
            )
            .all()
        )
        next_ids: list[int] = []
        seen_ids: set[int] = set()
        for rel, other in rows:
            key = (rel.subject_id, rel.predicate, rel.object_id)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            subj = db.query(Entity).filter(Entity.id == rel.subject_id).first()
            obj = db.query(Entity).filter(Entity.id == rel.object_id).first()
            if not subj or not obj:
                continue
            facts.append({
                "subject": subj.label,
                "predicate": rel.predicate,
                "object": obj.label,
                "hop": hop,
                "score": 1.0 / hop,
                "entity_ids": [rel.subject_id, rel.object_id],
            })
            if other.id not in seen_ids and other.id not in frontier_ids:
                seen_ids.add(other.id)
                next_ids.append(other.id)
        frontier_ids = next_ids[:20]  # 邻域扩展宽度限制，防大图爆炸
    return facts[:40]


def _prov_by_entity(db: Session, project_id: int, entity_ids: list[int]) -> dict[int, ProvenanceRecord]:
    """实体 → 溯源记录（evidence 原句 + char 定位，07 §4 引用升级）。"""
    if not entity_ids:
        return {}
    rows = (
        db.query(ProvenanceRecord)
        .filter(
            ProvenanceRecord.project_id == project_id,
            ProvenanceRecord.target_type == "entity",
            ProvenanceRecord.target_id.in_(entity_ids[:200]),
        )
        .order_by(ProvenanceRecord.char_start.is_(None), ProvenanceRecord.id)
        .all()
    )
    return {r.target_id: r for r in rows}


def _vector_sources(query: RetrievalQuery) -> list[RetrievedSource]:
    """路径 A：Milvus 向量召回（强制 project_id 表达式）。"""
    try:
        vm = VectorStoreManager()
        hits = vm.search_with_expr(query.question, _milvus_expr(query), top_k=min(query.top_k, 20))
    except Exception as e:  # 向量库抖动不阻断（08 §3 观察项：Milvus 2.3.5 租约脆弱）
        logger.warning(f"[retrieval] 向量召回失败，仅图扩展：{e}")
        return []
    out: list[RetrievedSource] = []
    for h in hits:
        meta = h.get("metadata") or {}
        distance = float(h.get("distance") or 0.0)
        # Milvus COSINE 度量的 distance 字段即相似度（越大越近），夹紧到 [0,1]
        sim = max(0.0, min(1.0, distance))
        out.append(RetrievedSource(
            doc_file=h.get("source_file") or meta.get("source_file") or "未知来源",
            quote=(h.get("source_quote") or meta.get("source_quote") or h.get("text") or "")[:1500],
            ref_type="vector_chunk",
            score=round(sim, 4),
            source_document_id=meta.get("source_document_id"),
        ))
    return out


def _question_terms(question: str) -> list[str]:
    """问题词项化：ASCII 词（≥2 字符）+ CJK 连续段 2~4 元滑窗（短段保留整段）。

    只切二元组会把"投资范围"这类决定性短语打碎（长问题串里它只是窗口一员），
    3~4 元窗口让短语整体成词，IDF 加权后才有足够区分度。
    """
    import re
    terms: list[str] = []
    for w in re.findall(r"[A-Za-z0-9]{2,}", question):
        terms.append(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        run = run.strip()
        if 2 <= len(run) <= 6:
            terms.append(run)
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                terms.append(run[i:i + n])
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def _keyword_sources(query: RetrievalQuery, db: Session) -> list[RetrievedSource]:
    """路径 B 兜底：MySQL document_chunks 关键词打分（IDF 加权，向量空/弱时保底召回原文）。

    产品名等全文档高频词会被均权词频淹没目标段落，按 log(1 + N/df) 加权后
    “投资范围”这类稀有且决定性的词项才能把正确切片顶进名额。
    """
    import math
    from app.infrastructure.database import DocumentChunk, UploadedDocument
    terms = _question_terms(query.question)
    if not terms:
        return []
    try:
        rows = (
            db.query(DocumentChunk, UploadedDocument.filename)
            .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
            .filter(UploadedDocument.project_id == query.project_id,
                    UploadedDocument.deleted_at.is_(None))
            .limit(5000)
            .all()
        )
    except Exception as e:
        logger.warning(f"[retrieval] 关键词兜底查询失败：{e}")
        return []
    if not rows:
        return []
    # 词项 → 命中切片集合（df）
    hits_by_chunk: list[tuple[DocumentChunk, str, set[str]]] = []
    df: dict[str, int] = {}
    for chunk, fname in rows:
        text = chunk.text or ""
        hit = {t for t in terms if t in text}
        if not hit:
            continue
        hits_by_chunk.append((chunk, fname or "未知来源", hit))
        for t in hit:
            df[t] = df.get(t, 0) + 1
    if not hits_by_chunk:
        return []
    n_docs = len(rows)
    weight = {t: math.log(1 + n_docs / d) for t, d in df.items()}
    scored = [
        (sum(weight[t] for t in hit), chunk, fname)
        for chunk, fname, hit in hits_by_chunk
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0] or 1.0
    out: list[RetrievedSource] = []
    for w, chunk, fname in scored[:min(query.top_k, 12)]:
        out.append(RetrievedSource(
            doc_file=fname,
            quote=(chunk.text or "")[:1500],
            ref_type="keyword_chunk",
            score=round(min(0.99, 0.3 + 0.6 * w / top_score), 4),
            source_document_id=chunk.document_id,
        ))
    return out


def retrieve(query: RetrievalQuery, db: Session | None = None) -> list[RetrievedSource]:
    """双路召回 + proximity 融合（07 §4）。db 为 None 时仅向量路径。

    融合规则：文本证据（向量 + 关键词兜底）按分数排满剩余名额，图事实最多占
    MAX_SOURCES//2——此前图事实整体排前曾把向量块全部挤出 10 条引用，
    LLM 拿不到原文只能答"上下文没有"。
    """
    sources: list[RetrievedSource] = []
    if db is not None:
        seeds = _match_entities(db, query)
        facts = _graph_expand(db, query, seeds) if query.use_graph else []
        if facts:
            prov = _prov_by_entity(db, query.project_id,
                                   sorted({i for f in facts for i in f["entity_ids"]}))
            doc_names: dict[int, str] = {}
            for f in facts:
                # 事实引用 = 端点实体的溯源证据（有 evidence 用原句，无则用三元组文本）
                p = next((prov.get(i) for i in f["entity_ids"] if prov.get(i)), None)
                quote = (p.evidence_text if p and p.evidence_text else
                         f"{f['subject']} —{f['predicate']}→ {f['object']}")
                doc_id = p.source_document_id if p else None
                if doc_id and doc_id not in doc_names:
                    d = db.query(UploadedDocument).filter(UploadedDocument.id == doc_id).first()
                    doc_names[doc_id] = d.filename if d else "未知来源"
                sources.append(RetrievedSource(
                    doc_file=doc_names.get(doc_id, "未知来源" if doc_id else "本体图谱"),
                    quote=quote[:1500],
                    char_start=p.char_start if p else None,
                    char_end=p.char_end if p else None,
                    ref_type="graph_edge",
                    score=round(f["score"], 4),
                    source_document_id=doc_id,
                ))
    sources.extend(_vector_sources(query))
    # 关键词兜底始终参与融合（混合检索）：向量相似度对短段落/专有词弱的场景由
    # 词项命中补位；向量正常时两路结果按 (doc_file, quote) 去重互补
    if db is not None:
        sources.extend(_keyword_sources(query, db))

    text_hits = sorted((s for s in sources if s.ref_type != "graph_edge"),
                       key=lambda s: s.score, reverse=True)
    graph_facts = sorted((s for s in sources if s.ref_type == "graph_edge"),
                         key=lambda s: s.score, reverse=True)

    # 先去重再限量：图事实大量共用同一实体证据原文，先截后去会把 5 个名额
    # 全花在重复引用上；图事实去重后最多占半数名额，文本证据保底另一半
    seen: set[tuple[str, str]] = set()

    def _dedup(items: list[RetrievedSource]) -> list[RetrievedSource]:
        out: list[RetrievedSource] = []
        for s in items:
            key = (s.doc_file, s.quote[:50])
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    fused = _dedup(graph_facts)[:MAX_GRAPH_FACTS] + _dedup(text_hits)
    return fused[:MAX_SOURCES]


_QA_SYSTEM_PROMPT = """你是一位专业的本体知识问答助手，基于提供的上下文信息回答用户问题。

【回答要求】：
1. 只根据提供的上下文回答，不要编造信息。
2. 在回答中使用 [1][2][3] 等标号标注引用来源，标号放在相关语句的末尾。
3. 如果上下文中没有相关信息，请如实告知用户。

【引用格式说明】：[1] 表示引用第 1 条参考信息，以此类推。
"""

_ANSWER_TAIL = "\n\n请根据以上信息回答问题，并在适当位置标注引用标号："


def _build_user_prompt(question: str, sources: list[RetrievedSource]) -> str:
    context = "\n\n".join(
        f"[{i + 1}] 来源：{s.doc_file}（{s.ref_type}）\n    内容：{s.quote}"
        for i, s in enumerate(sources)
    )
    return f"【用户问题】\n{question}\n\n【参考信息列表】\n{context}{_ANSWER_TAIL}"


def _confidence(sources: list[RetrievedSource]) -> float:
    """启发式置信：top 引用相似度均值，上限 0.95（不伪造确定感）。"""
    if not sources:
        return 0.0
    top = sources[:5]
    return round(min(0.95, sum(s.score for s in top) / len(top) * 0.9 + 0.05), 3)


def query_with_reasoning(query: RetrievalQuery, db: Session | None = None,
                         llm: LLMClient | None = None) -> QAResult:
    """检索 + LLM 接地答案（AgentContext.query_with_reasoning 语义，非流式聚合）。"""
    t0 = time.time()
    sources = retrieve(query, db=db)
    reasoning: list[str] = [
        f"向量召回 + 图扩展融合得 {len(sources)} 条引用",
    ]
    answer = ""
    model_name = None
    if sources and llm is not None:
        try:
            resp = llm.call_llm_text(
                system_prompt=_QA_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(query.question, sources),
                stream=False,
                timeout=300.0,
            )
            answer = (resp.get("content", "") if isinstance(resp, dict) else str(resp)).strip()
            model_name = getattr(llm, "model", None)
            reasoning.append("LLM 依据引用生成接地答案（[n] 标注）")
        except Exception as e:
            logger.error(f"[retrieval] LLM 生成失败：{e}")
            reasoning.append(f"LLM 生成失败，回退引用聚合（{e}）")
    if not answer:
        answer = ("未检索到足够的相关信息，无法回答该问题。" if not sources else
                  "根据检索到的信息：\n" + "\n".join(f"[{i + 1}] {s.quote}" for i, s in enumerate(sources[:5])))
    return QAResult(
        answer=answer,
        sources=sources,
        reasoning_path=reasoning,
        confidence=_confidence(sources),
        model=model_name,
        latency_ms=int((time.time() - t0) * 1000),
    )


def iter_answer(query: RetrievalQuery, db: Session | None = None,
                llm: LLMClient | None = None) -> Iterator[tuple[str, Any]]:
    """流式问答（SSE 后端，07 §4）：yield ("token", str) 增量 ... ("done", QAResult)。

    引用先检索再生成（引用集合在 token 流期间不变，done 事件一次性下发）。
    """
    t0 = time.time()
    sources = retrieve(query, db=db)
    yield ("meta", {"source_count": len(sources)})
    if not sources or llm is None:
        result = query_with_reasoning(query, db=db, llm=llm)
        yield ("token", result.answer)
        yield ("done", result)
        return
    answer_parts: list[str] = []
    try:
        for delta in llm.call_llm_stream(
            system_prompt=_QA_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(query.question, sources),
            timeout=300.0,
        ):
            answer_parts.append(delta)
            yield ("token", delta)
    except Exception as e:  # 流中断：把已收内容聚合返回，不静默吞
        logger.error(f"[retrieval] 流式生成中断：{e}")
        yield ("error", str(e))
        if not answer_parts:
            return
    answer = "".join(answer_parts).strip() or "（模型未返回内容）"
    yield ("done", QAResult(
        answer=answer,
        sources=sources,
        reasoning_path=[f"向量召回 + 图扩展融合得 {len(sources)} 条引用", "LLM 流式生成（[n] 标注）"],
        confidence=_confidence(sources),
        model=getattr(llm, "model", None),
        latency_ms=int((time.time() - t0) * 1000),
    ))


__all__ = ["RetrievedSource", "QAResult", "RetrievalQuery", "retrieve", "query_with_reasoning",
           "iter_answer", "_milvus_expr"]
