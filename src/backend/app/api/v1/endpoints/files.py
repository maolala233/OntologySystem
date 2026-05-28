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
from app.core.config import settings, ensure_dirs

router = APIRouter()


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(file: UploadFile = File(...)):
    try:
        ensure_dirs()
        temp_file_path = os.path.join(settings.TEMP_DIR, file.filename)
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        try:
            return FileUploadResponse(
                status="success",
                filename=file.filename,
                message=f"文件 {file.filename} 上传成功"
            )
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
    except Exception as e:
        logger.error(f"文件上传错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文件上传失败：{str(e)}")


@router.post("/parse")
async def parse_files(files: List[UploadFile] = File(...), scenario: str = Form(""), text_content: str = Form("")):
    temp_file_paths = []
    try:
        full_text = text_content

        if files:
            ensure_dirs()
            for uploaded_file in files:
                if not uploaded_file.filename:
                    continue
                temp_path = os.path.join(settings.TEMP_DIR, uploaded_file.filename)
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
        raise HTTPException(status_code=500, detail=f"文件解析失败：{str(e)}")
    finally:
        for path in temp_file_paths:
            if os.path.exists(path):
                os.remove(path)


@router.post("/merge")
async def merge_ontologies(ttl_files: List[UploadFile] = File(...)):
    temp_file_paths = []
    try:
        ensure_dirs()
        for ttl_file in ttl_files:
            temp_path = os.path.join(settings.TEMP_DIR, ttl_file.filename)
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(ttl_file.file, buffer)
            temp_file_paths.append(temp_path)

        if not temp_file_paths:
            raise FileProcessingException("请上传需要合并的 TTL 文件")

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
        raise HTTPException(status_code=500, detail=f"本体合并失败：{str(e)}")
    finally:
        for path in temp_file_paths:
            if os.path.exists(path):
                os.remove(path)
