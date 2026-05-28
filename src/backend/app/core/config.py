from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import os
import time
import threading


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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

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

    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: str = "19530"
    MILVUS_COLLECTION_NAME: str = "knowledge_graph_rag"

    UPLOAD_DIR: str = "uploads"
    UPLOAD_PROJECTS_DIR: str = "uploads/projects"
    TEMP_DIR: str = "temp"
    UPLOAD_MAX_SIZE_MB: int = 100

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