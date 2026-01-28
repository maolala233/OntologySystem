# app/core/exceptions.py - 自定义异常定义
# 功能：定义本体系统专用的各种异常类

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