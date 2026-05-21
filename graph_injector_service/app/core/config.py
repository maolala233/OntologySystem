"""
项目配置模块
从环境变量和.env文件加载配置
"""
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 加载.env文件
env_path = Path(__file__).parent.parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)


class LLMConfig(BaseModel):
    """LLM模型配置"""
    base_url: str = Field(default="http://localhost:8001/v1", description="LLM API地址")
    api_key: Optional[str] = Field(default=None, description="LLM API密钥")
    model: str = Field(default="qwen2.5-7b-instruct", description="模型名称")
    timeout: int = Field(default=120, description="请求超时时间(秒)")
    max_tokens: int = Field(default=4096, description="最大输出token数")


class EmbedConfig(BaseModel):
    """Embedding模型配置"""
    base_url: str = Field(default="http://localhost:11434", description="Embedding API地址")
    model: str = Field(default="bge-m3:latest", description="模型名称")
    timeout: int = Field(default=30, description="请求超时时间(秒)")
    dim: int = Field(default=1024, description="向量维度")


class ESConfig(BaseModel):
    """Elasticsearch配置"""
    host: str = Field(default="localhost", description="ES主机地址")
    port: int = Field(default=1200, description="ES端口")
    user: str = Field(default="elastic", description="用户名")
    password: str = Field(default="infini_rag_flow", description="密码")
    use_ssl: bool = Field(default=False, description="是否使用SSL")
    verify_certs: bool = Field(default=False, description="是否验证证书")

    @property
    def url(self) -> str:
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"


class ServiceConfig(BaseModel):
    """服务配置"""
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=8000, description="服务端口")
    workers: int = Field(default=4, description="工作进程数")
    log_level: str = Field(default="INFO", description="日志级别")


class AppConfig(BaseModel):
    """应用全局配置"""
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    es: ESConfig = Field(default_factory=ESConfig)
    ragflow_api_host: str = Field(default="http://localhost:9380", description="RAGFlow API地址")
    max_upload_size_mb: int = Field(default=100, description="最大上传文件大小(MB)")
    temp_upload_dir: str = Field(default="data/temp_uploads", description="临时上传目录")
    schema_output_dir: str = Field(default="data/schemas", description="Schema输出目录")
    output_dir: str = Field(default="data/output", description="输出目录")
    default_chunk_size: int = Field(default=1000, description="默认chunk大小")
    default_overlap_percentage: int = Field(default=10, description="默认重叠百分比")
    es_index_prefix: str = Field(default="ragflow", description="ES索引前缀")
    es_bulk_size: int = Field(default=100, description="ES批量写入大小")


# 单例配置对象
settings = AppConfig(
    service=ServiceConfig(
        host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVICE_PORT", "8000")),
        workers=int(os.getenv("SERVICE_WORKERS", "4")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    ),
    llm=LLMConfig(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:8001/v1"),
        api_key=os.getenv("LLM_API_KEY") or None,
        model=os.getenv("LLM_MODEL", "qwen2.5-7b-instruct"),
        timeout=int(os.getenv("LLM_TIMEOUT", "120")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
    ),
    embed=EmbedConfig(
        base_url=os.getenv("EMBED_BASE_URL", "http://localhost:11434"),
        model=os.getenv("EMBED_MODEL", "bge-m3:latest"),
        timeout=int(os.getenv("EMBED_TIMEOUT", "30")),
        dim=int(os.getenv("EMBED_DIM", "1024")),
    ),
    es=ESConfig(
        host=os.getenv("ES_HOST", "localhost"),
        port=int(os.getenv("ES_PORT", "1200")),
        user=os.getenv("ES_USER", "elastic"),
        password=os.getenv("ES_PASSWORD", "infini_rag_flow"),
        use_ssl=os.getenv("ES_USE_SSL", "false").lower() == "true",
        verify_certs=os.getenv("ES_VERIFY_CERTS", "false").lower() == "true",
    ),
    ragflow_api_host=os.getenv("RAGFLOW_API_HOST", "http://localhost:9380"),
    max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")),
    temp_upload_dir=os.getenv("TEMP_UPLOAD_DIR", "data/temp_uploads"),
    schema_output_dir=os.getenv("SCHEMA_OUTPUT_DIR", "data/schemas"),
    output_dir=os.getenv("OUTPUT_DIR", "data/output"),
    default_chunk_size=int(os.getenv("DEFAULT_CHUNK_SIZE", "1000")),
    default_overlap_percentage=int(os.getenv("DEFAULT_OVERLAP_PERCENTAGE", "10")),
    es_index_prefix=os.getenv("ES_INDEX_PREFIX", "ragflow"),
    es_bulk_size=int(os.getenv("ES_BULK_SIZE", "100")),
)
