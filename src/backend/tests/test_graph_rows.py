# tests/test_graph_rows.py - M3-3 graph_data blob → 行表双写契约（docs/design/02 §3.5/§4）
# 真连 MySQL（沿用 M3 套路）；S1 语义：blob 为准，行表幂等全量重建 + 对账。
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import (
    Entity,
    Project,
    ProvenanceRecord,
    Relation,
    UploadedDocument,
    User,
)
from main import app

client = TestClient(app)
TAG = f"m33{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    import bcrypt

    user = User(username=f"{TAG}_user", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add(user)
    db.commit()
    db.refresh(user)
    proj = Project(name=f"{TAG}_proj", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield {"user": user, "proj": proj}
    # teardown：行表（FK 级联：relations/prov 随 entities/proj）+ 文档 + 项目 + 用户
    (db.query(ProvenanceRecord).filter(ProvenanceRecord.project_id == proj.id)
     .delete(synchronize_session=False))
    (db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id)
     .delete(synchronize_session=False))
    p = db.query(Project).filter(Project.id == proj.id).first()
    if p:
        db.delete(p)
    row = db.query(User).filter(User.id == user.id).first()
    if row:
        db.delete(row)
    db.commit()


GRAPH = {
    "nodes": [
        {"id": "产品", "data": {"label": "产品", "type": "owl:Class", "properties": {}}},
        {"id": "零件", "data": {"label": "零件", "type": "owl:Class", "properties": {}}},
        {"id": "ＫＧ平台", "data": {"label": "ＫＧ平台", "type": "owl:Class", "properties": {}}},
        {"id": "inst_1", "data": {"label": "星辰一号", "type": "owl:NamedIndividual",
                                  "class_label": "产品", "properties": {"成立年份": "2020"},
                                  "source_document": "报告.txt", "source_quote": "星辰一号是旗舰产品",
                                  "source_chunk_index": 3}},
        {"id": "inst_2", "data": {"label": "星辰二号", "type": "owl:NamedIndividual",
                                  "class_label": "产品",
                                  "source_document": "不存在的文档.txt"}},
    ],
    "edges": [
        {"source": "零件", "target": "产品", "data": {"relation": "subclass_of", "label": "子类"}},
        {"source": "产品", "target": "ＫＧ平台", "data": {"relation": "object_property", "label": "属于"}},
        {"source": "inst_1", "target": "产品", "data": {"relation": "type"}},
        {"source": "inst_1", "target": "inst_2", "data": {"relation": "object_property"}},
        {"source": "inst_1", "target": "幽灵节点", "data": {"relation": "object_property"}},  # 悬空
        {"source": "零件", "target": "产品", "data": {"relation": "subclass_of"}},  # 重复边
    ],
}


def _rows(db, model, proj_id):
    db.commit()  # 结束 REPEATABLE_READ 快照
    return db.query(model).filter(model.project_id == proj_id).all()


def test_r5_tables_exist(db):
    """R5 迁移落地：四张治理表存在（alembic upgrade head 后）。"""
    from sqlalchemy import inspect

    names = set(inspect(db.connection()).get_table_names())
    assert {"entities", "relations", "provenance_records", "review_items"} <= names


def test_sync_tbox_abox_and_edges(db, env):
    """拆行：TBox/ABox 判定、归一化、关系端点解析、悬空边跳过、重复边去重、class_edge 判定。"""
    from app.services.graph_rows import entity_uri, sync_project_rows

    proj = env["proj"].id
    stats = sync_project_rows(db, proj, GRAPH)
    db.commit()
    assert stats["entities"] == 5 and stats["relations"] == 4
    assert stats["skipped_edges"] == 1

    ents = {e.label: e for e in _rows(db, Entity, proj)}
    assert ents["产品"].is_class_node and ents["零件"].is_class_node
    assert not ents["星辰一号"].is_class_node
    assert ents["星辰一号"].class_label == "产品"
    assert ents["星辰一号"].props == {"成立年份": "2020"}
    assert ents["星辰一号"].uri == entity_uri(proj, "inst_1")
    assert ents["星辰一号"].uri.startswith(f"urn:onto:{proj}:entity/")
    assert ents["星辰一号"].status == "auto"
    # 归一化：全角 → 半角 + 小写（isascii 守卫，中文原样）
    assert ents["ＫＧ平台"].label_normalized == "kg平台"

    rels = _rows(db, Relation, proj)
    lab = {k: v.id for k, v in ents.items()}
    by_pred = {}
    for r in rels:
        by_pred.setdefault(r.predicate, []).append(r)
    assert len(by_pred["subclass_of"]) == 1  # 重复边去重
    assert by_pred["subclass_of"][0].is_class_edge
    assert by_pred["object_property"][0].is_class_edge  # 类间
    type_rel = by_pred["type"][0]  # 实例→类
    assert not type_rel.is_class_edge
    assert (type_rel.subject_id, type_rel.object_id) == (lab["星辰一号"], lab["产品"])
    # 悬空边不落库
    assert all(r.subject_id in lab.values() and r.object_id in lab.values() for r in rels)


def test_sync_idempotent_and_reconcile(db, env):
    """幂等：重复 sync 不产生重复行；对账 ok=True（S2 切读前置条件）。"""
    from app.services.graph_rows import reconcile_project, sync_project_rows

    proj = env["proj"].id
    sync_project_rows(db, proj, GRAPH)
    db.commit()
    n1 = len(_rows(db, Entity, proj))
    stats2 = sync_project_rows(db, proj, GRAPH)
    db.commit()
    n2 = len(_rows(db, Entity, proj))
    assert n1 == n2 == 5 and stats2["entities"] == 5

    # blob 与行表一致 → 对账通过
    p = db.query(Project).filter(Project.id == proj).first()
    old = p.graph_data
    p.graph_data = GRAPH
    db.commit()
    rec = reconcile_project(db, proj)
    assert rec["ok"], rec
    assert rec["nodes_blob"] == rec["nodes_rows"] == 5
    assert rec["uri_missing"] == [] and rec["uri_extra"] == []
    p.graph_data = old
    db.commit()


def test_sync_provenance_resolution(db, env):
    """溯源：source_document 按文件名解析到 uploaded_documents；文件缺失跳过并计数。"""
    from app.services.graph_rows import sync_project_rows

    proj = env["proj"].id
    db.add(UploadedDocument(project_id=proj, filename="报告.txt", file_path="x/y.txt",
                            file_size=10, file_type="txt"))
    db.commit()
    stats = sync_project_rows(db, proj, GRAPH)
    db.commit()
    assert stats["skipped_prov"] == 1  # "不存在的文档.txt"

    provs = _rows(db, ProvenanceRecord, proj)
    assert len(provs) == 1
    p0 = provs[0]
    assert p0.target_type == "entity" and p0.chunk_index == 3
    assert p0.evidence_text == "星辰一号是旗舰产品"
    assert p0.extraction_method == "llm_ner" and len(p0.checksum) == 64


def test_canvas_save_dual_write(env, db):
    """画布保存（PUT graph_data）触发双写：行表落库 + schema 键存续（旧契约）。"""
    token_resp = client.post("/api/auth/login",
                             data={"username": env["user"].username, "password": "Passw0rd!123"})
    token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    proj = env["proj"].id

    # 预置 schema 键（抽取链路写入的权威 TBox 原文）
    p = db.query(Project).filter(Project.id == proj).first()
    p.graph_data = {"schema": {"classes": [{"name": "产品"}]}, **GRAPH}
    db.commit()

    r = client.put(f"/api/projects/{proj}", headers=headers,
                   json={"graph_data": {"nodes": GRAPH["nodes"], "edges": GRAPH["edges"]}})
    assert r.status_code == 200, r.text
    assert "schema" in r.json()["graph_data"]  # schema 键不被画布覆盖（旧契约）

    ents = _rows(db, Entity, proj)
    assert len(ents) == 5  # API 保存路径的双写生效（监听器覆盖，无需端点改造）


def test_project_delete_cleans_rows(db, env):
    """项目删除 → 行表清理（entities.project_id 无 FK，监听器显式删，防孤儿行）。"""
    proj = Project(name=f"{TAG}_del", owner_id=env["user"].id, graph_data=GRAPH)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    pid = proj.id
    db.commit()  # 结束快照，读监听器写入的行
    assert db.query(Entity).filter(Entity.project_id == pid).count() == 5

    db.delete(proj)
    db.commit()
    db.commit()
    assert db.query(Entity).filter(Entity.project_id == pid).count() == 0
    assert db.query(Relation).filter(Relation.project_id == pid).count() == 0
    assert db.query(ProvenanceRecord).filter(ProvenanceRecord.project_id == pid).count() == 0


def test_backfill_all_projects(db, env):
    """存量回填：直接改 blob（绕过监听器场景）→ backfill_all_projects 重建 + 对账通过。"""
    from app.services.graph_rows import backfill_all_projects, reconcile_project

    proj = env["proj"].id
    p = db.query(Project).filter(Project.id == proj).first()
    p.graph_data = {"nodes": [], "edges": []}  # 清空 blob，行表应同步为空
    db.commit()
    db.query(Entity).filter(Entity.project_id == proj).delete(synchronize_session=False)
    db.commit()

    results = backfill_all_projects(db)
    mine = [r for r in results if r["project_id"] == proj]
    assert mine and mine[0].get("reconcile_ok") is True, mine
    assert len(_rows(db, Entity, proj)) == 0  # blob 空 → 行表空（blob 为准）

    p.graph_data = GRAPH
    db.commit()
    results = backfill_all_projects(db)
    mine = [r for r in results if r["project_id"] == proj]
    assert mine[0]["entities"] == 5 and mine[0]["reconcile_ok"] is True
    rec = reconcile_project(db, proj)
    assert rec["ok"]


def test_review_items_model(db, env):
    """review_items 可用（M3-4/5/6 物化冲突与审核项的目标表）。"""
    from app.infrastructure.database import ReviewItem

    proj = env["proj"].id
    db.add(ReviewItem(project_id=proj, item_type="conflict_value",
                      payload={"a": 1}, reason="属性值冲突",
                      priority="high", status="pending"))
    db.commit()
    rows = db.query(ReviewItem).filter(ReviewItem.project_id == proj,
                                       ReviewItem.item_type == "conflict_value").all()
    assert len(rows) == 1 and rows[0].priority == "high" and rows[0].status == "pending"
    db.delete(rows[0])
    db.commit()
