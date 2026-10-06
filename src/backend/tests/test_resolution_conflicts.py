# tests/test_resolution_conflicts.py - M3-5 实例抽取/消解/冲突契约（docs/design/04 §4-§6）
# 纯函数直测；行表与 API 真连 MySQL/Redis（沿用 M3 套路）。
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
TAG = f"m35{uuid.uuid4().hex[:6]}"

TBOX = {
    "classes": {"理财产品": [], "客户": [], "银行": ["托管银行"]},
    "object_properties": {
        "购买": {"domain": "客户", "range": "理财产品"},
        "托管": {"domain": "银行", "range": "理财产品"},
    },
    "chunks": {0: "张三在工商银行购买了星辰一号理财产品。", 1: "没有证据的切片"},
}


# ── extract_instances：严格闸门 + 统计 ──


def _inst_llm(payload):
    def call(system, user, schema):
        return payload
    return call


def test_extract_instances_gate_and_stats():
    from app.adapters.extraction import extract_instances

    payload = {
        "entities": [
            {"label": "张三", "class_label": "客户", "props": {"风险等级": "R3"},
             "confidence": 0.95, "evidence": "张三在工商银行购买了星辰一号理财产品。"},
            {"label": "星辰一号", "class_label": "理财产品", "props": {},
             "confidence": 0.9, "evidence": "张三在工商银行购买了星辰一号理财产品。"},
            {"label": "神秘标的", "class_label": "虚拟资产", "props": {},
             "confidence": 0.9, "evidence": "凭空出现"},  # unknown_class
            {"label": "低信实体", "class_label": "客户", "props": {},
             "confidence": 0.5, "evidence": "张三在工商银行购买了星辰一号理财产品。"},  # low_confidence
            {"label": "无据实体", "class_label": "客户", "props": {},
             "confidence": 0.9, "evidence": "切片里不存在的句子"},  # missing_evidence
        ],
        "relations": [
            {"subject_label": "张三", "predicate": "购买", "object_label": "星辰一号",
             "confidence": 0.9, "evidence": "张三在工商银行购买了星辰一号理财产品。"},
            {"subject": "张三", "predicate": "抛售", "object": "星辰一号",
             "confidence": 0.9, "evidence": "……"},  # 键漂移 subject/object → 归一后 unknown_predicate
            {"subject_label": "张三", "predicate": "托管", "object_label": "星辰一号",
             "confidence": 0.9, "evidence": "……"},  # domain mismatch：张三≠银行
        ],
        "relationships": [],  # 键漂移归一：不应报错
    }
    res = extract_instances([{"index": 0, "text": TBOX["chunks"][0]}], TBOX,
                            base_uri="u", use_cache=False,
                            llm_call=_inst_llm(payload))
    # 通过：2 个正常实体 + 1 个无据实体（降级仍通过）+ 低信实体仍通过
    assert {e.label for e in res.entities} == {"张三", "星辰一号", "低信实体", "无据实体"}
    assert {r.predicate for r in res.relations} == {"购买"}
    # domain_range 违例直接丢弃；unknown_predicate 走 review 扣留（03 §8 promote 政策）
    assert res.discarded_count == 1
    assert res.withheld_count >= 1
    assert res.promote_count >= 1            # 虚拟资产 → new_class 候选
    assert res.low_confidence_count >= 1
    assert res.missing_evidence_count >= 1
    # 缺证据实体降级为摘要
    noev = [e for e in res.entities if e.label == "无据实体"][0]
    assert noev.evidence.startswith("[chunk 0 摘要]")


def test_extract_instances_discard_policy_and_dedupe():
    from app.adapters.extraction import extract_instances

    payload = {
        "entities": [
            {"label": "星辰一号", "class_label": "理财产品", "confidence": 0.9,
             "evidence": "张三在工商银行购买了星辰一号理财产品。"},
            {"label": "星辰一号", "class_label": "理财产品", "confidence": 0.6,
             "evidence": "……"},  # 同 chunk 同实体 → 去重保留高置信
            {"label": "X", "class_label": "未知类", "confidence": 0.9, "evidence": ""},
            {"label": "Y", "class_label": "未知类", "confidence": 0.9, "evidence": ""},
        ],
        "relations": [],
    }
    res = extract_instances([{"index": 0, "text": TBOX["chunks"][0]}], TBOX,
                            base_uri="u", promote_policy="discard",
                            use_cache=False, llm_call=_inst_llm(payload))
    assert len(res.entities) == 1  # 去重后只剩正常实体
    assert res.entities[0].confidence == 0.9
    assert res.discarded_count == 2  # discard 政策：未知类直接计数


# ── 消解三层 ──


def test_pinyin_keys_and_similarity():
    from app.adapters.resolution import jaro_winkler, levenshtein, pinyin_keys

    assert pinyin_keys("中国人民银行") == ["zhongguorenminyinhang", "zgrmyh"]
    assert pinyin_keys("Fund2024") == ["fund2024", "fund2024"]
    assert jaro_winkler("zhixingyihao", "zhixing1hao") > 0.85
    assert levenshtein("abc", "abd") == 1
    assert levenshtein("abcd", "zzzz") > 2  # 早停


def test_detect_duplicates_three_layers():
    from app.adapters.resolution import detect_duplicates

    ents = [
        {"id": 1, "label": "Ｓｔａｒ一号", "class_label": "理财产品", "props": {}},
        {"id": 2, "label": "Star一号", "class_label": "理财产品", "props": {}},  # 全角→半角归一相同 → L1
        {"id": 3, "label": "星辰1号", "class_label": "理财产品", "props": {}},   # 拼音层候选（L2 用例）
        {"id": 6, "label": " unrelated ", "class_label": "客户", "props": {}},
    ]
    # L1：全角/半角归一相同 → 必合并
    clusters = detect_duplicates(ents, blocking="none")
    assert len(clusters) == 1
    assert clusters[0].layer == "normalized"
    assert set(clusters[0].member_labels) == {"Ｓｔａｒ一号", "Star一号"}
    assert clusters[0].needs_review is False

    # L2 拼音：星辰1号 vs 星辰一号（xingchen1hao vs xingchengyihao）
    dup2 = detect_duplicates(
        [{"id": 3, "label": "星辰1号", "class_label": "理财产品", "props": {}},
         {"id": 1, "label": "星辰一号", "class_label": "理财产品", "props": {}}],
        blocking="pinyin")
    assert len(dup2) == 1 and dup2[0].layer == "pinyin"
    assert dup2[0].similarity >= 0.85 or dup2[0].needs_review

    # L2 拼音：繁体（NFKC 不做繁简转换，归到拼音层；銀行被 pypinyin 读作 yinxing，JW 仍过阈值）
    dup3 = detect_duplicates(
        [{"id": 4, "label": "工商银行", "class_label": "银行", "props": {}},
         {"id": 5, "label": "工商銀行", "class_label": "银行", "props": {}}],
        blocking="pinyin")
    assert len(dup3) == 1 and dup3[0].similarity >= 0.85 and dup3[0].needs_review is False


def test_detect_duplicates_vector_layer():
    from app.adapters.resolution import detect_duplicates

    # 注入 embedding：拼音完全不同但语义近（"腾讯"~"腾讯控股" 拼音远，向量近）
    def embeddings(texts):
        vec = {}
        for t in texts:
            if "腾讯" in t:
                vec[t] = [1.0, 0.0]
            elif "阿里" in t:
                vec[t] = [0.0, 1.0]
            else:
                vec[t] = [0.9, 0.1]
        return [vec[t] for t in texts]

    ents = [
        {"id": 1, "label": "腾讯科技", "class_label": "公司", "props": {}},
        {"id": 2, "label": "腾讯控股有限公司", "class_label": "公司", "props": {}},
        {"id": 3, "label": "阿里巴巴集团", "class_label": "公司", "props": {}},
    ]
    clusters = detect_duplicates(ents, blocking="pinyin", embeddings_fn=embeddings)
    merged = [c for c in clusters if not c.needs_review]
    assert any(set(c.member_labels) == {"腾讯科技", "腾讯控股有限公司"} for c in merged)
    assert all("阿里巴巴" not in c.member_labels for c in clusters)


def test_merge_and_split_entities(db):
    """合并执行：关系迁移+去重+溯源保留+状态；拆分可逆。"""
    from app.adapters.resolution import merge_entities, split_entities
    from app.infrastructure.database import Entity, Project, ProvenanceRecord, Relation, UploadedDocument, User

    user = User(username=f"{TAG}_mu", role="user", is_active=True,
                hashed_password="x" * 60)
    db.add(user)
    db.commit()
    db.refresh(user)
    proj = Project(name=f"{TAG}_mp", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)

    e1 = Entity(project_id=proj.id, uri=f"urn:t:{proj.id}:e1", label="工商银行",
                label_normalized="工商银行", class_label="银行", props={"成立": "1984"})
    e2 = Entity(project_id=proj.id, uri=f"urn:t:{proj.id}:e2", label="工商銀行",
                label_normalized="工商银行", class_label="银行", props={"资产": "30万亿"})
    db.add_all([e1, e2])
    db.flush()
    r1 = Relation(project_id=proj.id, subject_id=e2.id, predicate="托管", object_id=e1.id)
    db.add(r1)
    db.flush()
    doc = UploadedDocument(project_id=proj.id, filename=f"{TAG}_src.txt",
                           file_path=f"/tmp/{TAG}_src.txt", file_type="txt")
    db.add(doc)
    db.flush()
    db.add(ProvenanceRecord(project_id=proj.id, target_type="entity", target_id=e2.id,
                            source_document_id=doc.id, evidence_text="来源A", checksum="x"))
    db.commit()

    res = merge_entities(e1.id, [e2.id], db=db)
    assert res.canonical_id == e1.id and res.merged_ids == [e2.id]
    assert res.relations_migrated == 1
    db.commit()
    e2b = db.query(Entity).filter(Entity.id == e2.id).first()
    assert e2b.status == "merged" and e2b.canonical_id == e1.id
    r1b = db.query(Relation).filter(Relation.id == r1.id).first()
    assert r1b.subject_id == e1.id  # 关系迁移
    prov = db.query(ProvenanceRecord).filter(
        ProvenanceRecord.target_type == "entity",
        ProvenanceRecord.project_id == proj.id).all()
    assert prov and all(p.target_id == e1.id for p in prov)  # 溯源保留并改指
    e1b = db.query(Entity).filter(Entity.id == e1.id).first()
    assert e1b.props.get("资产") == "30万亿"  # keep_most_complete 补齐
    assert "工商銀行" in (e1b.aliases or [])
    # 合并留痕
    from app.infrastructure.database import ReviewItem

    hist = db.query(ReviewItem).filter(
        ReviewItem.project_id == proj.id, ReviewItem.item_type == "entity_merge",
        ReviewItem.status == "approved").all()
    assert len(hist) == 1

    # 拆分可逆
    assert split_entities(e1.id, [e2.id], db=db) is True
    db.commit()
    e2c = db.query(Entity).filter(Entity.id == e2.id).first()
    assert e2c.status == "auto" and e2c.canonical_id is None

    # 清理
    db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)\
        .delete(synchronize_session=False)
    db.query(ProvenanceRecord).filter(ProvenanceRecord.project_id == proj.id)\
        .delete(synchronize_session=False)
    db.query(Relation).filter(Relation.project_id == proj.id)\
        .delete(synchronize_session=False)
    db.query(Entity).filter(Entity.project_id == proj.id)\
        .delete(synchronize_session=False)
    db.query(UploadedDocument).filter(UploadedDocument.project_id == proj.id)\
        .delete(synchronize_session=False)
    db.delete(proj)
    db.delete(user)
    db.commit()


# ── 冲突检测 ──


def test_detect_conflicts_value_type_relationship():
    from app.adapters.conflicts import ConflictType, detect_conflicts

    ents = [
        {"id": 1, "uri": "u1", "label": "星辰一号", "class_label": "理财产品",
         "props": {"风险评级": "R3"}, "status": "auto"},
        {"id": 2, "uri": "u2", "label": "星辰一号", "class_label": "理财产品",
         "props": {"风险评级": "R2"}, "status": "auto"},  # VALUE
        {"id": 3, "uri": "u3", "label": "星辰一号", "class_label": "基金",
         "props": {}, "status": "auto"},  # TYPE
        {"id": 4, "uri": "u4", "label": "已合并实体", "class_label": "客户",
         "props": {"风险评级": "R1"}, "status": "merged"},  # 已合并不参与
    ]
    rels = [
        {"subject_id": 1, "predicate": "托管", "object_id": 2,
         "subject_label": "星辰一号", "object_label": "银行A"},
        {"subject_id": 1, "predicate": "托管", "object_id": 3,
         "subject_label": "星辰一号", "object_label": "银行B"},
    ]
    ops = {"托管": {"domain": "银行", "range": "理财产品", "cardinality": "one-to-one"}}
    records = detect_conflicts(ents, rels, object_properties=ops)
    kinds = {r.conflict_type for r in records}
    assert ConflictType.VALUE in kinds and ConflictType.TYPE in kinds
    assert ConflictType.RELATIONSHIP in kinds
    val = [r for r in records if r.conflict_type == ConflictType.VALUE][0]
    assert val.property_name == "风险评级" and set(val.conflicting_values) == {"R3", "R2"}
    assert val.guide["steps"]
    # 无基数声明不判 RELATIONSHIP（多对多不误报）
    records2 = detect_conflicts(ents, rels, object_properties={})
    assert ConflictType.RELATIONSHIP not in {r.conflict_type for r in records2}


def test_detect_temporal_conflicts():
    from app.adapters.conflicts import ConflictType, detect_temporal_conflicts

    ents = [
        {"id": 1, "uri": "u1", "label": "产品A", "class_label": "理财产品",
         "props": {"valid_from": "2020-01-01", "valid_until": "2022-12-31",
                   "管理费率": "1.2%"}, "status": "auto"},
        {"id": 2, "uri": "u2", "label": "产品A", "class_label": "理财产品",
         "props": {"valid_from": "2022-06-01", "valid_until": "2024-12-31",
                   "管理费率": "0.8%"}, "status": "auto"},  # 区间重叠+值不同
        {"id": 3, "uri": "u3", "label": "产品B", "class_label": "理财产品",
         "props": {"valid_from": "2030-01-01", "管理费率": "1.0%"}, "status": "auto"},
        {"id": 4, "uri": "u4", "label": "产品A", "class_label": "理财产品",
         "props": {"valid_from": "2024-01-01", "管理费率": "0.9%"}, "status": "auto"},
    ]
    records = detect_temporal_conflicts(ents)
    assert records and records[0].conflict_type == ConflictType.TEMPORAL
    assert records[0].severity == "high"


# ── API 契约（03 §9）──


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def api_env(db):
    import bcrypt

    from app.infrastructure.database import Project, User, UserModuleGrant

    user = User(username=f"{TAG}_api", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add(user)
    db.commit()
    db.refresh(user)
    for code in ("schema_build", "instance_build", "resolution"):
        db.add(UserModuleGrant(user_id=user.id, module_code=code, allowed=1,
                               granted_by=user.id))
    db.commit()
    proj = Project(name=f"{TAG}_ap", owner_id=user.id,
                   graph_data={"schema": {"classes": [{"label": "理财产品"}],
                                          "object_properties": []},
                               "nodes": [{"id": "c1", "data": {"label": "理财产品",
                                                               "type": "Class"}}],
                               "edges": []})
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield {"user": user, "proj": proj, "headers": {}}
    from app.infrastructure.database import Entity, ProvenanceRecord, Relation, ReviewItem

    (db.query(ProvenanceRecord).filter(ProvenanceRecord.project_id == proj.id)
     .delete(synchronize_session=False))
    (db.query(Relation).filter(Relation.project_id == proj.id)
     .delete(synchronize_session=False))
    (db.query(Entity).filter(Entity.project_id == proj.id)
     .delete(synchronize_session=False))
    (db.query(ReviewItem).filter(ReviewItem.project_id == proj.id)
     .delete(synchronize_session=False))
    (db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id)
     .delete(synchronize_session=False))
    db.delete(proj)
    db.delete(user)
    db.commit()


def _login(username: str) -> dict:
    r = client.post("/api/auth/login",
                    data={"username": username, "password": "Passw0rd!123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_resolution_merge_split_and_conflicts(api_env, db):
    from app.infrastructure.database import Entity

    env = api_env
    env["headers"] = _login(env["user"].username)
    h, proj = env["headers"], env["proj"].id

    # 预置三个实体（两个同归一 + 一个冲突值）
    e1 = Entity(project_id=proj, uri=f"urn:t:{proj}:m1", label="工商银行",
                label_normalized="工商银行", class_label="银行", props={"成立": "1984"})
    e2 = Entity(project_id=proj, uri=f"urn:t:{proj}:m2", label="工商銀行",
                label_normalized="工商银行", class_label="银行", props={})
    e3 = Entity(project_id=proj, uri=f"urn:t:{proj}:m3", label="星辰一号",
                label_normalized="星辰一号", class_label="理财产品",
                props={"风险评级": "R3"})
    e4 = Entity(project_id=proj, uri=f"urn:t:{proj}:m4", label="星辰一号",
                label_normalized="星辰一号", class_label="理财产品",
                props={"风险评级": "R2"})
    db.add_all([e1, e2, e3, e4])
    db.commit()
    db.refresh(e1)
    db.refresh(e2)

    # 手动合并
    r = client.post(f"/api/projects/{proj}/resolution/merge", headers=h,
                    json={"canonical_id": e1.id, "duplicate_ids": [e2.id]})
    assert r.status_code == 200 and r.json()["merged_ids"] == [e2.id], r.text
    # 拆分
    r = client.post(f"/api/projects/{proj}/resolution/split", headers=h,
                    json={"canonical_id": e1.id, "split_ids": [e2.id]})
    assert r.status_code == 200 and r.json()["split"] is True
    # 冲突检测（同步）
    r = client.post(f"/api/projects/{proj}/conflicts/detect", headers=h, json={})
    assert r.status_code == 200 and r.json()["detected"] >= 1, r.text
    # 冲突列表
    r = client.get(f"/api/projects/{proj}/conflicts", headers=h)
    assert r.status_code == 200 and len(r.json()["items"]) >= 1
    # 消解聚类列表（空但可访问）
    r = client.get(f"/api/projects/{proj}/resolution/clusters", headers=h)
    assert r.status_code == 200 and "items" in r.json()
    # 重复检测应去重
    r = client.post(f"/api/projects/{proj}/conflicts/detect", headers=h, json={})
    assert r.status_code == 200 and r.json()["deduped"] >= 1


def test_api_instances_requires_schema(api_env):
    env = api_env
    h = env["headers"]
    proj = env["proj"].id
    # 有 schema（fixture 预置）但无切片 → NO_CHUNKS
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_CHUNKS"
    # 非法 promote_policy
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h,
                    json={"promote_policy": "bad"})
    assert r.status_code == 400


def test_api_resolution_requires_module(db, api_env):
    import bcrypt

    from app.infrastructure.database import User

    plain = User(username=f"{TAG}_nomod", role="user", is_active=True,
                 hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add(plain)
    db.commit()
    try:
        h = _login(plain.username)
        proj = api_env["proj"].id
        r = client.post(f"/api/projects/{proj}/resolution/run", headers=h, json={})
        assert r.status_code == 403
    finally:
        db.delete(plain)
        db.commit()
