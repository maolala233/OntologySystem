# 07 · 工具层设计（MCP 与本体问答）

> 需求 7：**支持工具层模块（支持 MCP 调用服务以及基于本体的问答）**。两条线：
> ① MCP——让外部 Agent/Claude Desktop/IDE 等通过 MCP 协议读写平台本体；
> ② 问答——把现有"双路 RAG"升级为 GraphRAG（向量 + 图扩展 + 溯源引用 + 流式）。

---

## 1. 现状与 Semantica 能力底座

**现状**：平台无任何 MCP 代码（全库 grep 零命中）；问答为 `POST /api/projects/{id}/qa`（`DualPathRAGEngine`：Neo4j 结构化检索 + Milvus 向量召回 + 3 步 LLM Text2Cypher + `[1][2]` 引用），非流式、无图扩展、无权限化向量过滤。

**Semantica 提供**：
- `semantica-mcp`：**stdio 传输** MCP 服务器，19 个工具（15 基础 + 0.7.0 新增 4 个语义检索）+ 3 个只读 Resources；
- `AgentContext`：`store / retrieve / query_with_reasoning`——向量检索 + 图 BFS 扩展 + proximity 融合 + LLM 接地答案（response/reasoning_path/sources/confidence）。

**缺口（平台自建）**：MCP 的 HTTP 远程传输与多用户权限（Semantica 仅 stdio + 单 Key）；问答的 REST/SSE 化、项目隔离与审计。

## 2. MCP 集成形态选型

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 直接暴露 semantica-mcp 子进程 | 每用户/项目拉起 stdio 子进程 | ❌ 无法多用户权限、无法与平台行表/发布态一致 |
| B. 平台内置 MCP 网关（HTTP Streamable） | FastAPI 实现 `/mcp` 端点，工具处理器直连平台服务层与存储 | ✅ **采用**：权限/审计/项目隔离/发布只读全部复用平台机制 |
| C. semantica-mcp 作为离线批处理工具 | CLI 用法，M5 只交付 B | 备选保留（数据迁移/脚本场景） |

网关工具集**不照抄** Semantica 的 19 工具：平台事实源是 MySQL 行表 + outbox 投影，工具实现走平台 API 层语义（写操作落行表并自动 outbox 同步），仅检索类工具在合适处复用 `adapters/retrieval.py`。

## 3. 平台 MCP 网关设计

### 3.1 传输与生命周期

- 端点：`POST /mcp`（JSON-RPC）+ `GET /mcp`（SSE 流，HTTP Streamable 传输）；协议版本 `2025-03-26` 及兼容。
- 客户端配置示例（写入用户文档页）：

```json
{ "mcpServers": { "ontology-platform": {
    "type": "http",
    "url": "https://<host>/mcp",
    "headers": { "Authorization": "Bearer sk-mcp-xxxxxxxx" } } } }
```

### 3.2 令牌与权限矩阵

- 令牌：`sk-mcp-` 前缀随机 40 字符，SHA-256 哈希落库（`audit_logs` 同款存储路径；令牌表可并入 `system_configs` 或独立 `mcp_tokens` 表——**采用独立表** `mcp_tokens(id, user_id, name, token_hash, project_id, can_write, expires_at, last_used_at)`，M5 建）。
- 每令牌绑定：**一个用户 + 一个项目 + 读写范围**。工具可用性矩阵：

| 工具类别 | viewer 令牌 | editor 令牌 | 说明 |
|----------|:--:|:--:|------|
| 检索/只读（search/query/summary/export） | ✅ | ✅ | 导出走项目 scope 限制 |
| 增删改（add_entity / add_relationship / update / delete） | ❌ | ✅ | 落行表 + outbox；触发低置信审核路径与手动写操作一致 |
| 问答（ask） | ✅ | ✅ | 复用 §5 问答链路 |
| Resources（graph/summary、schema/info） | ✅ | ✅ | 只读 |

- 会话上下文：MCP 无跨请求状态——每次 initialize 绑定令牌 → user/project 解析；权限守卫复用 `core/deps.py` 的判定函数（非 FastAPI Depends，提取纯函数共用）。

### 3.3 工具清单（平台语义，MVP 10 个）

| 工具 | 入参 | 实现 |
|------|------|------|
| `search_entities` | query, class?, limit | 行表 label/alias 检索（03 §13 search 同源） |
| `get_entity` | uri | 实体 + 属性 + 溯源摘要 |
| `get_neighbors` | uri, hops≤2, limit | Neo4j 只读 Cypher |
| `query_graph` | cypher(只读), params | **只读白名单校验**（禁 CALL/CREATE/DELETE/SET/MERGE），超时 5s |
| `get_ontology_schema` | — | TBox 类/属性清单（快照） |
| `ask` | question, top_k? | §5 问答（非流式聚合返回） |
| `add_entity` / `add_relationship` | … | editor 令牌；写行表 + outbox + audit(mcp.write) |
| `export_graph` | format⊂{turtle,jsonld} | 复用 adapters/exporting（scope=full） |
| `list_versions` | — | 版本列表摘要 |

Resources：`ontology://graph/summary`（规模统计）、`ontology://schema/info`（TBox TTL）。分页/限制：单响应 ≤ 2MB，超限截断 + `next_cursor` 提示。

### 3.4 审计与限流

- 全部工具调用写 `audit_logs(action='mcp.read'|'mcp.write', detail={tool, args_digest})`。
- 限流：读 60 次/分/令牌，写 20 次/分/令牌（Redis 计数）。
- `mcp` 模块码：用户须有 `[M:mcp]` 才能申请/持有令牌（管理员签发）。

## 4. 问答架构升级（现状 → 目标）

```
现状:  question → Milvus 向量 TopK ┐
                → Text2Cypher 3步  ├→ prompt 拼接 → LLM → 答案+[n]引用
                                   ┘  (无图扩展、无置信度、非流式、引用=chunk前200字)

目标:  question ──► adapters/retrieval.AgentContext.retrieve
          ├─ 路径A 向量: Milvus(hybrid, 项目/知识域过滤表达式) TopK=12
          ├─ 路径B 图扩展: 命中实体 → BFS 1~2 跳邻域(带溯源属性) ← graph_expansion
          ├─ 融合: proximity score (向量距离 + 图距离)
          ├─ Text2Cypher 保留: 明显结构化意图时路由(白名单只读)
          └─ 答案: query_with_reasoning 语义
                → SSE 流式 token
                → sources[]: {file, quote, char_range, doc_url, type}
                → reasoning_path + confidence 展示在答案卡
```

- **权限过滤**：向量检索表达式强制 `project_id == X`（已发布项目走发布快照 collection 或表达式过滤 `published=1`）；图查询限定 project 子图；`[Pub+]` 端点对公共用户放行快照数据。
- **引用升级**：`[n]` 引用来自 `provenance_records`（evidence 原句 + char 偏移），点击打开文档预览高亮（05 §6.6 详情抽屉同源组件）。
- **流式**：SSE（03 §15）——`event: token`（增量）、`event: sources`（最终引用）、`event: done`（confidence/耗时）。前端 `QaDrawer`（05 §6.5 附属）打字机渲染 + 引用列表。
- **评测**：`test/fixtures/qa_eval.yaml` 30 条中文问答金样本（含多跳/比较/否认类），CI 断言：引用命中率 ≥ 80%，答案与金样本要点重合 ≥ 70%（LLM-judge 辅助）。
- 保留并升级 `rag_engine.py` 的双路实现为 `adapters/retrieval.py` 的后端之一；`/api/v1/rag/dual-path-query`（无鉴权）删除（03 §18）。

## 5. 与其他模块的联动

| 联动点 | 说明 |
|--------|------|
| 审核队列 | MCP 写操作产生低置信/缺证据项走同一审核路径，不旁路 |
| 发布 | MCP 对已发布项目一律只读（PUBLISHED_READONLY 同源拦截） |
| 溯源 | MCP 写入的实体 `method='mcp'`，携带令牌 user_id 写 audit |
| 图谱探索 | 问答 sources 可跳转探索视图对应节点 |

## 6. 验收清单

- [ ] Claude Desktop / 任意 MCP HTTP 客户端经 `sk-mcp-` 令牌完成 initialize → tools/list → search_entities → ask 全链路
- [ ] viewer 令牌调用写工具被拒（权限矩阵），editor 可写且产物进入 outbox 同步与审核队列
- [ ] `query_graph` 只读白名单：注入 `CREATE`/`CALL` 用例被拒
- [ ] 问答 SSE 流式输出；引用点击可定位原文（char 高亮）；30 条金样本引用命中率 ≥ 80%
- [ ] MCP 读/写全量审计可查；限流生效
- [ ] 无鉴权旧问答端点下线，`/mcp` 令牌泄露可撤销（令牌管理 API）
