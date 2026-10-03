# app/adapters/__init__.py - Semantica 适配层（docs/design/README §5.1 铁律一）
#
# ★★★ 本目录是业务代码接触 Semantica 的唯一通道 ★★★
# 业务层（api/services/tasks）禁止直接 import semantica，一律经本适配层；
# 适配层对外只暴露平台自有 Pydantic 类型，不泄漏 Semantica 数据类。
# 升级 Semantica 版本时只允许改 adapters/_compat.py。
#
# 模块 × 里程碑（均为 M0 骨架，函数体后续里程碑实现）：
#   _compat       M0  semantica 版本探测与中文补丁清单
#   provider      M2  LLM/Embedding/VL provider 解析（model_configs 表）
#   parsing       M3  Docling 主解析 + 现有 parser 兜底 + 扫描件 OCR 判定
#   chunking      M3  中文分句状态机 + recursive 切片（2000/15%）
#   extraction    M3  分块并行 LLM 抽取（NER/RE 全走 LLM 方法）
#   schema_gate   M3  SchemaGate 校验 + promote 回流策略
#   resolution    M3  实体消解三层流水线（归一/拼音/向量）
#   conflicts     M3  5 类冲突检测 + 7 种解决策略
#   provenance    M3  W3C PROV-O 溯源 + evidence 原句定位
#   versioning    M3  版本快照/diff/回滚/双时间轴
#   exporting     M4  18 种导出格式（Semantica exporter + rdflib）
#   retrieval     M5  AgentContext GraphRAG 检索封装
#   stores        M4  Neo4j/Milvus/Oxigraph 统一存储接口封装
#   errors        M0  Semantica 异常 → 平台 APIError 翻译

from app.adapters import errors as errors  # noqa: F401

__all__ = ["errors"]
