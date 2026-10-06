# tests/test_graph_view.py - M4 图视图契约 + outbox 消费链（docs/design/03 §13 / 06 §4 / 02 §5）
# 行表为事实源（监听器自动双写）；graph-view 端点 + 节点/边详情 + Neo4j 消费（真中间件）
# + Oxigraph 命名图写入 + 预布局。纯行表部分离线，Neo4j/Oxigraph 部分连真中间件。
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
TAG = f"m4{uuid.uuid4().hex[:6]}"
PWD = "Passw0rd!123"


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    import bcrypt

    from app.infrastructure.database import Project, UploadedDocument, User, UserModuleGrant

    user = User(username=f"{TAG}_u", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode())
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserModuleGrant(user_id=user.id, module_code="version_control", allowed=1,
                           granted_by=user.id))
    db.commit()

    # graph_data 提交 → 监听器自动拆行表（S1 双写）
    gd = {
        "schema": {"classes": [{"label": "公司"}, {"label": "产品"}],
                   "object_properties": [{"label": "研发", "domain": "公司", "range": "产品"}]},
        "nodes": [
            {"id": "c1", "data": {"label": "公司", "type": "owl:Class"}},
            {"id": "c2", "data": {"label": "产品", "type": "owl:Class"}},
            {"id": "i1", "data": {"label": "华信科技", "type": "owl:NamedIndividual",
                                  "class_label": "公司", "properties": {"成立": "2011"},
                                  "source_document": "d.txt", "source_quote": "华信科技成立于2011年"}},
            {"id": "i2", "data": {"label": "信鸽平台", "type": "owl:NamedIndividual",
                                  "class_label": "产品", "properties": {}}},
            {"id": "i3", "data": {"label": "信鸽Pro", "type": "owl:NamedIndividual",
                                  "class_label": "产品", "properties": {}}},
        ],
        "edges": [
            {"id": "e1", "source": "i1", "target": "c1", "data": {"relation": "instance_of"}},
            {"id": "e2", "source": "i2", "target": "c2", "data": {"relation": "instance_of"}},
            {"id": "e3", "source": "i3", "target": "c2", "data": {"relation": "instance_of"}},
            {"id": "e4", "source": "i1", "target": "i2", "data": {"relation": "研发", "confidence": 0.9}},
            {"id": "e5", "source": "i2", "target": "i1", "data": {"relation": "研发"}},
            {"id": "e6", "source": "i2", "target": "i3", "data": {"relation": "升级"}},
            {"id": "e7", "source": "c1", "target": "c2", "data": {"relation": "研发"}},
        ],
    }
    proj = Project(name=f"{TAG}_p", owner_id=user.id, graph_data=gd)
    db.add(proj)
    db.commit()  # 监听器首次拆行（此时无文档 → provenance 暂缺）
    db.refresh(proj)
    doc = UploadedDocument(project_id=proj.id, filename="d.txt",
                           file_path="x", file_type="txt", parse_status="parsed")
    db.add(doc)
    db.commit()
    db.refresh(doc)
    # 文档就位后重放行表同步 → provenance（entity 级溯源）建立
    from app.services.graph_rows import sync_project_rows

    sync_project_rows(db, proj.id, proj.graph_data)
    db.commit()
    yield {"user": user, "proj": proj, "doc": doc}
    # 清理：Neo4j 投影 + Oxigraph + 行表 + blob
    from app.infrastructure.neo4j_client import neo4j_client
    from app.services.oxigraph_sink import delete_project_graphs

    try:
        neo4j_client.delete_project_data(proj.id)
    except Exception:  # noqa: BLE001
        pass
    try:
        delete_project_graphs(proj.id)
    except Exception:  # noqa: BLE001
        pass
    from app.infrastructure.database import (
        AuditLog,
        Entity,
        GraphLayout,
        OutboxEvent,
        ProvenanceRecord,
        Relation,
        ReviewItem,
    )

    (db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == proj.id,
                                  OutboxEvent.aggregate_type.in_(("project", "publication")))
     .delete(synchronize_session=False))
    for model in (ProvenanceRecord, Relation, Entity, ReviewItem, GraphLayout):
        (db.query(model).filter(model.project_id == proj.id)
         .delete(synchronize_session=False))
    db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id)\
        .delete(synchronize_session=False)
    (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
     .delete(synchronize_session=False))
    db.delete(proj)
    (db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id)
     .delete(synchronize_session=False))
    db.delete(user)
    db.commit()


def _login(username: str) -> dict:
    r = client.post("/api/auth/login", data={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_graph_meta_and_nodes(env, db):
    h = _login(env["user"].username)
    pid = env["proj"].id
    db.rollback()
    r = client.get(f"/api/projects/{pid}/graph/meta", headers=h)
    body = r.json()
    assert r.status_code == 200 and body["node_count"] == 5 and body["edge_count"] == 7
    dist = {d["class_label"]: d["count"] for d in body["class_distribution"]}
    # 类节点自身也计入其 class_label（产品=2 实例 + 1 类节点）
    assert dist.get("产品") == 3 and dist.get("公司") == 2

    r = client.get(f"/api/projects/{pid}/graph/nodes?limit=3", headers=h)
    body = r.json()
    assert body["total"] == 3 and body["next_cursor"] == 3
    kinds = {n["kind"] for n in body["items"]}
    assert kinds <= {"class", "instance"}
    n1 = next(n for n in body["items"] if n["label"] == "华信科技")
    assert n1["group"] == "公司" and n1["degree"] >= 3  # instance_of + 研发×2

    r = client.get(f"/api/projects/{pid}/graph/nodes?class=产品", headers=h)
    # 类节点自身 class_label=产品 也命中过滤
    assert {n["label"] for n in r.json()["items"]} == {"信鸽平台", "信鸽Pro", "产品"}


def test_graph_edges_bidirectional(env, db):
    h = _login(env["user"].username)
    pid = env["proj"].id
    r = client.get(f"/api/projects/{pid}/graph/edges", headers=h)
    edges = {e["id"]: e for e in r.json()["items"]}
    assert len(edges) == 7
    # e4/e5 构成 (i1,i2,研发) 双向对 → 服务端判定 bidirectional + pairIndex 相异
    e4 = edges[[k for k, v in edges.items() if v["label"] == "研发"
                and not v["is_class_edge"]][0]]
    bidi = [e for e in edges.values() if e["bidirectional"]]
    assert len(bidi) == 2 and {e["pairIndex"] for e in bidi} == {0, 1}
    assert {e["source"] for e in bidi} == {e4["source"], e4["target"]}
    # 类型边判定
    type_edges = [e for e in edges.values() if e["kind"] == "type"]
    assert len(type_edges) == 3


def test_graph_neighbors_and_search(env, db):
    h = _login(env["user"].username)
    pid = env["proj"].id
    db.rollback()
    i1 = client.get(f"/api/projects/{pid}/graph/search?q=华信", headers=h).json()["items"][0]
    r = client.get(f"/api/projects/{pid}/graph/neighbors/{i1['id']}?hops=1", headers=h)
    body = r.json()
    labels = {n["label"] for n in body["nodes"]}
    assert body["root_id"] == i1["id"] and {"公司", "信鸽平台"} <= labels
    # 搜索前缀+包含
    r = client.get(f"/api/projects/{pid}/graph/search?q=信鸽", headers=h)
    assert len(r.json()["items"]) == 2


def test_node_and_edge_detail(env, db):
    h = _login(env["user"].username)
    pid = env["proj"].id
    db.rollback()
    from app.infrastructure.database import Entity, ReviewItem

    i1 = (db.query(Entity).filter(Entity.project_id == pid, Entity.label == "华信科技").first())
    # 造合并留痕 + 待审核项
    i2 = (db.query(Entity).filter(Entity.project_id == pid, Entity.label == "信鸽平台").first())
    item = ReviewItem(project_id=pid, item_type="entity_merge", priority="high",
                      status="approved",
                      payload={"canonical_id": i1.id, "merged_ids": [i2.id], "similarity": 0.9},
                      result_ref={"canonical_id": i1.id, "merged_ids": [i2.id]})
    db.add(item)
    db.commit()

    r = client.get(f"/api/projects/{pid}/graph/node/{i1.id}/detail", headers=h)
    body = r.json()
    assert r.status_code == 200
    assert body["node"]["label"] == "华信科技" and body["node"]["kind"] == "instance"
    assert body["entity"]["class_label"] == "公司"
    assert body["properties"].get("成立") == "2011"
    # 溯源：source_document=d.txt → provenance 命中且带 evidence
    provs = body["provenance"]
    assert len(provs) == 1 and provs[0]["document_name"] == "d.txt"
    assert "华信科技成立于2011年" in (provs[0]["evidence"] or "")
    # 关系（出边 研发→信鸽平台；入边 研发←信鸽平台；instance_of）
    preds = {(rr["predicate"], rr["direction"]) for rr in body["relations"]}
    assert ("研发", "out") in preds and ("研发", "in") in preds
    assert body["degree"] == len(body["relations"])
    # 合并留痕
    assert body["merge"]["canonical_id"] is None
    assert any(m["review_id"] == item.id for m in body["merge"]["merge_reviews"])
    # 时间线属性
    assert body["timeline"] == {"valid_from": None, "valid_until": None}

    # 边详情
    e_id = int([rr["id"] for rr in body["relations"] if rr["direction"] == "out"
                and rr["predicate"] == "研发"][0])
    r = client.get(f"/api/projects/{pid}/graph/edge/{e_id}/detail", headers=h)
    edge = r.json()
    assert r.status_code == 200 and edge["edge"]["label"] == "研发"
    assert edge["subject"]["label"] == "华信科技" and edge["object"]["label"] == "信鸽平台"
    assert edge["confidence"] is not None  # rel.props 里带 confidence 0.9 → props 侧
    # 404 分支
    assert client.get(f"/api/projects/{pid}/graph/node/999999/detail",
                      headers=h).status_code == 404


def test_layout_flow(env, db):
    h = _login(env["user"].username)
    pid = env["proj"].id
    # ≤25k → 受理拒绝
    r = client.post(f"/api/projects/{pid}/graph/layout", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "LAYOUT_NOT_NEEDED"
    # 直调布局计算（绕过 Celery）
    from app.api.graph_view import compute_and_store_layout

    db.rollback()
    res = compute_and_store_layout(db, pid)
    assert res["node_count"] == 5 and res["coords"] == 5
    coords = res["coords"]
    # 确定性：同输入两次布局坐标 diff = 0（06 §12.2）
    res2 = compute_and_store_layout(db, pid)
    assert coords == res2["coords"]
    r = client.get(f"/api/projects/{pid}/graph/layout", headers=h)
    body = r.json()
    assert body["available"] is True and len(body["coords"]) == 5


def test_outbox_consumer_neo4j(env, db):
    """outbox → worker-graph → Neo4j（真中间件，MERGE 幂等 + DETACH 删除）。"""
    from app.infrastructure.database import OutboxEvent
    from app.infrastructure.neo4j_client import neo4j_client
    from app.tasks.graph_tasks import _handle_graph_event

    pid = env["proj"].id
    db.rollback()
    ev = (db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == pid,
                                       OutboxEvent.event_type == "project.graph_rebuilt")
          .order_by(OutboxEvent.id.desc()).first())
    assert ev is not None  # 监听器已随 graph_data 提交发出
    # 直调处理器（模拟 worker）
    detail = _handle_graph_event(db, ev)
    assert detail == "neo4j synced"
    # Neo4j 里能查到该项目的节点（project_id 属性必写）
    with neo4j_client.driver.session() as session:
        rec = session.run("MATCH (n) WHERE n.project_id=$pid RETURN count(n) AS c",
                          pid=pid).single()
        assert rec["c"] >= 5, f"Neo4j 节点数 {rec['c']} < 5"
        rec2 = session.run("MATCH ()-[r]->() WHERE r.project_id=$pid RETURN count(r) AS c",
                           pid=pid).single()
        assert rec2["c"] >= 7
    # 删除事件 → DETACH DELETE
    db.add(OutboxEvent(aggregate_type="project", aggregate_id=pid,
                       event_type="project.deleted", payload={"project_id": pid}))
    db.commit()
    del_ev = (db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == pid,
                                           OutboxEvent.event_type == "project.deleted")
              .order_by(OutboxEvent.id.desc()).first())
    assert _handle_graph_event(db, del_ev) == "neo4j deleted"
    with neo4j_client.driver.session() as session:
        rec = session.run("MATCH (n) WHERE n.project_id=$pid RETURN count(n) AS c",
                          pid=pid).single()
        assert rec["c"] == 0


def test_oxigraph_named_graphs(env, db):
    """worker-rdf 语义：四命名图整体替换 + 发布快照图（真 pyoxigraph）。"""
    from app.services.oxigraph_sink import _store, delete_project_graphs, write_project_graphs

    pid = env["proj"].id
    db.rollback()
    res = write_project_graphs(db, pid, publication_version_no=1)
    assert res["ok"] is True, res
    store = _store()  # 复用 sink 单例（RocksDB 独占锁）
    names = {str(g).strip("<>") for g in store.named_graphs()}
    base = f"urn:onto:{pid}"
    for g in (f"{base}:tbox", f"{base}:abox", f"{base}:prov", f"{base}:pub:1"):
        assert g in names, f"缺命名图 {g}，现有={names}"
    # tbox：2 个类 + 1 个对象属性(domain/range)
    import pyoxigraph as ox

    tbox_quads = list(store.quads_for_pattern(None, None, None,
                                              graph_name=ox.NamedNode(f"{base}:tbox")))
    assert len(tbox_quads) >= 5
    # 幂等重写：计数不变
    res2 = write_project_graphs(db, pid, publication_version_no=1)
    assert res2["ok"] and res2["graphs"][f"{base}:tbox"] == res["graphs"][f"{base}:tbox"]
    # 清理
    assert delete_project_graphs(pid)["ok"] is True
    names2 = {str(g).strip("<>") for g in store.named_graphs()}
    assert f"{base}:tbox" not in names2


def test_graph_view_requires_membership(env, db):
    import bcrypt

    from app.infrastructure.database import SessionLocal, User, UserModuleGrant

    s2 = SessionLocal()
    other = User(username=f"{TAG}_x", role="user", is_active=True,
                 hashed_password=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode())
    s2.add(other)
    s2.commit()
    s2.refresh(other)
    try:
        h = _login(other.username)
        r = client.get(f"/api/projects/{env['proj'].id}/graph/meta", headers=h)
        assert r.status_code == 403
    finally:
        (s2.query(UserModuleGrant).filter(UserModuleGrant.user_id == other.id)
         .delete(synchronize_session=False))
        s2.delete(other)
        s2.commit()
        s2.close()
