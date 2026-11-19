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
VL_DPI = 300  # 150 对 9B 级小模型太小（349 实测：整页小字读成黑点），300 下小模型可读
VL_MAX_TOKENS = 8192


def _get_vl_config() -> dict:
    from app.adapters.provider import ModelPurpose, resolve_provider
    from app.infrastructure.database import SessionLocal

    # M2：改读 model_configs（vl 默认行，adapters/provider）；解析失败回退 env
    try:
        db = SessionLocal()
        try:
            vl = resolve_provider(ModelPurpose.VL, db=db)
            return {
                "base_url": vl.base_url,
                "api_key": vl.api_key,
                "model": vl.model_name,
                "disable_think": bool((vl.params or {}).get("disable_think", True)),
            }
        finally:
            db.close()
    except Exception:
        pass

    from app.core.config import settings

    return {
        "base_url": settings.EMBEDDING_BASE_URL,
        "api_key": settings.EMBEDDING_API_KEY,
        "model": settings.LLM_MODEL_NAME,
        "disable_think": True,
    }


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
                    default_headers=headers,
                    # 此前 create 完全没传超时（openai 库默认 600s 挂死）——接 vl 行
                    # params.llm_timeout（缺省 300），与文本模型行为对齐（2026-11-08）
                    timeout=float(vl_config.get("llm_timeout") or 300))
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

    # 耗时剖析：视觉模型调用同样计入 parse 阶段（此前绕过 LLMClient，未被统计）
    from app.services.pipeline_profiler import (
        begin_llm_call,
        finish_llm_call,
        note_llm_attempt,
        note_llm_response,
    )

    _scope = begin_llm_call(
        model=vl_config["model"], stream=False,
        system_prompt="", user_prompt=prompt,
        caller="vl_parser._call_vl_model")
    try:
        note_llm_attempt()
        response = client.chat.completions.create(
            model=vl_config["model"],
            messages=[{"role": "user", "content": content}],
            max_tokens=VL_MAX_TOKENS,
            extra_body=extra_body if extra_body else None,
        )
        note_llm_response(response)
        content = response.choices[0].message.content or ""
        # 空响应降级（349 修复）：复杂多段提示词 + 9B 级小模型 + 思考模式 →
        # 全部输出预算被思考耗尽，返回空串（HTTP 200 但 content 空）。
        # 降级为简短直接提示词重试一次——简单指令下 9B 模型可完整读图。
        if not content.strip():
            retry_body = dict(extra_body or {})
            body_messages = [{"role": "user", "content": [
                {"type": "text",
                 "text": "提取图片中全部文字与表格内容，逐字照录，用 markdown 表格表达，不要遗漏。"},
                *[c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]]}]
            fallback = client.chat.completions.create(
                model=vl_config["model"],
                messages=[{"role": "user", "content": [
                    {"type": "text",
                     "text": "提取图片中全部文字与表格内容，逐字照录，用 markdown 表格表达，不要遗漏。"},
                    *[c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]]}],
                timeout=float(vl_config.get("llm_timeout") or 300),
                extra_body=retry_body if retry_body else None,
            )
            note_llm_response(fallback)
            content = fallback.choices[0].message.content or ""
            logger.warning(f"[VL解析] 空响应降级重试，返回 {len(content)} 字符")
        return content
    finally:
        finish_llm_call(_scope)


def describe_images(image_paths: list[str]) -> str:
    """S14：PPTX 内嵌图片 → 视觉模型语义描述（批量 VL_BATCH_SIZE 张/次）。

    与 parse_file_with_vl（整文档页转图）的区别：输入是散图（截图/流程图/表格照片），
    供 PPTX 图文解析按页调用。模型未配置/全部批次失败返回空串（调用方降级 OCR）。
    """
    if not image_paths:
        return ""
    try:
        cfg = _get_vl_config()
        if not cfg.get("model"):
            return ""
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[VL解析] 读取 VL 模型配置失败: {e}")
        return ""

    # 提示词简化（349 修复）：四段结构化要求会让 9B 级视觉模型放弃识图并
    # 幻觉"分辨率极低"（同一张图：复杂提示词答"仅见 CMDB"，简单逐行指令全量读出）。
    # 大模型对简单指令同样有效。
    prompt = (
        "请仔细读取图片中的全部内容：文字逐字照录；表格转成 markdown 表格逐行完整输出；"
        "流程/架构关系用「A → B → C」式表达。不要遗漏任何一行，不要评论图片质量。"
        "每张图片以『图N』开头分别输出（N 从 1 开始按传入顺序）。"
    )
    outs: list[str] = []
    for i in range(0, len(image_paths), VL_BATCH_SIZE):
        batch = image_paths[i:i + VL_BATCH_SIZE]
        try:
            outs.append(_call_vl_model(batch, prompt, cfg))
        except Exception as e:  # noqa: BLE001 —— 单批失败不阻断其余批次
            logger.warning(f"[VL解析] 图片第 {i + 1}-{i + len(batch)} 张描述失败: {e}")
    return "\n\n".join(o for o in outs if o and o.strip()).strip()


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
                # DOCX 直接抽取内嵌图片 → VL 逐图描述（349 修复）：不再走
                # docx→pdf 转换——snap libreoffice 本环境安装损坏（Fatal Error
                # 静默失败），且内嵌图片直读粒度更好（每张截图独立描述，
                # 操作手册类文档的关键内容全在截图里）
                from app.adapters.parsing import _extract_docx_images, _vl_describe_image_blobs

                image_blobs = _extract_docx_images(open(file_path, "rb").read())
                logger.info(f"[VL解析] DOCX 内嵌图片抽取: {len(image_blobs)} 张")
                if not image_blobs:
                    logger.info("[VL解析] DOCX 无内嵌图片，交回原生文本解析")
                    return ""
                vl_text = _vl_describe_image_blobs(image_blobs)
                elapsed = round(time.time() - start_time, 1)
                logger.info(f"[VL解析] DOCX 图片描述完成，{len(vl_text)} 字符，耗时 {elapsed}s")
                return f"\n\n## 图片内容（VL 视觉模型提取）\n\n{vl_text}"
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
