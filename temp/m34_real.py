# -*- coding: utf-8 -*-
"""M3-4 真实验证探针：真实 LLM 链路（API→Celery extract 队列→方舟模型→画布+行表双写）。
从 src/backend 为 CWD 运行：../../.venv/Scripts/python temp/m34_real.py（.env 按 CWD 读取）。
"""
import io
import sys
import time
import uuid

import bcrypt
import requests

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")  # 探针任意 CWD 可跑

BASE = "http://127.0.0.1:3001"
TAG = f"m34r{uuid.uuid4().hex[:6]}"
results = []


def step(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


# ── 造数：用户 + schema_build/documents 授权 + 项目 ──
from app.infrastructure.database import (  # noqa: E402
    Entity, Project, Relation, SessionLocal, UploadedDocument, User, UserModuleGrant,
)

db = SessionLocal()
user = User(username=f"{TAG}_u", role="user", is_active=True,
            hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
db.add(user)
db.commit()
db.refresh(user)
for code in ("documents", "schema_build"):
    db.add(UserModuleGrant(user_id=user.id, module_code=code, allowed=1, granted_by=user.id))
proj = Project(name=f"{TAG}_p", owner_id=user.id)
db.add(proj)
db.commit()
db.refresh(proj)
PID = proj.id
UID = user.id

H = {}
try:
    # 1) 登录
    r = requests.post(f"{BASE}/api/auth/login",
                      data={"username": user.username, "password": "Passw0rd!123"}, timeout=10)
    step("登录", r.status_code == 200, f"status={r.status_code}")
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # 2) 上传真实金融文本（~7000 字 → 2000 字/片 → ≥3 切片，触发并行）
    para_1 = (
        "星辰基金管理有限公司是一家老牌公募基金公司，成立于2008年，注册资本3亿元人民币。"
        "公司旗下最著名的理财产品是星辰一号混合型证券投资基金，该产品成立于2015年3月，"
        "风险评级为R3（平衡型），最新规模为58.7亿元。星辰一号的投资目标是追求长期资本增值，"
        "主要投资于沪深300成分股。产品每年开放一次申购赎回，管理费率为1.2%。"
        "星辰二号债券型证券投资基金是公司的固定收益旗舰产品，成立于2017年8月，"
        "风险评级为R2（稳健型），最新规模102.4亿元。该产品主要投资于高等级信用债和利率债，"
        "管理费率为0.6%，每年开放四次申赎。"
        "2024年，公司推出了星辰稳健配置混合型FOF，该产品通过配置旗下基金实现资产配置，"
        "风险评级为R2，规模为15.3亿元。"
    )
    para_2 = (
        "在销售体系方面，客户可以通过星辰基金APP或各大代销银行购买上述理财产品。"
        "中国工商银行、建设银行和招商银行是公司最主要的代销渠道，三家银行合计贡献了"
        "超过六成的销售规模。购买前投资者需要进行风险测评，确保客户风险承受等级"
        "不低于产品风险评级。对于R3及以上风险等级的产品，销售时必须执行双录（录音录像）流程。"
        "机构客户如保险公司和银行理财产品户，则可以通过直销柜台完成申购，"
        "并享受大额申购的资金结算绿色通道。"
    )
    para_3 = (
        "在托管与运营方面，星辰基金担任基金管理人，与中国工商银行等托管银行签订托管协议，"
        "托管银行负责资金保管、净值复核与清算交收。星辰一号由中国工商银行托管，"
        "星辰二号由建设银行托管，星辰稳健配置FOF由招商银行托管。"
        "基金公司每年从基金资产中计提管理费，托管银行计提托管费，"
        "销售机构计提客户维护费（尾随佣金）。"
    )
    para_4 = (
        "在风险管理上，公司实行三级风控体系：投研部门自查、风控部门复核、合规部门审计。"
        "风控部门每日对投资组合进行合规监控，发现超限情况需要在规定时限内平仓整改。"
        "对于债券投资，公司严格控制单一发行人的集中度，防范信用风险。"
        "2023年公司发生过一次操作性风险事件：因系统故障导致一笔申赎指令延迟确认，"
        "公司按照法规对受影响客户进行了赔偿，并向监管机构报送了重大事项报告。"
        "合规部门每季度开展员工行为排查，防范老鼠仓与利益输送行为。"
    )
    para_5 = (
        "在投研团队方面，公司设立了权益投资部、固定收益部、量化投资部和研究部。"
        "研究部负责覆盖宏观经济、行业与上市公司基本面研究，为投资决策提供支持。"
        "星辰一号的基金经理张伟从业十五年，擅长成长股投资；"
        "星辰二号的基金经理李芳专注信用债研究十年以上。"
        "公司每年对基金经理进行业绩考核，考核指标包括相对收益、回撤控制与合规记录，"
        "连续两年考核不合格的基金经理将被调整岗位。"
        "星辰基金还与多家券商签订研究服务协议，券商提供卖方研究报告，公司支付交易分仓佣金。"
    )
    para_6 = (
        "在客户服务方面，公司建立了客户分级服务体系：普通投资者通过客服热线与APP获得标准服务，"
        "高净值客户由专属客户经理提供一对一服务，机构客户由机构销售团队对接。"
        "公司定期举办投资策略报告会，向客户解读市场展望与产品运作情况。"
        "客户投诉需要在规定时限内办结，重大投诉直接上报合规部门处理。"
        "公司还运营投资者教育基地，向公众普及基金投资知识与风险意识。"
    )
    doc_text = "\n\n".join([para_1, para_2, para_3, para_4, para_5, para_6])
    r = requests.post(
        f"{BASE}/api/projects/{PID}/documents/upload?auto_parse=false",
        headers=H, timeout=30,
        files=[("files", (f"{TAG}_基金简介.txt", io.BytesIO(doc_text.encode("utf-8")), "text/plain"))])
    step("上传文档", r.status_code in (200, 201), f"status={r.status_code} body={r.text[:120]}")
    doc_id = r.json()["saved"][0]["id"] if r.status_code in (200, 201) else None

    # 3) 真实解析（parse 队列真 worker；chunk_size=800 强制多切片触发并行）
    r = requests.post(f"{BASE}/api/projects/{PID}/documents/{doc_id}/parse",
                      headers=H, json={"backend": "auto", "chunk_size": 800}, timeout=30)
    step("发起解析", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
    status = ""
    for _ in range(60):
        time.sleep(2)
        r = requests.get(f"{BASE}/api/projects/{PID}/documents", headers=H, timeout=10)
        docs = r.json() if isinstance(r.json(), list) else r.json().get("documents", [])
        me = [d for d in docs if d["id"] == doc_id]
        status = me[0]["parse_status"] if me else "?"
        if status in ("parsed", "failed"):
            break
    n_chunks = 0
    if status == "parsed":
        r = requests.get(f"{BASE}/api/projects/{PID}/documents/{doc_id}/chunks",
                         headers=H, timeout=10)
        body = r.json()
        n_chunks = len(body["chunks"]) if isinstance(body, dict) else len(body)
    step("解析完成且切片≥2", status == "parsed" and n_chunks >= 2,
         f"parse_status={status} chunks={n_chunks}")

    # 4) 发起 Schema 抽取（extract 队列真 worker + 真 LLM）
    r = requests.post(f"{BASE}/api/projects/{PID}/extraction/schema",
                      headers=H, json={"parallelism": 4}, timeout=30)
    step("发起 Schema 抽取", r.status_code == 200 and "task_id" in r.json(),
         f"status={r.status_code} body={r.text[:150]}")
    task_id = r.json().get("task_id", "")

    # 5) 轮询任务进度至终态
    prog = {}
    t0 = time.time()
    while time.time() - t0 < 420:
        time.sleep(3)
        r = requests.get(f"{BASE}/api/projects/{PID}/extraction/tasks/{task_id}",
                         headers=H, timeout=10)
        prog = r.json()
        if prog.get("status") in ("completed", "failed", "cancelled"):
            break
    stats = prog.get("stats") or {}
    step("抽取任务完成", prog.get("status") == "completed",
         f"status={prog.get('status')} stage={prog.get('stage')} "
         f"classes={stats.get('classes')} ops={stats.get('object_properties')} "
         f"chunks={stats.get('chunks_processed')} cache_hits={stats.get('cache_hits')} "
         f"warn={len(stats.get('warnings') or [])}")

    # 6) 产物：graph_data["schema"] 权威 TBox + 画布节点边
    r = requests.get(f"{BASE}/api/projects/{PID}", headers=H, timeout=10)
    gd = r.json().get("graph_data") or {}
    schema = gd.get("schema") or {}
    nodes, edges = gd.get("nodes") or [], gd.get("edges") or []
    class_nodes = [n for n in nodes if (n.get("data") or {}).get("type") == "Class"]
    step("schema 键与画布产物", bool(schema.get("classes")) and len(class_nodes) >= 3
         and len(edges) >= 1,
         f"classes={len(schema.get('classes') or [])} ops={len(schema.get('object_properties') or [])} "
         f"nodes={len(nodes)}(Class={len(class_nodes)}) edges={len(edges)}")

    # 7) 行表双写：类节点 → entities(is_class_node=1)；对象属性边 → relations(is_class_edge=1)
    db.commit()
    ents = db.query(Entity).filter(Entity.project_id == PID).all()
    rels = db.query(Relation).filter(Relation.project_id == PID).all()
    step("行表双写（entities/relations）", len(ents) == len(nodes) and len(rels) == len(edges)
         and all(e.is_class_node for e in ents),
         f"entities={len(ents)}/{len(nodes)} relations={len(rels)}/{len(edges)}")

    # 8) SSE 事件流（一次性 ticket → events 收流）
    r = requests.post(f"{BASE}/api/projects/{PID}/extraction/schema",
                      headers=H, json={"parallelism": 4}, timeout=30)
    task2 = r.json()["task_id"]
    r = requests.get(f"{BASE}/api/auth/sse-ticket", headers=H, params={"task_id": task2}, timeout=10)
    ticket = r.json()["ticket"]
    got = {"progress": False, "terminal": False}
    t0 = time.time()
    with requests.get(f"{BASE}/api/projects/{PID}/extraction/tasks/{task2}/events",
                      params={"ticket": ticket}, stream=True, timeout=120) as resp:
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                payload = eval(line[5:].strip()) if False else __import__("json").loads(line[5:].strip())
                if payload.get("stage") in ("extracting", "queued", "writing"):
                    got["progress"] = True
                if payload.get("status") in ("completed", "failed", "cancelled"):
                    got["terminal"] = True
                    break
            if time.time() - t0 > 110:
                break
    step("SSE 进度事件收流", got["progress"] and got["terminal"],
         f"progress={got['progress']} terminal={got['terminal']}")

    # 9) 缺陷 #9 缓存：第二轮抽取 cache_hits 应等于切片数（重跑成本 0）
    t0 = time.time()
    while time.time() - t0 < 420:
        time.sleep(3)
        r = requests.get(f"{BASE}/api/projects/{PID}/extraction/tasks/{task2}",
                         headers=H, timeout=10)
        if r.json().get("status") in ("completed", "failed", "cancelled"):
            break
    s2 = (r.json().get("stats") or {})
    step("重跑命中缓存（缺陷#9）", s2.get("cache_hits") == s2.get("chunks_processed"),
         f"cache_hits={s2.get('cache_hits')}/{s2.get('chunks_processed')} "
         f"elapsed={time.time()-t0:.0f}s")

    # 10) 跨项目任务探测防护
    r = requests.get(f"{BASE}/api/projects/{PID}/extraction/tasks/fake-task-id",
                     headers=H, timeout=10)
    step("任务不存在→404", r.status_code == 404, f"status={r.status_code}")

finally:
    # 清理：行表 → 文档 → 项目 → 授权 → 用户
    try:
        db.commit()
        db.query(Entity).filter(Entity.project_id == PID).delete(synchronize_session=False)
        db.query(UploadedDocument).filter(UploadedDocument.project_id == PID)\
            .delete(synchronize_session=False)
        db.commit()
        p = db.query(Project).filter(Project.id == PID).first()
        if p:
            db.delete(p)
        db.query(UserModuleGrant).filter(UserModuleGrant.user_id == UID)\
            .delete(synchronize_session=False)
        u = db.query(User).filter(User.id == UID).first()
        if u:
            db.delete(u)
        db.commit()
        print("cleanup done")
    except Exception as e:
        db.rollback()
        print(f"cleanup error: {e}")
    finally:
        db.close()

fails = [r for r in results if not r[1]]
print(f"\n==== M3-4 真实验证 {len(results) - len(fails)}/{len(results)} PASS ====")
sys.exit(1 if fails else 0)
