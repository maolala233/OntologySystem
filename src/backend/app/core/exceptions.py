# app/core/exceptions.py - 统一异常与错误码（docs/design/03 §2.3）
# 功能：平台统一错误码体系 + 兼容保留旧异常类（既有代码仍在引用，签名不变）。
#
# 统一错误响应包络（03 §2.3）：
#   { "error": { "code": "...", "message": "...", "detail": {...}, "trace_id": "..." } }


class APIError(Exception):
    """平台统一业务异常：携带 HTTP 状态码、稳定错误码与结构化 detail。"""

    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, code: str | None = None,
                 detail: dict | None = None, http_status: int | None = None):
        self.message = message
        self.code = code or self.code
        self.detail = detail or {}
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_payload(self, trace_id: str | None = None) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
                "trace_id": trace_id,
            }
        }


class ValidationError(APIError):
    http_status = 400
    code = "VALIDATION_ERROR"


class UnsupportedFileTypeError(ValidationError):
    code = "UNSUPPORTED_FILE_TYPE"


class AuthError(APIError):
    http_status = 401
    code = "INVALID_CREDENTIALS"


class TokenExpiredError(AuthError):
    code = "TOKEN_EXPIRED"


class ForbiddenError(APIError):
    http_status = 403
    code = "MODULE_NOT_GRANTED"


class AdminRequiredError(ForbiddenError):
    code = "ADMIN_REQUIRED"


class ProjectForbiddenError(ForbiddenError):
    code = "PROJECT_FORBIDDEN"


class PublishedReadOnlyError(ForbiddenError):
    """对已发布项目的写操作拦截（发布快照不可变，见 03 §2.3 / 04 §10）。"""
    code = "PUBLISHED_READONLY"


class NotFoundError(APIError):
    http_status = 404
    code = "NOT_FOUND"


class ConflictError(APIError):
    http_status = 409
    code = "VERSION_CONFLICT"


class SchemaGateViolation(ConflictError):
    code = "SCHEMA_GATE_VIOLATION"


class ProjectStateInvalidError(ConflictError):
    code = "PROJECT_STATE_INVALID"


class DuplicateUploadError(ConflictError):
    """sha256 秒传命中：响应携带既有文档信息。"""
    code = "DUPLICATE_UPLOAD"


class EngineError(APIError):
    """模型/存储引擎侧失败（422）。"""
    http_status = 422
    code = "PROVIDER_UNAVAILABLE"


class LLMOutputInvalidError(EngineError):
    code = "LLM_OUTPUT_INVALID"


class EmbeddingDimMismatchError(EngineError):
    code = "EMBEDDING_DIM_MISMATCH"


class RateLimitedError(APIError):
    http_status = 429
    code = "RATE_LIMITED"


class InternalError(APIError):
    http_status = 500
    code = "INTERNAL_ERROR"


# ======================================================================
# 以下为既有异常类（保留，既有 services/api 代码仍在引用；M3 抽取链路
# 迁入 Celery/adapters 时逐步收敛到上方 APIError 体系）
# ======================================================================

class OntologyException(Exception):
    """本体系统基础异常类"""
    def __init__(self, message: str, code: str = "ONTOLOGY_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class FileProcessingException(OntologyException):
    """文件处理异常"""
    def __init__(self, message: str):
        super().__init__(message, "FILE_PROCESSING_ERROR")


class ExtractionException(OntologyException):
    """知识抽取异常"""
    def __init__(self, message: str):
        super().__init__(message, "EXTRACTION_ERROR")


class RAGException(OntologyException):
    """RAG系统异常"""
    def __init__(self, message: str):
        super().__init__(message, "RAG_ERROR")


class VectorStoreException(OntologyException):
    """向量存储异常"""
    def __init__(self, message: str):
        super().__init__(message, "VECTOR_STORE_ERROR")


class OntologyMergeException(OntologyException):
    """本体合并异常"""
    def __init__(self, message: str):
        super().__init__(message, "ONTOLOGY_MERGE_ERROR")
