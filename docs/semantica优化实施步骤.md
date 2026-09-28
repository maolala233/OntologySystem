# 用 Semantica 优化 OntologySystem · 完整实施步骤

> 目标：用 Semantica v0.7.0 的底层能力补齐 OntologySystem 的 8 项需求，**不重写平台**。
> 用法：按阶段喂给大模型。每个 Step 都标了「改哪个文件 / 不改什么 / 验证方式」。
> 前置阅读：`抽取优化方案.md`（4 个 P0 正确性缺陷必须先修）
>
> **配套文档**
> - `docs/docker-compose.middleware.yml` —— 中间件编排（必装 8 个镜像 + 4 个可选 profile，含健康检查/依赖顺序/数据卷）
> - `docs/图谱展示重构方案.md` —— 需求 5 的图谱展示部分（Step 5.1 的展开）

---

## 0. 总览

### 0.1 三条铁律

1. **适配层先行**。Semantica 有 12 处 API 会直接坑到你（见 §0.3）。业务层直接 `import semantica` 一定返工。所有调用必须经 `app/adapters/`。
2. **不改调用方签名**。优化 = 换实现，不是换接口。`merge_instances_to_graph_data` 内部换成 `DuplicateDetector`，函数签名不变 → API 和前端一行不用动，随时可回滚。
3. **保留已有的两阶段交互**。`extract-schema → 前端画布人工审核 → extract-instances` 这个流程已经跑通，**比 Semantica 的库函数更完整**（Semantica 没有审核 UI）。只换引擎，不换交互。

### 0.2 改造边界

| 层 | 处理 | 说明 |
|---|---|---|
| 前端页面/路由/认证 | **保留** | 10,752 行，React 18 + antd + ReactFlow |
| 项目/知识域/任务/SSE | **保留** | 17,403 行后端的平台骨架 |
| Neo4j / Milvus 集成 | **保留** | 已在用 |
| 抽取引擎（消解/冲突/溯源/导出/版本） | **替换** | 接 Semantica |
| 数据模型 | **扩展** | 5 张表 → 15 张表 |
| 中间件 | **加 3 个** | Redis（队列）+ 业务 MinIO（对象存储）+ 嵌入式 Oxigraph（RDF）；详见 `docs/docker-compose.middleware.yml` |
| 图谱渲染 | **新增查看态** | 保留 ReactFlow 做编辑，新增 Sigma 做探索 |

### 0.3 ⚠️ 12 个 Semantica 缺陷速查（必读）

| # | 位置 | 问题 | 绕法 |
|---|---|---|---|
| 1 | `split/methods.py:424` | 中文分句失效（只认半角 `.!?`） | 用自研中文分句 |
| 2 | `split/methods.py:340` | 硬编码 `en_core_web_sm` | 禁用该方法 |
| 3 | `split/methods.py:231` | `chunk_overlap >= chunk_size` 时每次前进 1 字符 | 强制 `overlap <= size//5` |
| 4 | `ner_extractor.py:412-415` | 守卫用 `isalnum()`，汉字返回 True → `\w` 误加 | 守卫改 `str.isascii()` |
| 5 | `ner_extractor.py:720` | 回标只在 `merge_strategy != "fallback"` 时执行 | 强制 `"union"` |
| 6 | `ner_extractor.py:774` | `except Exception: continue` 吞掉方法级异常 | 适配层包一层 |
| 7 | `semantic_extract/providers.py:2087` | provider 名是 `huggingface_llm` 不是 `huggingface` | 名称映射表 |
| 8 | `llms/` vs `semantic_extract/` | 两套 provider 默认值冲突 | 永远显式传 model |
| 9 | `parse/document_parser.py:90` | `DocumentParser` 只认 4 类格式 | 自建分发器 |
| 10 | `pipeline_builder.py:410` | `validate_pipeline()` 对数据类调 `.get()` 必崩 | 直接调 `PipelineValidator` |
| 11 | `cli.py:4387/4409/4430` | CLI 调不存在的 `engine.run/status/stop` | 用 `execute_pipeline` |
| 12 | `vector_store/faiss_store.py:536` | 默认 `dimension=768` 与实际 embedder 不符 | 从 embedder 实测注入 |

**补充约束**：Explorer 无用户体系；`GraphSession` 无持久化；`semantica/mcp_server` 只有 stdio；仓库有**两个 MCP 包**（选 `semantica_mcp`，22 工具）。

### 0.4 阶段划分

| 阶段 | 内容 | 周期 | 产出 |
|---|---|---|---|
| **P0** | 地基：适配层 + 修 4 个 P0 + 加表 | 1 周 | 数据是对的 |
| **P1** | 需求 1/2/3：权限 + 模型配置 + 解析 | 1.5 周 | 能管人、能配模型、能上传 |
| **P2** | 需求 4：分层建本体（最重） | 2.5 周 | 消解/冲突/溯源/审核/版本/发布 |
| **P3** | 需求 5/6：图谱重构 + 导出 + 四存储 | 2 周 | 图谱好用、多格式导出、RDF |
| **P4** | 需求 7/8：MCP + 问答 + 中文 + i18n | 1.5 周 | 对外服务 |
| 合计 | | **8.5 周** | |

---

# 阶段 P0 · 地基（1 周）

## Step 0.1 依赖隔离

**目标**：Semantica 装进独立 venv，避免和现有依赖冲突（特别是 `openai`、`pydantic`、`numpy` 版本）。

```bash
# 1. 在 OntologySystem 里加 Semantica
cd D:/python_code/OntologySystem
pip install "semantica==0.7.0"        # 锁定版本，别用 latest

# 2. 关键：验证版本兼容
python -c "
import semantica, openai, pydantic, numpy
print('semantica', semantica.__version__)
print('openai', openai.__version__)     # Semantica 要求 >=1.0
print('pydantic', pydantic.VERSION)     # 必须 v2
print('numpy', numpy.__version__)
"

# 3. 把依赖写进 requirements.txt（锁版本）
# semantica==0.7.0
```

**验证**：`python -c "from semantica.parse import DoclingParser"` 不报错。

**注意**：`DoclingParser` 需要额外装 docling + 系统级 Tesseract。OCR 建议放独立镜像（见 Step 2.4）。

---

## Step 0.2 建立适配层骨架

**目标**：所有 Semantica 调用收口到一个目录。

```
backend/app/adapters/
├─ __init__.py
├─ _compat.py          # 12 个缺陷的补丁函数（中文分句、回标、切片守卫）
├─ provider.py         # ProviderResolver（修 #7 #8 #12）
├─ parsing.py          # parse_document 分发器（修 #9）
├─ chunking.py         # 中文感知切片（修 #1 #2 #3）
├─ extraction.py       # 实体/关系抽取封装（修 #4 #5 #6）
├─ schema_gate.py      # Schema 闸门（cardinality + data_type + required）
├─ resolution.py       # DedupResolver / ConflictAnalyzer
├─ provenance.py       # ProvenanceManager 封装
├─ versioning.py       # TemporalVersionManager / TemporalGraphQuery 封装
├─ exporting.py        # 18 个 exporter 封装
├─ retrieval.py        # GraphRAG（AgentContext.retrieve）
└─ errors.py           # 统一异常
```

**统一异常**（`errors.py`）：

```python
class AdapterError(Exception): ...
class UnsupportedFormat(AdapterError): ...
class ProviderConfigError(AdapterError): ...
class ExtractionFailed(AdapterError): ...
class SchemaViolation(AdapterError): ...
class StorageUnavailable(AdapterError): ...
```

**验证**：`python -c "from app.adapters import _compat"` 不报错。

---

## Step 0.3 修 4 个 P0 正确性缺陷

详见 `抽取优化方案.md` §一。这里只列改动点：

| 缺陷 | 文件 | 改动 |
|---|---|---|
| #1 前缀误合并 | `services/extractor.py:2063-2078` | 删掉 `get_instance_prefix`，改保守归一 |
| #2 dict 塞 prompt | `extractor.py:894` / `:3067` | 取 `chunk["text"]`，并传 filename |
| #3 metadata 造假 | `extractor.py:1106-1108` | 真实计数 |
| #4 子串错配 | `extractor.py:1027-1029` | 删子串匹配，改归一+别名+编辑距离 |

**验证**（必须全绿才进 P1）：

```python
def test_no_false_merge():
    insts = [{"id": "I_1", "label": "合同2024", "type": "C_x"},
             {"id": "I_2", "label": "合同2025", "type": "C_x"}]
    out = OntologyExtractor.merge_instances_to_graph_data({"nodes": [], "edges": []}, insts)
    labels = {n["data"]["label"] for n in out["nodes"]
              if n["data"].get("type") == "owl:NamedIndividual"}
    assert labels == {"合同2024", "合同2025"}

def test_schema_prompt_plain_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(OntologyExtractor, "_call_llm",
                        lambda self, s, u, **k: captured.setdefault("u", u) or None)
    ...  # 跑 extract_schema
    assert "'text':" not in captured["u"]
    assert "\n" in captured["u"]        # 真实换行
```

---

## Step 0.4 加表（含 `User.role`）

**目标**：5 张表 → 15 张表。**只加表，不动现有表**（除 `users` 加一列）。

```python
# infrastructure/database.py —— 只改 User，新增 10 张表

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)
    # ★ 新增
    role = Column(String(16), default="user", nullable=False, index=True)  # admin | user
    email = Column(String(200), nullable=True)
    locale = Column(String(8), default="zh-CN")
    last_login_at = Column(DateTime, nullable=True)

# ★ 新增表
class Module(Base):                    # 模块字典（14 条 seed）
    __tablename__ = "modules"
    code = Column(String(32), primary_key=True)
    name_zh = Column(String(64), nullable=False)
    name_en = Column(String(64), nullable=False)
    sort_order = Column(SmallInteger, default=0)
    is_core = Column(Boolean, default=False)

class UserModuleGrant(Base):           # 需求 1 核心：管理员配模块
    __tablename__ = "user_module_grants"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    module_code = Column(String(32), ForeignKey("modules.code"), nullable=False)
    granted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    granted_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "module_code"),)

class ProjectMember(Base):             # 项目内角色
    __tablename__ = "project_members"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), default="viewer")   # owner|editor|reviewer|viewer
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

class ModelConfig(Base):               # 需求 2
    __tablename__ = "model_configs"
    id = Column(Integer, primary_key=True)
    scope = Column(String(16), default="global")     # global | project
    project_id = Column(Integer, nullable=True)
    kind = Column(String(16), nullable=False)        # llm | embedding | rerank | vl
    provider = Column(String(32), nullable=False)
    model_name = Column(String(128), nullable=False)
    base_url = Column(String(500), nullable=True)
    api_key_encrypted = Column(String(1000), nullable=True)
    params = Column(JSON, default=dict)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class OntologyVersion(Base):           # 需求 4.7 版本控制
    __tablename__ = "ontology_versions"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    label = Column(String(64), nullable=False)
    status = Column(String(24), default="draft", index=True)
    # draft | pending_review | approved | rejected | published
    ttl_content = Column(LONGTEXT, nullable=True)
    schema_json = Column(JSON, nullable=True)
    stats = Column(JSON, default=dict)          # {classes:24, props:49}
    parent_version_id = Column(Integer, nullable=True)
    change_summary = Column(JSON, default=dict) # {added:[], modified:[], removed:[]}
    created_by = Column(Integer, ForeignKey("users.id"))
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reject_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Entity(Base):                    # ★ 把 graph_data JSON blob 拆出来
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    det_id = Column(String(64), nullable=False, index=True)
    label = Column(String(500), nullable=False, index=True)
    label_norm = Column(String(500), nullable=False, index=True)   # 归一化，供消解
    type_id = Column(String(64), nullable=True, index=True)
    type_label = Column(String(200), nullable=True)
    properties = Column(JSON, default=dict)
    confidence = Column(Float, nullable=True)
    review_status = Column(String(16), default="auto", index=True)  # auto|pending|approved|rejected
    ontology_version_id = Column(Integer, nullable=True)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    merged_into = Column(String(64), nullable=True)   # 合并可回滚
    __table_args__ = (UniqueConstraint("project_id", "det_id"),)

class Relation(Base):
    __tablename__ = "relations"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    det_id = Column(String(64), nullable=False, index=True)
    predicate_id = Column(String(64), nullable=False, index=True)
    predicate_label = Column(String(200), nullable=True)
    source_det_id = Column(String(64), nullable=False, index=True)
    target_det_id = Column(String(64), nullable=False, index=True)
    source_type = Column(String(64), nullable=True)
    target_type = Column(String(64), nullable=True)
    properties = Column(JSON, default=dict)
    confidence = Column(Float, nullable=True)
    review_status = Column(String(16), default="auto", index=True)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)

class ProvenanceRecord(Base):          # 需求 4.5 溯源
    __tablename__ = "provenance_records"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    subject_type = Column(String(16), nullable=False)     # entity | relation
    subject_id = Column(String(64), nullable=False, index=True)
    activity = Column(String(32), nullable=False)         # llm_extraction|manual_edit|merge|import
    agent_type = Column(String(16), nullable=False)       # llm | user | system
    agent_id = Column(String(64), nullable=True)
    document_id = Column(Integer, nullable=True)
    chunk_index = Column(Integer, nullable=True)
    page_no = Column(Integer, nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    quote = Column(Text, nullable=True)                   # ★ 证据原句，不是 chunk[:200]
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class ReviewItem(Base):                # 需求 4.6 人工审核
    __tablename__ = "review_items"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    item_type = Column(String(16), nullable=False)        # entity|relation|ontology_version|conflict
    subject_id = Column(String(64), nullable=True)
    reason = Column(String(48), nullable=False)           # low_confidence|ambiguous_merge|type_conflict|...
    priority = Column(SmallInteger, default=20)
    payload = Column(JSON, nullable=False)                # 候选/备选/证据
    status = Column(String(16), default="pending", index=True)  # pending|claimed|approved|rejected|edited
    claimed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Publication(Base):               # 需求 4.8 发布（升级现有 is_published 布尔）
    __tablename__ = "publications"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    ontology_version_id = Column(Integer, nullable=False)
    include_instances = Column(Boolean, default=True)
    only_approved = Column(Boolean, default=True)
    snapshot_ttl = Column(LONGTEXT, nullable=True)        # ★ 快照固化，不引用活数据
    is_active = Column(Boolean, default=True)
    published_by = Column(Integer, ForeignKey("users.id"))
    published_at = Column(DateTime, default=datetime.datetime.utcnow)
    unpublished_at = Column(DateTime, nullable=True)

class GraphSnapshot(Base):             # 需求 4.7 时间轴
    __tablename__ = "graph_snapshots"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    label = Column(String(64), nullable=False)
    entity_count = Column(Integer, default=0)
    relation_count = Column(Integer, default=0)
    payload_key = Column(String(500), nullable=True)      # 存对象存储，不塞 MySQL
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class OutboxEvent(Base):               # 需求 6 四存储一致性
    __tablename__ = "outbox_events"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=False, index=True)
    target = Column(String(16), nullable=False)           # neo4j | milvus | oxigraph
    operation = Column(String(16), nullable=False)        # upsert | delete
    payload = Column(JSON, nullable=False)
    status = Column(String(16), default="pending", index=True)
    retry_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
```

**迁移**：项目现在没有 Alembic（`alembic` 在 requirements 里但没用）。**必须补上**：

```bash
alembic init migrations
# 改 migrations/env.py 指向 app.infrastructure.database.Base
alembic revision --autogenerate -m "add platform tables"
alembic upgrade head
```

**验证**：`alembic upgrade head` 成功，`users` 表有 `role` 列，10 张新表存在。

---

# 需求 1 · 用户登录与模块级权限

**现状**：有 `register` / `login` / `me` / `users` / `change-password` / `reset-password`，**但 `User` 没有 `role` 字段**，没有模块级授权。

## Step 1.1 后端：模块守卫

```python
# app/core/deps.py（新建）
from fastapi import Depends, HTTPException
from app.infrastructure.database import SessionLocal, User, UserModuleGrant, Project, ProjectMember

MODULE_NAMES_ZH = {
    "dashboard": "总览", "projects": "项目", "documents": "文档管理",
    "ontology": "本体构建", "instances": "实例构建", "review": "人工审核",
    "graph": "图谱查看", "qa": "知识问答", "export": "导出",
    "publish": "发布管理", "mcp": "MCP 服务", "models": "模型配置", "admin": "系统管理",
}
ROLE_RANK = {"viewer": 1, "editor": 2, "reviewer": 3, "owner": 4}


def require_module(code: str):
    """模块级守卫。管理员绕过。权限变更立即生效（不放进 JWT）。"""
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role == "admin":
            return user
        db = SessionLocal()
        try:
            granted = (db.query(UserModuleGrant)
                       .filter(UserModuleGrant.user_id == user.id,
                               UserModuleGrant.module_code == code,
                               or_(UserModuleGrant.expires_at.is_(None),
                                   UserModuleGrant.expires_at > datetime.utcnow()))
                       .first())
            if not granted:
                raise HTTPException(403, detail={
                    "code": "MODULE_NOT_GRANTED",
                    "message": f"您没有「{MODULE_NAMES_ZH.get(code, code)}」模块的访问权限，请联系管理员开通",
                    "module": code,
                })
            return user
        finally:
            db.close()
    return dep


def require_project_role(*roles: str):
    """项目内角色守卫。"""
    def dep(project_id: int, user: User = Depends(get_current_user)) -> str:
        if user.role == "admin":
            return "owner"
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(404, detail={"code": "PROJECT_NOT_FOUND"})
            if project.owner_id == user.id:
                return "owner"
            member = (db.query(ProjectMember)
                      .filter(ProjectMember.project_id == project_id,
                              ProjectMember.user_id == user.id).first())
            if not member or member.role not in roles:
                raise HTTPException(403, detail={
                    "code": "PROJECT_ROLE_DENIED",
                    "message": "您在该项目中没有执行此操作的权限",
                })
            return member.role
        finally:
            db.close()
    return dep
```

## Step 1.2 后端：`/auth/me` 返回模块清单

前端据此渲染侧栏。**必须返回 `modules` 数组**：

```python
@router.get("/me")
def me(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        if user.role == "admin":
            modules = [m.code for m in db.query(Module).all()]
        else:
            modules = [g.module_code for g in
                       db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).all()]
        return {
            "id": user.id, "username": user.username, "role": user.role,
            "locale": user.locale, "modules": modules,
        }
    finally:
        db.close()
```

## Step 1.3 后端：管理员 API

```python
# app/api/admin.py（新建）
@router.get("/users")                                  # 用户列表
@router.post("/users")                                 # 建用户（可带 role）
@router.patch("/users/{uid}")                          # 改 role / is_active
@router.get("/users/{uid}/modules")                    # 查某用户的模块
@router.patch("/users/{uid}/modules")                  # ★ 全量覆盖式授权
@router.get("/modules")                                # 模块清单
@router.get("/audit")                                  # 审计日志
```

`PATCH /users/{uid}/modules` 用**全量覆盖**语义（body 传完整模块码数组），避免增量歧义：

```python
class ModuleGrantIn(BaseModel):
    modules: List[str]

@router.patch("/users/{uid}/modules")
def set_modules(uid: int, body: ModuleGrantIn,
                admin: User = Depends(require_admin)):
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == uid).first()
        if not target:
            raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
        if target.role == "admin":
            raise HTTPException(400, detail={"code": "ADMIN_ALWAYS_FULL_ACCESS"})

        core = {m.code for m in db.query(Module).filter(Module.is_core.is_(True)).all()}
        wanted = set(body.modules) | core              # 核心模块强制保留

        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == uid).delete()
        for code in wanted:
            db.add(UserModuleGrant(user_id=uid, module_code=code, granted_by=admin.id))
        db.commit()
        return {"user_id": uid, "modules": sorted(wanted)}
    finally:
        db.close()
```

## Step 1.4 前端：模块守卫 + 授权矩阵页

```tsx
// src/components/ModuleGate.tsx（新建）
export function ModuleGate({ module, children }: { module: string; children: ReactNode }) {
  const { modules, role } = useAuthStore();
  if (role === "admin" || modules.includes(module)) return <>{children}</>;
  return <NoAccess module={module} />;   // 403 风格空状态，不跳登录
}

// src/components/ProtectedRoute.tsx（改造现有）—— 加模块校验
```

侧栏按 `modules` 过滤（`Navbar.tsx` 改造），路由 `beforeLoad` 二次拦截。

**新建 `src/pages/admin/GrantsPage.tsx`** —— 授权矩阵：

```
            总览 项目 文档 本体 实例 审核 图谱 问答 导出 发布 MCP 模型 管理
李工         ✅  ✅  ✅  ✅  ✅  ✅  ✅  ✅  ✅  ☐  ☐  ☐  ☐  ☐
王分析师     ✅  ✅  ✅  ✅  ☐  ☐  ✅  ✅  ✅  ☐  ☐  ☐  ☐  ☐
```

单元格点击切换 → 乐观更新 → 批量 `PATCH` → 失败回滚。核心模块（`is_core`）复选框禁用。

## 验收

- [ ] 管理员创建用户只授予 5 个模块 → 该用户登录后侧栏只有 5 项
- [ ] 该用户直接 `curl POST /api/projects/1/extract-schema` → 403 `MODULE_NOT_GRANTED`
- [ ] 管理员加授权后，用户**刷新页面**（不重新登录）即可见新模块
- [ ] 管理员行整行禁用（天然全权限）

---

# 需求 2 · 模型全可配

**现状**：`SystemConfig` 表里有 `llm_config`（半成品），已有 5 个连通性测试端点（`test-connectivity/llm|embedding|milvus|neo4j|vl`），**但配置是全局单例，无法按项目/按类型多套配置**。

## Step 2.1 适配层：`ProviderResolver`（修缺陷 #7 #8 #12）

```python
# app/adapters/provider.py
SEMANTICA_PROVIDER_ALIASES = {
    "huggingface": "huggingface_llm",   # ★ 缺陷 #7：实际注册名带 _llm
    "hf": "huggingface_llm",
    "local": "ollama",
    "openai_compatible": "openai",
    "qwen": "openai",                   # 通义走 OpenAI 兼容协议
    "zhipu": "openai",
    "deepseek": "deepseek",
}

class ProviderResolver:
    """DB 配置 → Semantica provider 对象。永远显式传 model，不依赖默认值（缺陷 #8）。"""

    def __init__(self, crypto):
        self.crypto = crypto

    def resolve_llm(self, cfg: ModelConfig):
        from semantica.semantic_extract.providers import (
            OpenAIProvider, AnthropicProvider, OllamaProvider,
            DeepSeekProvider, HuggingFaceLLMProvider, GeminiProvider,
            GroqProvider, NovitaProvider,
        )
        REGISTRY = {
            "openai": OpenAIProvider, "anthropic": AnthropicProvider,
            "ollama": OllamaProvider, "deepseek": DeepSeekProvider,
            "huggingface_llm": HuggingFaceLLMProvider, "gemini": GeminiProvider,
            "groq": GroqProvider, "novita": NovitaProvider,
        }
        provider = SEMANTICA_PROVIDER_ALIASES.get(cfg.provider, cfg.provider)
        cls = REGISTRY.get(provider)
        if cls is None:
            raise ProviderConfigError(
                f"未知 provider: {cfg.provider}；可用: {sorted(REGISTRY)}")

        kwargs = {"model": cfg.model_name, **(cfg.params or {})}   # ★ 显式 model
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if cfg.api_key_encrypted:
            kwargs["api_key"] = self.crypto.decrypt(cfg.api_key_encrypted)
        if provider == "ollama" and not cfg.base_url:
            raise ProviderConfigError("ollama 必须提供 base_url")

        try:
            return cls(**kwargs)
        except TypeError as e:
            raise ProviderConfigError(
                f"provider={provider} 不接受参数：{e}；支持字段见 GET /api/model-configs/providers"
            ) from e

    def probe_embedding_dimension(self, cfg: ModelConfig) -> int:
        """★ 缺陷 #12：写入配置前探测真实维度，避免 768/384/512 不匹配。"""
        emb = self.resolve_embedding(cfg)
        vec = emb.embed_query("维度探测")
        return len(vec)
```

## Step 2.2 后端 API

```
GET    /api/model-configs                 列表（全局 + 我有权的项目）
POST   /api/model-configs                 新建（global 作用域需 admin）
GET    /api/model-configs/{id}            详情（API Key 脱敏为 sk-***4a2f）
PATCH  /api/model-configs/{id}            修改
DELETE /api/model-configs/{id}            删除
POST   /api/model-configs/{id}/test       连通性测试（复用现有 test-connectivity 逻辑）
POST   /api/model-configs/{id}/set-default
GET    /api/model-configs/providers       ★ provider 字段 schema（前端动态渲染表单）
GET    /api/model-configs/usage           调用统计
```

`GET /model-configs/providers` 响应（前端据此渲染，**不要硬编码字段**）：

```json
[
  {"provider": "ollama", "label": "Ollama（本地）", "kinds": ["llm","embedding"],
   "requires": ["base_url","model_name"], "optional": [],
   "default_base_url": "http://host.docker.internal:11434",
   "hint": "本地部署，无需 API Key"},
  {"provider": "huggingface_llm", "label": "HuggingFace", "kinds": ["llm"],
   "requires": ["api_key","model_name"], "optional": ["base_url"],
   "hint": "⚠️ 抽取层实际注册名是 huggingface_llm，平台已自动映射"}
]
```

## Step 2.3 API Key 加密

```python
# app/core/crypto.py（新建）
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class SecretCrypto:
    """AES-256-GCM。密钥来自环境变量，不入库。"""
    def __init__(self, key_b64: str):
        self.key = base64.b64decode(key_b64)      # 32 字节
        if len(self.key) != 32:
            raise ValueError("SECRET_ENCRYPTION_KEY 必须是 base64 的 32 字节")

    def encrypt(self, plain: str) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self.key).encrypt(nonce, plain.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, blob: str) -> str:
        raw = base64.b64decode(blob)
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], None).decode()
```

`.env` 加 `SECRET_ENCRYPTION_KEY=<base64 32 字节>`。

## Step 2.4 前端：动态表单

`ModelConfigPage.tsx` 的字段由 `/providers` 驱动：

```tsx
const { data: providers } = useQuery({ queryKey: ["providers"], queryFn: fetchProviders });
const spec = providers?.find(p => p.provider === form.provider);
// 按 spec.requires / spec.optional 动态渲染
```

**关键提示**：切换 embedding 模型时，若 `dimension` 变化 → 弹确认框「向量维度从 384 变为 1024，需重建索引（预计 12 分钟）」，并提供重建按钮。

## 验收

- [ ] UI 新增 Ollama 配置 → 测试连接 ✅ 延迟 < 500ms
- [ ] 设为项目默认 → 立刻发起抽取 → 成功，无重启
- [ ] 用 `provider="huggingface"` → 平台自动映射，不报错
- [ ] 切换 embedding 384→1024 → 弹确认框，重建后检索正常
- [ ] 直连 DB 查 `api_key_encrypted` → 密文

---

# 需求 3 · 文件上传与解析（多格式 + 扫描件 + 切片）

**现状**：`parser.py` 用 PyMuPDF/pymupdf4llm/docx/pptx/pandas，`vl_parser.py` 用视觉模型，**无 OCR**；切片分隔符有中文 bug；`UploadedDocument.text_content` LONGTEXT 存解析文本（应进对象存储）。

## Step 3.1 换解析器：Docling + OCR（修缺陷 #9）

```python
# app/adapters/parsing.py
PLATFORM_FORMATS = {
    # 文档
    "pdf": "docling", "docx": "docling", "doc": "docling", "pptx": "docling",
    "xlsx": "docling", "xls": "docling", "rtf": "docling", "epub": "docling",
    "txt": "plain", "md": "plain", "html": "docling", "htm": "docling",
    "csv": "tabular", "tsv": "tabular",
    "json": "structured", "jsonl": "structured", "xml": "structured",
    # 图片（OCR）
    "png": "docling_ocr", "jpg": "docling_ocr", "jpeg": "docling_ocr",
    "bmp": "docling_ocr", "tiff": "docling_ocr", "tif": "docling_ocr",
    "webp": "docling_ocr",
}

def parse_document(file_path: str, *, force_ocr: bool = False) -> ParseResult:
    """
    ⚠️ 缺陷 #9：不要用 Semantica 的 DocumentParser（只认 pdf/docx/html/txt）。
    平台自建分发器。
    """
    ext = Path(file_path).suffix.lower().lstrip(".")
    kind = PLATFORM_FORMATS.get(ext)
    if kind is None:
        raise UnsupportedFormat(ext, allowed=sorted(PLATFORM_FORMATS))

    if kind == "plain":
        text = decode_text(Path(file_path).read_bytes())   # UTF-8 → GBK → GB18030 → UTF-16
        return ParseResult(text=text, blocks=[], is_scanned=False, parser="plain")

    if kind == "tabular":
        return parse_tabular(file_path)          # 保留表头 + 行号
    if kind == "structured":
        return parse_structured(file_path)

    # docling / docling_ocr
    from semantica.parse import DoclingParser
    enable_ocr = force_ocr or kind == "docling_ocr"
    parser = DoclingParser(enable_ocr=enable_ocr, export_format="markdown")
    result = parser.parse(file_path)

    # 扫描件自动判定 + 回退
    text = result.get("text", "") if isinstance(result, dict) else str(result)
    pages = text.count("\f") + 1 or 1
    if not enable_ocr and len(text) / pages < 50:      # 每页 < 50 字符 → 判定扫描件
        parser = DoclingParser(enable_ocr=True, export_format="markdown")
        result = parser.parse(file_path)
        text = result.get("text", "") if isinstance(result, dict) else str(result)
        is_scanned = True
    else:
        is_scanned = False

    return ParseResult(text=text, blocks=result.get("blocks", []),
                       is_scanned=is_scanned, parser="docling")
```

## Step 3.2 切片：修中文缺陷（修 #1 #2 #3）

```python
# app/adapters/chunking.py
import re, unicodedata

_END = "。！？!?…"
_CLOSERS = "”’」』）)】》"


def split_sentences_zh(text: str) -> list[str]:
    """
    ⚠️ 缺陷 #1：Semantica 的 _split_sentences_regex 是 re.split(r"(?<=[.!?])\\s+", text)
       只认半角 .!? 且要求后跟空白 → 中文整段返回 1 句。
    ⚠️ 缺陷 #2：split_by_sentences 硬编码 en_core_web_sm。
    结论：中文一律走这个状态机。
    """
    parts, buf, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in _END:
            while i + 1 < n and text[i + 1] in _END:        # 吞连续标点（"……" "？！"）
                i += 1; buf.append(text[i])
            while i + 1 < n and text[i + 1] in _CLOSERS:    # 吞收尾引号/括号
                i += 1; buf.append(text[i])
            parts.append("".join(buf).strip()); buf = []
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def safe_chunk_params(chunk_size: int, chunk_overlap_pct: int) -> tuple[int, int]:
    """
    ⚠️ 缺陷 #3：Semantica 的 split_recursive 用
       start = max(start + 1, split_pos - chunk_overlap)
       当 chunk_overlap >= chunk_size 时每次只前进 1 字符（实测 204 字 → 631 块）。
    强制 overlap <= 20%。
    """
    chunk_size = max(200, int(chunk_size))
    overlap_chars = int(chunk_size * min(chunk_overlap_pct, 20) / 100)
    return chunk_size, overlap_chars


CHUNK_DEFAULTS = {
    "zh": {"chunk_size": 2000, "chunk_overlap_pct": 12},   # ★ 原平台 15000 太大
    "en": {"chunk_size": 3000, "chunk_overlap_pct": 12},
}


def chunk_text(text: str, *, language: str = "zh",
               chunk_size: int | None = None,
               chunk_overlap_pct: int | None = None,
               filename: str = "unknown") -> list[dict]:
    """
    中文感知切片。返回 [{"text", "filename", "chunk_index", "page_no"}]。
    ★ 中文用自研分句，不用 Semantica 的 sentence 方法。
    """
    lang = "zh" if language.startswith("zh") else "en"
    defaults = CHUNK_DEFAULTS[lang]
    size = chunk_size or defaults["chunk_size"]
    pct = chunk_overlap_pct if chunk_overlap_pct is not None else defaults["chunk_overlap_pct"]
    size, overlap = safe_chunk_params(size, pct)

    if lang == "zh":
        sentences = split_sentences_zh(text)
        # 按句子贪心装箱（不切断句子）
        chunks, buf, cur_len = [], [], 0
        for s in sentences:
            if cur_len + len(s) > size and buf:
                chunks.append("".join(buf))
                # 带 overlap：回退最后若干句
                tail, tail_len = [], 0
                for prev in reversed(buf):
                    if tail_len + len(prev) > overlap:
                        break
                    tail.insert(0, prev); tail_len += len(prev)
                buf, cur_len = tail, tail_len
            buf.append(s); cur_len += len(s)
        if buf:
            chunks.append("".join(buf))
    else:
        chunks = _recursive_split(text, size, overlap,
                                  ["\n\n", "\n", ". ", "! ", "? ", " ", ""])

    return [{"text": c, "filename": filename, "chunk_index": i}
            for i, c in enumerate(chunks) if c.strip()]
```

## Step 3.3 解析文本改存对象存储

**现状**：`UploadedDocument.text_content = LONGTEXT` → 1000 篇文档 ≈ 1GB 塞进 MySQL。

**改动**：加 MinIO（或复用现有对象存储），`text_content` 改为 `text_key`（存路径），保留一列 `text_preview`（前 2000 字符）供列表页预览。

```python
class UploadedDocument(Base):
    ...
    text_key = Column(String(500), nullable=True)        # ★ 新增：对象存储路径
    text_preview = Column(String(2000), nullable=True)   # ★ 新增：前 2000 字
    text_content = Column(LONGTEXT, nullable=True)       # 保留兼容，新数据不再写入
```

## Step 3.4 秒传

```python
@router.post("/{project_id}/documents/check")
def check_duplicate(project_id: int, body: CheckIn):
    """传 sha256 + size，返回是否已存在。"""
    doc = db.query(UploadedDocument).filter(
        UploadedDocument.project_id == project_id,
        UploadedDocument.sha256 == body.sha256).first()
    return {"exists": bool(doc), "document_id": doc.id if doc else None}
```

`UploadedDocument` 加 `sha256` 列 + 索引。

## Step 3.5 VL 解析优化（修缺陷 #14）

```python
# vl_parser.py 改造
def _pages_needing_vl(pdf_path: str, min_chars_per_page: int = 50) -> list[int]:
    """★ 只渲染文本层为空的页 —— 300 页文档通常只有 20-30 页需要 VL。"""
    doc = fitz.open(pdf_path)
    need = []
    for i, page in enumerate(doc):
        if len(page.get_text("text").strip()) < min_chars_per_page:
            need.append(i)
    doc.close()
    return need

# 批次并发（原来 for 循环串行）
results = await asyncio.gather(*[_vl_batch(b, prompt, cfg) for b in batches])
results.sort(key=lambda r: r[0])      # 按 batch_start 排序后拼接，保持页序

# base64 → URL（若 VL 服务能访问对象存储）
# 不能则降 DPI 到 150 + JPEG q85 替代 PNG
```

## 验收

- [ ] 上传 32 页中文 PDF → 切片 128 个（原来会切错）
- [ ] 上传 240 页扫描版 PDF → 自动 OCR 成功，`is_scanned=true`
- [ ] 上传单张 PNG 发票 → OCR 出文字
- [ ] 上传 GBK 编码 txt → 无乱码
- [ ] 上传 `.mp4` → 415 明确提示
- [ ] 重复上传同文件 → 秒传命中
- [ ] 中文切片抽样 20 个 → 句末标点收尾率 ≥ 95%

---

# 需求 4 · 分层构建本体（最核心）

**现状**：两阶段已跑通（`extract-schema` → 画布审核 → `extract-instances`），但：
- Schema 闸门**不校验** `cardinality`、`data_type`、`required`
- 消解只有坏的 `names_are_similar` + 前缀合并
- 无冲突检测、无版本、无审核队列、无时间轴

## Step 4.1 保留现有交互，不动

`api/ontology.py` 的 4 个端点保持签名不变：

```
POST /{project_id}/extract-schema
POST /{project_id}/extract-schema-from-documents
POST /{project_id}/extract-instances
POST /{project_id}/extract-instances-from-documents
```

前端 `OntologyBuilderPage.tsx` 的 Step1/Step2 流程不动。

## Step 4.2 升级 Schema 闸门（补 cardinality / data_type / required）

```python
# app/adapters/schema_gate.py
class SchemaGate:
    """
    把「用户审核后的 schema_graph」变成抽取的硬闸门。
    原平台已有：类型校验 + domain/range（含继承）。
    ★ 本步补：cardinality、data_type、required。
    """

    def __init__(self, schema_graph: dict, *, policy: str = "strict"):
        self.classes = schema_graph.get("object_types", schema_graph.get("classes", []))
        self.links = schema_graph.get("link_types", schema_graph.get("object_properties", []))
        self.policy = policy          # strict | lenient | promote
        self._build_indexes()

    def _build_indexes(self):
        self.class_ids = {c.get("id") or c.get("name") for c in self.classes}
        self.class_by_id = {c.get("id") or c.get("name"): c for c in self.classes}
        # label → id
        self.label_to_id = {}
        for c in self.classes:
            for k in (c.get("label"), c.get("name")):
                if k:
                    self.label_to_id[k] = c.get("id") or c.get("name")
        # 继承链
        self.ancestors = {}
        for c in self.classes:
            cid = c.get("id") or c.get("name")
            chain, cur = {cid}, c.get("sub_class_of")
            seen = set()
            while cur and cur not in seen:
                seen.add(cur)
                pid = self.label_to_id.get(cur, cur)
                if pid in self.class_ids:
                    chain.add(pid)
                    cur = self.class_by_id[pid].get("sub_class_of")
                else:
                    break
            self.ancestors[cid] = chain
        # 关系约束
        self.op_constraints = {}
        for lt in self.links:
            oid = lt.get("id") or lt.get("name")
            src = self.label_to_id.get(lt.get("source_object_type"), lt.get("source_object_type"))
            tgt = self.label_to_id.get(lt.get("target_object_type"), lt.get("target_object_type"))
            self.op_constraints[oid] = {
                "domain": src, "range": tgt,
                "cardinality": lt.get("cardinality"),        # ★ 新增
            }
        self.op_label_to_id = {lt.get("label"): (lt.get("id") or lt.get("name"))
                               for lt in self.links if lt.get("label")}
        # 属性定义（含类型）
        self.prop_defs = {}
        for c in self.classes:
            cid = c.get("id") or c.get("name")
            defs = {}
            for p in c.get("properties", []) or []:
                if isinstance(p, dict):
                    defs[p.get("name")] = {"data_type": p.get("data_type", "string"),
                                           "required": bool(p.get("required"))}
                elif isinstance(p, str):
                    defs[p] = {"data_type": "string", "required": False}
            self.prop_defs[cid] = defs

    def gate_entities(self, entities: list[dict]) -> tuple[list[dict], list[dict]]:
        valid, rejected = [], []
        for e in entities:
            raw_type = (e.get("type") or "").strip()
            cid = raw_type if raw_type in self.class_ids else self.label_to_id.get(raw_type)
            if not cid:
                if self.policy == "promote":
                    valid.append(e); rejected.append({"item": e, "reason": "new_concept"})
                else:
                    rejected.append({"item": e, "reason": "type_not_in_schema"})
                continue
            e["type"] = cid
            # ★ 新增：data_props 值类型校验
            ok, bad = self._validate_data_props(cid, e.get("data_props") or {})
            if bad:
                rejected.append({"item": e, "reason": "datatype_mismatch", "detail": bad})
            e["data_props"] = ok
            # ★ 新增：required 校验
            missing = [k for k, v in self.prop_defs.get(cid, {}).items()
                       if v["required"] and k not in ok]
            if missing:
                rejected.append({"item": e, "reason": "missing_required", "detail": missing})
            valid.append(e)
        return valid, rejected

    def _validate_data_props(self, cid: str, props: dict) -> tuple[dict, list]:
        defs = self.prop_defs.get(cid, {})
        ok, bad = {}, []
        for k, v in props.items():
            dt = defs.get(k, {}).get("data_type", "string")
            if self._coerce(v, dt) is not None:
                ok[k] = self._coerce(v, dt)
            else:
                bad.append({"key": k, "value": v, "expected": dt})
        return ok, bad

    @staticmethod
    def _coerce(v, dt: str):
        if v is None or v == "":
            return None
        try:
            if dt in ("number", "integer", "float", "double"):
                return float(re.sub(r"[^\d.\-]", "", str(v)))
            if dt == "boolean":
                return str(v).strip().lower() in ("true", "1", "是", "yes", "y")
            if dt in ("date", "datetime"):
                return str(v).strip()
            if dt in ("array", "object"):
                return v if not isinstance(v, str) else json.loads(v)
            return str(v)
        except Exception:
            return None

    def gate_relations(self, relations: list[dict]) -> tuple[list[dict], list[dict]]:
        """校验谓词存在 + domain/range + ★ cardinality。"""
        valid, rejected = [], []
        for r in relations:
            rid = r.get("predicate") or r.get("link_type")
            rid = rid if rid in self.op_constraints else self.op_label_to_id.get(rid)
            if not rid:
                rejected.append({"item": r, "reason": "predicate_not_in_schema"}); continue
            spec = self.op_constraints[rid]
            st, tt = r.get("source_type"), r.get("target_type")
            st = st if st in self.class_ids else self.label_to_id.get(st)
            tt = tt if tt in self.class_ids else self.label_to_id.get(tt)
            if spec["domain"] and st not in self.ancestors.get(st, set()) | {spec["domain"]}:
                rejected.append({"item": r, "reason": "domain_violation",
                                 "detail": f"{st} -[{rid}]-> {tt}, 期望 domain={spec['domain']}"})
                continue
            if spec["range"] and tt not in self.ancestors.get(tt, set()) | {spec["range"]}:
                rejected.append({"item": r, "reason": "range_violation",
                                 "detail": f"{st} -[{rid}]-> {tt}, 期望 range={spec['range']}"})
                continue
            r["predicate"] = rid
            valid.append(r)

        # ★ 新增：cardinality 检查（1:1 / 1:N 的基数约束）
        valid = self._enforce_cardinality(valid, rejected)
        return valid, rejected

    def _enforce_cardinality(self, relations, rejected):
        """对 one-to-one / many-to-one 的谓词，检查是否违反基数。"""
        from collections import defaultdict
        out = []
        by_pred = defaultdict(list)
        for r in relations:
            by_pred[r["predicate"]].append(r)
        for rid, rels in by_pred.items():
            card = self.op_constraints[rid].get("cardinality")
            if card in ("one-to-one", "one-to-many"):
                seen_source = defaultdict(set)
                for r in rels:
                    seen_source[r["source_det_id"]].add(r["target_det_id"])
                for r in rels:
                    if len(seen_source[r["source_det_id"]]) > 1:
                        rejected.append({"item": r, "reason": "cardinality_violation",
                                         "detail": f"{rid} 是 {card}，但源 {r['source_det_id']} 有多个目标"})
                        continue
                    out.append(r)
            else:
                out.extend(rels)
        return out
```

**`promote` 策略是闭环关键**：ABox 抽出的、TBox 里没有的类型会被收集起来，作为**下一轮 TBox 迭代的候选**呈现给用户。这样"分层"不是一次性的，而是可迭代收敛的。

## Step 4.3 接 DuplicateDetector 做消解

**替换** `merge_instances_to_graph_data` 里的坏前缀合并 + `names_are_similar`。

```python
# app/adapters/resolution.py
class DedupResolver:
    """
    替换原平台的 names_are_similar（对中文是恒等变换）+ get_instance_prefix（会误合并）。
    """
    MODES = {
        "blocking_v2": "同类型 + 拼音首字母/双字 shingle 分块，块内比对（快）",
        "hybrid_v2":   "规则 + 向量 + 图结构（推荐）",
        "semantic_v2": "纯向量（简单，中文短实体易误合并）",
    }

    def __init__(self, embedder=None):
        from semantica.deduplication import DuplicateDetector
        self.embedder = embedder
        self._detector = None

    def resolve(self, entities: list[dict], *, mode: str = "hybrid_v2",
                auto_threshold: float = 0.85,
                review_threshold: float = 0.60) -> dict:
        # 0) 先做确定性归一（覆盖 60% 场景，零成本）
        norm_groups = defaultdict(list)
        for e in entities:
            norm_groups[normalize_entity(e["label"])].append(e)

        auto_merges, review_items, kept = [], [], []
        for key, group in norm_groups.items():
            if len(group) == 1:
                kept.append(group[0]); continue
            types = {g.get("type") for g in group}
            if len(types) > 1:
                review_items.append({"group": group, "score": 1.0,
                                     "reason": "type_conflict",
                                     "detail": f"归一后同名但类型不同：{types}"})
            else:
                auto_merges.append({"group": group, "score": 1.0, "reason": "exact_norm"})

        # 1) 剩下的走 Semantica 的 DuplicateDetector
        candidates = kept
        if len(candidates) > 1:
            from semantica.deduplication import DuplicateDetector
            detector = DuplicateDetector(mode=mode, embedding_provider=self.embedder)
            pairs = detector.detect_duplicates(candidates, threshold=review_threshold)
            for p in pairs:
                score = p.get("similarity", 0.0)
                if score >= auto_threshold:
                    auto_merges.append({"group": [p["entity1"], p["entity2"]],
                                        "score": score, "reason": "semantic_auto"})
                else:
                    review_items.append({"group": [p["entity1"], p["entity2"]],
                                         "score": score, "reason": "ambiguous_merge"})

        return {"auto_merges": auto_merges, "review_items": review_items, "kept": kept}
```

**中文消解的三个特化处理**（必须加）：

```python
def normalize_entity(name: str) -> str:
    """确定性归一。★ 绝对不动数字（原平台的 bug 就是去尾部数字）。"""
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"\s+", "", s)                                  # 去所有空白
    s = re.sub(r"[（）()【】\[\]「」]", "", s)                    # 去括号
    for suf in ("股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "公司", "集团"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s


def pinyin_blocking_key(name: str) -> str:
    """★ 中文无空格，首字符分块效果差。用拼音首字母。"""
    from pypinyin import lazy_pinyin
    return "".join(p[0] for p in lazy_pinyin(name) if p)
```

**短实体特化**：`len(name) < 6` 时**降低向量权重**（中文向量模型对短实体区分度低），提高规则权重。

## Step 4.4 接 ConflictDetector

```python
# app/adapters/resolution.py（续）
class ConflictAnalyzer:
    def __init__(self):
        from semantica.conflicts import ConflictDetector, ConflictResolver
        self.detector = ConflictDetector()
        self.resolver = ConflictResolver()

    def analyze(self, entities: list[dict], relations: list[dict]) -> dict:
        conflicts = []
        conflicts += self.detector.detect_type_conflicts(entities)          # 同一实体多类型
        conflicts += self.detector.detect_relationship_conflicts(relations) # 互斥关系
        conflicts += self.detector.detect_temporal_conflicts(entities)      # valid_from > valid_until
        conflicts += self.detector.detect_logical_conflicts(entities)

        # 属性值冲突：同实体同属性不同值
        by_id = defaultdict(list)
        for e in entities:
            by_id[e["det_id"]].append(e)
        for det_id, group in by_id.items():
            for prop in (group[0].get("data_props") or {}):
                vals = {str(g.get("data_props", {}).get(prop)) for g in group}
                if len(vals) > 1:
                    conflicts.append({"type": "value_conflict", "entity": det_id,
                                      "property": prop, "values": sorted(vals)})

        # 分类：可自动解决的 vs 必须转人工的
        auto, manual = [], []
        for c in conflicts:
            if c.get("type") == "temporal_conflict":
                auto.append(c)        # 时间冲突可自动修正（取左闭右开）
            elif c.get("type") == "duplicate_id":
                auto.append(c)        # 唯一性冲突可自动合并
            else:
                manual.append(c)      # 类型/属性/关系冲突 → 转人工
        return {"auto": auto, "manual": manual, "total": len(conflicts)}
```

## Step 4.5 接 ProvenanceManager 做溯源

**替换** `inst["_source_quote"] = chunk_text[:200]`。

```python
# app/adapters/provenance.py
class ProvenanceTracker:
    """封装 Semantica 的 ProvenanceManager（W3C PROV-O）。"""

    def __init__(self, storage_path: str = "./provenance"):
        from semantica.provenance import ProvenanceManager
        self.mgr = ProvenanceManager(storage_path=storage_path)

    def track_extraction(self, entities, relations, *, document_id, filename,
                         chunk_index, page_no, chunk_text, model_config_id):
        for e in entities:
            self.mgr.track_entity(
                entity_id=e["det_id"],
                source={"document_id": document_id, "filename": filename,
                        "chunk_index": chunk_index, "page_no": page_no,
                        "quote": locate_quote(chunk_text, e["label"]),   # ★ 证据原句
                        "char_start": e.get("char_start"), "char_end": e.get("char_end")},
                metadata={"model_config_id": model_config_id,
                          "confidence": e.get("confidence")},
            )
        for r in relations:
            self.mgr.track_relationship(
                relationship_id=r["det_id"],
                source={"document_id": document_id, "chunk_index": chunk_index},
                metadata={"confidence": r.get("confidence")},
            )

    def track_manual_edit(self, subject_type, subject_id, user_id, before, after):
        self.mgr.track_entity(
            entity_id=subject_id,
            source={"activity": "manual_edit", "agent_type": "user", "agent_id": user_id},
            metadata={"before": before, "after": after},
        )

    def export_prov_o(self, fmt: str = "turtle") -> str:
        return self.mgr.export_prov(format=fmt)


def locate_quote(chunk_text: str, label: str, window: int = 80) -> str:
    """★ 用实体名在 chunk 里定位，取上下文窗口。找不到才退回开头。"""
    idx = chunk_text.find(label)
    if idx < 0:
        return chunk_text[:200]
    return chunk_text[max(0, idx - window): idx + len(label) + window]
```

**同时改 prompt**：在 `INSTANCE_EXTRACTION_JSON_SCHEMA` 的 instances 项加 `evidence` 字段，让 LLM 逐字引用原句：

```python
"evidence": {"type": "string",
             "description": "该实例在原文中出现的原句（必须逐字引用，不超过100字）"}
```

prompt 约束加一条：`"7. 【溯源】每个实例必须提供 evidence 字段，逐字引用原文中包含该实体的句子。"`

## Step 4.6 加审核队列与状态机

```python
# app/api/review.py（新建）
GET    /projects/{pid}/review/queue             审核队列（按 priority 排序）
GET    /projects/{pid}/review/queue/stats       各原因分类计数
POST   /projects/{pid}/review/items/{iid}/claim  认领
POST   /projects/{pid}/review/items/{iid}/approve
POST   /projects/{pid}/review/items/{iid}/reject
POST   /projects/{pid}/review/items/{iid}/edit   改后通过
POST   /projects/{pid}/review/bulk               批量（≤100 条）
POST   /projects/{pid}/review/assign             指派
```

**审核触发条件与优先级**：

| reason | 触发条件 | priority |
|---|---|---|
| `manual_flag` | 用户手动标记 | 50 |
| `llm_hallucination_suspect` | 实体在原文中回标失败 | 40 |
| `ambiguous_merge` | 消解相似度 0.60–0.85 | 30 |
| `type_conflict` | 同实体多类型 | 30 |
| `low_confidence` | `confidence < min_confidence` | 20 |
| `cardinality_violation` | 违反基数约束 | 20 |
| `domain_range_violation` | 关系不符合 TBox | 20 |
| `new_concept` | `promote` 策略下的新类型 | 10 |

**状态机**：`pending → claimed → approved | rejected | edited`

**前端 `ReviewPage.tsx`（新建）** —— 卡片流 + 键盘流：

| 快捷键 | 动作 |
|---|---|
| `J` / `↓` | 下一项 |
| `K` / `↑` | 上一项 |
| `A` | 通过 |
| `R` | 驳回（弹理由） |
| `E` | 改后通过 |
| `M` | 合并到… |
| `S` | 跳过 |
| `Space` | 展开完整上下文 |

审核卡片必须展示**证据**（来自 `provenance_records.quote`）+ 本体约束提示。

## Step 4.7 加版本控制与时间轴

```python
# app/adapters/versioning.py
class VersionService:
    """封装 TemporalVersionManager + TemporalGraphQuery。"""

    def __init__(self, storage_path: str = "./versions"):
        from semantica.change_management import TemporalVersionManager
        self.mgr = TemporalVersionManager(storage_path=storage_path)

    def create_snapshot(self, graph: dict, label: str, author: str, description: str):
        return self.mgr.create_snapshot(graph, version_label=label,
                                        author=author, description=description)

    def diff(self, a: str, b: str):
        return self.mgr.diff(a, b)

    def rollback(self, graph: dict, target: str):
        return self.mgr.restore_snapshot(graph, target_version=target,
                                         require_confirmation=False)


class TemporalService:
    """封装 TemporalGraphQuery。
    ⚠️ 区间语义是左闭右开 [valid_from, valid_until)：
       kg/temporal_reasoning.py:113
       return start <= point and (end is TemporalBound.OPEN or point < self._coerce_datetime(end))
       即 valid_until="2017-12-31" 的关系在 12-30 存在、在 12-31 当天消失。
    ⚠️ 四个坑（已实测）：
       1. query 参数是空操作，不参与过滤，要自己筛返回列表
       2. include_history=True 会产生孤儿关系
       3. 实体是别名（与输入图同一批对象，无拷贝），关系才是 deepcopy
       4. 需自己处理时区
    """

    def __init__(self):
        from semantica.kg import TemporalGraphQuery
        self.engine = TemporalGraphQuery(enable_temporal_reasoning=True)

    def snapshot_at(self, graph, at: datetime) -> dict:
        result = self.engine.query_at_time(graph, query="", at_time=at)
        return self._to_payload(result)

    def evolution(self, graph, entity_key: str):
        return self.engine.analyze_evolution(graph, entity_key)
```

**API**：

```
GET  /projects/{pid}/ontology/versions                      版本列表
GET  /projects/{pid}/ontology/versions/{vid}                详情（含 TTL）
GET  /projects/{pid}/ontology/versions/{vid}/ttl            下载 TTL
GET  /projects/{pid}/ontology/versions/{vid}/diff?against=  版本 diff
PATCH /projects/{pid}/ontology/versions/{vid}               手动编辑 TTL（草稿态才可改）
POST /projects/{pid}/ontology/versions/{vid}/submit         提交审核
POST /projects/{pid}/ontology/versions/{vid}/approve        审核通过
POST /projects/{pid}/ontology/versions/{vid}/reject         驳回
POST /projects/{pid}/ontology/versions/{vid}/rollback       回滚
GET  /projects/{pid}/graph/at?at=...                        时点快照
GET  /projects/{pid}/graph/range?start=&end=                区间查询
GET  /projects/{pid}/graph/evolution?subject=               实体演化
GET  /projects/{pid}/timeline                               时间轴事件流
```

**前端 `TimelinePage.tsx`（新建）**：用 `vis-timeline`（Semantica Explorer 同款），三条泳道：本体版本 / 抽取运行 / 发布。时点滑块拖动 → 300ms debounce 查 `graph/at`。

## Step 4.8 升级发布机制

**现状**：`is_published` 布尔。**改为** `publications` 表 + 快照固化。

```python
@router.post("/{project_id}/publish")
def publish(project_id: int, body: PublishIn,
            user: User = Depends(require_module("publish"))):
    db = SessionLocal()
    try:
        version = db.query(OntologyVersion).filter(
            OntologyVersion.id == body.ontology_version_id).first()
        if not version or version.status != "approved":
            raise HTTPException(409, detail={
                "code": "VERSION_NOT_APPROVED",
                "message": "只有已审核通过的本体版本可以发布"})

        pub = Publication(
            project_id=project_id,
            ontology_version_id=version.id,
            include_instances=body.include_instances,
            only_approved=body.only_approved,
            snapshot_ttl=version.ttl_content,          # ★ 快照固化，不引用活数据
            published_by=user.id, is_active=True,
        )
        db.add(pub)
        # 兼容旧字段
        project = db.query(Project).filter(Project.id == project_id).first()
        project.is_published = True
        db.commit()
        return {"publication_id": pub.id}
    finally:
        db.close()
```

**只读强制（双层）**：

```python
async def assert_not_published(db, project_id: int):
    pub = db.query(Publication).filter(
        Publication.project_id == project_id,
        Publication.is_active.is_(True),
        Publication.unpublished_at.is_(None)).first()
    if pub:
        raise HTTPException(403, detail={
            "code": "PUBLISHED_READONLY",
            "message": "该项目已发布到公共区，编辑前请先取消发布",
            "publication_id": pub.id})
```

所有写接口第一步调它。前端公共区页面**不渲染任何编辑控件**。

## 验收

- [ ] 20 篇中文合同 → draft TBox ≥ 15 类，带 zh label
- [ ] 未审核版本发起 ABox → 409
- [ ] 312 篇 ABox 构建 9 阶段全过
- [ ] 抽查 20 个实体 → 100% 有溯源，且 `quote` 确实包含该实体
- [ ] `cardinality=one-to-one` 的关系出现一对多 → 被拦截并记录
- [ ] `data_type=number` 的属性收到 `"十万元"` → 被拒或转人工
- [ ] 中文消解：`北京华信科技有限公司` / `北京华信科技` 合并；`北京市` / `北京市政府` **不**合并
- [ ] `合同2024` / `合同2025` **不**合并（P0 回归）
- [ ] 审核队列按优先级排序，快捷键 A/R/E 生效
- [ ] 回滚本体到 v0.3.0 成功，产生新版本记录（不删历史）
- [ ] 查 2026-06-15 时点快照正确
- [ ] 发布后写操作 403 `PUBLISHED_READONLY`

---

# 需求 5 · 可视化与多格式导出

## Step 5.1 图谱重构 → 见独立文档《图谱展示重构方案.md》

## Step 5.2 多格式导出

**现状**：只有 `turtle`。

```python
# app/adapters/exporting.py
EXPORT_FORMATS = {
    "rdf":     {"turtle": "export_to_turtle", "rdfxml": "export_to_rdfxml",
                "ntriples": "export_to_ntriples", "jsonld": "export_to_jsonld",
                "owl": "export_to_owl"},
    "graph":   {"graphml": "export_to_graphml", "lpg": "export_to_lpg",
                "neo4j_csv": "export_to_neo4j_csv", "arango": "export_to_arango",
                "json": "export_to_json"},
    "tabular": {"parquet": "export_to_parquet", "arrow": "export_to_arrow",
                "csv": "export_to_csv"},
    "other":   {"yaml": "export_to_yaml"},
}


def export_graph(graph_data: dict, fmt: str, *, out_dir: str = "./exports",
                 options: dict | None = None) -> str:
    """★ 格式名以 EXPORT_FORMATS 为准，不要用 Semantica 的
       MULTI_FILE_FORMATS = ("arrow","csv","parquet")（那只覆盖 3 种）。"""
    import semantica.export as E
    if fmt not in {f for group in EXPORT_FORMATS.values() for f in group}:
        raise ValueError(f"不支持的导出格式: {fmt}")

    fn_name = next(fn for group in EXPORT_FORMATS.values() for k, fn in group.items() if k == fmt)
    fn = getattr(E, fn_name, None)
    if fn is None:
        raise AdapterError(f"Semantica 未提供 {fn_name}")
    return fn(graph_data, out_dir, **(options or {}))
```

**导出是异步任务**（可能 18MB+），产物存对象存储，返回签名 URL。

**API**：

```
GET  /projects/{pid}/exports/formats     支持的格式（从 EXPORT_FORMATS 拉）
POST /projects/{pid}/exports             发起导出（异步）
GET  /projects/{pid}/exports             导出历史
GET  /projects/{pid}/exports/{eid}/download
```

**前端 `ExportPage.tsx`** —— `<FormatPicker>` 按类别分组渲染（从 API 拉，不硬编码）。

## 验收

- [ ] 导出 `turtle` / `parquet` / `graphml` / `owl` / `jsonld` 全部成功
- [ ] Turtle 用 Protégé 能打开，类数一致
- [ ] GraphML 用 Gephi 能打开
- [ ] 同时发起 3 个导出，各自独立完成

---

# 需求 6 · 四存储持久化

**现状**：MySQL ✅ + Neo4j ✅ + Milvus ✅ + Elasticsearch ✅（`services/inject_service.py` 用于向外部 RAGFlow 注入）。**缺**：独立 RDF triple store、跨存储一致性。

> **向量库选型确认**：用 **Milvus**，不加 Qdrant。依据：① Semantica 自带 `semantica/vector_store/milvus_store.py`（`MilvusStore` 已导出）；② 平台已跑 Milvus v2.3.5（含 etcd + MinIO + attu），无需引入第二套向量库；③ `milvus_store` 支持 `dimension` 显式注入，正好用于修缺陷 #12（维度不一致）。
>
> **Elasticsearch 的定位**：它不是平台自己的存储，而是**下游推送目标**——`GraphInjectService.inject_graph()` 把本体转成 entities/relationships 写进 `ragflow_{tenant_id}` 索引。因此 `es_host/es_port` 来自 `project.inject_config`（用户自填），docker-compose 里作为**可选 profile** 提供，用于本地联调 RAGFlow。
>
> **嵌入模型维度**：`inject_service.py` 默认 `bge-m3:latest` / `dim=1024`，而 `.env.example` 里是 `nomic-embed-text` / `dim=768` —— **两处不一致，是缺陷 #12 的实例**，迁移时必须统一。

## Step 6.1 加 Oxigraph（TripletStore）

> 📄 **完整的中间件编排见 `docs/docker-compose.middleware.yml`** —— 含 MySQL / Neo4j / Milvus(etcd+MinIO) / Redis / 业务 MinIO 全套必装 8 个，以及 Elasticsearch、Ollama、Adminer、Oxigraph HTTP 服务等可选 profile，附健康检查、依赖顺序、数据卷与端口清单。
>
> ⚠️ **Oxigraph 本身不需要容器**（见下方 adapter 注释第 1 条：它是嵌入式 pyoxigraph）。
> 下面这段 compose 片段是**可选的**，只在「外部工具要用标准 SPARQL HTTP 协议访问」时才需要 ——
> **Semantica 的 `OxigraphStore` 连不上它**。

```yaml
# 可选：仅在需要独立 SPARQL HTTP 端点时添加（对应 compose 里的 profile: sparql）
  oxigraph:
    image: ghcr.io/oxigraph/oxigraph:latest
    container_name: oxigraph-onto
    profiles: ["sparql"]
    ports: ["7878:7878"]
    volumes: ["./volumes/oxigraph-server:/data"]
    command: serve --location /data --bind 0.0.0.0:7878
```

```python
# app/adapters/triple_store.py
import rdflib
import pyoxigraph as ox
from semantica.triplet_store import TripletStore
from semantica.semantic_extract.triplet_extractor import Triplet

class TripleStoreAdapter:
    """封装 Semantica 的 TripletStore（oxigraph 嵌入式后端）。

    ⚠️ 三处易错点（已逐条核对 semantica 0.7.0 源码）：

    1) oxigraph 后端是【嵌入式】的，不是 HTTP 服务。
       semantica/triplet_store/oxigraph_store.py 的类文档原文：
         "Embedded RDF storage backed by Oxigraph. … provides an in-process
          SPARQL 1.1 store … persist its data to a local directory."
       底层是 pyoxigraph（RocksDB 绑定），签名是 __init__(path=None, **config)，
       只收【本地目录】。TripletStore 的分支是：
           elif self.backend_type == "oxigraph":
               self._store_backend = OxigraphStore(**self.config)
       —— 注意它【不传 endpoint】（self.endpoint 被赋了值但从不下传），
       config.py 里也没有 oxigraph_endpoint 键。
       所以：传 path，不传 endpoint。装依赖：
           pip install "semantica[tripletstore-oxigraph]"

    2) TripletStore 没有 load(ttl, graph=..., format=...) 这个方法。
       真实可用方法只有：store(kg, ontology) / add_triplet(s) /
       get_triplets() / delete_triplet() / execute_query() / get_stats()。
       要批量灌 TTL，用 pyoxigraph 原生 Store.extend()（见 upsert_ttl）。

    3) RocksDB 是独占锁 —— 同一 path 只能被一个进程打开。
       RDF 写入必须只走 worker-rdf（concurrency=1）；API 进程不要持有可写句柄
       （docker-compose.middleware.yml 里 backend 对 oxigraph 目录挂 :ro）。
    """

    def __init__(self, path: str = "/data/oxigraph"):
        self.store = TripletStore(backend="oxigraph", path=path)
        # TripletStore 未暴露底层 pyoxigraph 句柄，批量导入需要它。
        # 这是本适配层唯一的私有属性访问，已隔离在此处便于日后上游修复后替换。
        self._raw: ox.Store = self.store._store_backend.store

    # ---------- 命名图划分 ----------
    # urn:semantica:project:{pid}:ontology:v{vid}   本体（TBox）
    # urn:semantica:project:{pid}:instance          实例（ABox）
    # urn:semantica:project:{pid}:provenance        溯源
    # urn:semantica:publication:{pubid}             发布快照（只读）

    def upsert_ttl(self, ttl: str, graph_uri: str) -> int:
        """把 TTL 灌进指定命名图，返回新增四元组数。

        为什么不用 store.add_triplets()：那条路要求先把 TTL 解析成
        Semantica 的 Triplet 对象（字符串三元组），会丢掉语言标签/数据类型，
        且对大文件慢一到两个数量级。这里直接走 pyoxigraph 原生 Quads。

        RDF 是集合语义：重复灌同一份 TTL 不会产生重复三元组，天然幂等。
        """
        before = len(self._raw)
        quads = [
            ox.Quad(
                self._term(s), self._term(p), self._term(o),
                ox.NamedNode(graph_uri),
            )
            for s, p, o in rdflib.Graph().parse(data=ttl, format="turtle")
        ]
        if quads:
            self._raw.extend(quads)
            self._raw.flush()          # pyoxigraph 的后台线程是异步落盘，显式同步
        return len(self._raw) - before

    @staticmethod
    def _term(node) -> "ox.NamedNode | ox.BlankNode | ox.Literal":
        """rdflib 节点 → pyoxigraph 节点。保留语言标签与数据类型。"""
        if isinstance(node, rdflib.URIRef):
            return ox.NamedNode(str(node))
        if isinstance(node, rdflib.BNode):
            return ox.BlankNode(str(node))
        if isinstance(node, rdflib.Literal):
            if node.language:
                return ox.Literal(str(node), language=str(node.language))
            if node.datatype:
                return ox.Literal(str(node), datatype=ox.NamedNode(str(node.datatype)))
            return ox.Literal(str(node))
        raise TypeError(f"无法转换的 RDF 节点类型: {type(node)!r}")

    def add_triplets(self, triplets: list[Triplet], graph_uri: str) -> dict:
        """结构化三元组走 Semantica 原生 API（事务批量 + 自动 flush）。"""
        return self.store.add_triplets(triplets, graph=graph_uri)

    def query(self, sparql: str) -> list[dict]:
        """SELECT → [ {var: value}, ... ]"""
        return self.store.execute_query(sparql).get("bindings", [])

    def ask(self, sparql: str) -> bool:
        return bool(self.store.execute_query(sparql).get("boolean"))

    def stats(self) -> dict:
        return self.store.get_stats()

    def flush(self) -> None:
        self._raw.flush()
```

> **为什么不需要 Oxigraph 容器**：它是进程内的 RocksDB。`docker-compose.middleware.yml`
> 里仍保留一个 `profile: sparql` 的 `oxigraph` 服务，但那只对「外部工具要用标准
> SPARQL HTTP 协议访问」或调试有意义 —— **Semantica 的 `OxigraphStore` 连不上它**，
> 因为它走的是嵌入式 API 而不是 HTTP。
>
> **代价与对策**：嵌入式意味着无法多副本水平扩展 RDF 层。对策是把它当作
> 「单一写入者的派生物」：真相在 MySQL，RDF 只是投影，由 `worker-rdf` 独占写入，
> 随时可以从 `outbox_events` 重放重建。这与 Step 6.2 的 Outbox 模式天然吻合。

## Step 6.2 Outbox 一致性

```python
# app/services/outbox.py
def emit(db, project_id: int, target: str, operation: str, payload: dict):
    """与业务写在同一事务里提交。PG 提交成功 = 业务成功（单一事实源）。"""
    db.add(OutboxEvent(project_id=project_id, target=target,
                       operation=operation, payload=payload))


def dispatch_pending(db, batch: int = 200):
    """Celery beat 每 5 秒跑一次。幂等写入，失败指数退避。"""
    events = (db.query(OutboxEvent)
              .filter(OutboxEvent.status == "pending",
                      or_(OutboxEvent.next_retry_at.is_(None),
                          OutboxEvent.next_retry_at <= datetime.utcnow()))
              .limit(batch).all())
    for ev in events:
        try:
            if ev.target == "neo4j":
                neo4j_upsert(ev.payload)          # ★ 用 MERGE，不用 CREATE
            elif ev.target == "milvus":
                milvus_upsert(ev.payload)         # ★ pk = chunk_id，upsert 天然幂等
            elif ev.target == "oxigraph":
                oxigraph_upsert(ev.payload)       # ★ RDF 集合语义天然幂等
            ev.status = "done"
        except Exception as e:
            ev.retry_count += 1
            ev.last_error = str(e)[:2000]
            ev.next_retry_at = datetime.utcnow() + timedelta(seconds=min(300, 2 ** ev.retry_count))
            if ev.retry_count >= 10:
                ev.status = "failed"
                alert(f"outbox {ev.target} 事件 {ev.id} 连续失败 10 次")
    db.commit()
```

**幂等键设计**：

| 存储 | 幂等策略 |
|---|---|
| Neo4j | 按 `det_id` `MERGE`，不用 `CREATE` |
| Milvus | 主键 = `chunk_id`（xxhash64），`upsert` 天然幂等；注意 `collection.dimension` 必须与 embedder 实测维度一致 |
| Oxigraph | 三元组去重（RDF 语义本身是集合） |

**重建能力**（关键验收）：清空 Neo4j/Milvus/Oxigraph，重放 `outbox_events` 完全恢复。

## Step 6.3 迁移现有 JSON blob → entities/relations 表

**这是最需要小心的一步。** 分三步走，不破坏现有功能：

```
阶段 1（双写）：extract 完成后，同时写 graph_data(JSON) 和 entities/relations 表
阶段 2（读切换）：新功能（审核队列、图谱查询）只读表；旧页面仍读 JSON
阶段 3（停写 JSON）：确认无回归后，graph_data 只保留一份轻量索引
```

```python
def persist_graph(db, project_id: int, graph_data: dict, version_id: int | None):
    """双写：JSON（兼容）+ 行表（新功能）。"""
    # 1) 旧的 JSON blob（保持兼容）
    project = db.query(Project).filter(Project.id == project_id).first()
    project.graph_data = graph_data

    # 2) 新的行表
    db.query(Entity).filter(Entity.project_id == project_id).delete()
    db.query(Relation).filter(Relation.project_id == project_id).delete()
    for n in graph_data.get("nodes", []):
        d = n.get("data", {})
        if d.get("type") not in ("owl:NamedIndividual", "instance"):
            continue
        db.add(Entity(
            project_id=project_id, det_id=n["id"],
            label=d.get("label", ""), label_norm=normalize_entity(d.get("label", "")),
            type_id=d.get("class_id"), type_label=d.get("class_label"),
            properties=d.get("properties") or d.get("data_props") or {},
            confidence=d.get("confidence"),
            ontology_version_id=version_id,
        ))
    for e in graph_data.get("edges", []):
        d = e.get("data", {})
        if d.get("type") == "instance_of":
            continue
        db.add(Relation(
            project_id=project_id, det_id=e["id"],
            predicate_id=d.get("predicate_id"), predicate_label=d.get("label"),
            source_det_id=e["source"], target_det_id=e["target"],
            source_type=d.get("source_type"), target_type=d.get("target_type"),
            confidence=d.get("confidence"),
        ))
    db.commit()
```

## 验收

- [ ] ABox 构建完成 → 四处数据量一致
- [ ] 停掉 Neo4j 发起构建 → PG 写入成功，outbox 累积 pending，业务不报错
- [ ] 重启 Neo4j → 5 秒内自动补写
- [ ] 清空 Milvus / Oxigraph 重放 outbox → 完全恢复
- [ ] SPARQL 查某实体全部三元组 → 正确

---

# 需求 7 · MCP 服务与基于本体的问答

## Step 7.1 MCP：用 `semantica_mcp` + 自建 HTTP 传输

**关键决策**：仓库里有**两个 MCP 实现**。

| 包 | 工具数 | CLI 入口 | HTTP 传输 | 选 |
|---|---|---|---|---|
| `semantica.mcp_server` | 16 | ✅ | ❌ 仅 stdio | ✗ |
| `semantica_mcp` | **22（含 `retrieve_context`）** | ❌ | ❌ 仅 stdio | ✅ |

→ **用 `semantica_mcp` 的工具实现，平台自建 HTTP 传输层**。

```python
# app/mcp/server.py（新建）
@router.post("/api/v1/mcp")
async def mcp_endpoint(request: Request, x_mcp_token: str = Header(..., alias="X-MCP-Token")):
    """MCP Streamable HTTP 传输（JSON-RPC 2.0）。"""
    ctx = await mcp_auth.authenticate(x_mcp_token)     # → (user_id, project_id, allow_write)
    body = await request.json()

    if body.get("method") == "tools/list":
        return {"jsonrpc": "2.0", "id": body["id"],
                "result": {"tools": visible_tools(ctx.allow_write)}}

    tool = body["params"]["name"]
    if tool in WRITE_TOOLS and not ctx.allow_write:
        return error(body["id"], -32601, f"工具 {tool} 需要写权限令牌")

    from semantica_mcp.mcp.server import MCPServer
    server = MCPServer()
    server.bind_context(project_id=ctx.project_id, user_id=ctx.user_id)  # ★ 多租户隔离
    result = server.dispatch(body)
    await audit_mcp_call(ctx, tool, result)
    return result
```

**工具权限矩阵**：

| 工具 | 只读令牌 | 写令牌 |
|---|---|---|
| `retrieve_context` / `search_graph` / `get_graph_summary` / `get_provenance` | ✅ | ✅ |
| `extract_entities` / `extract_relations` / `store_document` | ❌ | ✅ |
| `add_entity` / `update_node` / `delete_node` / `run_reasoning` | ❌ | ✅ |

**令牌**：`sk-mcp-` 前缀 + 32 字节随机，存 SHA-256 哈希，可设 `allow_write` / `expires_at` / `project_id`。

## Step 7.2 升级问答为 GraphRAG

**现状**：`rag_engine.py` 有 dual-path query。**升级**：接 `AgentContext.retrieve`（向量 + 图扩展）。

```python
# app/adapters/retrieval.py
class GraphRAGRetriever:
    """
    封装 AgentContext.retrieve()。平台负责三件事：
      1) 项目隔离（Semantica 无多租户）
      2) 时点过滤（retrieve 不支持 at_time）
      3) 引用构建（chunk → 文档/页码/原文）
    """

    def __init__(self, vector_store, graph_store):
        from semantica.context import AgentContext
        self.ctx = AgentContext(vector_store=vector_store, graph_store=graph_store)

    async def retrieve(self, project_id: int, question: str, *,
                       top_k: int = 8, use_graph: bool = True,
                       expand_graph: bool = True, max_hops: int = 2,
                       at_time: datetime | None = None) -> dict:
        # 1) 向量检索（Milvus，带 project_id 过滤）
        chunks = search_chunks(project_id, question, limit=top_k)
        # 2) 图扩展
        graph_ctx = self.ctx.retrieve(
            question, max_results=top_k, use_graph=use_graph,
            expand_graph=expand_graph, max_hops=max_hops,
        )
        # 3) 时点过滤（平台补的能力）
        if at_time:
            graph_ctx = filter_at_time(graph_ctx, at_time)
        # 4) 引用构建
        citations = build_citations(chunks)
        return {"chunks": chunks, "entities": graph_ctx.get("entities", []),
                "relations": graph_ctx.get("relations", []), "citations": citations}
```

**SSE 流式响应**（`sse-starlette` 已在 requirements）：

```
event: retrieval
data: {"chunks": 8, "entities": 12, "relations": 19}

event: delta
data: {"text": "根据检索到的资料，"}

event: citation
data: {"index": 1, "document_name": "采购合同A.pdf", "page_no": 3, "quote": "……"}

event: done
data: {"message_id": "uuid", "tokens": 412, "latency_ms": 1840}
```

**中文优先的提示词**：

```python
QA_SYSTEM_PROMPT = """你是知识库问答助手。严格遵守：
1. **只依据提供的资料回答**，不得引入外部知识。
2. 每个事实性陈述后必须标注来源编号，如 [1][2]。
3. 若资料不足以回答，明确说明"资料中未提及"，不要猜测。
4. 涉及实体关系时，注意区分不同实体（例如"北京市"与"北京市政府"不是同一实体）。
5. 用简体中文回答，专业术语保留原文。

资料：
{context}

来源编号对照：
{citations}
"""
```

## 验收

- [ ] Claude Desktop 通过 HTTP 连上 MCP，`tools/list` 返回 12 个（只读令牌）
- [ ] 调 `retrieve_context` → 只返回本项目内容，**不跨项目**
- [ ] 只读令牌调 `store_document` → 拒绝
- [ ] 问答返回带引文的答案，点击引文跳原文定位
- [ ] 问资料中没有的问题 → 明确"资料中未提及"

---

# 需求 8 · 中文优化与前后端分离

## Step 8.1 中文缺陷修复汇总

| 缺陷 | 修法 | 落点 |
|---|---|---|
| #1 中文分句失效 | `split_sentences_zh()` 状态机 | `adapters/chunking.py`（Step 3.2 已给） |
| #2 硬编码 `en_core_web_sm` | 禁用 `split_by_sentences` | 同上 |
| #3 overlap 守卫 | `safe_chunk_params()` 强制 ≤20% | 同上 |
| #4 中文回标 0/10 命中 | 守卫 `isalnum()` → `str.isascii()` | `adapters/extraction.py` |
| #5 幻觉不过滤 | 强制 `merge_strategy="union"` | 同上 |

**缺陷 #4 的正确修法**（关键，别改错）：

```python
# app/adapters/_compat.py
import re

def build_alignment_pattern(needle: str) -> re.Pattern:
    """
    ⚠️ 缺陷 #4：Semantica 的 ner_extractor.py:412-415 守卫是
         needle[0].isalnum() or needle[0] == "_"
       Python 对汉字调 isalnum() 返回 True（CJK 属 Unicode 字母），
       于是给中文实体也加了 (?<!\\w) / (?!\\w)，而 \\w 又匹配中文 ——
       中文没有空格，前后字符都是汉字 → 负向断言必然失败。
       实测：上游 0/10 命中。
    ✅ 正确修法：守卫改用 str.isascii()。不是"直接删掉守卫"（那会破坏英文边界）。
    """
    escaped = re.escape(needle)
    if needle and needle[0].isascii() and (needle[0].isalnum() or needle[0] == "_"):
        left = r"(?<![A-Za-z0-9_])"
    else:
        left = ""
    if needle and needle[-1].isascii() and (needle[-1].isalnum() or needle[-1] == "_"):
        right = r"(?![A-Za-z0-9_])"
    else:
        right = ""
    return re.compile(left + escaped + right, re.IGNORECASE)
```

## Step 8.2 中文模型配置

| 用途 | 模型 | 维度 |
|---|---|---|
| Embedding | `BAAI/bge-m3` | 1024 |
| Embedding | `BAAI/bge-large-zh-v1.5` | 1024 |
| Embedding | `BAAI/bge-small-zh-v1.5` | 512 |
| LLM（本地） | Ollama `qwen2.5:14b` | — |
| LLM（远程） | `deepseek-chat` / `qwen-max` / `glm-4` | — |
| 拼音 blocking | `pypinyin`（已在 requirements） | — |

⚠️ **维度必须一致**：换 embedding 后同步 Milvus collection 的 dimension。适配层从 embedder 实测注入（缺陷 #12）。

## Step 8.3 前端 i18n

```bash
npm i i18next react-i18next
```

```
src/i18n/locales/
├─ zh-CN/{common,auth,project,ontology,graph,review,qa,admin}.json   ← 默认，主源
└─ en-US/...
```

```ts
i18n.use(initReactI18next).init({
  resources, lng: detectLocale(),
  fallbackLng: "zh-CN",        // ★ fallback 是中文，不是英文
  interpolation: { escapeValue: false },
});
```

**中文 UI 规范（不是翻译，是重写）**：

| 场景 | ❌ | ✅ |
|---|---|---|
| 按钮 | "提交" | 用动词："保存" / "生成" / "发布" |
| 空状态 | "无数据" | "还没有文档，上传一份开始构建本体" + 行动按钮 |
| 加载 | "Loading..." | "正在解析文档…" |
| 错误 | "Error occurred" | "解析失败：文件可能已损坏。可尝试重新上传" |
| 数字 | "1284" | "1,284"；大数 "1.2 万" / "3.4 亿" |
| 列表 | "A, B and C" | "A、B、C"（中文顿号） |

**中英混排 CSS**：

```css
.text-mixed { text-autospace: normal; }        /* Chrome 118+ 自动加空隙 */
.prose-zh {
  line-break: strict;                          /* 避头尾 */
  word-break: normal;
  overflow-wrap: break-word;                   /* 长英文/URL 仍可断 */
  text-wrap: pretty;
}
.num { font-variant-numeric: tabular-nums; }
```

**中文输入法兼容**（搜索框必做）：

```tsx
const composing = useRef(false);
<input
  onCompositionStart={() => (composing.current = true)}
  onCompositionEnd={(e) => { composing.current = false; doSearch(e.currentTarget.value); }}
  onChange={(e) => { if (!composing.current) debouncedSearch(e.target.value); }}
/>
```

**字体栈**：`--font-sans: "Inter var", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Noto Sans SC", system-ui`。中文正文 15px / 行高 1.7（英文 14px / 1.5）。

## Step 8.4 前后端分离 + OpenAPI 契约

```bash
npm i -D openapi-typescript
npx openapi-typescript http://localhost:3001/openapi.json -o src/api/schema.d.ts
```

CI 里检查 `schema.d.ts` 的 diff —— 接口悄悄变更会导致前端 typecheck 失败。

## 验收

- [ ] 中文分句单元测试 10 个用例全过
- [ ] 中文实体回标命中率 ≥ 90%
- [ ] 204 字文本切片 ≤ 3 块（防缺陷 #3）
- [ ] 中文消解：全称/简称正确合并，`北京市`/`北京市政府` 不合并
- [ ] UI 中英切换无残留硬编码文案
- [ ] 中文输入法组字期间不发请求
- [ ] `document.documentElement.lang` 随语言切换更新

---

# 执行顺序与里程碑

```
P0 地基        ████████░░░░░░░░░░░░░░░░░░░░  1 周
                └ 适配层骨架 + 4 个 P0 修复 + 加表 + Alembic
P1 需求 1/2/3  ████████████░░░░░░░░░░░░░░░░  1.5 周
                └ 模块授权 + 模型配置中心 + Docling/OCR + 中文切片
P2 需求 4      ████████████████████░░░░░░░░  2.5 周
                └ Schema 闸门升级 + 消解 + 冲突 + 溯源 + 审核 + 版本 + 发布
P3 需求 5/6    ████████████████░░░░░░░░░░░░  2 周
                └ 图谱重构（见独立文档）+ 18 格式导出 + Oxigraph + Outbox
P4 需求 7/8    ████████████░░░░░░░░░░░░░░░░  1.5 周
                └ MCP HTTP + GraphRAG + 中文优化 + i18n
                                            合计 8.5 周
```

**关键路径**：P0 → P2 → P3。P1 可与 P2 部分并行。

**每个阶段结束的硬门槛**：

| 阶段 | 必须全绿才进下一阶段 |
|---|---|
| P0 | `合同2024`/`合同2025` 不合并；prompt 无 dict 语法；metadata 真实；`alembic upgrade head` 成功 |
| P1 | 未授权模块返回 403；模型配置改完立刻生效无需重启；扫描件 OCR 成功 |
| P2 | 溯源 `quote` 确实包含实体；`cardinality` 违规被拦截；审核快捷键生效；版本回滚成功 |
| P3 | 清空 Neo4j/Milvus/Oxigraph 后重放 outbox 完全恢复；5 种格式导出成功 |
| P4 | MCP 只读令牌无法调写工具；问答引文可跳原文 |

---

# 喂给大模型的提示词模板

每个 Step 开一个新会话，不要一次全塞。

```
你是一名资深全栈工程师。以下是项目背景与实施步骤：

<贴入：本文档的 §0 总览 + 本 Step 的完整内容>

项目现状（关键约束）：
- 现有平台：D:\python_code\OntologySystem\src（后端 17,403 行 / 前端 10,752 行）
- 后端 FastAPI + SQLAlchemy + MySQL + Neo4j + Milvus，前端 React 18 + antd + ReactFlow
- 已有能力：认证、项目、知识域、任务队列+SSE、两阶段本体抽取、TTL 合并、RAG
- ⚠️ 现有 User 表没有 role 字段；Project.graph_data 是一整块 JSON

要求：
1. 严格按文档中的接口签名、表结构、字段名实现，不要自行改名或改设计。
2. 文档中标注「⚠️ 缺陷 #N」的地方，必须按文档给的绕法实现，
   不要直接调用 Semantica 的对应 API。
3. **不要改现有 API 的调用签名**——优化是换实现，不是换接口。
4. 每实现一个模块，同时写单元测试。
5. 遇到文档未覆盖的细节，先问我，不要自己拍脑袋决定。

现在请实现：<具体 Step 编号与内容>
```

**反模式**（会翻车）：

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| 让模型"自由发挥"架构 | 每个模块风格不一致 | 文档已定架构，明确说"按文档实现" |
| 一次要求实现 5 个 Step | 质量下降 | 一次一个 Step + 测试 |
| 不说缺陷绕法 | 模型直接调有坑的 API | 每次强调"缺陷 #N 必须绕" |
| 让模型改调用签名 | 前端/API 全部返工 | 明确"签名不变" |
| 跳过 P0 直接做 P2 | 在错误数据上做优化 | P0 门槛测试必须全绿 |

---

# 附录：需求 → 文档索引

| # | 需求 | 本文章节 | 主要文件 |
|---|---|---|---|
| 1 | 登录 + 模块级权限 | 需求 1 | `core/deps.py`、`api/admin.py`、`admin/GrantsPage.tsx` |
| 2 | 模型全可配 | 需求 2 | `adapters/provider.py`、`core/crypto.py`、`ModelConfigPage.tsx` |
| 3 | 上传解析 + OCR + 切片 | 需求 3 | `adapters/parsing.py`、`adapters/chunking.py`、`vl_parser.py` |
| 4 | 分层建本体 | 需求 4 | `adapters/schema_gate.py`、`resolution.py`、`provenance.py`、`versioning.py`、`api/review.py` |
| 5 | 可视化 + 导出 | 需求 5 + 《图谱展示重构方案》 | `adapters/exporting.py`、`ExportPage.tsx` |
| 6 | 四存储持久化 | 需求 6 | `adapters/triple_store.py`、`services/outbox.py` |
| 7 | MCP + 问答 | 需求 7 | `mcp/server.py`、`adapters/retrieval.py` |
| 8 | 中文 + 前后端分离 | 需求 8 | `adapters/_compat.py`、`i18n/` |
