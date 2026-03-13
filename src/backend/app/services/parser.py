# app/services/parser.py - 文件解析器服务
# 功能：解析多种格式文件（PDF, DOCX, PPTX, XLS 等）并提取文本内容

import os
import re
import time
import pypdf
import docx
from pptx import Presentation
import pandas as pd
import subprocess
from typing import Union, List, Optional
from app.core.exceptions import FileProcessingException
from app.core.logging import logger


def clean_surrogate_characters(text: str) -> str:
    """
    清理文本中的 Unicode 代理字符（surrogate characters）。
    
    代理字符是 Unicode 中 U+D800 到 U+DFFF 范围内的字符，它们用于 UTF-16 编码中的
    辅助平面字符表示，但不能单独存在于有效的 UTF-8/UTF-32 文本中。
    
    PDF 文本提取时可能会产生这些无效字符，导致保存到 MySQL 时出现编码错误。
    
    Args:
        text: 输入文本
        
    Returns:
        清理后的文本
    """
    if not text:
        return text
    
    # 移除所有代理字符（U+D800 到 U+DFFF）
    # 这些字符在 Python 字符串中可能以 '\ud800' 到 '\udfff' 形式出现
    cleaned_text = re.sub(r'[\ud800-\udfff]', '', text)
    
    # 记录是否有字符被清理
    if len(cleaned_text) < len(text):
        removed_count = len(text) - len(cleaned_text)
        logger.warning(f"[clean_surrogate_characters] 已移除 {removed_count} 个 Unicode 代理字符")
    
    return cleaned_text


def process_files(file_list: Union[List, str]) -> str:
    """
    解析文件列表，提取文本内容。
    支持 PDF, DOCX, DOC, PPTX, XLSX, XLS, TXT, MD, CSV。
    """
    if not file_list:
        logger.info("[文件解析] 文件列表为空，跳过解析")
        return ""
    
    text_accumulated = ""
    # 兼容 Gradio 的文件列表和普通路径列表
    files = file_list if isinstance(file_list, list) else [file_list]
    
    logger.info(f"[文件解析] 开始解析 {len(files)} 个文件")
    
    for idx, f in enumerate(files):
        parse_start_time = time.time()
        try:
            # 获取文件名和路径
            fname = f.name if hasattr(f, 'name') else str(f)
            base_name = os.path.basename(fname)
            fname_lower = fname.lower()
            file_content = ""
            
            logger.info(f"[文件解析] [{idx+1}/{len(files)}] 开始处理文件：{base_name}")
            
            # 使用 'file' 命令检测实际的文件类型 (MIME type)
            mime_start_time = time.time()
            try:
                mime_result = subprocess.run(['file', '--mime-type', '-b', fname], capture_output=True, text=True, check=True)
                mime_type = mime_result.stdout.strip()
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 文件类型检测结果：MIME={mime_type}, 耗时={time.time()-mime_start_time:.2f}s")
            except Exception as e:
                logger.warning(f"[文件解析] [{idx+1}/{len(files)}] 文件类型检测失败：{e}")
                mime_type = ""

            # 优先根据 MIME 类型处理，如果检测失败则根据后缀处理
            if mime_type == "application/pdf" or fname_lower.endswith(".pdf"):
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 识别为 PDF 文件，开始提取文本...")
                pdf_start_time = time.time()
                reader = pypdf.PdfReader(fname)
                total_pages = len(reader.pages)
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] PDF 文件共 {total_pages} 页，开始逐页提取...")
                
                page_texts = []
                for page_idx, page in enumerate(reader.pages):
                    page_start = time.time()
                    page_text = page.extract_text() or ""
                    page_texts.append(page_text)
                    if (page_idx + 1) % 5 == 0 or page_idx == total_pages - 1:
                        logger.info(f"[文件解析] [{idx+1}/{len(files)}] 已提取 {page_idx+1}/{total_pages} 页，当前页耗时={time.time()-page_start:.2f}s")
                
                file_content = "\n".join(page_texts)
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] PDF 文本提取完成，总耗时={time.time()-pdf_start_time:.2f}s, 内容长度={len(file_content)} 字符")
            
            elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or fname_lower.endswith(".docx"):
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 识别为 DOCX 文件，开始提取文本...")
                docx_start_time = time.time()
                doc = docx.Document(fname)
                content_list = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        content_list.append(para.text)
                for table in doc.tables:
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        if any(row_cells):
                            content_list.append(" | ".join(row_cells))
                file_content = "\n".join(content_list)
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] DOCX 文本提取完成，耗时={time.time()-docx_start_time:.2f}s, 内容长度={len(file_content)} 字符")

            elif mime_type == "application/msword" or fname_lower.endswith(".doc"):
                # 尝试 antiword
                try:
                    result = subprocess.run(['antiword', fname], capture_output=True, text=True, check=True)
                    file_content = result.stdout
                except:
                    # 如果 antiword 失败，尝试作为 docx 处理
                    try:
                        doc = docx.Document(fname)
                        file_content = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
                    except:
                        # 尝试作为 MHTML 或 HTML 处理 (应对"另存为网页"的 doc)
                        try:
                            with open(fname, "r", encoding="utf-8", errors="ignore") as fo:
                                raw_data = fo.read()
                            
                            if "MIME-Version" in raw_data and "multipart/related" in raw_data:
                                # 解析 MHTML
                                import email
                                from email import policy
                                msg = email.message_from_string(raw_data, policy=policy.default)
                                html_part = ""
                                for part in msg.walk():
                                    if part.get_content_type() == "text/html":
                                        payload = part.get_payload(decode=True)
                                        charset = part.get_content_charset()
                                        # 修复 Word 生成的 MHTML 中常见的 charset=3D"utf-8" 导致 charset 被识别为 "3d" 的问题
                                        if charset and charset.lower().startswith('3d'):
                                            charset = charset[2:].strip('"')
                                        try:
                                            html_part = payload.decode(charset or 'utf-8', errors='ignore')
                                        except:
                                            html_part = payload.decode('utf-8', errors='ignore')
                                        break
                                if html_part:
                                    import re
                                    import html
                                    # 1. 移除 <style> 和 <script> 块
                                    html_part = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html_part, flags=re.DOTALL | re.IGNORECASE)
                                    # 2. 移除所有 HTML 标签
                                    file_content = re.sub(r'<[^>]+>', '', html_part)
                                    # 3. 还原 HTML 实体 (包括 &#25237; 这种数字编码)
                                    file_content = html.unescape(file_content)
                                    # 4. 还原 Unicode 转义序列 (如 \u3001)
                                    if "\\u" in file_content:
                                        import re
                                        file_content = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), file_content)
                                    # 5. 清洗空白符
                                    file_content = re.sub(r'\s+', ' ', file_content).strip()
                            elif "<html>" in raw_data.lower():
                                import re
                                import html
                                raw_data = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', raw_data, flags=re.DOTALL | re.IGNORECASE)
                                file_content = re.sub(r'<[^>]+>', '', raw_data)
                                file_content = html.unescape(file_content)
                                if "\\u" in file_content:
                                    file_content = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), file_content)
                                file_content = re.sub(r'\s+', ' ', file_content).strip()
                            else:
                                # 最后的保底：纯文本
                                file_content = raw_data if len(raw_data) > 10 else ""
                        except:
                            file_content = ""

            elif mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation" or fname_lower.endswith(".pptx"):
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 识别为 PPTX 文件，开始提取文本...")
                pptx_start_time = time.time()
                prs = Presentation(fname)
                ppt_text = []
                for i, slide in enumerate(prs.slides):
                    slide_content = []
                    for shape in slide.shapes:
                        if shape.has_table:
                            table_str = []
                            for row in shape.table.rows:
                                row_cells = [cell.text_frame.text.strip() for cell in row.cells if cell.text_frame]
                                if any(row_cells):
                                    table_str.append(" | ".join(row_cells))
                            if table_str:
                                slide_content.append("\n[表格内容]:\n" + "\n".join(table_str) + "\n")
                        elif shape.has_text_frame:
                            if shape.text_frame.text.strip():
                                slide_content.append(shape.text_frame.text.strip())
                    if slide_content:
                        ppt_text.append(f"[Page {i + 1}]:\n" + "\n".join(slide_content))
                file_content = "\n".join(ppt_text)
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] PPTX 文本提取完成，耗时={time.time()-pptx_start_time:.2f}s, 内容长度={len(file_content)} 字符")

            elif "excel" in mime_type or "spreadsheet" in mime_type or fname_lower.endswith((".xlsx", ".xls")):
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 识别为 Excel 文件，开始提取文本...")
                excel_start_time = time.time()
                try:
                    # 使用 openpyxl 引擎读取
                    dfs = pd.read_excel(fname, sheet_name=None, engine='openpyxl' if fname_lower.endswith('.xlsx') else None)
                    excel_parts = []
                    for sheet_idx, (sheet_name, df) in enumerate(dfs.items()):
                        if not df.empty:
                            # 使用 to_string 代替 to_markdown，避免依赖 tabulate 库
                            df_str = df.fillna("").astype(str).to_string(index=False)
                            excel_parts.append(f"\n-- Sheet: {sheet_name} --\n{df_str}\n")
                            logger.info(f"[文件解析] [{idx+1}/{len(files)}] 已处理 Sheet {sheet_idx+1}/{len(dfs)}: {sheet_name}")
                    file_content = "\n".join(excel_parts)
                    logger.info(f"[文件解析] [{idx+1}/{len(files)}] Excel 文本提取完成，耗时={time.time()-excel_start_time:.2f}s, 内容长度={len(file_content)} 字符")
                except Exception as e:
                    logger.warning(f"Excel 解析出错 ({base_name}): {str(e)}")
                    file_content = ""
            
            elif "text" in mime_type or fname_lower.endswith((".txt", ".md", ".csv")):
                with open(fname, "r", encoding="utf-8", errors="ignore") as fo:
                    file_content = fo.read()
            
            if file_content.strip():
                # 清理 Unicode 代理字符，避免保存到数据库时出现编码错误
                file_content = clean_surrogate_characters(file_content)
                text_accumulated += f"\n\n<<<<<< 文件开始：{base_name} >>>>>>\n{file_content}\n<<<<<< 文件结束：{base_name} >>>>>>\n"
                logger.info(f"[文件解析] [{idx+1}/{len(files)}] 文件处理成功，内容长度={len(file_content)} 字符")
            else:
                logger.warning(f"[解析警告] 无法从 {base_name} 提取有效内容 (MIME: {mime_type})")
        
        except Exception as e:
            logger.error(f"[读取失败] {base_name}: {str(e)}", exc_info=True)
        finally:
            logger.info(f"[文件解析] [{idx+1}/{len(files)}] 文件处理完成，总耗时={time.time()-parse_start_time:.2f}s")
            
    logger.info(f"[文件解析] 所有文件处理完成，累计内容长度={len(text_accumulated)} 字符")
    return text_accumulated


class FileParser:
    """
    文件解析类，负责多种文件格式的加载与文本提取
    """
    def parse_file(self, file_path: str) -> str:
        """
        解析单个文件并返回其文本内容
        """
        return process_files(file_path)

    def parse_files(self, file_paths: Union[List[str], str]) -> str:
        """
        解析多个文件并合并其文本内容
        """
        return process_files(file_paths)