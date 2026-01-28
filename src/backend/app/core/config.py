# app/core/config.py - 系统配置文件
# 功能：定义各种API密钥、URL、模型配置和向量库设置

import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ================= vLLM 配置 (内网) =================
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://28.221.28.7:9082/v1/chat/completions")
VLLM_MODEL = os.getenv("VLLM_MODEL", "DeepSeek-V3")

# ================= OpenRouter 配置 =================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nex-agi/deepseek-v3.1-nex-n1:free")

# ================= Embedding & Milvus 配置 =================
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "ollama")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

MILVUS_HOST = os.getenv("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
MILVUS_COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "knowledge_graph_rag")