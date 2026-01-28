from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import os

class Settings(BaseSettings):
    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    # Database
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3309
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "password"
    MYSQL_DATABASE: str = "ontology_db"
    MYSQL_URL: Optional[str] = None

    # SQLite backup (optional)
    SQLITE_PATH: Optional[str] = "./ontology_system.db"

    # JWT
    JWT_SECRET_KEY: str = "your_super_secret_jwt_key_here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # LLM (Unified)
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = "z-ai/glm-4.5-air:free"
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"

    # Legacy / specific model configs (kept for compatibility)
    VLLM_API_KEY: str = "EMPTY"
    VLLM_BASE_URL: str = "http://localhost:9080/v1/chat/completions"
    VLLM_MODEL: str = "qwen2.5-7B"

    # Embedding & Milvus
    EMBEDDING_API_KEY: str = "ollama"
    EMBEDDING_BASE_URL: str = "http://localhost:11434/v1"
    EMBEDDING_MODEL: str = "nomic-embed-text:latest"
    EMBEDDING_DIM: int = 768

    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: str = "19530"
    MILVUS_COLLECTION_NAME: str = "knowledge_graph_rag"

    @property
    def DATABASE_URL(self) -> str:
        # 优先使用环境变量中的 MYSQL_URL，否则根据设置构造
        if self.MYSQL_URL:
            return self.MYSQL_URL
        # 如果没有显式设置 MYSQL_URL，则根据配置构造 MySQL URL
        return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()