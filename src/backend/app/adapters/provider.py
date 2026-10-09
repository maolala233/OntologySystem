# app/adapters/provider.py - 模型 Provider 解析（docs/design/03 §5 / 04 §1.2）
# M2 实现。解析优先级（02 §3.2）：
#   请求级参数 → 项目级 model_configs(scope=project, purpose, is_default) → 全局级 → 环境变量兜底
# purpose 四类：chat（对话）/ extract（抽取）/ embedding（嵌入）/ vl（视觉）
# api_key 仅在内存流转：DB 存 AES-256-GCM 密文（core/security），响应一律 mask_secret。

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import EngineError, ValidationError
from app.core.security import decrypt_str, encrypt_str, mask_secret
from app.infrastructure.database import ModelConfig

PROVIDER_META: dict[str, dict[str, Any]] = {
    # 预设 provider（表单动态渲染用；openai_compatible 为万能兜底）
    "openai_compatible": {"label": "OpenAI 兼容（通用）", "needs_key": True, "default_base_url": "", "hint": "任何 OpenAI 兼容端点：远程 API / 云厂商 / vLLM"},
    "ollama": {"label": "Ollama（本地/内网）", "needs_key": False, "default_base_url": "http://localhost:11434/v1", "hint": "Ollama 原生 /v1 兼容端点，无需 api_key"},
    "vllm": {"label": "vLLM（自托管）", "needs_key": False, "default_base_url": "http://localhost:8000/v1", "hint": "vLLM OpenAI 兼容服务"},
}


class ModelPurpose(str, Enum):
    CHAT = "chat"
    EXTRACT = "extract"
    EMBEDDING = "embedding"
    VL = "vl"


class ModelConfigData(BaseModel):
    """model_configs 表的平台侧投影（不携带明文 api_key）。"""

    id: int
    scope: str = "global"
    project_id: Optional[int] = None
    purpose: ModelPurpose
    name: str
    provider: str
    base_url: str
    model_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    dims: Optional[int] = None
    is_default: bool = False


class ResolvedProvider(BaseModel):
    """一次解析结果：调用方据此构造 OpenAI 兼容客户端（llm_client/embedding_client）。"""

    base_url: str
    api_key: str  # 已解密明文，仅在内存中流转，禁止写日志/响应
    model_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "model_config"  # model_config | request_override | env_fallback


class ResolvedEmbedding(ResolvedProvider):
    dims: int = 1024  # 与 Milvus collection 对齐（02 §3.2）


def _clean_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _pick_default(db: Session, purpose: ModelPurpose,
                  project_id: Optional[int]) -> Optional[ModelConfig]:
    """项目级默认优先于全局级默认（scope 内 enabled 且 is_default）。"""
    q = (db.query(ModelConfig)
         .filter(ModelConfig.purpose == purpose.value, ModelConfig.enabled.is_(True)))
    if project_id is not None:
        row = (q.filter(ModelConfig.scope == "project", ModelConfig.project_id == project_id,
                        ModelConfig.is_default.is_(True)).first())
        if row:
            return row
    return q.filter(ModelConfig.scope == "global", ModelConfig.is_default.is_(True)).first()


def _env_fallback(purpose: ModelPurpose) -> Optional[ResolvedProvider]:
    from app.core.config import settings

    if purpose in (ModelPurpose.CHAT, ModelPurpose.EXTRACT):
        if settings.VLLM_BASE_URL and "CHANGE_ME" not in settings.VLLM_BASE_URL:
            return ResolvedProvider(
                base_url=_clean_base_url(settings.VLLM_BASE_URL),
                api_key=settings.VLLM_API_KEY or "",
                model_name=settings.VLLM_MODEL or "",
                source="env_fallback")
        return None
    if purpose == ModelPurpose.EMBEDDING:
        if settings.EMBEDDING_BASE_URL and "CHANGE_ME" not in settings.EMBEDDING_BASE_URL:
            return ResolvedEmbedding(
                base_url=_clean_base_url(settings.EMBEDDING_BASE_URL),
                api_key=settings.EMBEDDING_API_KEY or "",
                model_name=settings.EMBEDDING_MODEL or "",
                dims=settings.EMBEDDING_DIM or 1024,
                source="env_fallback")
        return None
    return None  # vl 无 env 兜底


def resolve_provider(purpose: ModelPurpose, project_id: Optional[int] = None,
                     request_override: Optional[dict] = None,
                     db: Optional[Session] = None,
                     config_id: Optional[int] = None) -> ResolvedProvider:
    """按解析优先级返回可用的 provider；找不到抛 EngineError(PROVIDER_UNAVAILABLE)。

    config_id：显式指定某条 model_configs（调用侧已做可见性校验，如用户个人选择）。
    该行不可用（删除/禁用）时静默回落到默认解析，保证抽取任务不被历史选择卡死。
    """
    if request_override and request_override.get("base_url") and request_override.get("model_name"):
        return ResolvedProvider(
            base_url=_clean_base_url(request_override["base_url"]),
            api_key=request_override.get("api_key", ""),
            model_name=request_override["model_name"],
            params=request_override.get("params") or {},
            source="request_override")
    if db is not None:
        row = None
        if config_id is not None:
            row = (db.query(ModelConfig)
                   .filter(ModelConfig.id == config_id,
                           ModelConfig.enabled.is_(True)).first())
        if row is None:
            row = _pick_default(db, purpose, project_id)
        if row:
            return _row_to_resolved(row, purpose)
    fallback = _env_fallback(purpose)
    if fallback:
        return fallback
    raise EngineError(f"未配置可用的 {purpose.value} 模型：请在管理后台「模型配置」中添加",
                      code="PROVIDER_UNAVAILABLE")


def _row_to_resolved(row: ModelConfig, purpose: ModelPurpose) -> ResolvedProvider:
    """model_configs 行 → ResolvedProvider（密钥仅内存流转，禁止写日志/响应）。"""
    api_key = ""
    if row.api_key_encrypted:
        api_key = decrypt_str(bytes(row.api_key_encrypted))
    base = ResolvedProvider(
        base_url=_clean_base_url(row.base_url),
        api_key=api_key,
        model_name=row.model_name,
        params=row.params or {},
        source="model_config")
    if purpose == ModelPurpose.EMBEDDING:
        return ResolvedEmbedding(**base.model_dump(), dims=row.dims or 1024)
    return base


def resolve_embedding(project_id: Optional[int] = None,
                      request_override: Optional[dict] = None,
                      db: Optional[Session] = None) -> ResolvedEmbedding:
    """嵌入模型解析；必须携带 dims 供 Milvus collection 校验。"""
    resolved = resolve_provider(ModelPurpose.EMBEDDING, project_id, request_override, db)
    return resolved if isinstance(resolved, ResolvedEmbedding) else ResolvedEmbedding(
        **resolved.model_dump(), dims=1024)


def build_legacy_llm_config(db: Session, project_id: Optional[int] = None) -> dict:
    """兼容层：产出与旧 system_configs.llm_config 相同形状的 dict，
    供 _build_extractor / QA / extractor 无缝切换数据源（下游字段引用零改动）。

    抽取用途的运行参数（chunk_size/request_interval/llm_timeout/disable_think/
    streaming_enabled）存放在 extract 行的 params 中。
    """
    llm = resolve_provider(ModelPurpose.EXTRACT, project_id, db=db)
    emb = resolve_embedding(project_id, db=db)
    params = llm.params or {}
    return {
        "api_key": llm.api_key,
        "base_url": llm.base_url,
        "model": llm.model_name,
        "embedding_base_url": emb.base_url,
        "embedding_model": emb.model_name,
        "embedding_api_key": emb.api_key,
        "embedding_dims": emb.dims,
        "chunk_size": params.get("chunk_size", 2000),
        "chunk_overlap": params.get("chunk_overlap", 15),
        "request_interval": params.get("request_interval", 2),
        "llm_timeout": params.get("llm_timeout", 300),
        "disable_think": params.get("disable_think", False),
        "streaming_enabled": params.get("streaming_enabled", False),
    }


def build_legacy_vl_config(db: Session, project_id: Optional[int] = None) -> dict:
    """兼容层：旧 vl_config 形状 {api_key, base_url, model, disable_think}。"""
    vl = resolve_provider(ModelPurpose.VL, project_id, db=db)
    return {
        "api_key": vl.api_key,
        "base_url": vl.base_url,
        "model": vl.model_name,
        "disable_think": (vl.params or {}).get("disable_think", False),
    }


# ---- 连通性探测（真实 HTTP，轻量请求）----

def _probe_chat(base_url: str, api_key: str, model_name: str,
                timeout: float = 10.0) -> tuple[bool, int, str, str]:
    import time

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            f"{_clean_base_url(base_url)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            json={"model": model_name, "messages": [{"role": "user", "content": "Hi"}],
                  "max_tokens": 5},
            timeout=timeout,
        )
        latency = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
            return True, latency, "连通成功", (text or "")[:40]
        detail = resp.text[:160]
        return False, latency, f"HTTP {resp.status_code}: {detail}", ""
    except Exception as exc:  # noqa: BLE001 —— 探测失败如实返回
        return False, int((time.perf_counter() - t0) * 1000), f"连接失败: {exc}", ""


def _probe_embedding(base_url: str, api_key: str, model_name: str,
                     timeout: float = 10.0) -> tuple[bool, int, str, Optional[int]]:
    import time

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            f"{_clean_base_url(base_url)}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            json={"input": ["连通性测试"], "model": model_name},
            timeout=timeout,
        )
        latency = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            embedding = resp.json()["data"][0]["embedding"]
            return True, latency, f"连通成功，向量维度 {len(embedding)}", len(embedding)
        return False, latency, f"HTTP {resp.status_code}: {resp.text[:160]}", None
    except Exception as exc:  # noqa: BLE001
        return False, int((time.perf_counter() - t0) * 1000), f"连接失败: {exc}", None


def test_connectivity(purpose: ModelPurpose, provider: str, base_url: str,
                      api_key: str, model_name: str,
                      dims: Optional[int] = None) -> dict:
    """连通性测试（03 §5）：chat/extract/vl 探 chat/completions；
    embedding 探 /embeddings 并校验维度与声明 dims 一致（EMBEDDING_DIM_MISMATCH）。"""
    if not base_url or not model_name:
        raise ValidationError("base_url 与 model_name 必填")
    if purpose == ModelPurpose.EMBEDDING:
        ok, latency, msg, actual = _probe_embedding(base_url, api_key, model_name)
        result = {"ok": ok, "latency_ms": latency, "message": msg}
        if ok:
            result["dims"] = actual
            if dims and actual and actual != dims:
                result["ok"] = False
                result["message"] = (f"连通成功但维度不匹配：实测 {actual}，"
                                     f"声明 {dims}（EMBEDDING_DIM_MISMATCH）")
        return result
    ok, latency, msg, sample = _probe_chat(base_url, api_key, model_name)
    return {"ok": ok, "latency_ms": latency, "message": msg, "sample_output": sample}


def mark_test_result(db: Session, config_id: int, ok: bool) -> None:
    row = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if row:
        row.last_test_at = datetime.utcnow()
        row.last_test_ok = ok
        db.commit()


__all__ = [
    "PROVIDER_META", "ModelPurpose", "ModelConfigData", "ResolvedProvider", "ResolvedEmbedding",
    "resolve_provider", "resolve_embedding", "build_legacy_llm_config", "build_legacy_vl_config",
    "test_connectivity", "mark_test_result", "mask_secret", "encrypt_str", "_clean_base_url",
]
