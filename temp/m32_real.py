# M3-2 真实 API 验证（对照 08 §2 / 04 §2 验收门槛，真实 worker 队列 + SSE）
# 1) 上传 auto_parse → 真实 parse 队列解析 parsed  2) SSE parse-events 收到终态流
# 3) chunks 落库 + 偏移可回溯  4) text_key MinIO 对象一致  5) 手动 re-parse 指定 backend
# 6) 扫描件/坏文件诚实失败（parse_error 可见）
import io
import json
import sys
import time
import uuid

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")

import bcrypt
import requests

BASE = "http://127.0.0.1:3001"
TAG = f"m32r{uuid.uuid4().hex[:6]}"

from app.core.config import settings  # noqa: E402
from app.infrastructure.database import (  # noqa: E402
    DocumentChunk, Project, SessionLocal, UploadedDocument, User, UserModuleGrant,
)
from app.infrastructure.minio_client import get_minio_client  # noqa: E402

db = SessionLocal()
admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
             hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
user = User(username=f"{TAG}_user", role="user", is_active=True,
            hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
db.add_all([admin, user]); db.commit(); db.refresh(admin); db.refresh(user)
db.add(UserModuleGrant(user_id=user.id, module_code="documents", allowed=1, granted_by=admin.id))
db.commit()
proj = Project(name=f"{TAG}_proj", owner_id=user.id)
db.add(proj); db.commit(); db.refresh(proj)

minio_keys = []


def login(u):
    r = requests.post(f"{BASE}/api/auth/login", data={"username": u, "password": "Passw0rd!123"}, timeout=15)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


ok = []


def step(name, cond, detail=""):
    ok.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def wait_parsed(doc_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.commit()
        row = db.query(UploadedDocument).filter(UploadedDocument.id == doc_id).first()
        if row is not None and row.parse_status in ("parsed", "failed"):
            return row
        time.sleep(1)
    return None


try:
    ut = login(f"{TAG}_user")

    # 1) 上传 auto_parse（txt 长 + md + csv）→ 真队列解析
    body = ("星辰科技是一家从事人工智能的中国公司。它成立于2020年，总部位于杭州。"
            "主营产品包括知识图谱平台与本体编辑器，服务金融、政务与制造行业客户。"
            "公司拥有员工八百余人，其中研发人员占比超过百分之六十。" * 40)
    files = [
        ("files", ("真实验证报告.txt", io.BytesIO(body.encode()), "text/plain")),
        ("files", ("真实验证.md", io.BytesIO(("# 章节标题\n- 要点甲\n- 要点乙\n" * 80).encode()), "text/markdown")),
        ("files", ("真实验证.csv", io.BytesIO("姓名,部门,职级\n张三,研发,P7\n李四,产品,P5\n王五,交付,P6\n".encode() * 20), "text/csv")),
    ]
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/upload?auto_parse=true",
                      headers=ut, files=files, timeout=30)
    step("upload auto_parse=200", r.status_code == 200, r.text[:200])
    saved = r.json()["saved"]
    step("task_id 已派发", all(d.get("task_id") for d in saved), str([d.get("task_id", "")[:8] for d in saved]))

    rows = []
    for d in saved:
        row = wait_parsed(d["id"])
        rows.append(row)
        step(f"文档{d['id']} parsed", row is not None and row.parse_status == "parsed",
             f"status={row.parse_status if row else '超时'} err={(getattr(row, 'parse_error', None) or '')[:120]}")
    step("parse_backend=native", all(row.parse_backend == "native" for row in rows),
         str([row.parse_backend for row in rows]))
    step("text_key 规范", all(row.text_key == f"{proj.id}/{row.id}.md" for row in rows))
    step("text_content 双写", all((row.text_content or "").strip() for row in rows),
         str([len(row.text_content or "") for row in rows]))
    step("page_count/language 回填", all(row.page_count >= 1 and row.language for row in rows),
         str([(row.page_count, row.language) for row in rows]))

    # 2) chunks 落库 + 偏移可回溯原文
    all_chunks = []
    for row in rows:
        db.commit()
        cs = (db.query(DocumentChunk).filter(DocumentChunk.document_id == row.id)
              .order_by(DocumentChunk.chunk_index).all())
        all_chunks.append(cs)
        step(f"文档{row.id} chunks≥1", len(cs) >= 1, f"count={len(cs)}")
        if cs:
            src = row.text_content
            c0 = cs[0]
            step(f"文档{row.id} 偏移回溯", src[c0.char_start:c0.char_start + len(c0.text)] == c0.text)

    # 3) SSE：签发 ticket → parse-events 收流（用第一个文档手动 re-parse 产生新任务）
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/{rows[0].id}/parse",
                      headers=ut, json={"backend": "native"}, timeout=15)
    step("re-parse 200", r.status_code == 200, r.text[:200])
    task_id = r.json()["task_id"]
    tk = requests.get(f"{BASE}/api/auth/sse-ticket?task_id={task_id}", headers=ut, timeout=15)
    step("sse-ticket 200", tk.status_code == 200, tk.text[:120])
    ticket = tk.json()["ticket"]
    events = []
    with requests.get(f"{BASE}/api/projects/{proj.id}/documents/parse-events?task_id={task_id}&ticket={ticket}",
                      stream=True, timeout=120) as sse:
        step("SSE 200 + event-stream", sse.status_code == 200 and "text/event-stream" in sse.headers.get("content-type", ""))
        for line in sse.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1].get("status") in ("completed", "failed"):
                    break
    step("SSE 收到终态", events and events[-1]["status"] == "completed",
         f"事件数={len(events)} 末态={events[-1]['status'] if events else '无'}")
    step("SSE 事件含阶段进度", any(e.get("stage") for e in events),
         str({e.get("stage") for e in events}))
    step("SSE 事件含 stats", any(e.get("stats") for e in events), str(events[-1].get("stats")))

    # ticket 一次性：复用 → 401
    r = requests.get(f"{BASE}/api/projects/{proj.id}/documents/parse-events?task_id={task_id}&ticket={ticket}", timeout=10)
    step("ticket 一次性(复用 401)", r.status_code == 401)

    # re-parse 后重读新快照（REPEATABLE_READ：旧对象读不到其他会话的更新）
    db.commit(); db.expire_all()
    rows = [db.query(UploadedDocument).filter(UploadedDocument.id == x.id).first() for x in rows]

    # 4) text_key MinIO 对象字节一致
    mc = get_minio_client()
    for row in rows:
        if row.text_key:
            minio_keys.append((settings.MINIO_BUCKET_PARSED, row.text_key))
            data = mc.get_bytes(settings.MINIO_BUCKET_PARSED, row.text_key)
            step(f"文档{row.id} MinIO 全文一致", data.decode("utf-8") == row.text_content,
                 f"obj={len(data)} db={len(row.text_content.encode('utf-8'))}")

    # 5) 游标分页（真实 chunks）
    row_big = max(rows, key=lambda x: len(all_chunks[rows.index(x)]))
    n = len(all_chunks[rows.index(row_big)])
    if n >= 2:
        r1 = requests.get(f"{BASE}/api/projects/{proj.id}/documents/{row_big.id}/chunks?limit=1", headers=ut, timeout=10)
        j1 = r1.json()
        step("分页 page1=1 且有 cursor", len(j1["chunks"]) == 1 and j1["next_cursor"] is not None)
        r2 = requests.get(f"{BASE}/api/projects/{proj.id}/documents/{row_big.id}/chunks?limit=200&cursor={j1['next_cursor']}", headers=ut, timeout=10)
        j2 = r2.json()
        step("分页 page2 补齐且末页 cursor=null",
             len(j2["chunks"]) == n - 1 and j2["next_cursor"] is None, f"total={n}")

    # 6) 诚实失败：伪扫描 PDF（无文本层）→ docling_ocr 熔断 → 明确 parse_error
    fake_pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
                b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\n%%EOF")
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/upload?auto_parse=false",
                      headers=ut,
                      files=[("files", ("扫描件.pdf", io.BytesIO(fake_pdf), "application/pdf"))], timeout=30)
    scan_id = r.json()["saved"][0]["id"]
    r = requests.post(f"{BASE}/api/projects/{proj.id}/documents/{scan_id}/parse",
                      headers=ut, json={"backend": "docling_ocr"}, timeout=15)
    step("扫描件+docling_ocr 派发 200", r.status_code == 200, r.text[:150])
    scan_row = wait_parsed(scan_id, timeout=120)
    step("扫描件诚实失败", scan_row is not None and scan_row.parse_status == "failed"
         and (scan_row.parse_error or "").strip() != "",
         f"status={scan_row.parse_status if scan_row else '超时'} err={(scan_row.parse_error or '')[:150]}")
    if scan_row and scan_row.storage_key:
        minio_keys.append((settings.MINIO_BUCKET_UPLOADS, scan_row.storage_key))

    # 列表端点 chunk_count 汇总
    rl = requests.get(f"{BASE}/api/projects/{proj.id}/documents", headers=ut, timeout=10)
    listing = {d["id"]: d for d in rl.json()["documents"]}
    step("列表 chunk_count 正确", all(
        listing[row.id]["chunk_count"] == len(all_chunks[i]) for i, row in enumerate(rows)))
finally:
    print("\n===== 汇总 =====")
    passed = sum(1 for _, c in ok if c)
    print(f"{passed}/{len(ok)} PASS")
    # 清理
    db.commit()
    for d in db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id).all():
        if d.storage_key:
            try:
                get_minio_client().remove_object(settings.MINIO_BUCKET_UPLOADS, d.storage_key)
            except Exception:
                pass
        if d.text_key:
            try:
                get_minio_client().remove_object(settings.MINIO_BUCKET_PARSED, d.text_key)
            except Exception:
                pass
    (db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(
        [d.id for d in db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id).all()]
    )).delete(synchronize_session=False)) if False else None
    (db.query(UploadedDocument)
     .filter(UploadedDocument.project_id == proj.id).delete(synchronize_session=False))
    p = db.query(Project).filter(Project.id == proj.id).first()
    if p:
        db.delete(p)
    db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).delete()
    for u in (admin, user):
        row = db.query(User).filter(User.id == u.id).first()
        if row:
            db.delete(row)
    db.commit()
    print("清理完成")
