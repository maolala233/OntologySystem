# 本体构建系统 (Ontology System) 标准操作流程 (SOP)

## 目录

1. [系统概述](#1-系统概述)
2. [系统架构](#2-系统架构)
3. [核心业务流程](#3-核心业务流程)
4. [两阶段本体提取流程详解](#4-两阶段本体提取流程详解)
5. [API 接口文档](#5-api-接口文档)
6. [LLM 提示词工程](#6-llm-提示词工程)
7. [数据结构定义](#7-数据结构定义)
8. [前端操作流程](#8-前端操作流程)
9. [数据库设计](#9-数据库设计)
10. [部署与配置](#10-部署与配置)

---

## 1. 系统概述

### 1.1 系统目标

本系统是一个基于大语言模型 (LLM) 的**自动化本体构建平台**，支持从非结构化文档（PDF、Word、TXT 等）中自动提取 OWL 本体，并提供可视化编辑、审核、发布功能。

### 1.2 核心功能

| 功能模块 | 描述 |
|---------|------|
| **骨架提取 (Schema Extraction)** | 从文档中提取 OWL Class 和 ObjectProperty，形成本体骨架 |
| **实例提取 (Instance Extraction)** | 在 Schema 约束下，从文档中提取 NamedIndividual 实例 |
| **可视化编辑** | 基于 D3.js 的力导向图，支持节点/边的拖拽、编辑、删除 |
| **TTL 序列化** | 将图数据序列化为标准 OWL TTL 格式，支持下载 |
| **Neo4j 同步** | 将本体数据同步到图数据库，支持查询和推理 |
| **发布与共享** | 将本体发布到资产中心，供其他用户查看和使用 |

### 1.3 技术栈

| 层级 | 技术 |
|-----|------|
| **前端** | React + TypeScript + Vite + Ant Design + D3.js |
| **后端** | Python + FastAPI + SQLAlchemy |
| **数据库** | SQLite (元数据) + Neo4j (图数据) + Milvus (向量库) |
| **LLM** | OpenAI 兼容 API (支持 vLLM、OpenRouter 等) |

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         前端 (Frontend)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Ontology    │  │ D3 Force    │  │ Ant Design UI           │  │
│  │ BuilderPage │  │ Graph       │  │ (Forms, Modals, etc.)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP/REST API
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         后端 (Backend)                           │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ API Layer (FastAPI Routes)                              │    │
│  │ - /api/projects/my          - 获取项目列表               │    │
│  │ - /api/projects/{id}/extract-schema  - 骨架提取         │    │
│  │ - /api/projects/{id}/extract-instances - 实例提取       │    │
│  │ - /api/projects/{id}/update-ontology - 保存草稿         │    │
│  │ - /api/projects/{id}/publish  - 发布资产                │    │
│  │ - /api/projects/{id}/download-ttl - 下载 TTL            │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Service Layer (Business Logic)                          │    │
│  │ - OntologyExtractor: LLM 调用与结果解析                  │    │
│  │ - FileParser: 文件解析 (PDF/DOCX/TXT)                    │    │
│  │ - TTLGenerator: TTL 序列化                               │    │
│  │ - Merger: 图数据合并                                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Infrastructure Layer                                    │    │
│  │ - LLMClient: OpenAI 兼容 API 调用                        │    │
│  │ - Neo4jClient: 图数据库操作                              │    │
│  │ - VectorClient: 向量库操作 (Milvus)                      │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ SQLite   │   │ Neo4j    │   │ Milvus   │
        │ (元数据)  │   │ (图数据)  │   │ (向量)   │
        └──────────┘   └──────────┘   └──────────┘
```

### 2.2 项目目录结构

```
OntologySystem/
├── src/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── api/              # API 路由层
│   │   │   │   ├── ontology.py   # 本体相关 API
│   │   │   │   ├── auth.py       # 认证 API
│   │   │   │   └── system.py     # 系统配置 API
│   │   │   ├── services/         # 服务层
│   │   │   │   ├── extractor.py  # 本体提取器
│   │   │   │   ├── parser.py     # 文件解析器
│   │   │   │   ├── merger.py     # 图数据合并
│   │   │   │   └── rag_engine.py # RAG 引擎
│   │   │   ├── infrastructure/   # 基础设施层
│   │   │   │   ├── llm_client.py # LLM 客户端
│   │   │   │   ├── neo4j_client.py
│   │   │   │   └── vector_client.py
│   │   │   ├── schemas/          # Pydantic 数据模型
│   │   │   │   ├── extraction.py # 提取相关 Schema
│   │   │   │   └── ontology.py   # 本体相关 Schema
│   │   │   └── core/             # 核心配置
│   │   │       ├── config.py     # 系统配置
│   │   │       └── logging.py    # 日志配置
│   │   └── main.py               # FastAPI 入口
│   └── frontend/
│       ├── src/
│       │   ├── pages/
│       │   │   └── OntologyBuilderPage.tsx  # 本体构建页面
│       │   ├── components/
│       │   │   └── OntologyGraph/
│       │   │       ├── D3ForceGraph.tsx     # 力导向图组件
│       │   │       └── OntologyCanvas.tsx   # 画布组件
│       │   ├── api/
│       │   │   └── projects.ts   # 项目 API 客户端
│       │   └── types/
│       │       └── ontology.ts   # TypeScript 类型定义
│       └── package.json
├── volumes/                      # Docker 数据卷
├── docker-compose.yml            # Docker 编排
└── requirements.txt              # Python 依赖
```

---

## 3. 核心业务流程

### 3.1 用户操作流程

```
Step 1: 创建项目
   │
   ▼
┌─────────────────────────────────────┐
│ 输入项目名称和描述                    │
│ → POST /api/projects                │
│ ← 返回项目 ID                        │
└─────────────────────────────────────┘
   │
   ▼
Step 2: 上传文档，提取骨架 (Schema Extraction)
   │
   ▼
┌─────────────────────────────────────┐
│ 选择文件 (PDF/DOCX/TXT)              │
│ 可选：填写用户意图/场景描述           │
│ → POST /api/projects/{id}/extract-schema │
│   - file: 上传的文件                 │
│   - user_intent: 用户意图 (可选)     │
│   - chunk_size: 15000               │
│   - chunk_overlap: 500              │
│ ← 返回 schema_graph + graph_data    │
└─────────────────────────────────────┘
   │
   ▼
Step 3: 审核骨架 (Schema Review)
   │
   ▼
┌─────────────────────────────────────┐
│ 在画布中查看提取的类和关系            │
│ 可执行操作：                         │
│ - 拖拽调整节点位置                   │
│ - 编辑节点名称/属性                  │
│ - 删除不需要的类/关系                │
│ - 手动添加新的类/关系                │
└─────────────────────────────────────┘
   │
   ▼
Step 4: 提取实例 (Instance Extraction)
   │
   ▼
┌─────────────────────────────────────┐
│ 点击「提取实例」按钮                 │
│ → POST /api/projects/{id}/extract-instances │
│   - text_content: 原文本内容         │
│   - schema_graph: 用户审核后的 Schema │
│ ← 返回 instances + graph_data       │
└─────────────────────────────────────┘
   │
   ▼
Step 5: 审核实例 (Instance Review)
   │
   ▼
┌─────────────────────────────────────┐
│ 在画布中查看提取的实例               │
│ 可执行操作：                         │
│ - 编辑实例属性                       │
│ - 删除不正确的实例                  │
│ - 手动添加新实例                     │
│ - 展开/收起类的实例                  │
└─────────────────────────────────────┘
   │
   ▼
Step 6: 保存草稿 / 发布
   │
   ├──→ 保存草稿：POST /api/projects/{id}/update-ontology
   │       → 同步到 Neo4j
   │       → 生成 TTL
   │
   └──→ 发布资产：POST /api/projects/{id}/publish
           → 设置 is_published = true
           → 同步到 Neo4j
           → 生成 TTL
   │
   ▼
Step 7: 下载 TTL (可选)
   │
   ▼
┌─────────────────────────────────────┐
│ 点击「下载 TTL」按钮                 │
│ → GET /api/projects/{id}/download-ttl │
│ ← 返回 TTL 文件内容                   │
└─────────────────────────────────────┘
```

### 3.2 两阶段提取流程图

```
                    ┌─────────────────┐
                    │   用户上传文档   │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  阶段 1: Schema Extraction    │
              │  (骨架提取 - 仅 Class + OP)   │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  LLM Prompt (System)         │
              │  - 禁止提取实例               │
              │  - 只提取 Class 和 ObjectProperty│
              │  - DataProperty 在类中定义     │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  LLM 返回 JSON                │
              │  {                            │
              │    "classes": [...],          │
              │    "object_properties": [...] │
              │  }                            │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  后端处理                     │
              │  - 生成确定性 ID (MD5)        │
              │  - 术语规范化                 │
              │  - 转换为 GraphData           │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  前端展示骨架 (Step 2)        │
              │  用户审核、编辑、调整          │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  阶段 2: Instance Extraction   │
              │  (带 Schema 约束的实例提取)    │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  LLM Prompt (System)         │
              │  - 只能实例化 Schema 中的类    │
              │  - 连线必须符合 domain/range │
              │  - 实例 ID 使用确定性算法      │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  LLM 返回 JSON                │
              │  {                            │
              │    "instances": [...]         │
              │  }                            │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  后端防御性校验               │
              │  - 检查 type 是否在 Schema 中  │
              │  - 检查 object_props 是否符合  │
              │    domain/range 约束          │
              │  - 丢弃不合规的连线            │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  合并实例到 GraphData         │
              │  - 添加 rdf:type 虚线边       │
              │  - 添加 ObjectProperty 实线边  │
              └──────────────┬───────────────┘
                             │
              ┌──────────────▼───────────────┐
              │  前端展示完整图 (Step 5)      │
              │  用户审核、编辑、调整          │
              └──────────────────────────────┘
```

---

## 4. 两阶段本体提取流程详解

### 4.1 阶段 1: 骨架提取 (Schema Extraction)

#### 4.1.1 接口定义

**请求:**
```http
POST /api/projects/{project_id}/extract-schema
Authorization: Bearer {token}
Content-Type: multipart/form-data

file: <上传的文件>
user_intent: string (可选)
chunk_size: number (默认 15000)
chunk_overlap: number (默认 500)
request_interval: number (默认 2)
```

**响应:**
```json
{
  "schema_graph": {
    "classes": [
      {
        "id": "C_a1b2c3d4",
        "label": "产品",
        "sub_class_of": null,
        "data_properties": ["名称", "价格", "规格"]
      }
    ],
    "object_properties": [
      {
        "id": "OP_12345678",
        "label": "属于",
        "domain": "C_a1b2c3d4",
        "range": "C_56789abc"
      }
    ]
  },
  "graph_data": {
    "nodes": [
      {
        "id": "C_a1b2c3d4",
        "type": "custom",
        "position": {"x": 100, "y": 200},
        "data": {
          "label": "产品",
          "type": "owl:Class",
          "properties": {}
        }
      }
    ],
    "edges": [
      {
        "id": "e_C_a1b2c3d4_C_56789abc_OP_12345678",
        "source": "C_a1b2c3d4",
        "target": "C_56789abc",
        "label": "属于",
        "type": "custom",
        "data": {"label": "属于"}
      }
    ]
  },
  "text_content": "解析后的文档文本内容...",
  "message": "骨架提取完成：5 个类，3 个关系。请在画布中审核、修改后，点击「提取实例」进入第二阶段。"
}
```

#### 4.1.2 后端处理流程

```python
# src/backend/app/api/ontology.py

# 1. 保存上传的文件到临时目录
os.makedirs("temp_uploads", exist_ok=True)
temp_path = os.path.join("temp_uploads", file.filename)
with open(temp_path, "wb") as buf:
    buf.write(await file.read())

# 2. 解析文件内容 (PDF/DOCX/TXT → 纯文本)
from app.services.parser import FileParser
parser = FileParser()
text_content = parser.parse_file(temp_path)

# 3. 获取 LLM 配置
extractor = _build_extractor(db)  # 从数据库读取 API Key, Base URL, Model

# 4. 调用 extract_schema_only() 方法
schema = extractor.extract_schema_only(
    text=text_content,
    user_intent=user_intent,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    request_interval=request_interval,
)

# 5. 转换为前端渲染格式
graph_data = OntologyExtractor.schema_to_graph_data(schema)

# 6. 存储到数据库
db_project.graph_data = {
    "schema": schema,
    **graph_data,
}
db.commit()

# 7. 返回结果
return {
    "schema_graph": schema,
    "graph_data": graph_data,
    "text_content": text_content,
    "message": f"骨架提取完成：{len(schema['classes'])} 个类，{len(schema['object_properties'])} 个关系。"
}
```

#### 4.1.3 LLM 调用详情

**System Prompt:**
```text
你是一位精通 OWL2 DL 标准的本体架构师，正在执行「骨架提取」任务。

【严格约束 - 违反将导致任务失败】：
1. 【禁止提取实例】: 绝对不允许提取任何具体实例 (NamedIndividual)。只提取抽象的「类」和「属性」。
   - 错误示例：提取「张三」「产品 A」「订单 001」→ 这些是实例，禁止！
   - 正确示例：提取「人员」「产品」「订单」→ 这些是类，允许！
2. 【ID 不由你决定】: 你输出的 id 字段用于辅助识别，最终 ID 将由系统重新计算，请直接用英文语义名（如 "Product", "hasName"）。
3. 【Label 必须中文】: 所有 label 字段必须是简洁中文（非英文）。
4. 【Data Property】: 在 classes 的 data_properties 字段中列出该类应有的属性名称列表（字符串数组）。
5. 【鼓励抽取隐含关系】: 如果文本中存在明确描述或强烈暗示的「系统 A 依赖于系统 B / A 调用 B / A 部署在 B 上 / A 与 B 对接」等关系，
   即使没有出现"关系名称"这个词，也请将其提取为类与类之间的 ObjectProperty，
   关系的 label 可用简洁中文动词短语（如「依赖于」「调用」「部署于」「对接」等）。

【用户意图约束】(如果用户填写了):
用户关注领域为「{user_intent}」。请严格聚焦该领域，提取与之直接相关的类和关系，忽略无关领域的概念。
```

**User Prompt Template:**
```text
【当前文本片段】:
"{chunk}"

【输出 JSON 格式（严格遵守，不输出任何注释）】:
{
  "classes": [
    {
      "id": "Product",
      "label": "产品",
      "sub_class_of": null,
      "data_properties": ["名称", "价格", "规格"]
    }
  ],
  "object_properties": [
    {
      "id": "belongsTo",
      "label": "属于",
      "domain": "Product",
      "range": "Category"
    }
  ]
}

【约束提醒】: 不得包含 instances 字段。只输出 classes 和 object_properties。
```

**LLM 返回示例:**
```json
{
  "classes": [
    {
      "id": "Product",
      "label": "产品",
      "sub_class_of": "Category",
      "data_properties": ["名称", "价格", "规格", "描述"]
    },
    {
      "id": "Category",
      "label": "类别",
      "sub_class_of": null,
      "data_properties": ["名称", "描述"]
    },
    {
      "id": "Brand",
      "label": "品牌",
      "sub_class_of": null,
      "data_properties": ["名称", "成立时间", "官网"]
    }
  ],
  "object_properties": [
    {
      "id": "belongsTo",
      "label": "属于",
      "domain": "Product",
      "range": "Category"
    },
    {
      "id": "manufacturedBy",
      "label": "生产商",
      "domain": "Product",
      "range": "Brand"
    }
  ]
}
```

---

## 5. API 接口文档

### 5.1 项目管理 API

| 接口 | 方法 | 描述 |
|-----|------|------|
| `/api/projects/my` | GET | 获取我的项目列表 |
| `/api/projects` | POST | 创建新项目 |
| `/api/projects/{id}` | PUT | 更新项目（保存草稿） |
| `/api/projects/{id}` | DELETE | 删除项目 |
| `/api/projects/{id}/publish` | POST | 发布项目 |
| `/api/projects/{id}/unpublish` | POST | 取消发布项目 |

### 5.2 本体提取 API

| 接口 | 方法 | 描述 |
|-----|------|------|
| `/api/projects/{id}/extract-schema` | POST | 骨架提取（阶段 1） |
| `/api/projects/{id}/extract-instances` | POST | 实例提取（阶段 2） |
| `/api/projects/{id}/upload-ttl` | POST | 上传 TTL 文件 |
| `/api/projects/{id}/update-ontology` | POST | 更新本体数据 |
| `/api/projects/{id}/download-ttl` | GET | 下载 TTL 文件 |

### 5.3 系统配置 API

| 接口 | 方法 | 描述 |
|-----|------|------|
| `/api/system/config/llm_config` | GET | 获取 LLM 配置 |
| `/api/system/config/llm_config` | PUT | 更新 LLM 配置 |
| `/api/system/test-connectivity/llm` | POST | 测试 LLM 连通性 |

---

## 6. LLM 提示词工程

### 6.1 骨架提取提示词

#### System Prompt

```text
你是一位精通 OWL2 DL 标准的本体架构师，正在执行「骨架提取」任务。

【严格约束 - 违反将导致任务失败】：
1. 【禁止提取实例】: 绝对不允许提取任何具体实例 (NamedIndividual)。只提取抽象的「类」和「属性」。
2. 【ID 不由你决定】: 你输出的 id 字段用于辅助识别，最终 ID 将由系统重新计算。
3. 【Label 必须中文】: 所有 label 字段必须是简洁中文（非英文）。
4. 【Data Property】: 在 classes 的 data_properties 字段中列出该类应有的属性名称列表。
5. 【鼓励抽取隐含关系】: 提取类与类之间的 ObjectProperty。

【用户意图约束】:
用户关注领域为「{user_intent}」。请严格聚焦该领域。
```

#### User Prompt Template

```text
【当前文本片段】:
"{chunk}"

【输出 JSON 格式】:
{
  "classes": [...],
  "object_properties": [...]
}
```

### 6.2 实例提取提示词

#### System Prompt

```text
你是一位精通 OWL2 DL 的本体工程师，正在执行「实例提取」任务。

【已审核的类 Schema（你只能实例化这些类）】:
{class_list_str}

【已审核的关系 Schema（连线只能使用这些关系）】:
{op_list_str}

【严格约束】:
1. 【区分属性与关系】: 文本值放入 data_props，指向实体放入 object_props。
2. 【仅实例化已定义的类】: type 必须是 Schema 中的类 ID。
3. 【连线必须符合 domain→range】。
4. 【Label 必须中文】。
5. 【不遗漏】: 提取所有符合类定义的实体。
```

---

## 7. 数据结构定义

### 7.1 Pydantic Schema (后端)

```python
# src/backend/app/schemas/extraction.py

class NodeData(BaseModel):
    """节点数据"""
    label: str
    type: str  # 'owl:Class' 或 'owl:NamedIndividual'
    properties: Dict[str, Any]

class GraphNode(BaseModel):
    """图节点"""
    id: str
    type: str = "custom"
    position: Dict[str, float]
    data: NodeData

class GraphEdge(BaseModel):
    """图中的一条边"""
    id: str
    source: str
    target: str
    label: str
    data: EdgeData

class GraphData(BaseModel):
    """完整的图数据结构"""
    nodes: List[GraphNode]
    edges: List[GraphEdge]

class OntologyClass(BaseModel):
    """OWL Class 的规范化表示"""
    id: str
    label: str
    sub_class_of: Optional[str]
    data_properties: List[str]

class OntologyObjectProperty(BaseModel):
    """OWL ObjectProperty 的规范化表示"""
    id: str
    label: str
    domain: str
    range: str

class SchemaGraph(BaseModel):
    """骨架图：仅包含 Class 和 ObjectProperty"""
    classes: List[OntologyClass]
    object_properties: List[OntologyObjectProperty]

class OntologyInstance(BaseModel):
    """OWL NamedIndividual 的规范化表示"""
    id: str
    label: str
    type: str
    object_props: Dict[str, List[str]]
    data_props: Dict[str, str]
```

### 7.2 TypeScript Types (前端)

```typescript
// src/frontend/src/types/ontology.ts

export interface OntologyNode {
    id: string;
    type: 'custom';
    position: { x: number; y: number };
    data: {
        label: string;
        type: 'owl:Class' | 'owl:NamedIndividual' | string;
        properties: Record<string, any>;
    };
}

export interface OntologyEdge {
    id: string;
    source: string;
    target: string;
    label: string;
    data: { label: string; relation?: string };
}

export interface GraphData {
    nodes: OntologyNode[];
    edges: OntologyEdge[];
}

export interface SchemaClass {
    id: string;
    label: string;
    sub_class_of: string | null;
    data_properties: string[];
}

export interface SchemaGraph {
    classes: SchemaClass[];
    object_properties: SchemaObjectProperty[];
}
```

---

## 8. 前端操作流程

### 8.1 页面组件结构

```
OntologyBuilderPage.tsx
├── 创建项目表单
├── 顶部工具栏
│   ├── 提取骨架
│   ├── 提取实例
│   ├── 保存草稿
│   ├── 发布
│   └── 下载 TTL
├── 画布区域 (D3ForceGraph)
├── 属性编辑抽屉
└── 各种 Modal
```

### 8.2 关键状态管理

```typescript
const [nodes, setNodes] = useState<any[]>([]);
const [edges, setEdges] = useState<any[]>([]);
const [selectedElement, setSelectedElement] = useState<any | null>(null);
const [loading, setLoading] = useState(false);
const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
```

### 8.3 两阶段流程实现

```typescript
// 阶段 1: 提取 Schema
const handleStartSchemaExtraction = async () => {
    const response = await projectsApi.extractSchema(projectId, file, options);
    localStorage.setItem(`project_${projectId}_text_content`, response.text_content);
    localStorage.setItem(`project_${projectId}_schema_graph`, JSON.stringify(response.schema_graph));
    setNodes(response.graph_data.nodes);
    setEdges(response.graph_data.edges);
};

// 阶段 2: 提取实例
const handleStartInstanceExtraction = async () => {
    const textContent = localStorage.getItem(`project_${projectId}_text_content`);
    const schemaGraph = JSON.parse(localStorage.getItem(`project_${projectId}_schema_graph`));
    const response = await projectsApi.extractInstances(projectId, {
        text_content: textContent,
        schema_graph: schemaGraph,
    });
    setNodes(response.graph_data.nodes);
    setEdges(response.graph_data.edges);
};
```

---

## 9. 数据库设计

### 9.1 SQLite Schema

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL
);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    owner_id INTEGER NOT NULL,
    graph_data JSON DEFAULT '{"nodes": [], "edges": []}',
    is_published BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE system_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSON NOT NULL
);
```

### 9.2 Neo4j 数据模型

```cypher
// 节点
(:Class {id: "...", label: "..."})
(:Instance {id: "...", label: "..."})

// 关系
-[:SUB_CLASS_OF]->
-[:INSTANCE_OF]->
-[:OBJECT_PROPERTY {name: "..."}]->
```

---

## 10. 部署与配置

### 10.1 环境变量

```bash
# .env
LLM_API_KEY=sk-...
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL_NAME=default-model
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
DATABASE_URL=sqlite:///./ontology_system.db
```

### 10.2 Docker 部署

```yaml
# docker-compose.yml
version: '3.8'
services:
  backend:
    build: ./src/backend
    ports:
      - "8000:8000"
  frontend:
    build: ./src/frontend
    ports:
      - "3000:80"
  neo4j:
    image: neo4j:5
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      - NEO4J_AUTH=neo4j/password
```

### 10.3 启动命令

```bash
# 后端
cd src/backend && python -m uvicorn main:app --reload

# 前端
cd src/frontend && npm install && npm run dev

# Docker
docker-compose up -d
```

---

## 附录：常见问题

### A.1 LLM 返回格式错误
系统内置 JSON 修复逻辑。如仍失败，检查 LLM 配置、网络连接，或调整 `chunk_size`。

### A.2 实例提取时连线被丢弃
以下情况连线会被丢弃：
1. 关系未在 Schema 中定义
2. domain/range 与 Schema 不匹配
3. 实例的 type 不在 Schema 中

---

**文档结束**