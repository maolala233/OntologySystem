# 03 · 后端 API 与服务设计

> 全部端点挂 `/api` 前缀，资源化拆分为 16 个 router（现状 `api/ontology.py` 4221 行按域拆分）。旧 `/api/v1/*` 全部删除收编（§15 映射表）。每个端点标注：方法 路径 — 权限（模块码 / 项目角色）。
>
> 权限标注约定：`[M:xxx]` = 需模块码授权；`[PR:owner]` = 项目 owner；`[PR:editor]` = owner 或 editor；`[PR:view]` = 任意项目成员（owner/editor/viewer）；`[Pub+]` = 项目成员或该项目已发布（公共可读）。

---

## 1. 分层与依赖注入（`core/deps.py`）

```python
get_db()                        # SQLAlchemy session，请求级
get_current_user                # JWT 解析 → User；401 统一处理
require_role("admin")           # 平台角色守卫 → 403
require_module(code)            # 模块码守卫：admin 直通；否则查 user_module_grants
require_project_role(project_id, min_role)  # 项目成员守卫；已发布项目对 viewer 放行只读
get_rate_limiter                # LLM 类端点限流（每用户每分钟 N 次）
```

- 守卫实现为 FastAPI 依赖工厂：`Depends(require_module("review"))`；项目内端点同时叠加 `[M:xxx]` 与 `[PR:*]`，两者取交集。
- 首次登录响应与 `GET /api/auth/me` 返回 `modules: string[]`，前端据此渲染菜单（05 §3）。
- 审计：`audit(action, resource_type, resource_id, detail)` 在服务层显式调用（不搞中间件魔法），异步写入不阻塞响应。

## 2. 通用约定

### 2.1 响应包络

```jsonc
// 成功：直接返回资源对象或列表（HTTP 200/201），不包 code/data 壳
// 列表：统一游标分页
{ "items": [...], "next_cursor": "eyJpZCI6MTAwfQ", "has_more": true, "total": 128 }
// total 为可选字段：只在低成本时可算（COUNT），大表省略
```

游标 = base64(`{"id": <last_id>, "sort": "<sort_key>"}`)，禁止 offset 深翻页。

### 2.2 幂等与并发

- 写操作支持 `Idempotency-Key` 头（Redis 保存 24h，重复请求返回首次结果）——上传与抽取提交必须带。
- 画布保存用 `If-Match: <version_no>` 乐观锁；冲突返回 409 + 服务端最新版。

### 2.3 错误码（`core/exceptions.py` 统一）

```jsonc
{ "error": { "code": "REVIEW_ITEM_ALREADY_DECIDED", "message": "该审核项已被处理", "detail": {...}, "trace_id": "..." } }
```

| HTTP | code（节选） | 场景 |
|------|-------------|------|
| 400 | VALIDATION_ERROR / UNSUPPORTED_FILE_TYPE / CHUNK_PARAM_INVALID | 参数与文件 |
| 401 | INVALID_CREDENTIALS / TOKEN_EXPIRED | 认证 |
| 403 | MODULE_NOT_GRANTED / PROJECT_FORBIDDEN / PUBLISHED_READONLY / ADMIN_REQUIRED | 授权与只读拦截 |
| 404 | PROJECT_NOT_FOUND / VERSION_NOT_FOUND / ... | 资源不存在 |
| 409 | SCHEMA_GATE_VIOLATION / VERSION_CONFLICT / PROJECT_STATE_INVALID | 状态冲突 |
| 409 | DUPLICATE_UPLOAD | sha256 秒传命中（返回既有文档） |
| 422 | LLM_OUTPUT_INVALID / PROVIDER_UNAVAILABLE / EMBEDDING_DIM_MISMATCH | 引擎与模型 |
| 429 | RATE_LIMITED | 限流 |
| 500 | INTERNAL_ERROR | 兜底（含 trace_id） |

`PUBLISHED_READONLY`：对已发布项目的一切写端点在服务层统一拦截（发布快照不可变；需修改→另起版本，见 04 §9）。

## 3. 认证与用户（需求 1）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/auth/register` | 公开 | 注册；role=user；默认模块按 `modules.is_default_on` |
| `POST /api/auth/login` | 公开 | OAuth2 表单（兼容现有前端）；返回 access(2h)/refresh(7d) |
| `POST /api/auth/refresh` | refresh token | 换发 access |
| `GET  /api/auth/me` | 登录 | `{user, modules[], roles}` —— 前端菜单数据源 |
| `PUT  /api/auth/me` | 登录 | 改 display_name/locale/password |
| `GET  /api/auth/sse-ticket?task_id=` | 登录 | 签发一次性 SSE ticket（60s TTL，替代 `?token=`） |
| `GET  /api/admin/users` | admin | 用户列表（分页/搜索） |
| `POST /api/admin/users` | admin | 创建用户 |
| `PATCH /api/admin/users/{id}` | admin | 改 role/启用禁用/重置密码 |
| `GET  /api/admin/audit-logs` | admin | 审计查询（action/user/时间过滤） |

## 4. 模块授权矩阵（需求 1）

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET  /api/admin/modules` | admin | 14 个模块码字典 + 每模块开通人数 |
| `GET  /api/admin/modules/grants?user_id=` | admin | 单用户授权矩阵 |
| `PUT  /api/admin/modules/grants` | admin | 批量保存 `{user_id, grants: [{module_code, allowed}]}`；审计 `user.grant` |

## 5. 模型配置（需求 2）

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET  /api/model-configs/providers` | 登录 | provider 元数据（表单动态渲染）：字段/是否要 key/默认 base_url/说明 |
| `GET  /api/model-configs?scope=&purpose=&project_id=` | 登录（项目级需 [PR:view]） | 列表；api_key 只回 `sk-***last4` |
| `POST /api/model-configs` | admin（global）/ [PR:owner]（project） | 创建；key 落库 AES 加密；审计 `model.create` |
| `PATCH /api/model-configs/{id}` | 同上 | 更新；审计 `model.update` |
| `DELETE /api/model-configs/{id}` | 同上 | 删除（被引用时 409） |
| `PUT  /api/model-configs/{id}/default` | 同上 | 设为该 purpose 默认（同 scope 互斥） |
| `POST /api/model-configs/test` | admin / [PR:owner] | 连通性测试：`{purpose, provider, base_url, api_key?, model_name, dims?}` → `{ok, latency_ms, message, sample_output?}`；embedding 测试额外校验向量维度 |

**迁移**：`system_configs.llm_config/vl_config` 读取方（`_build_extractor`、QA、`VectorStoreManager._load_dynamic_config`、`vl_parser`）全部改为 `adapters/provider.py` 查 `model_configs`；旧 key 一次性导入脚本（M2 执行）。现有 5 个连通性测试端点（`api/system.py:49-353`）并入 `POST /api/model-configs/test`，前端 `utils/connectivity.ts` 保留复用。

## 6. 项目与知识域

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET/POST /api/projects` | [M:projects] | 列表（我参与的：owner_id 或 project_members 命中）/创建 |
| `GET /api/projects/{id}` | [PR:view] 或 [Pub+] | 详情：含 status、current_version、build_progress |
| `PATCH /api/projects/{id}` | [PR:owner] | 改名/描述/知识域 |
| `DELETE /api/projects/{id}` | [PR:owner] | 软删（deleted_at），级联停掉进行中任务 |
| `GET/POST /api/projects/{id}/members` | [PR:owner] | 成员管理（grant project_members，role=editor/viewer） |
| `GET /api/domains` | 登录 | 知识域字典（+code） |
| `POST/PATCH/DELETE /api/domains[/{id}]` | admin | 知识域维护（删除需指定迁移目标，沿用现有迁移交互） |

## 7. 文档：上传 / 解析 / 分块（需求 3）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/documents/upload` | [M:documents] + [PR:editor] | multipart 多文件；服务端算 sha256：命中且同项目→409 `DUPLICATE_UPLOAD`（响应带既有 doc）；存 MinIO；建 `uploaded_documents(parse_status=uploaded)`；自动触发解析任务（可 `?auto_parse=false` 关闭）；响应含 task_id |
| `POST /api/projects/{id}/documents/{doc_id}/parse` | 同上 | 手动重解析；body 可指定 `backend`（auto/docling_ocr/vl_model/native）与 `chunk` 参数（缺省用系统默认 2000/15%） |
| `GET  /api/projects/{id}/documents` | [PR:view] | 列表（parse_status/语言/页数/分块数） |
| `GET  /api/projects/{id}/documents/{doc_id}/chunks` | [PR:view] | 分块列表（游标分页；供溯源定位与人工查看） |
| `GET  /api/projects/{id}/documents/{doc_id}/download` | [PR:view] | 原件预签名 URL（302） |
| `DELETE /api/projects/{id}/documents/{doc_id}` | [PR:editor] | 软删；联动清理 chunks 与向量 |
| `GET  /api/projects/{id}/documents/parse-events?task_id=&ticket=` | [M:documents] | **SSE**：parse 队列进度（Redis 进度键） |

解析任务链（Celery parse 队列）：`上传落桶 → parse_document（后端选择见 04 §2）→ 切片 → 写 document_chunks → outbox(chunk.vectorize)`；进度以 `parse:{task_id}` 键写 Redis（`{stage, percent, message}`），SSE 端点订阅转发。

## 8. 两阶段抽取（需求 4 前半）

> 交互模型不变：**抽骨架 → 画布人工修订 → 抽实例**。变化：任务进 Celery extract 队列；产物落行表；支持 promote；每阶段自动落 `ontology_versions`。

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/extraction/schema` | [M:schema_build] + [PR:editor] | body: `{document_ids[], chunk_size?, model_config_id?, parallelism?}`；返回 task_id；完成后 `project.status=draft→building→ready`，schema 产物在 `entities(is_class_node=1)`+`relations(is_class_edge=1)`，并生成版本 `kind=schema` |
| `POST /api/projects/{id}/extraction/schema/revise` | 同上 | 画布修订提交：`{classes[], relations[], deleted_uris[], If-Match}`；服务端 diff 后更新行表并重发 outbox |
| `POST /api/projects/{id}/extraction/instances` | [M:instance_build] + [PR:editor] | body: `{document_ids[], strict_gate=true, promote_policy="review"|"discard"|"auto", model_config_id?}`；返回 task_id |
| `GET  /api/projects/{id}/extraction/tasks/{task_id}` | [PR:view] | 任务状态+统计（chunks/discard_count/promote_count/merge_count） |
| `GET  /api/projects/{id}/extraction/tasks/{task_id}/events?ticket=` | [M:*] | **SSE** 进度（事件：progress/chunk_done/warning/completed/failed/cancelled） |
| `POST /api/projects/{id}/extraction/tasks/{task_id}/cancel` | [PR:editor] | 取消（Celery revoke + 状态落库） |
| `GET  /api/projects/{id}/extraction/history` | [PR:view] | 历次抽取记录（版本号、统计、耗时、模型配置） |

SchemaGate 违例（strict_gate=true）不再静默丢弃：计入 `discard_count` 并按 `promote_policy` 处理 —— `review`=生成 `review_items(item_type='new_class')`；`discard`=仅计数；`auto`=连续 ≥3 个 chunk 出现同类新类型时自动生成候选类（仍需审核）。

## 9. 实体消解与冲突（需求 4）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/resolution/run` | [M:resolution] + [PR:editor] | body: `{scope: "all"|"class:<X>", thresholds?: {auto:0.85, manual:0.60}, blocking?: "pinyin"|"none"}`；异步；产出 `review_items(entity_merge)` + 直接合并项统计 |
| `GET  /api/projects/{id}/resolution/clusters` | [PR:view] | 消解聚类结果（含合并/待审） |
| `POST /api/projects/{id}/resolution/merge` | 同 run | 手动合并：`{canonical_id, duplicate_ids[], property_strategy?}`（即时执行 + outbox entity.merge） |
| `POST /api/projects/{id}/resolution/split` | 同 run | 拆分误合并：`{canonical_id, split_ids[]}`（保留合并历史可逆） |
| `POST /api/projects/{id}/conflicts/detect` | [M:resolution] + [PR:editor] | 显式触发 5 类冲突检测（value/type/relationship/temporal/logical，实现范围见 04 §5） |
| `GET  /api/projects/{id}/conflicts` | [PR:view] | 冲突列表（= review_items(item_type='conflict_*')） |

## 10. 审核队列（需求 4）

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET  /api/projects/{id}/reviews?status=&type=&priority=` | [M:review] + [PR:view] | 队列列表 |
| `GET  /api/reviews?status=&type=` | [M:review] | **跨项目队列**（审核工作台首页数据源；自动过滤为用户有 [PR:view] 的项目） |
| `GET  /api/reviews/{id}` | [M:review] + [PR:view] | 详情：payload 完整上下文（候选项、证据原文、来源文档定位、冲突双方、建议动作） |
| `POST /api/reviews/{id}/claim` | [M:review] + [PR:editor] | 认领 pending→claimed（防重复裁决） |
| `POST /api/reviews/{id}/decide` | [M:review] + [PR:editor] | body: `{action: "approve"|"reject"|"edit", edited_payload?, note?}`；claimed/pending→终态；落地对应业务（合并执行/类回写/属性修正）+ 审计 `review.decide` |
| `POST /api/projects/{id}/reviews/batch-decide` | 同上 | 批量裁决（同类型同动作，上限 50） |

## 11. 版本与时间轴（需求 4）

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET  /api/projects/{id}/versions` | [PR:view] | 版本列表（时间轴主数据：version_no/label/kind/stats/author/时间） |
| `POST /api/projects/{id}/versions` | [M:version_control] + [PR:editor] | 手动打版本：`{label?, description?, kind?}`（自动版本在抽取完成/发布/回滚前产生） |
| `GET  /api/projects/{id}/versions/{a}/diff/{b}` | [PR:view] | diff：`{classes:{added,removed,modified}, relations:{...}, instances:{...}}`（复用 TemporalVersionManager.compare_versions 语义） |
| `GET  /api/projects/{id}/versions/{vid}/snapshot` | [PR:view] | 下载快照（TTL/JSON，预签名 URL） |
| `POST /api/projects/{id}/versions/{vid}/restore` | [M:version_control] + [PR:owner] | 回滚：先自动落 `pre_rollback` 快照与新版本（kind=rollback），再恢复；产 outbox 全量重建三类存储 |
| `GET  /api/projects/{id}/timeline?entity_uri=&time_axis=` | [PR:view] | 实体时间线：`time_axis=valid|transaction|both`（双时间轴，复用 TemporalGraphQuery） |
| `GET  /api/projects/{id}/entities/{eid}/history` | [PR:view] | 单实体变更史（get_node_history 语义，从行表+版本快照拼装） |

## 12. 发布与公共资产（需求 4）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/publish` | [M:publish] + [PR:owner] | 前置校验：知识域必填、无 pending 关键审核项（critical/high）、当前版本健康检查通过；→ 落版本（kind=publication）+ graph_snapshots + publications；outbox `publication.created`（RDF pub 命名图） |
| `POST /api/projects/{id}/unpublish` | 同上 | status=unpublished；公共区即刻不可见；pub 命名图保留 |
| `GET  /api/assets` | [M:asset_center] 登录 | 公共区：已发布项目列表（按知识域分组；仅读 publications status=published） |
| `GET  /api/assets/{project_id}` | [M:asset_center] | 资产详情 = **快照只读投影**（graph-view 接口带 `readonly=1` 旗标走快照） |
| `GET  /api/assets/{project_id}/graph/nodes|edges` | [M:asset_center] | 同 §13，但数据源是 publication 快照 |
| `GET  /api/assets/{project_id}/export/{format}` | [M:export] + [M:asset_center] | 从快照导出（与 §14 同格式集） |

**只读强制**：已发布项目的写端点（extraction/revise/review/merge/delete...）在 deps 层校验 `project.status=='published'` → 403 `PUBLISHED_READONLY`，提示"另起新版本或先取消发布"。

## 13. 图视图（图谱探索数据契约，配 06）

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET /api/projects/{id}/graph/meta` | [PR:view] | `{node_count, edge_count, class_distribution, sources[], layout_available, readonly}` |
| `GET /api/projects/{id}/graph/nodes?cursor=&limit=500&class=&status=&source_doc=&include=pending` | [PR:view] | 节点游标分页；返回 06 §4.1 的 7 字段契约 + 服务端已算 `degree` |
| `GET /api/projects/{id}/graph/edges?cursor=&limit=1000&...` | [PR:view] | 边游标分页；**双向边判定在服务端完成**（同 (min,max,predicate) 分组标记 bidirectional 与 pair_index） |
| `GET /api/projects/{id}/graph/neighbors/{node_id}?hops=1&limit=200` | [PR:view] | 邻域展开（探索视图右键"展开实例"） |
| `GET /api/projects/{id}/graph/path?from=&to=&max_hops=4` | [PR:view] | 最短路径（graphology-shortest-path 语义，服务端 Neo4j 执行） |
| `GET /api/projects/{id}/graph/communities` | [PR:view] | Louvain 社区（Celery graph 队列预计算，结果缓存） |
| `POST /api/projects/{id}/graph/layout` | [PR:editor] | 请求服务端预布局：`{node_count>25000 才受理}` → 任务 → 写 `graph_layouts` |
| `GET /api/projects/{id}/graph/search?q=&class=&limit=20` | [PR:view] | 画布内搜索（label/alias 前缀+包含，MySQL 索引） |

## 14. 导出（需求 5）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/exports` | [M:export] + [PR:editor] | body: `{format, scope: "full"|"schema_only"|"public_snapshot", options?}` → 任务；18 格式见下表 |
| `GET  /api/projects/{id}/exports` | [PR:view] | 导出历史（状态/产物链接） |
| `GET  /api/projects/{id}/exports/{task_id}/download` | [PR:view] | 预签名 URL |

**18 种格式**（经 `adapters/exporting.py`，底层 Semantica exporter + rdflib 补充）：

```
RDF(6):    turtle · ntriples · rdfxml · jsonld · trig · owl
图(5):     graphml · gexf · dot · neo4j-cypher · arango-aql
表格(4):   csv · tsv · parquet · xlsx
其他(3):   json(graph_data 兼容现有格式) · yaml · html-report(含溯源附录)
```

大产物异步落 MinIO `ontology-exports/`；`turtle/json` 保留同步直下（兼容现有前端下载交互）。

## 15. 问答（需求 7，详见 07）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /api/projects/{id}/qa` | [M:qa] + [Pub+] | body: `{question, top_k?, use_graph=true, stream=true, model_config_id?}`；**SSE 流式**（token 事件 + 最终 `sources` 事件带溯源引用）；权限过滤：向量检索按项目/知识域表达式，图查询限定 project_id |
| `GET  /api/projects/{id}/qa/history` | [PR:view] | 问答历史（问题/答案/引用/模型/耗时） |
| `GET  /api/projects/{id}/qa/sources/{ref_id}` | [Pub+] | 引用溯源详情：chunk 原文 + char 定位 + 文档预签名链接 |

## 16. MCP 网关（需求 7，详见 07）

| 端点 | 权限 | 说明 |
|------|------|------|
| `POST /mcp`（及 GET/SSE 生命周期） | `Authorization: Bearer sk-mcp-*` | HTTP Streamable 传输的 MCP 端点；令牌在 `/api/admin/mcp-tokens` 签发，绑定用户 + 项目 + 读写范围 |
| `GET/POST/DELETE /api/admin/mcp-tokens` | admin | 令牌管理（只回明文一次） |

## 17. 系统与连通性

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET  /api/system/health` | 公开 | 后端 + 中间件探活（mysql/neo4j/milvus/redis/minio/oxigraph 五灯） |
| `GET  /api/system/middleware` | admin | 中间件配置视图（只读，敏感字段掩码） |

模型连通性已并入 §5；`api/system.py` 其余端点（配置 CRUD）删除。

## 18. 旧接口收编映射表（M3 执行）

| 旧端点 | 处置 |
|--------|------|
| `POST /api/projects/{id}/upload` | → §7 upload（删除） |
| `POST /api/projects/{id}/parse-files` | → §7 parse |
| `POST /api/projects/{id}/extract-schema[-from-documents]` | → §8 extraction/schema |
| `POST /api/projects/{id}/extract-instances[-from-documents]` | → §8 extraction/instances |
| `GET  /api/projects/{id}/download-ttl` / `download-json` | → §14 exports（turtle/json 同步直下） |
| `GET  /api/projects/{id}/task/{task_id}/progress-stream` / cancel / tasks | → §8 extraction events/cancel |
| `POST /api/projects/{id}/qa` | → §15（流式化） |
| `POST /api/projects/{id}/inject-*`（RAGFlow） | → 保留为 `POST /api/projects/{id}/integrations/ragflow-inject`（[PR:owner]），逻辑进 `services/` |
| `/api/v1/rag/dual-path-query`（无鉴权） | **删除**（QA 端点已覆盖） |
| `/api/v1/files/*`（无鉴权） | **删除**（§7 覆盖） |
| `/api/v1/ontology/*`（update-ontology/sync-ttl/dashboard/stats） | update-ontology→schema/revise；sync-ttl→outbox 自动化；stats→`GET /api/dashboard/stats`（[M:dashboard]） |

## 19. 契约与测试要求

- 全部端点 Pydantic v2 请求/响应模型（`schemas/` 按 router 拆分），OpenAPI 自动生成；前端以 `openapi-typescript` 生成类型（05 §4）。
- 契约测试：pytest + httpx AsyncClient 覆盖每个端点的权限矩阵（admin/授权用户/未授权用户/已发布只读）——权限是本方案最高风险点，测试必须先于实现合并。
- SSE 事件格式统一：`event: progress|completed|failed|cancelled` + `data: {task_id, stage, percent, message, stats?}`。
