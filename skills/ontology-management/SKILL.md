---
name: ontology-management
description: 驾驶 OntologySystem 的构建-审核-发布-进化全流程（REST API）。当用户要上传文档、审核抽取结果、发布本体到资产中心、或发起一轮本体进化时使用。MCP 只读；一切写操作走本技能的 API 并带人工审批。
version: 1.0.0
tags: [ontology, build, publish, evolution]
---

# OntologySystem 构建与治理

## Overview
写路径全部走 REST（JWT 登录 + 模块授权），每一步带人审或机检门禁：
文档解析 → 抽取 → 审核（certified 位）→ 发布（五道门禁 + 配对评估）→ 进化（状态机）。

## Core lifecycle
```
上传解析（POST /projects/{id}/documents/upload）
  → 抽取候选 → 审核批准（POST /reviews/{id}/decide，approve 即置 certified）
  → 发布（POST /projects/{id}/publish：五道门禁 + CQ 配对评估，幂等）
  → 进化（POST /evolution/runs/start → rounds 评估 → 通过后 publish）
```

## Key rules（违反会被门禁拦截或污染数据）
- **审核即认证**：approve 置 `certified=true + steward_specified`；此后重跑抽取/自进化**永不覆盖**。
- **发布幂等**：内容未变化的重发布直接返回既有版本（不重跑评估）；改了内容才产生新版本。`force: true` 可强制重发。
- **进化纪律**：一项目同时仅一个 running；被拒的轮次可继续修订假设（checkpoint 不推进）；
  判断类中断（missing_data 等）需先积累 ≥2 次 reject。
- **公理认证**：`POST /reasoning/axioms/certify {key}` —— 存量公理盘点入口。

## API recipe
| 动作 | 调用 |
| --- | --- |
| 上传文档 | POST /projects/{id}/documents/upload（multipart `files`） |
| 审核批准 | POST /reviews/{id}/decide {"action":"approve"} |
| 门禁预检 | GET /projects/{id}/publish/gate-report（只读，五道检查逐项） |
| 发布 | POST /projects/{id}/publish {"note": "..."} |
| 进化开始 | POST /projects/{id}/evolution/runs/start {"hypothesis": "..."} |
| 进化评估 | POST /projects/{id}/evolution/runs/{run}/rounds {"hypothesis": "..."} |
| 失败挖掘 | POST /projects/{id}/evolution/analyze |

## Common errors
| 症状 | 原因 | 修法 |
| --- | --- | --- |
| DOMAIN_REQUIRED | 未设置知识域 | 项目设置里补知识域 |
| PENDING_CRITICAL_REVIEWS | 有 high 及以上待审项 | 先完成审核（/reviews） |
| EVIDENCE_CLOSURE_FAILED | 证据引用的切片已删除 | 补抽证据或重新解析文档 |
| GATE_REJECTED | 配对评估：新版本答得不如旧版本 | 旧版本仍生效；修订本体后重新走候选评估 |
| EVOLUTION_RUN_RUNNING | 已有进行中的进化 | resume 语义：完成它而不是并行开新运行 |
| EVOLUTION_ANTI_STAGNATION | 判断类中断但 reject 不足 2 次 | 设计下一个候选再评估，而不是放弃 |

## Hard rules
- 不绕过审核直接改库；MCP 对已发布项目只读。
- 发布被拒时旧版本继续生效——**任何进化都不会把系统改坏**（门禁由代码重算，不由模型宣称）。
