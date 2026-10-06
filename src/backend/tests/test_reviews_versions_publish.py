# tests/test_reviews_versions_publish.py - M3-6 审核/版本/发布契约（docs/design/03 §10-§12 / 04 §7-§10）
# 状态机与裁决落地直连 MySQL/MinIO（沿用 M3 套路）：MinIO 快照写真实 ontology-parsed 桶。
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
TAG = f"m36{uuid.uuid4().hex[:6]}"
PWD = "Passw0rd!123"


def _mk_user(db, name, role="user"):
    import bcrypt

    from app.infrastructure.database import User

    u = User(username=name, role=role, is_active=True,
             hashed_password=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode())
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _mk_proj(db, owner_id, graph_data=None, domains=None):
    from app.infrastructure.database import Project

    p = Project(name=f"{TAG}_{uuid.uuid4().hex[:6]}", owner_id=owner_id,
                domains=domains, graph_data=graph_data or {"schema": {"classes": [], "object_properties": []},
                                                           "nodes": [], "edges": []})
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _mk_item(db, project_id, item_type="conflict_value", priority="high", payload=None):
    from app.infrastructure.database import ReviewItem

    it = ReviewItem(project_id=project_id, item_type=item_type, priority=priority,
                    payload=payload or {"foo": "bar"}, reason="测试项")
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


# ── 审核状态机（04 §7.2）──


def test_review_claim_and_state_machine(db):
    from app.services.review_service import ReviewStateError, claim_item, decide_item

    user = _mk_user(db, f"{TAG}_sm")
    other = _mk_user(db, f"{TAG}_sm2")
    proj = _mk_proj(db, user.id)
    try:
        item = _mk_item(db, proj.id)
        # 非法 action
        with pytest.raises(ReviewStateError) as ei:
            decide_item(item, user, "delete", db=db)
        assert ei.value.code == "INVALID_ACTION"
        # 认领 → claimed
        claim_item(item, user, db)
        assert item.status == "claimed" and item.assigned_to == user.id
        # 他人重复认领 → ALREADY_CLAIMED
        with pytest.raises(ReviewStateError) as ei:
            claim_item(item, other, db)
        assert ei.value.code == "ALREADY_CLAIMED"
        # 他人裁决 → ALREADY_CLAIMED；本人裁决 → approved 终态
        with pytest.raises(ReviewStateError) as ei:
            decide_item(item, other, "approve", db=db)
        assert ei.value.code == "ALREADY_CLAIMED"
        res = decide_item(item, user, "approve", note="ok", db=db)
        assert res["status"] == "approved" and res["effect"] == {"closed": "conflict_value"}
        # 终态不可再裁决
        with pytest.raises(ReviewStateError):
            decide_item(item, user, "reject", db=db)
    finally:
        from app.infrastructure.database import AuditLog, OutboxEvent, ReviewItem

        (db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(OutboxEvent).filter(OutboxEvent.event_type.like("review.%"),
                                      OutboxEvent.aggregate_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.delete(other)
        db.commit()


def test_decide_entity_merge_approve_executes_merge(db):
    from app.infrastructure.database import Entity, OutboxEvent
    from app.services.review_service import decide_item

    user = _mk_user(db, f"{TAG}_em")
    proj = _mk_proj(db, user.id)
    try:
        e1 = Entity(project_id=proj.id, uri=f"urn:t:{proj.id}:a", label="工商银行",
                    label_normalized="工商银行", class_label="银行", props={})
        e2 = Entity(project_id=proj.id, uri=f"urn:t:{proj.id}:b", label="工商銀行",
                    label_normalized="工商银行", class_label="银行", props={})
        db.add_all([e1, e2])
        db.commit()
        db.refresh(e1)
        db.refresh(e2)
        item = _mk_item(db, proj.id, item_type="entity_merge", priority="high",
                        payload={"canonical_id": e1.id, "merged_ids": [e2.id],
                                 "similarity": 0.95, "label": "工商银行"})
        res = decide_item(item, user, "approve", db=db)
        assert res["status"] == "approved"
        assert res["effect"]["merged"] == 1
        db.expire_all()
        e2b = db.query(Entity).filter(Entity.id == e2.id).first()
        assert e2b.status == "merged" and e2b.canonical_id == e1.id
        # outbox 留痕
        ev = (db.query(OutboxEvent).filter(OutboxEvent.event_type == "review.approve")
              .order_by(OutboxEvent.id.desc()).first())
        assert ev is not None and ev.payload["review_id"] == item.id
        e2_bid = e2.id
        e1_id = e1.id
    finally:
        from app.infrastructure.database import AuditLog, Entity, OutboxEvent, ReviewItem

        (db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(OutboxEvent).filter(OutboxEvent.aggregate_type == "project")
         .delete(synchronize_session=False))
        (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
         .delete(synchronize_session=False))
        (db.query(Entity).filter(Entity.project_id == proj.id)
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.commit()
    assert e2_bid and e1_id  # 行 id 在事务外仍可引用


def test_decide_new_class_approve_writes_tbox(db):
    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project
    from app.services.review_service import decide_item

    user = _mk_user(db, f"{TAG}_nc")
    proj = _mk_proj(db, user.id)
    try:
        item = _mk_item(db, proj.id, item_type="new_class", priority="medium",
                        payload={"label": "虚拟资产", "definition": "链上资产"})
        res = decide_item(item, user, "approve", db=db)
        assert res["effect"] == {"class_upserted": "虚拟资产"}
        db.expire_all()
        gd = db.query(Project).filter(Project.id == proj.id).first().graph_data
        labels = [c["label"] for c in gd["schema"]["classes"]]
        assert "虚拟资产" in labels
        node = [n for n in gd["nodes"] if n["data"].get("label") == "虚拟资产"][0]
        assert node["data"]["type"] == "Class" and node["data"]["approved_from"] == "new_class"
        # 重复批准不重复写入
        item2 = _mk_item(db, proj.id, item_type="new_class",
                         payload={"label": "虚拟资产"})
        decide_item(item2, user, "approve", db=db)
        db.expire_all()
        gd2 = db.query(Project).filter(Project.id == proj.id).first().graph_data
        assert [c["label"] for c in gd2["schema"]["classes"]].count("虚拟资产") == 1
        assert flag_modified is not None
    finally:
        from app.infrastructure.database import AuditLog, OutboxEvent, ReviewItem

        (db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(OutboxEvent).filter(OutboxEvent.aggregate_type == "project")
         .delete(synchronize_session=False))
        (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.commit()


def test_batch_decide_constraints(db):
    from app.services.review_service import ReviewStateError, batch_decide

    user = _mk_user(db, f"{TAG}_bd")
    proj = _mk_proj(db, user.id)
    try:
        i1 = _mk_item(db, proj.id, item_type="entity_merge", priority="high",
                      payload={"canonical_id": 1, "merged_ids": [2], "similarity": 0.6})
        i2 = _mk_item(db, proj.id, item_type="conflict_value")
        # 混类型
        with pytest.raises(ReviewStateError) as ei:
            batch_decide(proj.id, [i1.id, i2.id], "approve", user, db)
        assert ei.value.code == "BATCH_TYPE_MISMATCH"
        # entity_merge 批量仅 approve
        with pytest.raises(ReviewStateError) as ei:
            batch_decide(proj.id, [i1.id], "reject", user, db)
        assert ei.value.code == "BATCH_ACTION_INVALID"
        # similarity < 0.75
        with pytest.raises(ReviewStateError) as ei:
            batch_decide(proj.id, [i1.id], "approve", user, db)
        assert ei.value.code == "BATCH_SIMILARITY_TOO_LOW"
        # 超 50 条
        with pytest.raises(ReviewStateError) as ei:
            batch_decide(proj.id, list(range(1, 52)), "approve", user, db)
        assert ei.value.code == "BATCH_SIZE_INVALID"
        # 同类型 conflict_value 批量拒绝 → 全部终态
        i3 = _mk_item(db, proj.id, item_type="conflict_value")
        res = batch_decide(proj.id, [i2.id, i3.id], "reject", user, db, note="批量核实")
        assert res["batch"] == 2 and res["rejected"] == 2
    finally:
        from app.infrastructure.database import AuditLog, OutboxEvent, ReviewItem

        (db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(OutboxEvent).filter(OutboxEvent.aggregate_type == "project")
         .delete(synchronize_session=False))
        (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.commit()


# ── 版本控制（04 §9）：打版/快照/diff/回滚/时间轴 ──

GD_V1 = {"schema": {"classes": [{"label": "客户", "definition": "人", "properties": []}],
                    "object_properties": []},
         "nodes": [{"id": "c1", "data": {"label": "客户", "type": "Class"}}],
         "edges": []}


def test_versioning_snapshot_diff_restore(db):
    from app.adapters.versioning import (
        compute_checksum,
        create_version,
        diff_versions,
        get_snapshot_json,
        restore_version,
    )
    from app.core.config import settings
    from app.infrastructure.database import GraphSnapshot, OntologyVersion
    from app.infrastructure.minio_client import get_minio_client

    user = _mk_user(db, f"{TAG}_ver")
    proj = _mk_proj(db, user.id, graph_data=GD_V1)
    keys = []
    try:
        v1 = create_version(db, proj, "schema", user.id, label="v1")
        db.commit()
        assert v1.version_no == 1
        assert proj.current_version_id == v1.id
        assert v1.stats["classes"] == 1
        assert v1.checksum == compute_checksum(GD_V1)
        assert v1.full_snapshot_key == f"snapshots/{proj.id}/v1.json"
        keys.append(v1.full_snapshot_key)
        # MinIO 快照真实落桶
        payload = get_snapshot_json(v1.full_snapshot_key)
        assert payload is not None and payload["graph_data"]["nodes"][0]["id"] == "c1"
        assert get_minio_client().stat_object(settings.MINIO_BUCKET_PARSED,
                                              v1.full_snapshot_key) is not None

        # v2：加一个类 + 一个实例
        import copy

        gd2 = copy.deepcopy(GD_V1)
        gd2["schema"]["classes"].append({"label": "账户", "definition": "", "properties": []})
        gd2["nodes"].append({"id": "i1", "data": {"label": "张三", "type": "Instance",
                                                  "class_label": "客户", "properties": {}}})
        proj.graph_data = gd2
        db.commit()
        v2 = create_version(db, proj, "full", user.id, label="v2")
        db.commit()
        keys.append(v2.full_snapshot_key)
        assert v2.version_no == 2 and v2.parent_version_id is None
        diff = diff_versions(db, proj, v1, v2)
        assert diff["classes"]["added"] == ["账户"]
        assert diff["instances"]["added"] == ["i1"]

        # 回滚到 v1：pre_rollback 快照 + rollback 版本（parent=回滚前 current）
        prev_current = proj.current_version_id
        res = restore_version(db, proj, v1, user.id)
        db.commit()
        keys.append(res["pre_rollback_key"])
        assert res["restored_from"] == 1 and res["new_version_no"] == 3
        db.expire_all()
        proj2 = db.query(type(proj)).filter_by(id=proj.id).first()
        assert proj2.graph_data["schema"]["classes"] == GD_V1["schema"]["classes"]
        rb = (db.query(OntologyVersion)
              .filter(OntologyVersion.project_id == proj.id, OntologyVersion.kind == "rollback")
              .order_by(OntologyVersion.version_no.desc()).first())
        assert rb.version_no == 3 and rb.parent_version_id == prev_current
        pre = (db.query(GraphSnapshot)
               .filter(GraphSnapshot.project_id == proj.id,
                       GraphSnapshot.purpose == "pre_rollback").first())
        assert pre is not None and pre.version_id is None
        # 回滚后可再回滚（版本链不断）
        res2 = restore_version(db, proj2, rb, user.id)
        db.commit()
        keys.append(f"snapshots/{proj.id}/v4.json")
        assert res2["new_version_no"] == 4
    finally:

        for v in (db.query(OntologyVersion).filter(OntologyVersion.project_id == proj.id).all()):
            keys.append(v.full_snapshot_key)
        (db.query(OntologyVersion).filter(OntologyVersion.project_id == proj.id)
         .delete(synchronize_session=False))
        (db.query(GraphSnapshot).filter(GraphSnapshot.project_id == proj.id)
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.commit()
        for k in {k for k in keys if k}:
            try:
                get_minio_client().client.remove_object(settings.MINIO_BUCKET_PARSED, k)
            except Exception:  # noqa: BLE001
                pass


def test_timeline_and_entity_history(db):

    from app.adapters.versioning import entity_history, timeline
    from app.infrastructure.database import Entity, UploadedDocument

    user = _mk_user(db, f"{TAG}_tl")
    proj = _mk_proj(db, user.id)
    doc = None
    try:
        doc = UploadedDocument(project_id=proj.id, filename=f"{TAG}.txt",
                               file_path=f"/tmp/{TAG}.txt", storage_key=f"{TAG}/f.txt",
                               file_type="txt", parse_status="parsed")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        e = Entity(project_id=proj.id, uri=f"urn:t:{proj.id}:t1", label="星辰一号",
                   label_normalized="星辰一号", class_label="理财产品",
                   props={"valid_from": "2024-01-01", "valid_until": "2025-06-30"})
        db.add(e)
        db.commit()
        db.refresh(e)
        tl = timeline(db, proj.id, time_axis="both")
        axes = {ev["axis"] for ev in tl["events"]}
        assert axes == {"valid", "transaction"}
        valid_ev = [ev for ev in tl["events"] if ev["axis"] == "valid"]
        assert {ev["type"] for ev in valid_ev} == {"valid_from", "valid_until"}
        bad = timeline(db, proj.id, entity_uri=f"urn:t:{proj.id}:t1", time_axis="valid")
        assert len(bad["events"]) == 2
        # 实体史（行表 + 溯源 + 合并留痕）
        h = entity_history(db, proj.id, e.id)
        assert h["entity"]["label"] == "星辰一号"
        assert isinstance(h["provenance"], list) and h["merge_history"] == []
    finally:
        from app.infrastructure.database import AuditLog, Entity

        (db.query(Entity).filter(Entity.project_id == proj.id)
         .delete(synchronize_session=False))
        if doc is not None:
            db.delete(doc)
        (db.query(AuditLog).filter(AuditLog.resource_id == str(proj.id))
         .delete(synchronize_session=False))
        db.delete(proj)
        db.delete(user)
        db.commit()


def test_quality_gate_warnings(db):
    from app.adapters.versioning import quality_gate

    gd = {"schema": {"classes": [{"label": "客户"}],
                     "object_properties": [{"label": "购买", "domain": "客户", "range": "产品"}]},
          "nodes": [{"id": "i1", "data": {"type": "Instance", "class_label": "账户"}}],
          "edges": []}
    health = quality_gate(gd)
    assert health["passed"] is True
    assert any("「产品」不在类清单" in w for w in health["warnings"])
    assert any("未定义类" in w and "账户" in w for w in health["warnings"])
    assert quality_gate({"schema": {"classes": []}, "nodes": [], "edges": []})["passed"] is False


# ── API 契约（03 §11/§12）──


@pytest.fixture(scope="module")
def api_env(db):
    from app.infrastructure.database import UserModuleGrant

    user = _mk_user(db, f"{TAG}_api")
    for code in ("review", "version_control", "publish", "asset_center"):
        db.add(UserModuleGrant(user_id=user.id, module_code=code, allowed=1,
                               granted_by=user.id))
    db.commit()
    proj = _mk_proj(db, user.id,
                    graph_data={"schema": {"classes": [{"label": "理财产品"}],
                                           "object_properties": []},
                                "nodes": [{"id": "c1", "data": {"label": "理财产品",
                                                                "type": "Class"}}],
                                "edges": []},
                    domains="金融")
    yield {"user": user, "proj": proj, "headers": _login(user.username)}
    _cleanup_project(db, proj, user)


def _login(username: str) -> dict:
    r = client.post("/api/auth/login",
                    data={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _cleanup_project(db, proj, user):
    from app.core.config import settings
    from app.infrastructure.database import (
        AuditLog,
        Entity,
        GraphSnapshot,
        OntologyVersion,
        OutboxEvent,
        ProvenanceRecord,
        Publication,
        Relation,
        ReviewItem,
        UploadedDocument,
        UserModuleGrant,
    )
    from app.infrastructure.minio_client import get_minio_client

    for v in db.query(OntologyVersion).filter(OntologyVersion.project_id == proj.id).all():
        if v.full_snapshot_key:
            try:
                get_minio_client().client.remove_object(settings.MINIO_BUCKET_PARSED,
                                                        v.full_snapshot_key)
            except Exception:  # noqa: BLE001
                pass
    item_ids = [str(i[0]) for i in db.query(ReviewItem.id)
                .filter(ReviewItem.project_id == proj.id).all()]
    for model in (ProvenanceRecord, Relation, Entity, ReviewItem, Publication,
                  OntologyVersion, GraphSnapshot, UploadedDocument):
        (db.query(model).filter(model.project_id == proj.id)
         .delete(synchronize_session=False))
    (db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == proj.id,
                                  OutboxEvent.aggregate_type.in_(("project", "publication")))
     .delete(synchronize_session=False))
    (db.query(AuditLog).filter((AuditLog.resource_id == str(proj.id))
                               | (AuditLog.resource_id.in_(item_ids)))
     .delete(synchronize_session=False))
    (db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id)
     .delete(synchronize_session=False))
    db.delete(proj)
    db.delete(user)
    db.commit()


def test_api_versions_flow(api_env, db):
    env = api_env
    h, proj = env["headers"], env["proj"].id

    # 手动打版：kind 非法 → 400
    r = client.post(f"/api/projects/{proj}/versions", headers=h,
                    json={"kind": "publication"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_KIND"
    r = client.post(f"/api/projects/{proj}/versions", headers=h,
                    json={"kind": "schema", "label": "手动版"})
    assert r.status_code == 200 and r.json()["version_no"] == 1, r.text
    r = client.post(f"/api/projects/{proj}/versions", headers=h, json={"kind": "full"})
    assert r.status_code == 200 and r.json()["version_no"] == 2
    # 列表 + is_current
    r = client.get(f"/api/projects/{proj}/versions", headers=h)
    body = r.json()
    assert body["total"] == 2
    assert [it["version_no"] for it in body["items"]] == [2, 1]
    assert body["items"][0]["is_current"] is True and body["items"][1]["is_current"] is False
    # diff（v1→v2 无实质差异，四级键齐全）
    r = client.get(f"/api/projects/{proj}/versions/1/diff/2", headers=h)
    assert r.status_code == 200
    for k in ("classes", "properties", "instances", "relations"):
        assert k in r.json()
    # 快照预签名 URL
    r = client.get(f"/api/projects/{proj}/versions/1/snapshot", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("http"), r.text
    # 时间轴 + 非法轴
    r = client.get(f"/api/projects/{proj}/timeline", headers=h)
    assert r.status_code == 200 and "events" in r.json()
    r = client.get(f"/api/projects/{proj}/timeline?time_axis=bogus", headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_AXIS"
    # 不存在的版本 → 404
    r = client.get(f"/api/projects/{proj}/versions/999/diff/1", headers=h)
    assert r.status_code == 404
    # 回滚到 v1（owner）→ 新 rollback 版本
    r = client.post(f"/api/projects/{proj}/versions/1/restore", headers=h, json={})
    assert r.status_code == 200 and r.json()["restored_from"] == 1, r.text
    r = client.get(f"/api/projects/{proj}/versions", headers=h)
    kinds = {it["version_no"]: it["kind"] for it in r.json()["items"]}
    assert kinds.get(3) == "rollback"


def test_api_reviews_flow(api_env, db):
    env = api_env
    h, proj = env["headers"], env["proj"].id

    item = _mk_item(db, proj, item_type="conflict_value", priority="high")
    # 项目内列表（含 priority 排序）
    r = client.get(f"/api/projects/{proj}/reviews", headers=h)
    assert r.status_code == 200 and any(it["id"] == item.id for it in r.json()["items"])
    # 跨项目列表
    r = client.get("/api/reviews", headers=h)
    assert r.status_code == 200 and any(it["id"] == item.id for it in r.json()["items"])
    # 详情
    r = client.get(f"/api/reviews/{item.id}", headers=h)
    assert r.status_code == 200 and r.json()["payload"] == {"foo": "bar"}
    # claim → decide（edit 改 payload）
    r = client.post(f"/api/reviews/{item.id}/claim", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "claimed"
    r = client.post(f"/api/reviews/{item.id}/decide", headers=h,
                    json={"action": "edit", "edited_payload": {"foo": "baz"}, "note": "修订"})
    assert r.status_code == 200 and r.json()["status"] == "edited"
    r = client.get(f"/api/reviews/{item.id}", headers=h)
    assert r.json()["payload"] == {"foo": "baz"} and r.json()["result_ref"]["note"] == "修订"
    # 终态再 claim → 409
    r = client.post(f"/api/reviews/{item.id}/claim", headers=h)
    assert r.status_code == 409
    # 批量裁决（先再造两条 pending）
    b1 = _mk_item(db, proj, item_type="low_confidence_entity", priority="low")
    b2 = _mk_item(db, proj, item_type="low_confidence_entity", priority="low")
    r = client.post(f"/api/projects/{proj}/reviews/batch-decide", headers=h,
                    json={"ids": [b1.id, b2.id], "action": "reject", "note": "批量"})
    assert r.status_code == 200 and r.json()["rejected"] == 2, r.text
    # 审计与 outbox 留痕
    from app.infrastructure.database import AuditLog

    db.rollback()
    assert (db.query(AuditLog).filter(AuditLog.action == "review.edit",
                                      AuditLog.resource_id == str(item.id)).first()
            is not None)


def test_api_publish_gate_and_readonly(api_env, db):
    env = api_env
    h, proj = env["headers"], env["proj"].id

    # 门禁 1：清空知识域 → DOMAIN_REQUIRED
    from app.infrastructure.database import Project

    db.expire_all()
    p = db.query(Project).filter(Project.id == proj).first()
    saved_domains = p.domains
    p.domains = None
    db.commit()
    r = client.post(f"/api/projects/{proj}/publish", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "DOMAIN_REQUIRED"
    p.domains = saved_domains
    db.commit()

    # 门禁 2：pending high 审核项阻断
    blocker = _mk_item(db, proj, item_type="conflict_value", priority="high")
    r = client.post(f"/api/projects/{proj}/publish", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "PENDING_CRITICAL_REVIEWS"
    assert r.json()["error"]["detail"]["blockers"][0]["id"] == blocker.id
    # 裁决掉 → 门禁放行
    r = client.post(f"/api/reviews/{blocker.id}/decide", headers=h,
                    json={"action": "approve"})
    assert r.status_code == 200

    # 发布成功：publication 行 + project.status=published + outbox
    r = client.post(f"/api/projects/{proj}/publish", headers=h,
                    json={"note": "首次发布"})
    assert r.status_code == 200, r.text
    ver_no = r.json()["version_no"]
    assert r.json()["status"] == "published" and isinstance(r.json()["warnings"], list)
    from app.infrastructure.database import OntologyVersion, OutboxEvent, Publication

    # API 会话已提交；本会话事务快照需重开才能看到新行（REPEATABLE READ）
    db.rollback()
    pub = (db.query(Publication).filter(Publication.project_id == proj)
           .order_by(Publication.id.desc()).first())
    assert pub is not None and pub.status == "published" and pub.note == "首次发布"
    ver = (db.query(OntologyVersion)
           .filter(OntologyVersion.project_id == proj,
                   OntologyVersion.version_no == ver_no).first())
    assert ver.kind == "publication"
    assert (db.query(OutboxEvent).filter(OutboxEvent.event_type == "publication.created")
            .first() is not None)
    db.rollback()
    assert db.query(Project).filter(Project.id == proj).first().status == "published"

    # 只读强制：published 项目写端点 → 403 PUBLISHED_READONLY
    r = client.post(f"/api/projects/{proj}/versions", headers=h, json={"kind": "schema"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PUBLISHED_READONLY"
    # 读端点不受影响
    r = client.get(f"/api/projects/{proj}/versions", headers=h)
    assert r.status_code == 200

    # 公共区：列表 + 详情（readonly 投影）
    r = client.get("/api/assets", headers=h)
    groups = {g["domain"]: g["items"] for g in r.json()["groups"]}
    assert any(it["project_id"] == proj for items in groups.values() for it in items)
    r = client.get(f"/api/assets/{proj}", headers=h)
    assert r.status_code == 200 and r.json()["readonly"] is True
    assert r.json()["version_no"] == ver_no

    # 下线 → 公共区即刻不可见 + 项目恢复可写
    r = client.post(f"/api/projects/{proj}/unpublish", headers=h, json={})
    assert r.status_code == 200 and r.json()["status"] == "unpublished"
    db.rollback()
    assert db.query(Project).filter(Project.id == proj).first().status == "ready"
    r = client.get(f"/api/assets/{proj}", headers=h)
    assert r.status_code == 404
    r = client.post(f"/api/projects/{proj}/versions", headers=h, json={"kind": "schema"})
    assert r.status_code == 200  # 只读解除
