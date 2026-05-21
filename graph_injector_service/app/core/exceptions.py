"""
自定义异常模块
定义服务中使用的各类异常
"""


class GraphInjectorException(Exception):
    """基础异常类"""
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class FileProcessingException(GraphInjectorException):
    """文件处理异常"""
    def __init__(self, message: str, filename: str = None):
        detail = f"文件处理失败: {message}"
        if filename:
            detail = f"文件 [{filename}] 处理失败: {message}"
        super().__init__(message=detail, code="FILE_PROCESSING_ERROR")


class SchemaBuildException(GraphInjectorException):
    """Schema构建异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="SCHEMA_BUILD_ERROR")


class InstanceBuildException(GraphInjectorException):
    """实例构建异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="INSTANCE_BUILD_ERROR")


class ESInjectionException(GraphInjectorException):
    """ES注入异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="ES_INJECTION_ERROR")


class LLMCallException(GraphInjectorException):
    """LLM调用异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="LLM_CALL_ERROR")


class EmbeddingException(GraphInjectorException):
    """Embedding调用异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="EMBEDDING_ERROR")


class ValidationError(GraphInjectorException):
    """参数校验异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="VALIDATION_ERROR")


class ConfigurationException(GraphInjectorException):
    """配置异常"""
    def __init__(self, message: str):
        super().__init__(message=message, code="CONFIGURATION_ERROR")
