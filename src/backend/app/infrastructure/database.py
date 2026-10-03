from sqlalchemy import Column, Integer, String, Boolean, Text, JSON, DateTime, ForeignKey, Enum, UniqueConstraint, LargeBinary, Numeric, Text as SQLAlchemyText
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.mysql import VARCHAR, LONGTEXT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime
import bcrypt
from app.core.config import settings

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)
    # M1 权限模型（docs/design/02 §3.1）
    role = Column(Enum("admin", "user", name="user_role"), nullable=False, server_default="user")
    email = Column(String(255), unique=True, nullable=True)
    display_name = Column(String(64), nullable=True)
    locale = Column(String(10), nullable=False, server_default="zh-CN")
    last_login_at = Column(DateTime, nullable=True)

    projects = relationship("Project", back_populates="owner")

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), index=True)
    description = Column(String(500), nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    domain_id = Column(Integer, ForeignKey("knowledge_domains.id"), nullable=True)  # 知识域 ID
    domains = Column(String(500), nullable=True)  # 知识域名称（冗余字段，用于快速访问，如"IT 架构，财务规范"）
    
    # 图谱数据（JSON 格式，用于前端 React Flow 渲染）
    graph_data = Column(JSON, nullable=True)
    
    # TTL 文件内容（同步到 Neo4j 之前的最终形态）
    ttl_content = Column(LONGTEXT, nullable=True)
    
    # RAGFlow 注入配置
    inject_config = Column(JSON, nullable=True)
    
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="projects")
    domain = relationship("KnowledgeDomain", back_populates="projects")

class KnowledgeDomain(Base):
    """
    知识域字典表
    用于标识本体项目所属的知识领域，如"IT 架构"、"财务规范"等
    """
    __tablename__ = "knowledge_domains"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)  # 知识域名称
    description = Column(String(500), nullable=True)  # 知识域描述
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # 关联项目
    projects = relationship("Project", back_populates="domain")


class SystemConfig(Base):
    __tablename__ = "system_configs"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True)  # e.g., 'llm_config'
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class UploadedDocument(Base):
    """
    已上传文档记录表
    用于跟踪项目中上传的文档，支持文档管理（查看列表、删除等）
    M3-1（docs/design/02 §3.4，R4）：原件/解析文本落 MinIO（storage_key/text_key），
    sha256 同项目秒传判重，parse_status 解析状态机（M3-2 parse 队列驱动）。
    """
    __tablename__ = "uploaded_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    filename = Column(String(255), nullable=False)  # 原始文件名
    file_path = Column(String(500), nullable=False)  # 旧本地路径（兼容期保留；新上传以 storage_key 为准）
    file_size = Column(Integer, nullable=True)  # 文件大小（字节）
    file_type = Column(String(50), nullable=True)  # 文件类型：txt, pdf, doc, docx, xlsx, xls, csv, pptx, md
    text_content = Column(LONGTEXT, nullable=True)  # 解析后的文本内容（兼容期保留，text_key 就绪后清空）

    # R4 加列（02 §3.4）
    storage_key = Column(String(512), nullable=True)  # MinIO ontology-uploads 键：{project}/{uuid}.{ext}
    text_key = Column(String(512), nullable=True)     # MinIO ontology-parsed 键：解析文本（M3-2 写入）
    sha256 = Column(String(64), nullable=True, index=True)  # 内容哈希，同项目秒传判重
    parse_status = Column(
        Enum("uploaded", "parsing", "parsed", "failed", name="doc_parse_status"),
        nullable=False, server_default="uploaded",
    )
    parse_error = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    language = Column(String(10), nullable=True)   # zh/en/mixed（M3-2 解析时回填）
    parse_backend = Column(String(32), nullable=True)  # docling_ocr/docling/pymupdf4llm/vl_model/native（M3-2）
    deleted_at = Column(DateTime, nullable=True)   # 软删标记
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # 关联项目
    project = relationship("Project", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document",
                          cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index")


class DocumentChunk(Base):
    """长文档切片（02 §3.4，R4）：抽取与向量化的统一单位，M3-2 parse 队列写入。"""
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=False, server_default="0")  # 原文偏移（溯源定位用）
    char_end = Column(Integer, nullable=False, server_default="0")
    token_count = Column(Integer, nullable=True)
    meta = Column(JSON, nullable=True)  # 章节路径/页码/表格标记
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))

    document = relationship("UploadedDocument", back_populates="chunks")
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uk_doc_chunk"),
    )


# 更新 Project 模型，添加 documents 关系
Project.documents = relationship("UploadedDocument", back_populates="project", cascade="all, delete-orphan")


class Module(Base):
    """功能模块字典（M1，docs/design/02 §3.1）：14 个模块码见 docs/design/README §4.1。"""
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    description = Column(String(255), nullable=True)
    is_default_on = Column(Boolean, nullable=False, server_default="0")
    sort_order = Column(Integer, nullable=False, server_default="0")


class UserModuleGrant(Base):
    """用户 × 模块授权矩阵（M1）：显式 allow/deny 可覆盖 is_default_on。"""
    __tablename__ = "user_module_grants"
    __table_args__ = (UniqueConstraint("user_id", "module_code", name="uk_user_module"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    module_code = Column(String(32), nullable=False)
    allowed = Column(Boolean, nullable=False, server_default="1")
    granted_by = Column(Integer, nullable=False)
    granted_at = Column(DateTime, default=datetime.datetime.utcnow)


class ProjectMember(Base):
    """项目级协作权限（M1）：owner 与 projects.owner_id 冗余同步（服务层维护）。"""
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uk_proj_user"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(Enum("owner", "editor", "viewer", name="project_role"), nullable=False)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ModelConfig(Base):
    """模型配置（M2，docs/design/02 §3.2）：本地(Ollama/vLLM)与远程(OpenAI兼容)统一抽象。

    api_key 仅存 AES-256-GCM 密文（core/security）；明文只在解析结果内存中流转。
    抽取运行参数（chunk_size/request_interval/llm_timeout/disable_think/streaming_enabled）
    存放于 params JSON（extract 行）。
    """
    __tablename__ = "model_configs"

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(Enum("global", "project", name="model_scope"), nullable=False, server_default="global")
    project_id = Column(Integer, nullable=True)
    purpose = Column(Enum("chat", "extract", "embedding", "vl", name="model_purpose"), nullable=False)
    name = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)
    base_url = Column(String(512), nullable=False)
    api_key_encrypted = Column(LargeBinary(1024), nullable=True)
    model_name = Column(String(128), nullable=False)
    params = Column(JSON, nullable=True)
    dims = Column(Integer, nullable=True)
    is_default = Column(Boolean, nullable=False, server_default="0")
    enabled = Column(Boolean, nullable=False, server_default="1")
    last_test_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Entity(Base):
    """实体行表（M3-3 R5，docs/design/02 §3.5）：graph_data blob 的拆行事实源。

    消解/审核/导出以行表为准，Neo4j/Milvus/Oxigraph 为其投影（02 §4 三阶段迁移）。
    uri = urn:onto:{project_id}:entity/{graph_data 节点 id}，节点 id 由抽取侧
    make_deterministic_id 或画布保证稳定。is_class_node 判据 data.type ∈
    {owl:Class, owl:ActionType, Class}（TBox）；owl:NamedIndividual/Instance 为 ABox。
    """
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False)
    uri = Column(String(512), nullable=False)
    label = Column(String(255), nullable=False)
    label_normalized = Column(String(255), nullable=False)
    class_label = Column(String(128), nullable=False)
    aliases = Column(JSON, nullable=True)
    props = Column(JSON, nullable=True)
    confidence = Column(Numeric(4, 3), nullable=True)  # 0-1，LLM 自报
    status = Column(Enum("auto", "merged", "pending_review", "approved", "rejected",
                         name="entity_status"), nullable=False, server_default="auto")
    canonical_id = Column(Integer, ForeignKey("entities.id", ondelete="SET NULL"), nullable=True)
    is_class_node = Column(Boolean, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False,
                        server_default=sa_text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("project_id", "uri", name="uk_proj_uri"),
    )


class Relation(Base):
    """关系行表（02 §3.5）：subject/object 外键实体；双向边判定在后端（06 §4.4）。"""
    __tablename__ = "relations"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False)
    subject_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    predicate = Column(String(128), nullable=False)
    object_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    props = Column(JSON, nullable=True)
    confidence = Column(Numeric(4, 3), nullable=True)
    status = Column(Enum("auto", "pending_review", "approved", "rejected",
                         name="relation_status"), nullable=False, server_default="auto")
    is_class_edge = Column(Boolean, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("project_id", "subject_id", "predicate", "object_id", name="uk_proj_edge"),
    )


class ProvenanceRecord(Base):
    """溯源记录（02 §3.6）：W3C PROV-O 对齐，evidence 定位到原句（修复缺陷 #11）。"""
    __tablename__ = "provenance_records"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False)
    target_type = Column(Enum("entity", "relation", "chunk", name="prov_target_type"),
                         nullable=False)
    target_id = Column(Integer, nullable=False)
    source_document_id = Column(Integer, ForeignKey("uploaded_documents.id", ondelete="CASCADE"),
                                nullable=False)
    chunk_index = Column(Integer, nullable=True)
    evidence_text = Column(String(1024), nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    extraction_method = Column(String(32), nullable=True)  # llm_ner/llm_re/manual/merge
    checksum = Column(String(64), nullable=False)  # SHA-256(对象键|偏移|证据)，防篡改
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))


class ReviewItem(Base):
    """人工审核队列（02 §3.6）：8 种触发原因；状态机 pending→claimed→approved/rejected/edited。

    冲突不单独建表：ConflictDetector 检出项物化为 review_items(item_type='conflict_*')。
    """
    __tablename__ = "review_items"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False)
    item_type = Column(Enum("entity_merge", "new_class", "low_confidence_entity",
                            "low_confidence_relation", "conflict_value", "conflict_type",
                            "conflict_relationship", "missing_evidence",
                            name="review_item_type"), nullable=False)
    payload = Column(JSON, nullable=False)
    reason = Column(String(255), nullable=True)
    priority = Column(Enum("low", "medium", "high", "critical", name="review_priority"),
                      nullable=False, server_default="medium")
    status = Column(Enum("pending", "claimed", "approved", "rejected", "edited",
                         name="review_status"), nullable=False, server_default="pending")
    suggested_action = Column(JSON, nullable=True)
    assigned_to = Column(Integer, nullable=True)
    decided_by = Column(Integer, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    result_ref = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=sa_text("CURRENT_TIMESTAMP"))


# 14 个功能模块种子（M1 R2 迁移同样写入，保持两处一致；README §4.1）
MODULE_SEEDS = [
    {"code": "dashboard",       "name": "工作台",          "description": "首页统计与快捷入口",     "is_default_on": True,  "sort_order": 1},
    {"code": "projects",        "name": "项目管理",        "description": "项目创建与列表",         "is_default_on": True,  "sort_order": 2},
    {"code": "documents",       "name": "文档管理",        "description": "上传/解析/分块",         "is_default_on": False, "sort_order": 3},
    {"code": "schema_build",    "name": "骨架构建",        "description": "TBox 抽取与画布修订",    "is_default_on": False, "sort_order": 4},
    {"code": "instance_build",  "name": "实例构建",        "description": "ABox 抽取",              "is_default_on": False, "sort_order": 5},
    {"code": "resolution",      "name": "实体消解与冲突",  "description": "消解三层流水线与冲突检测", "is_default_on": False, "sort_order": 6},
    {"code": "review",          "name": "人工审核",        "description": "审核队列与裁决",         "is_default_on": False, "sort_order": 7},
    {"code": "version_control", "name": "版本与时间轴",    "description": "快照/diff/回滚/时间线",  "is_default_on": False, "sort_order": 8},
    {"code": "publish",         "name": "发布管理",        "description": "发布到公共区",           "is_default_on": False, "sort_order": 9},
    {"code": "asset_center",    "name": "公共资产中心",    "description": "浏览已发布本体",         "is_default_on": True,  "sort_order": 10},
    {"code": "graph_explore",   "name": "图谱探索",        "description": "大规模图只读探索",       "is_default_on": False, "sort_order": 11},
    {"code": "qa",              "name": "本体问答",        "description": "基于本体的 GraphRAG 问答", "is_default_on": False, "sort_order": 12},
    {"code": "mcp",             "name": "MCP 工具层",      "description": "MCP 令牌与工具调用",     "is_default_on": False, "sort_order": 13},
    {"code": "export",          "name": "导出",            "description": "18 种格式导出",          "is_default_on": False, "sort_order": 14},
]


def init_knowledge_domains(db):
    """
    初始化知识域字典数据
    创建一些常用的知识域分类
    """
    try:
        # 检查是否已有知识域数据
        domain_count = db.query(KnowledgeDomain).count()
        if domain_count > 0:
            print(f"ℹ️ [KnowledgeDomain] Found {domain_count} existing domains, skipping initialization")
            return
        
        print("📝 [KnowledgeDomain] Creating initial knowledge domains...")
        
        # 创建常用知识域
        initial_domains = [
            KnowledgeDomain(name="IT 架构", description="信息技术架构相关的知识领域"),
            KnowledgeDomain(name="财务规范", description="财务管理与规范相关的知识领域"),
            KnowledgeDomain(name="医疗健康", description="医疗健康行业相关的知识领域"),
            KnowledgeDomain(name="教育培训", description="教育培训行业相关的知识领域"),
            KnowledgeDomain(name="制造业", description="制造业相关的知识领域"),
            KnowledgeDomain(name="金融服务", description="金融服务行业相关的知识领域"),
            KnowledgeDomain(name="法律合规", description="法律与合规相关的知识领域"),
            KnowledgeDomain(name="通用领域", description="通用知识领域，适用于跨行业场景"),
        ]
        
        for domain in initial_domains:
            db.add(domain)
        
        db.commit()
        print("✅ [KnowledgeDomain] Initial knowledge domains created")
        for domain in initial_domains:
            print(f"   - {domain.name}: {domain.description}")
            
    except Exception as e:
        print(f"⚠️ [KnowledgeDomain] Failed to create initial domains: {e}")
        db.rollback()

# 数据库连接 —— M0 统一走 settings.DATABASE_URL（src/backend/.env 环境变量驱动，
# 删除原来的 os.getenv 重复拼接逻辑，docs/design/02 §3）
DB_URL = settings.DATABASE_URL
print(f"🔧 [Database] Using connection URL: {DB_URL}")

# 根据数据库类型选择合适的参数
if DB_URL.startswith("mysql"):
    engine = create_engine(
        DB_URL,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
    )
else:
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _run_migrations() -> None:
    """M0：表结构演进统一走 Alembic（docs/design/02 §6），替代 create_all + _auto_migrate。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[2]  # src/backend
    alembic_cfg = Config(str(backend_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(alembic_cfg, "head")


def init_db():
    """
    初始化数据库（M0 精简版，docs/design/08 §2 M0 任务 2）：
    1. 创建数据库（如果使用 MySQL）
    2. Alembic upgrade head（R1 baseline 建表；后续演进走新增 revision）
    3. 创建种子数据（用户 / 知识域 / 示例项目）
    """
    # 如果是 MySQL，尝试先连接到服务器创建数据库（如果不存在）
    if settings.DATABASE_URL.startswith("mysql"):
        import pymysql

        # 提取不包含数据库名的连接信息
        try:
            print(f"🔍 [Database] Connecting to MySQL at {settings.MYSQL_HOST}:{settings.MYSQL_PORT}...")
            conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD
            )
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {settings.MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
                print(f"✅ [Database] Database '{settings.MYSQL_DATABASE}' is ready")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ [Database Init Warning] Failed to check/create database: {e}")

    # Alembic 迁移到最新版本
    print("🔨 [Database] Running alembic upgrade head...")
    _run_migrations()
    print("✅ [Database] Migrations up to date")

    # 创建种子数据
    db = SessionLocal()
    try:
        _create_initial_data(db)
        # 初始化知识域字典
        init_knowledge_domains(db)
    finally:
        db.close()

def _create_initial_data(db):
    """创建初始测试数据"""
    try:
        # 检查是否已有用户
        user_count = db.query(User).count()
        if user_count > 0:
            print(f"ℹ️ [Database] Found {user_count} existing users, skipping initial data creation")
            return
        
        print("📝 [Database] Creating initial test users...")
        
        # 创建测试用户
        test_users = [
            User(
                username="admin",
                hashed_password=bcrypt.hashpw("cbil123456".encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                is_active=True
            ),
            User(
                username="testuser",
                hashed_password=bcrypt.hashpw("123456".encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                is_active=True
            )
        ]
        
        for user in test_users:
            db.add(user)
        
        db.commit()
        print("✅ [Database] Test users created:")
        print("   - Username: admin, Password: cbil123456")
        print("   - Username: testuser, Password: 123456")
        
        # 创建示例项目
        print("📝 [Database] Creating sample projects...")
        
        admin_user = db.query(User).filter(User.username == "admin").first()
        
        # 获取"通用领域"知识域
        default_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.name == "通用领域").first()
        domain_id = default_domain.id if default_domain else None
        
        sample_projects = [
            Project(
                name="工业本体示例",
                description="这是一个工业领域的本体模型示例",
                owner_id=admin_user.id,
                domain_id=domain_id,
                graph_data={
                    "nodes": [
                        {
                            "id": "node_1",
                            "type": "default",
                            "position": {"x": 250, "y": 100},
                            "data": {"label": "产品", "type": "Class", "properties": {}},
                            "style": {"background": "#fff", "border": "2px solid #3b82f6", "borderRadius": "8px", "padding": "10px"}
                        },
                        {
                            "id": "node_2",
                            "type": "default",
                            "position": {"x": 100, "y": 250},
                            "data": {"label": "零件", "type": "Class", "properties": {}},
                            "style": {"background": "#fff", "border": "2px solid #3b82f6", "borderRadius": "8px", "padding": "10px"}
                        },
                        {
                            "id": "node_3",
                            "type": "default",
                            "position": {"x": 400, "y": 250},
                            "data": {"label": "工序", "type": "Class", "properties": {}},
                            "style": {"background": "#fff", "border": "2px solid #3b82f6", "borderRadius": "8px", "padding": "10px"}
                        }
                    ],
                    "edges": [
                        {
                            "id": "edge_1",
                            "source": "node_1",
                            "target": "node_2",
                            "type": "smoothstep",
                            "animated": True,
                            "data": {"label": "包含", "relation": "contains"}
                        },
                        {
                            "id": "edge_2",
                            "source": "node_1",
                            "target": "node_3",
                            "type": "smoothstep",
                            "animated": True,
                            "data": {"label": "需要", "relation": "requires"}
                        }
                    ]
                },
                is_published=False
            ),
            Project(
                name="已发布的公共本体",
                description="这是一个已发布的公共本体示例，所有用户都可以在资产中心查看",
                owner_id=admin_user.id,
                domain_id=domain_id,
                graph_data={
                    "nodes": [
                        {
                            "id": "node_1",
                            "type": "default",
                            "position": {"x": 200, "y": 150},
                            "data": {"label": "人员", "type": "Entity", "properties": {}},
                            "style": {"background": "#fff", "border": "2px solid #6366f1", "borderRadius": "8px", "padding": "10px"}
                        },
                        {
                            "id": "node_2",
                            "type": "default",
                            "position": {"x": 400, "y": 150},
                            "data": {"label": "部门", "type": "Entity", "properties": {}},
                            "style": {"background": "#fff", "border": "2px solid #6366f1", "borderRadius": "8px", "padding": "10px"}
                        }
                    ],
                    "edges": [
                        {
                            "id": "edge_1",
                            "source": "node_1",
                            "target": "node_2",
                            "type": "smoothstep",
                            "data": {"label": "属于", "relation": "belongs_to"}
                        }
                    ]
                },
                is_published=True
            )
        ]
        
        for project in sample_projects:
            db.add(project)
        
        db.commit()
        print("✅ [Database] Sample projects created")
        print(f"   - Total projects: {len(sample_projects)}")
        
    except Exception as e:
        print(f"⚠️ [Database] Failed to create initial data: {e}")
        db.rollback()


# M0：_auto_migrate 已删除 —— 表结构演进统一走 Alembic（docs/design/02 §6），
# 旧的 projects.inject_config 手写 ALTER 由 R1 baseline 覆盖。


def _register_graph_rows_listener() -> None:
    """M3-3 双写挂钩（docs/design/02 §4 S1）：监听 Project.graph_data 变更，
    拆写 entities/relations/provenance_records。

    覆盖全部写入点（画布保存/抽取/注入/v1 旧端点/未来新增），无需逐一挂钩。
    行表走独立短会话在 after_flush 写入（S1 blob 为准）：主事务失败仅产生
    行表暂时超前的漂移，由对账脚本检出、下次保存收敛；行表失败不影响 blob 保存。
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SASession

    @event.listens_for(SASession, "before_flush")
    def _on_before_flush(session, flush_context, instances):  # noqa: ANN001
        # 项目删除 → 行表清理（entities.project_id 无 FK，孤儿行需显式删；
        # 放 before_flush：session.deleted 在此处保证可见）
        deleted_ids = [obj.id for obj in session.deleted if isinstance(obj, Project)]
        if deleted_ids:
            rows = SessionLocal()
            try:
                (rows.query(ProvenanceRecord)
                 .filter(ProvenanceRecord.project_id.in_(deleted_ids),
                         ProvenanceRecord.target_type.in_(["entity", "relation"]))
                 .delete(synchronize_session=False))
                (rows.query(Relation).filter(Relation.project_id.in_(deleted_ids))
                 .delete(synchronize_session=False))
                (rows.query(Entity).filter(Entity.project_id.in_(deleted_ids))
                 .delete(synchronize_session=False))
                rows.commit()
            except Exception as e:  # noqa: BLE001
                rows.rollback()
                print(f"⚠️ [graph-rows] 项目 {deleted_ids} 行表清理失败: {e}")
            finally:
                rows.close()

    @event.listens_for(SASession, "after_flush")
    def _on_after_flush(session, flush_context):  # noqa: ANN001
        dirty = [obj for obj in list(session.new) + list(session.dirty)
                 if isinstance(obj, Project)
                 and (obj in session.new or session.is_modified(obj, ["graph_data"]))]
        if not dirty:
            return
        from app.services.graph_rows import sync_project_rows

        for proj in dirty:
            rows = SessionLocal()
            try:
                sync_project_rows(rows, proj.id, proj.graph_data)
                rows.commit()
            except Exception as e:  # noqa: BLE001 —— 行表失败不影响 blob 写入链路
                rows.rollback()
                print(f"⚠️ [graph-rows] 项目 {proj.id} 行表双写失败（对账脚本可检出）: {e}")
            finally:
                rows.close()


_register_graph_rows_listener()

