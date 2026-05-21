# Graph Injector Service

知识图谱注入服务 - 从文档自动构建知识图谱(Schema+实例)并注入到Elasticsearch(RAGFlow)中。

## 功能特性

- **多格式文档解析**: 支持PDF、DOCX、TXT、Markdown格式
- **智能Schema构建**: 利用LLM从文档中自动提取本体结构(实体类型和关系类型)
- **实例提取**: 基于Schema从文档中提取实体和关系实例，支持跨Chunk实体去重
- **ES注入**: 将知识图谱数据(实体、关系、图结构、类型映射)注入到Elasticsearch
- **灵活的模型支持**: 同时支持vLLM(OpenAI兼容)和Ollama两种LLM/Embedding服务
- **异步任务处理**: 支持后台异步注入和同步注入两种模式
- **完善的日志系统**: 控制台+文件双输出，支持日志轮转

## 系统架构

```
graph_injector_service/
├── app/
│   ├── api/                  # API层
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── inject.py     # 注入端点
│   │       │   ├── schema.py     # Schema管理端点
│   │       │   └── system.py     # 系统健康检查端点
│   │       └── api.py            # API路由器
│   ├── core/                 # 核心模块
│   │   ├── config.py             # 配置管理
│   │   ├── exceptions.py         # 自定义异常
│   │   └── logging.py            # 日志配置
│   ├── infrastructure/       # 基础设施层
│   │   ├── es_client.py          # Elasticsearch客户端
│   │   ├── llm_client.py         # LLM客户端(vLLM/Ollama)
│   │   ├── embedding_client.py   # Embedding客户端
│   │   └── ragflow_client.py     # RAGFlow API客户端
│   ├── schemas/              # 数据模型
│   │   ├── request.py              # 请求模型
│   │   └── response.py             # 响应模型
│   ├── services/             # 业务逻辑层
│   │   ├── schema_service.py       # Schema构建服务
│   │   ├── instance_service.py     # 实例构建服务
│   │   ├── inject_service.py       # ES注入服务
│   │   └── orchestrator.py         # 流程协调服务
│   ├── utils/                # 工具模块
│   │   ├── chunker.py              # 文本分块工具
│   │   ├── file_parser.py          # 文档解析工具
│   │   └── entity_registry.py      # 实体注册与去重
│   └── main.py               # FastAPI应用入口
├── data/                   # 数据目录
│   ├── temp_uploads/           # 临时上传文件
│   ├── schemas/                # 生成的Schema文件
│   ├── output/                 # 输出文件
│   └── logs/                   # 日志文件
├── .env                    # 环境配置
├── .env.example            # 环境配置示例
├── requirements.txt        # Python依赖
├── start.py                # 启动脚本
└── README.md               # 说明文档
```

## 工作流程

```
1. 接收请求
   ├── 上传文档文件(PDF/DOCX/TXT/MD) 或 直接提供文本
   ├── 指定chunk_size和overlap_percentage
   ├── 选择是否使用已有Schema
   └── 提供RAGFlow的kb_id、tenant_id、api_key

2. 文档解析
   └── 将上传的文件解析为纯文本

3. 文本分块
   └── 按chunk_size和overlap_percentage切分文本

4. Schema构建 (如果未使用已有Schema)
   └── 利用LLM从各chunk中提取实体类型和关系类型
   └── 合并各chunk的Schema并保存到JSON文件

5. 实例构建
   └── 基于Schema，利用LLM从各chunk中提取实体和关系实例
   └── 使用EntityRegistry进行跨chunk实体去重(GUID化)
   └── 使用RelationshipResolver解析关系(临时ID→GUID)

6. ES注入
   ├── 生成实体/关系的embedding向量
   ├── 注入实体文档到ES(knowledge_graph_kwd=entity)
   ├── 注入关系文档到ES(knowledge_graph_kwd=relation)
   ├── 注入图结构到ES(knowledge_graph_kwd=graph)
   └── 注入实体类型映射到ES(knowledge_graph_kwd=ty2ents)

7. 返回结果
   └── 注入统计(实体数、关系数、成功/失败数)
```

## 快速开始

### 1. 创建并激活虚拟环境

```bash
cd graph_injector_service
python3 -m venv venv
source venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑.env文件，配置LLM、Embedding、ES等地址
vim .env
```

关键配置项说明:

```bash
# LLM模型 (vLLM示例)
LLM_BASE_URL=http://localhost:8001/v1
LLM_API_KEY=your_api_key
LLM_MODEL=qwen2.5-7b-instruct

# LLM模型 (Ollama示例)
LLM_BASE_URL=http://localhost:11434
LLM_API_KEY=
LLM_MODEL=qwen2.5:7b

# Embedding模型
EMBED_BASE_URL=http://localhost:11434
EMBED_MODEL=bge-m3:latest

# Elasticsearch
ES_HOST=localhost
ES_PORT=1200
ES_USER=elastic
ES_PASSWORD=infini_rag_flow
```

### 4. 启动服务

```bash
# 方式1: 使用启动脚本
python start.py

# 方式2: 直接使用uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

服务启动后访问:
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/v1/system/health

## API接口

### 注入接口

#### POST /api/v1/inject/ (异步注入)

提交注入任务，后台异步处理。

**请求参数 (multipart/form-data):**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | File[] | 否 | 文档文件列表(PDF/DOCX/TXT/MD)，可多文件 |
| text_content | string | 否 | 直接提供的文本内容 |
| chunk_size | int | 否 | 每个chunk长度，默认1000，范围100-10000 |
| overlap_percentage | int | 否 | chunk重叠百分比，默认10，范围0-50 |
| use_existing_schema | bool | 否 | 是否使用已有Schema，默认false |
| existing_schema_path | string | 条件 | 已有Schema文件路径(use_existing_schema=true时必填) |
| ragflow_kb_id | string | 是 | RAGFlow知识库ID |
| ragflow_tenant_id | string | 是 | RAGFlow租户ID |
| ragflow_api_key | string | 是 | RAGFlow API Key |
| additional_instructions | string | 否 | 额外的构建指令 |

**响应示例:**

```json
{
  "status": "success",
  "task_id": "uuid-string",
  "message": "注入任务已提交，正在后台处理"
}
```

#### POST /api/v1/inject/sync (同步注入)

同步执行注入，等待完成后返回完整结果。参数与异步版本相同。

**响应示例:**

```json
{
  "status": "success",
  "task_id": "uuid-string",
  "message": "注入流程完成",
  "summary": {
    "doc_id": "graph_injector_xxxx",
    "schema": {"entity_types": 5, "relation_types": 8},
    "instances": {"entities": 20, "relationships": 35},
    "injection": {
      "entities_created": 20,
      "relations_created": 35
    }
  },
  "entities_injected": 20,
  "relationships_injected": 35
}
```

#### GET /api/v1/inject/task/{task_id}

查询任务状态。

**响应示例:**

```json
{
  "task_id": "uuid-string",
  "status": "processing",
  "progress": 60,
  "message": "实例构建完成: 实体=20, 关系=35",
  "result": null,
  "created_at": "2024-01-01T12:00:00"
}
```

### Schema接口

#### POST /api/v1/schema/extract

从上传的文档文件或文本内容中提取知识图谱Schema。支持多文件上传，只执行Schema提取，不执行后续的实例构建和ES注入。

**请求参数 (multipart/form-data):**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | File[] | 否 | 文档文件列表(PDF/DOCX/TXT/MD)，可多文件 |
| text_content | string | 否 | 直接提供的文本内容 |
| chunk_size | int | 否 | 每个chunk长度，默认1000，范围100-10000 |
| overlap_percentage | int | 否 | chunk重叠百分比，默认10，范围0-50 |
| additional_instructions | string | 否 | 额外的构建指令 |

**响应示例:**

```json
{
  "status": "success",
  "schema_path": "data/schemas/schema_20240101_120000.json",
  "schema_content": {
    "entity_types": [
      {
        "name": "Person",
        "description": "人物实体",
        "attributes": [
          {"name": "name", "description": "姓名"},
          {"name": "title", "description": "职位"}
        ]
      },
      {
        "name": "Organization",
        "description": "组织机构",
        "attributes": [
          {"name": "name", "description": "名称"}
        ]
      }
    ],
    "relation_types": [
      {
        "name": "works_at",
        "description": "工作于",
        "source_types": ["Person"],
        "target_types": ["Organization"]
      }
    ]
  },
  "message": "Schema提取完成，共提取 2 个实体类型和 1 个关系类型",
  "entity_types": ["Person", "Organization"],
  "relation_types": ["works_at"]
}
```

#### POST /api/v1/schema/build

从文档内容构建Schema。

```json
{
  "text_content": "文档内容...",
  "chunk_size": 1000,
  "overlap_percentage": 10,
  "additional_instructions": "重点关注人物和组织关系"
}
```

#### POST /api/v1/schema/load

加载已有Schema文件。

```json
{
  "schema_path": "data/schemas/schema_20240101_120000.json"
}
```

#### GET /api/v1/schema/list

列出所有已保存的Schema文件。

### 系统接口

#### GET /api/v1/system/health

健康检查。

```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T12:00:00",
  "version": "1.0.0",
  "components": {
    "elasticsearch": "healthy",
    "llm": "healthy",
    "embedding": "healthy"
  }
}
```

#### POST /api/v1/system/test-connectivity/llm

测试LLM服务连通性。

#### POST /api/v1/system/test-connectivity/embedding

测试Embedding服务连通性。

#### POST /api/v1/system/test-connectivity/es

测试Elasticsearch连通性。

## cURL使用示例

### 提取Schema(上传文件)

```bash
curl -X POST "http://localhost:8000/api/v1/schema/extract" \
  -F "files=@/path/to/document1.pdf" \
  -F "files=@/path/to/document2.txt" \
  -F "chunk_size=1000" \
  -F "overlap_percentage=10" \
  -F "additional_instructions=重点关注人物和组织关系"
```

### 提取Schema(直接提供文本)

```bash
curl -X POST "http://localhost:8000/api/v1/schema/extract" \
  -F "text_content=这里是文档的文本内容..." \
  -F "chunk_size=1000" \
  -F "overlap_percentage=10"
```

### 异步注入(上传文件)

```bash
curl -X POST "http://localhost:8000/api/v1/inject/" \
  -F "files=@/path/to/document1.pdf" \
  -F "files=@/path/to/document2.txt" \
  -F "chunk_size=1000" \
  -F "overlap_percentage=10" \
  -F "use_existing_schema=false" \
  -F "ragflow_kb_id=d7379c60421e11f19e770242ac170006" \
  -F "ragflow_tenant_id=your_tenant_id" \
  -F "ragflow_api_key=your_api_key"
```

### 使用已有Schema注入

```bash
curl -X POST "http://localhost:8000/api/v1/inject/" \
  -F "files=@/path/to/document.pdf" \
  -F "chunk_size=800" \
  -F "overlap_percentage=15" \
  -F "use_existing_schema=true" \
  -F "existing_schema_path=data/schemas/schema_20240101_120000.json" \
  -F "ragflow_kb_id=d7379c60421e11f19e770242ac170006" \
  -F "ragflow_tenant_id=your_tenant_id" \
  -F "ragflow_api_key=your_api_key"
```

### 同步注入(直接提供文本)

```bash
curl -X POST "http://localhost:8000/api/v1/inject/sync" \
  -F "text_content=这里是文档的文本内容..." \
  -F "chunk_size=1000" \
  -F "overlap_percentage=10" \
  -F "use_existing_schema=false" \
  -F "ragflow_kb_id=d7379c60421e11f19e770242ac170006" \
  -F "ragflow_tenant_id=your_tenant_id" \
  -F "ragflow_api_key=your_api_key"
```

### 查询任务状态

```bash
curl "http://localhost:8000/api/v1/inject/task/{task_id}"
```

## Schema文件格式

生成的Schema JSON文件格式:

```json
{
  "entity_types": [
    {
      "name": "Person",
      "description": "人物实体",
      "attributes": [
        {"name": "name", "description": "姓名"},
        {"name": "title", "description": "职位"},
        {"name": "description", "description": "描述"}
      ]
    },
    {
      "name": "Organization",
      "description": "组织机构",
      "attributes": [
        {"name": "name", "description": "名称"},
        {"name": "type", "description": "类型"},
        {"name": "description", "description": "描述"}
      ]
    }
  ],
  "relation_types": [
    {
      "name": "works_at",
      "description": "工作于",
      "source_types": ["Person"],
      "target_types": ["Organization"]
    },
    {
      "name": "owns",
      "description": "拥有",
      "source_types": ["Person", "Organization"],
      "target_types": ["Product", "Organization"]
    }
  ]
}
```

## 生产部署建议

1. **使用Gunicorn作为WSGI服务器**:
   ```bash
   gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
   ```

2. **配置日志轮转**: 服务已内置日志轮转(RotatingFileHandler)，单文件最大50MB，保留10个备份。

3. **设置进程守护**: 使用systemd或supervisor管理进程。

4. **配置反向代理**: 使用Nginx作为反向代理，处理SSL终止和静态文件。

5. **资源监控**: 监控内存使用(特别是大文档处理)和ES连接池。

6. **备份Schema文件**: 定期备份`data/schemas/`目录下的Schema文件。
