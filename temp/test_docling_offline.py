# docling 离线验证（内网模拟：HF_HUB_OFFLINE=1）：文本层 PDF + 扫描件 OCR 两条通道
import io
import os

os.environ["HF_HUB_OFFLINE"] = "1"  # 禁 HuggingFace 联网，模拟内网

import sys

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")
os.chdir(r"D:\python_code\OntologySystem\src\backend")  # .env 按 CWD 读取

import fitz

# ① 文本层 PDF
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 100), "Ontology Platform Docling Offline Test", fontsize=14)
page.insert_text((72, 130), "Investment scope: domestic securities investment fund.", fontsize=11)
b = doc.tobytes()
doc.close()
io.open("../temp/_t_text.pdf", "wb").write(b)

# ② 扫描件 PDF（页面栅格化成图片再嵌入，无文本层）
doc = fitz.open(stream=b, filetype="pdf")
p = doc[0]
rect = p.rect
pix = p.get_pixmap(dpi=150)
img = pix.tobytes("png")
doc.close()
doc2 = fitz.open()
p2 = doc2.new_page(width=rect.width, height=rect.height)
p2.insert_image(p2.rect, stream=img)
b2 = doc2.tobytes()
doc2.close()
io.open("../temp/_t_scan.pdf", "wb").write(b2)
print("test PDFs generated")

import app.adapters.parsing as P

d1 = P._parse_docling(b, "text.pdf", ocr=False)
print("text-layer:", P.ParseBackend.DOCLING if d1 else "FALLBACK",
      "|", d1.full_text_md[:80].replace("\n", " ") if d1 else "")
d2 = P._parse_docling(b2, "scan.pdf", ocr=True)
print("scan-ocr:", P.ParseBackend.DOCLING_OCR if d2 else "FALLBACK",
      "|", d2.full_text_md[:80].replace("\n", " ") if d2 else "")
