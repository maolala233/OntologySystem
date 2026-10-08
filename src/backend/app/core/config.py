import os
import threading
import time
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3309
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "password"
    MYSQL_DATABASE: str = "ontology_db"
    MYSQL_URL: Optional[str] = None

    SQLITE_PATH: Optional[str] = "./ontology_system.db"

    JWT_SECRET_KEY: str = "your_super_secret_jwt_key_here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120   # M1：2h（01 §8），refresh 负责续期
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "z-ai/glm-4.5-air:free"
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_THINK_MODE: str = "auto"

    VLLM_API_KEY: str = "EMPTY"
    VLLM_BASE_URL: str = "http://localhost:9080/v1/chat/completions"
    VLLM_MODEL: str = "qwen2.5-7B"

    EMBEDDING_API_KEY: str = "ollama"
    EMBEDDING_BASE_URL: str = "http://localhost:11434/v1"
    EMBEDDING_MODEL: str = "nomic-embed-text:latest"
    EMBEDDING_DIM: int = 768

    # docling 离线模型目录（内网部署）：docling-tools models download 预下载后指过去，
    # 解析 PDF 不再联网拉 HuggingFace；留空 = 默认行为（首次联网下载到 ~/.cache/docling）
    DOCLING_ARTIFACTS_PATH: str = ""

    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: str = "19530"
    MILVUS_COLLECTION_NAME: str = "knowledge_graph_rag"

    UPLOAD_DIR: str = "uploads"
    UPLOAD_PROJECTS_DIR: str = "uploads/projects"
    TEMP_DIR: str = "temp"
    UPLOAD_MAX_SIZE_MB: int = 100

    # ===== M0 新增（docs/design/01 §4.2 / 02 §3.8 / 03 §2）=====
    # 队列：Celery broker/backend 缺省回落 Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None

    # 业务对象存储（健康检查 M0；文件链路 M3 起使用）
    MINIO_ENDPOINT: str = "localhost:9010"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_UPLOADS: str = "ontology-uploads"
    MINIO_BUCKET_PARSED: str = "ontology-parsed"
    MINIO_BUCKET_EXPORTS: str = "ontology-exports"

    # 模型密钥加密主密钥（model_configs.api_key 落库加密，M2 起使用）。
    # 未配置时回退派生自 JWT_SECRET_KEY（仅限开发，生产必须显式设置）。
    SECRET_MASTER_KEY: str = ""

    # CORS 白名单（替代以前的 "*"）
    CORS_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3080,http://127.0.0.1:3080"
    )

    # 嵌入式 RDF 存储（M4 接入；RocksDB 独占锁 → 仅 worker-rdf 写）
    OXIGRAPH_PATH: str = "./data/oxigraph"

    @property
    def cors_origins_list(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    @property
    def DATABASE_URL(self) -> str:
        if self.MYSQL_URL:
            return self.MYSQL_URL
        host = os.getenv('MYSQL_HOST', self.MYSQL_HOST)
        port = os.getenv('MYSQL_PORT', str(self.MYSQL_PORT))
        user = os.getenv('MYSQL_USER', self.MYSQL_USER)
        password = os.getenv('MYSQL_PASSWORD', self.MYSQL_PASSWORD)
        database = os.getenv('MYSQL_DATABASE', self.MYSQL_DATABASE)
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def ensure_dirs():
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.UPLOAD_PROJECTS_DIR, exist_ok=True)
    os.makedirs(settings.TEMP_DIR, exist_ok=True)


def get_dir_size_mb(dir_path: str) -> float:
    total = 0
    if not os.path.exists(dir_path):
        return 0.0
    for dirpath, _, filenames in os.walk(dir_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total / (1024 * 1024)


def cleanup_dir_if_exceeded(dir_path: str, max_size_mb: int):
    size_mb = get_dir_size_mb(dir_path)
    if size_mb <= max_size_mb:
        return
    from app.core.logging import logger
    logger.warning(f"[cleanup] 目录 {dir_path} 大小 {size_mb:.1f}MB 超过限制 {max_size_mb}MB，开始清理")
    file_list = []
    for dirpath, _, filenames in os.walk(dir_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                file_list.append((fp, os.path.getmtime(fp), os.path.getsize(fp)))
    file_list.sort(key=lambda x: x[1])
    freed = 0
    target_free = (size_mb - max_size_mb) * 1024 * 1024
    for fp, mtime, fsize in file_list:
        try:
            os.remove(fp)
            freed += fsize
            if freed >= target_free:
                break
        except Exception:
            pass
    for dirpath, dirnames, filenames in os.walk(dir_path, topdown=False):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            try:
                if not os.listdir(full):
                    os.rmdir(full)
            except Exception:
                pass
    logger.info(f"[cleanup] 清理完成，释放 {freed / (1024*1024):.1f}MB")


def start_periodic_cleanup(interval_seconds: int = 600):
    def _worker():
        while True:
            time.sleep(interval_seconds)
            try:
                cleanup_dir_if_exceeded(settings.UPLOAD_DIR, settings.UPLOAD_MAX_SIZE_MB)
                cleanup_dir_if_exceeded(settings.TEMP_DIR, settings.UPLOAD_MAX_SIZE_MB)
            except Exception:
                pass
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
