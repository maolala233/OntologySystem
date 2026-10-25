# app/core/repair_budget.py - 自修复/工具循环预算统一合同（S25-3，docs/15）
# EvoOntology/NVIDIA 同款"预算+降级"纪律：所有修复循环的轮次上限只从这里取，
# 防止各调用点各自为政（无限重试/半途而废两个极端）。
from __future__ import annotations

# 工具循环（tool_loop）：单次 LLM 编排内的最大工具轮次；末轮强制 final（已有实现）
TOOL_LOOP_ROUNDS = 2
# 抽取修复（extraction_repair）：闸门违例修复 agent 的最大工具轮次
EXTRACTION_REPAIR_ROUNDS = 2
# QA 自修复（query_with_repair）：失败检测→换策略重试轮数（R1/R2 已固化在流水线内）
QA_REPAIR_STRATEGIES = 2
# 未来扩展位：重构类修复 ≤7 次、软检查 5 次后放行（docs/15 S25-3，接 соответствующих 调用点时取用）
MAX_RECONSTRUCTION_ATTEMPTS = 7
SOFT_CHECK_PASSES_AFTER = 5
FULL_PIPELINE_RESTARTS = 2
