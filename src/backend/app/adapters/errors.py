# app/adapters/errors.py - Semantica 异常 → 平台 APIError 翻译（docs/design/03 §2.3）
# 里程碑：M0 定义骨架；各适配模块在 M3/M4/M5 调用 translate() 收口异常。

from __future__ import annotations

from app.core.exceptions import APIError, EngineError, InternalError


class AdapterError(Exception):
    """适配层内部错误基类：永远不向上抛 Semantica 原生异常类型。"""


def translate(exc: Exception, context: str = "") -> APIError:
    """把适配层内部异常翻译为平台统一 APIError。

    翻译规则：
      - AdapterError / ValueError（含 AES InvalidTag、JSON 解析）→ EngineError(LLM_OUTPUT_INVALID)
      - ConnectionError / TimeoutError / OSError                  → EngineError(PROVIDER_UNAVAILABLE)
      - 其余                                                       → InternalError
    """
    prefix = f"[{context}] " if context else ""
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, (AdapterError, ValueError)):
        return EngineError(f"{prefix}引擎输出无效: {exc}", code="LLM_OUTPUT_INVALID")
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return EngineError(f"{prefix}引擎不可达: {exc}", code="PROVIDER_UNAVAILABLE")
    return InternalError(f"{prefix}适配层内部错误: {exc}")


__all__ = ["AdapterError", "translate"]
