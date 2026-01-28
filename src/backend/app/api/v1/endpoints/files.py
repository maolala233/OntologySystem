# app/api/v1/endpoints/files.py - 文件处理端点
# 功能：提供文件上传、解析和本体合并的API接口

from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from typing import Optional, List
import os
import shutil
from app.schemas.request import FileUploadRequest
from app.schemas.response import FileUploadResponse, OntologyResponse
from app.services.parser import process_files
from app.services.merger import OntologyMerger
from app.core.exceptions import FileProcessingException
from app.core.logging import logger

router = APIRouter()


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件
    """
    try:
        # 创建临时目录
        os.makedirs("temp_uploads", exist_ok=True)
        
        # 构建临时文件路径
        temp_file_path = os.path.join("temp_uploads", file.filename)
        
        # 保存上传的文件
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        return FileUploadResponse(
            status="success",
            filename=file.filename,
            message=f"文件 {file.filename} 上传成功"
        )
    except Exception as e:
        logger.error(f"文件上传错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


@router.post("/parse")
async def parse_files(files: List[UploadFile] = File(...), scenario: str = Form(""), text_content: str = Form("")):
    """
    解析上传的文件
    """
    temp_file_paths = []
    try:
        full_text = text_content
        
        # 处理上传的文件
        if files:
            os.makedirs("temp_uploads", exist_ok=True)
            for uploaded_file in files:
                if not uploaded_file.filename: 
                    continue
                temp_path = os.path.join("temp_uploads", uploaded_file.filename)
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(uploaded_file.file, buffer)
                temp_file_paths.append(temp_path)

            if temp_file_paths:
                file_text = process_files(temp_file_paths)
                full_text += f"\n{file_text}"
        
        if not full_text.strip():
            raise FileProcessingException("请提供文本内容或上传文件")

        return {"status": "success", "text": full_text}
    
    except Exception as e:
        logger.error(f"文件解析错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文件解析失败: {str(e)}")
    finally:
        # 清理临时文件
        for path in temp_file_paths:
            if os.path.exists(path):
                os.remove(path)


@router.post("/merge")
async def merge_ontologies(ttl_files: List[UploadFile] = File(...)):
    """
    合并多个本体文件
    """
    temp_file_paths = []
    try:
        # 保存上传的 TTL 文件
        os.makedirs("temp_uploads", exist_ok=True)
        for ttl_file in ttl_files:
            temp_path = os.path.join("temp_uploads", ttl_file.filename)
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(ttl_file.file, buffer)
            temp_file_paths.append(temp_path)
        
        if not temp_file_paths:
            raise FileProcessingException("请上传需要合并的 TTL 文件")
        
        # 合并本体
        merger = OntologyMerger()
        merged_file, report, changes_detail = merger.merge_files(temp_file_paths)
        
        return {
            "status": "success", 
            "merged_file": merged_file, 
            "report": report,
            "changes_detail": changes_detail
        }
    
    except Exception as e:
        logger.error(f"本体合并错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"本体合并失败: {str(e)}")
    finally:
        # 清理临时文件
        for path in temp_file_paths:
            if os.path.exists(path):
                os.remove(path)