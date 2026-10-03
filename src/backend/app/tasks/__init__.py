# app/tasks/__init__.py - Celery 异步层（docs/design/01 §4.4）
# 四队列：parse（解析/OCR/切片）· extract（LLM 抽取）· graph（Neo4j/Milvus 同步 + outbox）
#         · rdf（Oxigraph 写入，worker-rdf concurrency=1 唯一写者）
