"""
文档解析工具
支持PDF、DOCX、TXT、MD等格式的文档解析
"""
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from app.core.exceptions import FileProcessingException


class DocumentMetadata:
    """文档元数据"""
    def __init__(self):
        self.title: Optional[str] = None
        self.author: Optional[str] = None
        self.creation_date: Optional[datetime] = None
        self.modification_date: Optional[datetime] = None
        self.page_count: Optional[int] = None
        self.word_count: Optional[int] = None
        self.character_count: Optional[int] = None
        self.file_type: Optional[str] = None


def parse_file(file_path: str) -> str:
    """
    解析文件并返回纯文本内容

    Args:
        file_path: 文件路径

    Returns:
        提取的文本内容

    Raises:
        FileProcessingException: 文件处理失败时
    """
    path = Path(file_path)
    if not path.exists():
        raise FileProcessingException(f"文件不存在: {file_path}")

    ext = path.suffix.lower()
    logger = logging.getLogger("graph_injector.file_parser")
    logger.info(f"解析文件: {file_path}, 类型: {ext}")

    try:
        if ext == ".pdf":
            return _parse_pdf(path)
        elif ext == ".docx":
            return _parse_docx(path)
        elif ext in (".txt", ".md", ".markdown"):
            return _parse_text(path)
        else:
            raise FileProcessingException(f"不支持的文件类型: {ext}")
    except FileProcessingException:
        raise
    except Exception as e:
        raise FileProcessingException(f"文件解析异常: {str(e)}", filename=str(file_path))


def _parse_pdf(path: Path) -> str:
    """解析PDF文件"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text.strip()
    except ImportError:
        raise FileProcessingException("PDF解析依赖未安装，请安装: pip install PyMuPDF")
    except Exception as e:
        raise FileProcessingException(f"PDF解析失败: {str(e)}")


def _parse_docx(path: Path) -> str:
    """解析DOCX文件"""
    try:
        from docx import Document
        doc = Document(path)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text.strip()
    except ImportError:
        raise FileProcessingException("DOCX解析依赖未安装，请安装: pip install python-docx")
    except Exception as e:
        raise FileProcessingException(f"DOCX解析失败: {str(e)}")


def _parse_text(path: Path) -> str:
    """解析纯文本文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as f:
                return f.read().strip()
        except Exception as e:
            raise FileProcessingException(f"文本文件解码失败: {str(e)}")
    except Exception as e:
        raise FileProcessingException(f"文本文件读取失败: {str(e)}")


def detect_file_type(filename: str) -> str:
    """
    检测文件类型(MIME类型)

    Args:
        filename: 文件名

    Returns:
        MIME类型字符串
    """
    ext_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
    }
    ext = Path(filename).suffix.lower()
    return ext_map.get(ext, "application/octet-stream")


def validate_file_type(filename: str) -> bool:
    """
    验证文件类型是否支持

    Args:
        filename: 文件名

    Returns:
        是否支持
    """
    allowed_extensions = {".pdf", ".docx", ".txt", ".md", ".markdown"}
    ext = Path(filename).suffix.lower()
    return ext in allowed_extensions
