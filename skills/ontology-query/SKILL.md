---
name: ontology-query
description: 智能体用 OntologySystem 回答业务问题的标准姿势。当用户向智能体提问业务/数据问题、智能体需要取本体语义做依据、或需要判断"这个问题该不该答"时使用。
version: 1.0.0
tags: [ontology, qa, trust]
---

# OntologySystem 问答（运行时可信）

## Overview
问答 = Tier0 确定性口径（术语命中的公理/约束置顶）+ 向量/图检索 + LLM 生成（[n] 溯源）。
可信来自三层机制：**可拒绝**（coverage=no 不硬答）、**可溯源**（每条引用带原文+文档定位）、
**可解释**（本体口径的公理引用与文档事实区分标注）。

## Recommended sequence（MCP）
1. `resources/read ontology://session-manifest` —— 只注入摘要（版本/计数/指引）。
2. `resolve_semantics {mentions:[<=5], context}` —— 概念+适用公理（含 severity/认证标记）+证据；
   `ambiguous` 会返回候选列表——**用 context 指定，不要猜**。
3. `check_answerable {question}` —— 确定性覆盖分级：
   `no`（0 命中）→ 不要硬答，走澄清；`partial`（1）→ 答案须声明覆盖有限；`likely`（≥2）→ 正常作答。
4. `ask {question}` —— GraphRAG 聚合答案（带 [n] 引用）。

## 带断言的答案核查清单（回答业务问题前自查）
- 总体：回答针对的总体/范围是否与问题一致？
- 口径：数值/术语用的是本体口径还是资料原文？两者冲突时优先本体口径（已人工认证的公理）。
- 单位与粒度：金额/笔数/日期粒度是否明确？
- 证据表述：**观察 / 计算 / 推导（语义推理）/ 预测** 必须区分表述，不得混为"资料记载"。
- 溯源：关键断言是否有 [n] 标注；无标注的断言要么补引要么删除。
- **fail closed**：证据不足时明确说"上下文没有"，不要补常识凑答案。

## Coverage 语义（S20）
| 分级 | 含义 | 智能体动作 |
| --- | --- | --- |
| no | 术语零命中且检索无果 | 后端已拒答：转澄清/建议补建本体 |
| partial | 单术语命中 | 可答，但明示"仅覆盖 X，未涉及 Y" |
| likely | ≥2 术语命中 | 正常作答，仍须过溯源核查清单 |

## Common errors
| 症状 | 原因 | 修法 |
| --- | --- | --- |
| 答案与最新文档不符 | 回答依据的是旧版本 | check `session-manifest.ontology_version`；已发布项目以资产中心版本为准 |
| 两个概念都像 | ambiguous | 让用户在候选中指定（resolve_semantics 已给候选清单） |
| 答案里有 [n] 但点不开 | 引用源是"本体口径/语义推理"而非文档 | 正常：按口径与推理的表述规则呈现，不当作原文引用 |
| 409 Conversation in progress | 同会话上一问未结束 | 等上一轮 done 再发下一问 |

## Hard rules
- `coverage=no` / `unavailable` / `ambiguous` 时禁止编造——这是平台与智能体的共同合同。
- 本体口径（公理，尤其人工认证的）优先于模型常识。
