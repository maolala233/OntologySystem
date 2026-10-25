---
name: ontology-setup
description: 连接 OntologySystem 并自检就绪状态。当用户要把智能体（Claude/Cursor 等）接入本平台、申请 MCP 令牌、或确认某项目"能不能问"时使用。
version: 1.0.0
tags: [ontology, mcp, setup]
---

# OntologySystem 接入与就绪检查

## Overview
OntologySystem 是本体构建平台：文档 → 本体 → 对外语义服务。智能体通过 MCP
（Streamable HTTP，POST /mcp）读取语义（概念+公理+证据），通过 REST API 做构建/审核。

## Usage
- 拿到 MCP 令牌后先跑本技能的就绪检查，再进 ontology-query。
- 平台管理操作（建项目/传文档/审核）走 ontology-management。

## Quick Start
1. 管理页「MCP 工具」为你的用户+项目签发令牌（明文 `sk-mcp-` 只出现一次）。
2. 会话初始化只读一个资源：`resources/read ontology://session-manifest` —— 版本/计数/认证统计/推荐序列全在里面。
3. 推荐调用序列（manifest 里也有）：
   `session-manifest → resolve_semantics（按需取语义）→ check_answerable（执行前确认覆盖）→ 执行动作前按公理/约束校验`。

## Common errors
| 症状 | 原因 | 修法 |
| --- | --- | --- |
| 401 令牌格式非法 | 令牌丢了 `sk-mcp-` 前缀或用了 REST 的 Bearer JWT | MCP 用 `Authorization: Bearer sk-mcp-…`；REST 用登录 JWT |
| 403 只读令牌 | viewer 令牌调了写工具或项目已发布 | 写操作走 REST（带审批），或换 editor 令牌 |
| resolve 返回 unavailable | 本体未覆盖该概念 | **不要编造**：改走澄清，或用 ontology-management 补文档重抽 |
| 工具列表里没有 resolve_semantics | 服务端版本旧 | 确认后端已升级到 S19+ |

## Hard rules
- 语义服务返回 `unavailable/ambiguous` 时，宁可澄清，不可编造。
- resolved 语义是"引导"不是"最终答案"：执行业务动作前必须按返回的公理/约束校验。
