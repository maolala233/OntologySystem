# OntologySystem × Semantica 平台设计方案（v1.0）

> 本方案是一套面向 **vibe coding** 的生产级平台设计文档：以现有 OntologySystem 代码库与已配备中间件为基线做**演进重构**，引入 Semantica v0.7.0 作为本体构建引擎，覆盖 8 项平台需求与图谱可视化重构。
>
> 撰写日期：2026-10-03。方案事实基础来自对 Semantica 官方文档（docs.getsemantica.ai）与其 GitHub 源码（semantica-agi/semantica）、本仓库前后端代码的完整调研。

---

## 1. 平台要解决的 8 项需求

| # | 需求 | 主责设计章节 |
|---|------|------------|
| 1 | 用户登录与权限控制（管理员可配置普通用户可用模块） | 02 §3、03 §3–4 |
| 2 | 模型自行配置（本地/远程，多模型多用途） | 03 §5、04 §2 |
| 3 | 文件上传与解析（多格式、扫描件 OCR、长文档切片） | 04 §2 |
| 4 | 大模型分层构建本体：先 schema 后 instance；实体消解、冲突分析、去重、溯源、人工审核、版本与时间轴、发布公共区只读 | 04 §3–§9、03 §8–§12 |
| 5 | 可视化查看与调整修改 + 多格式导出 | 06、05 §6、03 §13–14 |
| 6 | 持久化存储：图数据库 + 向量数据库 + RDF triple store | 02 |
| 7 | 工具层：MCP 调用服务 + 基于本体的问答 | 07 |
| 8 | 中文支持优化 + 前后端分离 | 04 §10、05 |

## 2. 文档清单与阅读顺序

| # | 文件 | 内容 | 依赖 |
|---|------|------|------|
| 0 | **README.md**（本文件） | 索引、术语表、投喂指引 | — |
| 1 | `01-总体架构与技术选型.md` | 现状盘点、需求差距矩阵、五层目标架构、适配层铁律、技术选型、目录规划、部署拓扑、非功能性指标 | — |
| 2 | `02-数据模型与存储设计.md` | 20 张表完整 DDL、5 存储职责矩阵、Outbox 单写者、blob 拆行迁移、Alembic | 01 |
| 3 | `03-后端API与服务设计.md` | 分层架构、权限守卫、统一响应/错误码、全部 REST 契约、旧接口收编 | 01、02 |
| 4 | `04-核心业务流程设计.md` | 解析管道、切片、两阶段构建、SchemaGate/promote、实体消解三层、冲突、溯源、审核队列、版本时间轴、发布、中文专项、14 缺陷修复 | 02、03 |
| 5 | `05-前端设计与页面规格.md` | 设计系统、路由与信息架构、状态管理、i18n、逐页面规格、BuilderPage 拆分 | 01、03 |
| 6 | `06-图谱可视化重构设计.md` | semantica-explorer 方案落地：Sigma.js 3 双模式、LOD、中文渲染、确定性布局、数据契约、三级性能策略 | 03（graph-view API）、05 |
| 7 | `07-工具层设计（MCP与本体问答）.md` | MCP 网关（HTTP 传输、令牌、权限矩阵）、GraphRAG 问答升级 | 03、04 |
| 8 | `08-实施路线与投喂指引.md` | M0–M5 里程碑、每期验收门槛、风险清单、分阶段投喂提示词模板 | 全部 |

**首次通读顺序**：01 → 02 → 03 → 04 → 05 → 06 → 07 → 08。
**按需求查阅**：直接用上表"主责设计章节"列定位。

## 3. 术语表（全文统一，投喂大模型时请一并携带）

| 术语 | 含义 |
|------|------|
| **TBox / 骨架（schema）** | 本体的类（owl:Class）、对象属性、数据属性层。平台第一阶段产出物 |
| **ABox / 实例（instance）** | 个体（NamedIndividual）与实例关系层。平台第二阶段产出物 |
| **两阶段交互** | 现有核心资产：抽取骨架 → 前端画布人工审核修订 → 再抽实例。重构中**保留只换引擎** |
| **适配层（adapters）** | `app/adapters/` 下的 13+1 个文件，是业务代码接触 Semantica 的唯一通道。**业务层禁止直接 `import semantica`** |
| **SchemaGate** | schema 校验闸门：ABox 抽取结果必须命中 TBox 已定义的类与属性，未命中的走丢弃或 promote |
| **promote（回流）** | ABox 中反复出现但 TBox 未定义的新类型，经人工审核后回写 TBox 的机制 |
| **模块码（module code）** | 14 个功能模块的授权粒度单位，见 §4 |
| **Outbox（单写者）** | 关系库先行落库，经 `outbox_events` 表由单一消费者同步到 Neo4j/Milvus/Oxigraph 的模式。RDF 写入者唯一（worker-rdf，concurrency=1），因嵌入式 Oxigraph 的 RocksDB 独占锁 |
| **LOD / labelBudget** | 图谱三级细节层次；每级只渲染预算内的标签数量，而非按阈值开关 |
| **双模式图谱** | ReactFlow 负责 TBox 画布编辑（需连线/拖拽），Sigma.js 3 负责 ABox 只读探索（大规模性能） |
| **命名图（named graph）** | Oxigraph RDF 存储的划分单位：TBox 图 / ABox 图 / 溯源图 / 发布快照图 |
| **M0–M5** | 实施里程碑，见 08 |

## 4. 全文统一的设计常量（跨册一致，勿改动后不同步）

### 4.1 模块码（14 个）

```
dashboard        工作台/首页
projects         项目管理
documents        文档管理（上传/解析/分块）
schema_build     骨架（schema）构建
instance_build   实例（instance）构建
resolution       实体消解与冲突分析
review           人工审核
version_control  版本与时间轴
publish          发布管理
asset_center     公共资产中心（浏览已发布本体）
graph_explore    图谱探索（Sigma 大图交互）
qa               本体问答
mcp              MCP 工具层
export           导出
```

- 平台角色：`admin`（管理员）/ `user`（普通用户）。项目级角色：`owner` / `editor` / `viewer`。
- 管理员通过"模块授权矩阵"（`user_module_grants` 表）配置每个普通用户可用的模块。
- 注册用户默认仅开通：`dashboard`、`projects`、`asset_center`（其余由管理员按需授予）。

### 4.2 数据库（5 → 20 张表）

```
users, modules, user_module_grants, project_members, model_configs,
projects, knowledge_domains, uploaded_documents, document_chunks,
entities, relations, provenance_records, review_items,
ontology_versions, graph_snapshots, publications,
outbox_events, audit_logs, graph_layouts, mcp_tokens(M5)
```

完整 DDL 见 02。迁移一律走 Alembic（本项目首次引入）。

### 4.3 关键阈值与参数

| 参数 | 值 | 出处 |
|------|----|------|
| 中文切片 chunk_size | 2000 字符，overlap 15% | 04 §2.3 |
| 扫描件判定 | 解析后每页平均可见字符 < 50 触发 OCR | 04 §2.2 |
| 实体消解自动合并阈值 | 相似度 ≥ 0.85 自动合并 | 04 §4 |
| 实体消解转人工区间 | 0.60 – 0.85 生成审核项 | 04 §4 |
| LOD labelBudget | overview=4 / structure=20 / inspection=60 | 06 §5 |
| 服务端预布局触发 | 节点 > 25000 | 06 §10 |
| 上传单文件上限 | 100 MB；解析文本与原件入 MinIO | 03 §7 |
| API 性能基线 | 非抽取类接口 P95 < 300 ms | 01 §9 |
| 图渲染基线 | 500 节点首屏 < 0.8 s 且 60 fps | 06 §12 |

### 4.4 异步任务队列（Celery + Redis，中间件已就绪）

```
queue=parse    文档解析/OCR/切片
queue=extract  schema / instance 抽取（LLM 密集）
queue=graph    Neo4j / Milvus 同步、outbox 消费、布局任务
queue=rdf      Oxigraph RDF 写入（worker-rdf，concurrency=1，唯一写者）
beat           定时：目录清理、失败任务重试、审核超时提醒
```

### 4.5 API 前缀

统一 `/api`，资源化路由；**旧 `/api/v1/*` 全部删除收编**（映射表见 03 §15）。SSE 一律走 `GET /api/.../events` 端点，鉴权用短期一次性 ticket（query 参数），不再传长期 JWT。

## 5. 如何投喂大模型进行 vibe coding

### 5.1 通用规则

1. **一次只投喂一个里程碑**（M0–M5，见 08），携带：`README.md` + `01` + 该期涉及的章节 + 对应表结构/API 契约。
2. 要求模型**先复述任务与验收门槛、列出将要新建/修改的文件清单**，确认后再写码。
3. 明确告知"本仓库是演进重构"：新代码放 `app/adapters/`、`app/tasks/`、`features/` 等新目录；对旧文件（如 `api/ontology.py`、`extractor.py`）只做**标注过的改造**，不做顺手重写。
4. 每期结束时投喂 08 对应的验收清单，要求模型自测并输出"验收报告 + 未完成项"。
5. 铁律复述（每期提示词都带上）：
   - 业务层禁止直接 `import semantica`，一律经 `app/adapters/`；
   - 不修改既有函数签名（调用方兼容优先）；
   - 数据库变更只经 Alembic，禁止手写 `ALTER TABLE`；
   - 前端禁止新增与 `D3ForceGraph`/`OntologyCanvas` 平行的第三套图实现。

### 5.2 提示词骨架（每期替换 `{{MILESTONE}}` 与章节清单）

```
你是本仓库（D:\python_code\OntologySystem）的实现工程师。请先通读以下设计文档：
docs/design/README.md、docs/design/01-总体架构与技术选型.md，
以及本期涉及的：{{CHAPTER_FILES}}。

本期任务：{{MILESTONE_TITLE}}（里程碑 {{MILESTONE}}）。
任务清单与验收门槛见 docs/design/08-实施路线与投喂指引.md §{{MILESTONE_SECTION}}。

约束：
1. 演进重构，遵守 docs/design/README.md §5.1 的五条铁律；
2. 涉及表结构时只写 Alembic migration（参考 02 §6 的分批方案）；
3. 涉及 API 时严格按 03 的路径/请求/响应契约实现，不要自创字段；
4. 完成后输出：改动文件清单、自测结果、与验收门槛的逐条对照。

请先复述你的理解与实施文件清单，等我确认后再开始写代码。
```

### 5.3 与仓库既有三份文档的关系

本方案是以下三份既有文档的**收敛落地版**，冲突处以本方案为准：

- `docs/semantica优化实施步骤.md`（2084 行）——其 P0–P4 分阶段、15 张表、适配层清单被本方案吸收并修订（表结构调整为 20 张，见 02）；
- `docs/抽取优化方案.md`（794 行）——其 14 项缺陷逐条进入 04 §11 的修复对照表；
- `docs/图谱展示重构方案.md`（1098 行）——其 Sigma.js 3 技术路线被 06 继承并补充数据契约与实施细节。

## 6. 本轮范围说明

本方案**只含设计，不含代码**。所有"改造点"均以 `现状 → 目标 → 涉及文件` 的格式标注，供后续 vibe coding 逐期实施。
