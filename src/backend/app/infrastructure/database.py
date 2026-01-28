from sqlalchemy import Column, Integer, String, Boolean, Text, JSON, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime
from app.core.config import settings

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)

    projects = relationship("Project", back_populates="owner")

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    
    # 图谱数据（JSON 格式，用于前端 React Flow 渲染）
    graph_data = Column(JSON, nullable=True)
    
    # TTL 文件内容（同步到 Neo4j 之前的最终形态）
    ttl_content = Column(Text, nullable=True)
    
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="projects")

# 数据库连接
DB_URL = settings.DATABASE_URL
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
    # 如果是 MySQL，尝试先连接到服务器创建数据库（如果不存在）
    if settings.DATABASE_URL.startswith("mysql"):
        import pymysql
        from sqlalchemy import text
        
        # 提取不包含数据库名的连接信息
        # root:password@localhost:3309/ontology_db -> root, password, localhost, 3309
        try:
            conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD
            )
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {settings.MYSQL_DATABASE} CHARACTER SET utf8mb4;")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ [Database Init Warning] Failed to check/create database: {e}")

    # 创建所有表
    Base.metadata.create_all(bind=engine)
