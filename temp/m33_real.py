# M3-3 真实验证（02 §4 S1 / 02 §9 验收）
# 1) 存量项目全量回填 + 对账  2) 幂等重跑  3) 画布保存 API 双写  4) 删除清理
import io
import sys
import uuid

sys.path.insert(0, r"D:\python_code\OntologySystem\src\backend")

import bcrypt
import requests

BASE = "http://127.0.0.1:3001"
TAG = f"m33r{uuid.uuid4().hex[:6]}"

from app.infrastructure.database import (  # noqa: E402
    Entity, Project, ProvenanceRecord, Relation, SessionLocal, User,
)
from app.services.graph_rows import backfill_all_projects, reconcile_project  # noqa: E402

db = SessionLocal()
ok = []


def step(name, cond, detail=""):
    ok.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


GRAPH = {
    "nodes": [
        {"id": "设备", "data": {"label": "设备", "type": "owl:Class", "properties": {}}},
        {"id": "传感器", "data": {"label": "传感器", "type": "owl:Class", "properties": {}}},
        {"id": "inst_s1", "data": {"label": "温度传感器A", "type": "owl:NamedIndividual",
                                   "class_label": "传感器", "properties": {"量程": "-40~120℃"}}},
    ],
    "edges": [
        {"source": "传感器", "target": "设备", "data": {"relation": "subclass_of", "label": "子类"}},
        {"source": "inst_s1", "target": "传感器", "data": {"relation": "type"}},
    ],
}

user = proj = None
try:
    # 1) 存量回填 + 对账（真实项目）
    results = backfill_all_projects(db)
    for r in results:
        print("  回填:", r)
    step("存量回填无 error", all("error" not in r for r in results))
    step("全部项目对账通过", results and all(r.get("reconcile_ok") for r in results),
         f"{sum(1 for r in results if r.get('reconcile_ok'))}/{len(results)}")

    # 2) 幂等：重跑行数不变
    before = db.query(Entity).count()
    backfill_all_projects(db)
    after = db.query(Entity).count()
    step("回填幂等（行数不变）", before == after, f"{before} == {after}")

    # 3) 画布保存 API 双写（真实 HTTP → after_flush 监听器）
    admin = User(username=f"{TAG}_admin", role="admin", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add(admin)
    db.commit()
    db.refresh(admin)
    user = admin
    proj = Project(name=f"{TAG}_proj", owner_id=admin.id,
                   graph_data={"schema": {"classes": []}})
    db.add(proj)
    db.commit()
    db.refresh(proj)

    r = requests.post(f"{BASE}/api/auth/login",
                      data={"username": admin.username, "password": "Passw0rd!123"}, timeout=15)
    token = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = requests.put(f"{BASE}/api/projects/{proj.id}", headers=token, json={"graph_data": GRAPH},
                     timeout=15)
    step("画布保存 200", r.status_code == 200, r.text[:150])
    step("schema 键存续", "schema" in r.json().get("graph_data", {}))

    db.commit()
    ents = db.query(Entity).filter(Entity.project_id == proj.id).all()
    rels = db.query(Relation).filter(Relation.project_id == proj.id).all()
    step("画布保存双写 entities=3", len(ents) == 3, f"n={len(ents)}")
    step("画布保存双写 relations=2", len(rels) == 2, f"n={len(rels)}")
    step("TBox/ABox 判定", any(e.is_class_node for e in ents) and any(not e.is_class_node for e in ents))
    rec = reconcile_project(db, proj.id)
    step("画布保存后对账 ok", rec["ok"], str(rec))

    # 4) 再保存（删一个节点）→ 行表收敛到新 blob
    g2 = {"nodes": GRAPH["nodes"][:2], "edges": GRAPH["edges"][:1]}
    r = requests.put(f"{BASE}/api/projects/{proj.id}", headers=token, json={"graph_data": g2},
                     timeout=15)
    step("二次保存 200", r.status_code == 200)
    db.commit()
    ents2 = db.query(Entity).filter(Entity.project_id == proj.id).all()
    step("行表随 blob 收敛 entities=2", len(ents2) == 2, f"n={len(ents2)}")
    rec2 = reconcile_project(db, proj.id)
    step("二次保存对账 ok", rec2["ok"])

    # 5) 项目删除 → 行表清理（无 FK，监听器显式删）
    db.delete(proj)
    db.commit()
    n_left = db.query(Entity).filter(Entity.project_id == proj.id).count()
    step("项目删除行表清理", n_left == 0, f"left={n_left}")
finally:
    print("\n===== 汇总 =====")
    print(f"{sum(1 for _, c in ok if c)}/{len(ok)} PASS")
    # 清理测试用户/项目（proj 已删则跳过）
    try:
        if proj is not None:
            p = db.query(Project).filter(Project.id == proj.id).first()
            if p:
                db.delete(p)
                db.commit()
        if user is not None:
            row = db.query(User).filter(User.id == user.id).first()
            if row:
                db.delete(row)
                db.commit()
    except Exception as e:
        print(f"清理异常: {e}")
    print("清理完成")
