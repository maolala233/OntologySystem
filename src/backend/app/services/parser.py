# app/services/parser.py - 文件解析器服务
# 功能：解析多种格式文件（PDF, DOCX, PPTX, XLS等）并提取文本内容

import os
import pypdf
import docx
from pptx import Presentation
import pandas as pd
import subprocess
from typing import Union, List, Optional
from app.core.exceptions import FileProcessingException
from app.core.logging import logger


def process_files(file_list: Union[List, str]) -> str:
    """
    解析文件列表，提取文本内容。
    支持 PDF, DOCX, DOC, PPTX, XLSX, XLS, TXT, MD, CSV。
    """
    if not file_list:
        return ""
    
    text_accumulated = ""
    # 兼容 Gradio 的文件列表和普通路径列表
    files = file_list if isinstance(file_list, list) else [file_list]
    
    for f in files:
        try:
            # 获取文件名和路径
            fname = f.name if hasattr(f, 'name') else str(f)
            base_name = os.path.basename(fname)
            fname_lower = fname.lower()
            file_content = ""
            
            # 使用 'file' 命令检测实际的文件类型 (MIME type)
            try:
                mime_result = subprocess.run(['file', '--mime-type', '-b', fname], capture_output=True, text=True, check=True)
                mime_type = mime_result.stdout.strip()
            except:
                mime_type = ""

            # 优先根据 MIME 类型处理，如果检测失败则根据后缀处理
            if mime_type == "application/pdf" or fname_lower.endswith(".pdf"):
                reader = pypdf.PdfReader(fname)
                file_content = "\n".join([page.extract_text() or "" for page in reader.pages])
            
            elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or fname_lower.endswith(".docx"):
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

            elif "excel" in mime_type or "spreadsheet" in mime_type or fname_lower.endswith((".xlsx", ".xls")):
                try:
                    # 使用 openpyxl 引擎读取
                    dfs = pd.read_excel(fname, sheet_name=None, engine='openpyxl' if fname_lower.endswith('.xlsx') else None)
                    excel_parts = []
                    for sheet_name, df in dfs.items():
                        if not df.empty:
                            # 使用 to_string 代替 to_markdown，避免依赖 tabulate 库
                            df_str = df.fillna("").astype(str).to_string(index=False)
                            excel_parts.append(f"\n-- Sheet: {sheet_name} --\n{df_str}\n")
                    file_content = "\n".join(excel_parts)
                except Exception as e:
                    logger.warning(f"Excel 解析出错 ({base_name}): {str(e)}")
                    file_content = ""
            
            elif "text" in mime_type or fname_lower.endswith((".txt", ".md", ".csv")):
                with open(fname, "r", encoding="utf-8", errors="ignore") as fo:
                    file_content = fo.read()
            
            if file_content.strip():
                text_accumulated += f"\n\n<<<<<< 文件开始: {base_name} >>>>>>\n{file_content}\n<<<<<< 文件结束: {base_name} >>>>>>\n"
            else:
                logger.warning(f"[解析警告] 无法从 {base_name} 提取有效内容 (MIME: {mime_type})")
        
        except Exception as e:
            logger.error(f"[读取失败] {base_name}: {str(e)}")
            
    return text_accumulated