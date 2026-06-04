import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import fitz
from openai import OpenAI

from app.core.logging import logger


VL_BATCH_SIZE = 3
VL_DPI = 150
VL_MAX_TOKENS = 8192


def _get_vl_config() -> dict:
    from app.core.config import settings
    from app.infrastructure.database import SessionLocal, SystemConfig

    base_url = ""
    api_key = ""
    model = ""
    disable_think = True

    try:
        db = SessionLocal()
        try:
            config = db.query(SystemConfig).filter(SystemConfig.key == "vl_config").first()
            if config and config.value:
                base_url = config.value.get("vl_base_url", "")
                api_key = config.value.get("vl_api_key", "")
                model = config.value.get("vl_model", "")
                disable_think = config.value.get("vl_disable_think", True)
        finally:
            db.close()
    except Exception:
        pass

    if not base_url:
        base_url = settings.EMBEDDING_BASE_URL
    if not api_key:
        api_key = settings.EMBEDDING_API_KEY
    if not model:
        model = settings.LLM_MODEL_NAME

    return {"base_url": base_url, "api_key": api_key, "model": model, "disable_think": disable_think}


def _docx_to_pdf(docx_path: str, work_dir: str) -> Optional[str]:
    tmp_docx = os.path.join(work_dir, "input.docx")
    shutil.copy2(docx_path, tmp_docx)
    result = subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", work_dir, tmp_docx],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error(f"[VL解析] LibreOffice 转换失败: {result.stderr}")
        return None
    pdf_path = os.path.join(work_dir, "input.pdf")
    if not os.path.exists(pdf_path):
        logger.error("[VL解析] PDF 文件未生成")
        return None
    return pdf_path


def _pdf_to_page_images(pdf_path: str, work_dir: str, dpi: int = VL_DPI) -> list[str]:
    doc = fitz.open(pdf_path)
    image_paths = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(work_dir, f"page_{page_num + 1:03d}.png")
        pix.save(img_path)
        image_paths.append(img_path)
    doc.close()
    return image_paths


def _encode_image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_vl_model(image_paths: list[str], prompt: str, vl_config: dict) -> str:
    headers = {}
    api_key = vl_config.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["apikey"] = api_key
    client = OpenAI(base_url=vl_config["base_url"], api_key=api_key or "EMPTY",
                    default_headers=headers)
    content = [{"type": "text", "text": prompt}]
    for img_path in image_paths:
        b64 = _encode_image_to_base64(img_path)
        ext = os.path.splitext(img_path)[1].lstrip(".")
        mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
        mime = mime_map.get(ext, "image/png")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    extra_body = {}
    if vl_config.get("disable_think", True):
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}

    response = client.chat.completions.create(
        model=vl_config["model"],
        messages=[{"role": "user", "content": content}],
        max_tokens=VL_MAX_TOKENS,
        extra_body=extra_body if extra_body else None,
    )
    return response.choices[0].message.content


def parse_file_with_vl(file_path: str) -> str:
    start_time = time.time()
    fname_lower = file_path.lower()
    base_name = os.path.basename(file_path)

    logger.info(f"[VL解析] 开始处理文件：{base_name}")

    try:
        mime_type = ""
        try:
            mime_result = subprocess.run(
                ["file", "--mime-type", "-b", file_path],
                capture_output=True, text=True, check=True,
            )
            mime_type = mime_result.stdout.strip()
        except Exception:
            pass

        is_pdf = mime_type == "application/pdf" or fname_lower.endswith(".pdf")
        is_docx = (
            mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or fname_lower.endswith(".docx")
        )

        if not is_pdf and not is_docx:
            logger.info(f"[VL解析] 文件 {base_name} 不是 PDF/DOCX，跳过 VL 解析")
            return ""

        work_dir = os.path.join(os.path.dirname(file_path), ".vl_work")
        os.makedirs(work_dir, exist_ok=True)
        try:
            if is_docx:
                logger.info(f"[VL解析] DOCX → PDF → 图片 → VL模型")
                pdf_path = _docx_to_pdf(file_path, work_dir)
                if not pdf_path:
                    return ""
            else:
                pdf_path = file_path

            page_images = _pdf_to_page_images(pdf_path, work_dir)
            logger.info(f"[VL解析] 共渲染 {len(page_images)} 页")

            if not page_images:
                return ""

            vl_config = _get_vl_config()
            logger.info(f"[VL解析] 使用模型: {vl_config['model']}")

            prompt = (
                "请仔细阅读这些文档页面图片，逐页提取所有内容，包括：\n"
                "1. 所有文字内容（标题、正文、注释等）\n"
                "2. 表格的完整内容（逐行逐列）\n"
                "3. 流程图/架构图的含义和步骤\n"
                "4. 截图中的界面元素、菜单路径、按钮文字\n"
                "5. 图片中传达的任何业务信息\n\n"
                "请按页码顺序输出，每页用 '## 第X页' 标记。"
            )

            all_results = []
            for batch_start in range(0, len(page_images), VL_BATCH_SIZE):
                batch_end = min(batch_start + VL_BATCH_SIZE, len(page_images))
                batch = page_images[batch_start:batch_end]

                batch_start_time = time.time()
                try:
                    result = _call_vl_model(batch, prompt, vl_config)
                    all_results.append(result)
                    logger.info(
                        f"[VL解析] 第 {batch_start + 1}-{batch_end} 页完成 "
                        f"({time.time() - batch_start_time:.1f}s)"
                    )
                except Exception as e:
                    logger.error(f"[VL解析] 第 {batch_start + 1}-{batch_end} 页失败: {e}")
                    all_results.append(f"[第{batch_start + 1}-{batch_end}页 VL识别失败]")

            combined = "\n\n".join(all_results)
            logger.info(
                f"[VL解析] 文件 {base_name} 处理完成，"
                f"内容长度={len(combined)} 字符，总耗时={time.time() - start_time:.1f}s"
            )
            logger.info(f"[VL解析] 解析结果预览:\n{combined[:2000]}")
            return combined

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"[VL解析] 文件 {base_name} 处理失败: {e}", exc_info=True)
        return ""
