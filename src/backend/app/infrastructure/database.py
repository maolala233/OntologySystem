from sqlalchemy import Column, Integer, String, Boolean, Text, JSON, DateTime, ForeignKey, Text as SQLAlchemyText
from sqlalchemy.dialects.mysql import VARCHAR, LONGTEXT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime
from app.core.config import settings

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)

    projects = relationship("Project", back_populates="owner")

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), index=True)
    description = Column(String(500), nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    domain_id = Column(Integer, ForeignKey("knowledge_domains.id"), nullable=True)  # 知识域 ID
    
    # 图谱数据（JSON 格式，用于前端 React Flow 渲染）
    graph_data = Column(JSON, nullable=True)
    
    # TTL 文件内容（同步到 Neo4j 之前的最终形态）
    ttl_content = Column(LONGTEXT, nullable=True)
    
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
    """
    __tablename__ = "uploaded_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    filename = Column(String(255), nullable=False)  # 原始文件名
    file_path = Column(String(500), nullable=False)  # 文件存储路径
    file_size = Column(Integer, nullable=True)  # 文件大小（字节）
    file_type = Column(String(50), nullable=True)  # 文件类型：txt, pdf, doc, docx
    text_content = Column(LONGTEXT, nullable=True)  # 解析后的文本内容
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # 关联项目
    project = relationship("Project", back_populates="documents")


# 更新 Project 模型，添加 documents 关系
Project.documents = relationship("UploadedDocument", back_populates="project", cascade="all, delete-orphan")


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

# 数据库连接 - 直接使用环境变量确保正确配置
import os
MYSQL_HOST = os.getenv('MYSQL_HOST', settings.MYSQL_HOST)
MYSQL_PORT = os.getenv('MYSQL_PORT', str(settings.MYSQL_PORT))
MYSQL_USER = os.getenv('MYSQL_USER', settings.MYSQL_USER)
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', settings.MYSQL_PASSWORD)
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', settings.MYSQL_DATABASE)

DB_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
print(f"🔧 [Database] Using connection URL: {DB_URL}")

# 根据数据库类型选择合适的参数
if DB_URL.startswith("mysql"):
    engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=3600)
else:
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    初始化数据库
    1. 创建数据库（如果使用 MySQL）
    2. 创建所有表
    3. 创建测试用户（如果不存在）
    4. 创建知识域字典数据
    5. 创建示例项目（可选）
    """
    # 如果是 MySQL，尝试先连接到服务器创建数据库（如果不存在）
    if settings.DATABASE_URL.startswith("mysql"):
        import pymysql
        from sqlalchemy import text
        
        # 提取不包含数据库名的连接信息
        # root:password@localhost:3309/ontology_db -> root, password, localhost, 3309
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

    # 创建所有表
    print("🔨 [Database] Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ [Database] All tables created")
    
    # 创建测试用户和示例数据
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
                hashed_password="cbil123456",  # TODO: 实际应使用 bcrypt 加密
                is_active=True
            ),
            User(
                username="testuser",
                hashed_password="123456",
                is_active=True
            )
        ]
        
        for user in test_users:
            db.add(user)
        
        db.commit()
        print("✅ [Database] Test users created:")
        print("   - Username: admin, Password: 123456")
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

