# tests/test_extraction_gate.py - M3-4 Schema 抽取 + SchemaGate 契约（docs/design/04 §3/§4）
# 纯函数部分（gate/归并/缓存）直接测；API 契约真连 MySQL/Redis（沿用 M3 套路）。
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
TAG = f"m34{uuid.uuid4().hex[:6]}"

# ── match_class 四级匹配（修复缺陷 #4）──


def test_match_class_four_levels():
    from app.adapters.schema_gate import match_class

    tbox = {"理财产品": ["金融产品", "Fund"], "机构投资者": []}
    assert match_class("理财产品", tbox) == "理财产品"          # 1) 精确
    assert match_class("理财产品　", tbox) == "理财产品"        # 2) 归一化（全角空格）
    assert match_class("fund", tbox) == "理财产品"              # 2/3) ASCII 小写 + 别名
    assert match_class("金融产品", tbox) == "理财产品"          # 3) 别名表
    # ★ 缺陷 #4 回归：废除子串双向包含 —— "投资者" 不得命中 "机构投资者"
    assert match_class("投资者", tbox) is None
    assert match_class("机构投资者管理产品", tbox) is None
    assert match_class("", tbox) is None


def test_locate_quote_whitespace_insensitive():
    from app.adapters.schema_gate import locate_quote

    chunk = "星辰一号是旗舰产品，成立于 2020 年。"
    assert locate_quote("星辰一号是旗舰产品", chunk)
    assert locate_quote("星辰一号是旗舰产品，\n成立于2020年。", chunk)  # 空白差异兜底
    assert not locate_quote("文本中不存在这句话", chunk)
    assert not locate_quote("", chunk)


# ── apply_gate：promote 三模式 + 违例路径 ──

TBOX = {
    "classes": {"理财产品": [], "客户": []},
    "object_properties": {"购买": {"domain": "客户", "range": "理财产品"}},
    "chunks": {0: "张三购买了一款理财产品。", 1: "没有证据的切片", 2: "切片二"},
}


def _ent(label="星辰一号", cls="理财产品", conf=0.9, evidence="张三购买了一款理财产品。", idx=0):
    from app.adapters.extraction import ExtractedEntity

    return ExtractedEntity(label=label, class_label=cls, confidence=conf,
                           evidence=evidence, chunk_index=idx)


def _rel(sub="张三", pred="购买", obj="星辰一号", conf=0.9,
         evidence="张三购买了一款理财产品。", idx=0):
    from app.adapters.extraction import ExtractedRelation

    return ExtractedRelation(subject_label=sub, predicate=pred, object_label=obj,
                             confidence=conf, evidence=evidence, chunk_index=idx)


def test_gate_pass_normalizes_class_and_predicate():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    res = apply_gate([_ent(cls="理财产品　"), _rel()], TBOX, PromotePolicy.REVIEW)
    assert len(res.passed) == 2
    assert res.passed[0].class_label == "理财产品"   # 归一化回 TBox 类名
    assert res.passed[1].predicate == "购买"
    assert res.discarded_count == 0 and res.withheld_count == 0
    assert res.promote_candidates == []


def test_gate_review_withholds_unknown_class():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    res = apply_gate([_ent(cls="虚拟资产池")], TBOX, PromotePolicy.REVIEW)
    assert res.passed == []                       # 扣留转审核，不直接落图谱
    assert res.withheld_count == 1
    kinds = [v.kind for v in res.violations]
    assert "unknown_class" in kinds
    assert res.promote_candidates[0]["kind"] == "unknown_class"
    assert res.promote_candidates[0]["label"] == "虚拟资产池"


def test_gate_discard_counts_unknown_class():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    res = apply_gate([_ent(cls="虚拟资产池")], TBOX, PromotePolicy.DISCARD)
    assert res.passed == [] and res.discarded_count == 1 and res.withheld_count == 0


def test_gate_auto_promotes_at_three_chunks():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    ents = [_ent(cls="虚拟资产池", idx=i) for i in (0, 1, 2)]  # 3 个不同 chunk
    res = apply_gate(ents, TBOX, PromotePolicy.AUTO)
    assert len(res.passed) == 3                   # 达阈值 → 放行
    cand = res.promote_candidates[0]
    assert cand["auto_promoted"] is True and cand["chunk_indices"] == [0, 1, 2]
    # 不足 3 chunks 仍扣留
    res2 = apply_gate([_ent(cls="虚拟资产池", idx=0), _ent(cls="虚拟资产池", idx=0)],
                      TBOX, PromotePolicy.AUTO)
    assert res2.passed == [] and res2.withheld_count == 2
    assert res2.promote_candidates[0]["auto_promoted"] is False


def test_gate_unknown_predicate_and_domain_range():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    ents = [_ent(label="星辰二号", cls="客户")]
    res = apply_gate(ents + [_rel(pred="抛售"), _rel(sub="客户甲", pred="购买", obj="星辰二号")],
                     TBOX, PromotePolicy.REVIEW)
    kinds = [v.kind for v in res.violations]
    assert "unknown_predicate" in kinds           # 谓词不在 TBox
    dr = [v for v in res.violations if v.kind == "domain_range_mismatch"]
    assert dr and dr[0].detail["side"] == "range"  # 客户 ≠ range 理财产品
    assert res.discarded_count == 1               # domain/range 违例直接丢弃


def test_gate_low_confidence_and_missing_evidence():
    from app.adapters.schema_gate import PromotePolicy, apply_gate

    res = apply_gate(
        [_ent(conf=0.5, evidence="张三购买了一款理财产品。"),
         _ent(label="星辰二号", conf=0.9, evidence="切片里根本没有这句话", idx=1)],
        TBOX, PromotePolicy.REVIEW)
    kinds = [v.kind for v in res.violations]
    assert "low_confidence" in kinds
    me = [v for v in res.violations if v.kind == "missing_evidence"]
    assert me and me[0].label == "星辰二号"
    # 证据定位失败 → 降级为 chunk 摘要，但实体仍通过
    star2 = [p for p in res.passed if p.label == "星辰二号"][0]
    assert star2.evidence.startswith("[chunk 1 摘要]")


# ── extract_schema：并行 / 字段化 prompt / 缓存 / 归并 / 重试 ──


def _fake_llm(responses):
    """按调用序返回预设响应并记录 prompt。"""
    calls = {"n": 0, "prompts": []}

    def call(system, user, json_schema):
        calls["prompts"].append(user)
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        r = responses[i]
        return r() if callable(r) else r

    return call, calls


def test_extract_schema_merges_and_marks_pending():
    from app.adapters.extraction import extract_schema

    def call(system, user, schema):
        if "#0" in user:
            return {"classes": [
                {"label": "理财产品", "definition": "银行理财产品",
                 "properties": [{"name": "风险评级", "data_type": "string"}]},
                {"label": "客户", "definition": "购买主体", "properties": []},
            ], "object_properties": [{"label": "购买", "domain": "客户", "range": "理财产品"}]}
        return {"classes": [{"label": "理财产品", "definition": "",
                             "properties": [{"name": "风险评级", "data_type": "string"},
                                            {"name": "成立日期", "data_type": "date"}]}],
                "object_properties": [{"label": "转让", "domain": "客户", "range": "资产池"}]}

    res = extract_schema(["切片零", "切片一"], base_uri="urn:onto:t", parallelism=2,
                         use_cache=False, llm_call=call)
    labels = {c["label"] for c in res.classes}
    assert labels == {"理财产品", "客户", "资产池"}
    # 归并：两个 chunk 的"理财产品"合一，properties 去重合并
    prod = [c for c in res.classes if c["label"] == "理财产品"][0]
    assert {p["name"] for p in prod["properties"]} == {"风险评级", "成立日期"}
    # 悬空 range "资产池" → 占位类 pending
    pool = [c for c in res.classes if c["label"] == "资产池"][0]
    assert pool.get("pending") is True
    assert {op["label"] for op in res.object_properties} == {"购买", "转让"}
    assert res.chunks_processed == 2 and res.warnings == []


def test_extract_schema_parallel_and_fielded_prompt():
    import threading
    import time as _time

    from app.adapters.extraction import extract_schema

    seen_threads = set()

    def call(system, user, schema):
        seen_threads.add(threading.current_thread().name)
        _time.sleep(0.2)  # 模拟 LLM 延迟：串行 4×0.2s，并行应明显快于 0.6s
        return {"classes": [{"label": "基金"}], "object_properties": [], "datatype_properties": []}

    t0 = _time.monotonic()
    res = extract_schema([f"切片{i}" for i in range(4)], base_uri="u", parallelism=4,
                         use_cache=False, llm_call=call)
    elapsed = _time.monotonic() - t0
    assert res.chunks_processed == 4 and len(res.classes) == 1
    assert len(seen_threads) >= 2                  # 确实并行
    assert elapsed < 0.6                           # 缺陷 #5：串行需 0.8s


def test_extract_schema_fielded_prompt_contains_known_classes():
    from app.adapters.extraction import extract_schema

    prompts = []

    def call(system, user, schema):
        prompts.append(user)
        return {"classes": [{"label": "基金"}]}

    extract_schema(["切片"], base_uri="u", use_cache=False, llm_call=call,
                   known_classes=["理财产品", "客户"])
    assert "【已发现类清单】" in prompts[0]
    assert "- 理财产品" in prompts[0] and "- 客户" in prompts[0]


def test_extract_schema_guidance_injected_into_prompt():
    """引导模板（提示词注入）：guidance 出现在每次切片抽取的 user prompt，且随 prompt 进缓存键。"""
    from app.adapters.extraction import extract_schema

    prompts = []

    def call(system, user, schema):
        prompts.append(user)
        return {"classes": [{"label": "基金"}]}

    guidance = "场景：行业研报\n- 主体「机构与公司」（关注属性：主营业务；常见关系：研发）"
    extract_schema(["切片一", "切片二"], base_uri="u", use_cache=False,
                   llm_call=call, guidance=guidance)
    assert len(prompts) == 2
    for p in prompts:
        assert "【抽取引导（用户注入，优先遵守；与文档内容冲突时以文档为准）】" in p
        assert "主体「机构与公司」" in p


def test_extract_schema_retry_with_error_feedback():
    from app.adapters.extraction import extract_schema

    prompts = []

    def call(system, user, schema):
        prompts.append(user)
        if len(prompts) == 1:
            return {"classes": "不是数组"}  # 本地校验失败
        return {"classes": [{"label": "基金"}]}

    res = extract_schema(["切片"], base_uri="u", use_cache=False, llm_call=call)
    assert [c["label"] for c in res.classes] == ["基金"] and res.warnings == []
    assert "【上次输出错误，必须修正】" in prompts[1]  # 带错误反馈重试 1 次


def test_extract_schema_warning_after_failed_retry():
    from app.adapters.extraction import extract_schema

    def call(system, user, schema):
        return "完全不是 JSON"

    res = extract_schema(["切片"], base_uri="u", use_cache=False, llm_call=call)
    assert res.classes == [] and len(res.warnings) == 1


def test_extract_schema_redis_cache():
    from app.adapters.extraction import extract_schema

    n_calls = {"n": 0}

    def call(system, user, schema):
        n_calls["n"] += 1
        return {"classes": [{"label": f"缓存类{len(user)}"}]}

    chunks = [{"index": 0, "text": "唯一缓存探针文本"}]
    r1 = extract_schema(chunks, base_uri="u", model_override={"model_name": TAG},
                        use_cache=True, llm_call=call)
    r2 = extract_schema(chunks, base_uri="u", model_override={"model_name": TAG},
                        use_cache=True, llm_call=call)
    assert n_calls["n"] == 1                       # 第二次命中缓存，不再调 LLM（缺陷 #9）
    assert r2.cache_hits == 1 and r1.cache_hits == 0
    assert r2.classes == r1.classes


def test_extract_schema_stitches_cross_chunk_relations():
    """跨切片关系缝合：切片各自只产出类，归并后缝合轮把跨片类对连上（带原文证据）。"""
    from app.adapters.extraction import extract_schema

    stitch_calls = []
    stitch_progress = []

    def call(system, user, schema):
        if "跨切片关系缝合" in system:
            stitch_calls.append(user)
            return {"relations": [
                {"label": "投资", "domain": "基金", "range": "债券",
                 "evidence": "基金募集资金投资于债券"}]}
        # 切片抽取：切片一产 基金，切片二产 债券（互不共现 → 无关系边）
        if "切片一" in user:
            return {"classes": [{"label": "基金", "definition": "募集资金的投资计划"}]}
        return {"classes": [{"label": "债券", "definition": "固定收益证券"}]}

    res = extract_schema(["切片一：基金相关", "切片二：债券相关"], base_uri="u",
                         use_cache=False, llm_call=call,
                         stitch_progress_cb=lambda si, segs: stitch_progress.append((si, segs)))
    assert res.stitched_relations == 1
    op = next(o for o in res.object_properties if o["label"] == "投资")
    assert op["domain"] == "基金" and op["range"] == "债券"
    # 缝合 prompt 带全类清单与文档摘录
    assert "- 基金" in stitch_calls[0] and "- 债券" in stitch_calls[0]
    assert "【文档内容摘录】" in stitch_calls[0]
    # 缝合进度实时上报（否则 UI 在 90% 静默卡住）
    assert stitch_progress == [(1, 1)]


def test_extract_schema_stitch_ignores_unknown_classes_and_self_loops():
    """缝合防线：domain/range 不在类清单或自环的关系一律丢弃，不造类。"""
    from app.adapters.extraction import extract_schema

    def call(system, user, schema):
        if "跨切片关系缝合" in system:
            return {"relations": [
                {"label": "投资", "domain": "基金", "range": "不存在的类"},
                {"label": "自环", "domain": "基金", "range": "基金"},
                {"label": "持有", "domain": "基金", "range": "债券"}]}
        if "切片一" in user:
            return {"classes": [{"label": "基金"}]}
        return {"classes": [{"label": "债券"}]}

    res = extract_schema(["切片一", "切片二"], base_uri="u", use_cache=False, llm_call=call)
    assert res.stitched_relations == 1
    labels = {(o["domain"], o["range"]) for o in res.object_properties}
    assert ("基金", "债券") in labels and ("基金", "不存在的类") not in labels
    assert all(c["label"] in ("基金", "债券") for c in res.classes)  # 不造类


def test_extract_schema_stitch_cached():
    """缝合结果同样进 (model, prompt) 缓存：同输入第二次抽取不再调用 LLM。"""
    from app.adapters.extraction import extract_schema

    n_calls = {"n": 0}

    def call(system, user, schema):
        n_calls["n"] += 1
        if "跨切片关系缝合" in system:
            return {"relations": [{"label": "投资", "domain": "基金", "range": "债券"}]}
        if "缓存探针甲" in user:
            return {"classes": [{"label": "基金"}]}
        return {"classes": [{"label": "债券"}]}

    chunks = [{"index": 0, "text": "缝合缓存探针甲"}, {"index": 1, "text": "缝合缓存探针乙"}]
    r1 = extract_schema(chunks, base_uri="u", model_override={"model_name": TAG},
                        use_cache=True, llm_call=call)
    assert r1.stitched_relations == 1
    r2 = extract_schema(chunks, base_uri="u", model_override={"model_name": TAG},
                        use_cache=True, llm_call=call)
    assert r2.stitched_relations == 1
    assert n_calls["n"] == 3  # 2 次切片抽取 + 1 次缝合，第二轮全部命中缓存


def test_build_nodes_writes_provenance_fields():
    """类节点携带来源文档/切片/定义原文 → graph_rows 据此落 ProvenanceRecord（详情页溯源证据）。"""
    from app.tasks.extract_tasks import _build_nodes

    nodes, _ = _build_nodes([
        {"label": "基金", "definition": "募集资金的投资计划",
         "_source_chunk_index": 0, "_source_doc": "研报.txt"},
        {"label": "债券", "definition": "固定收益证券"},  # 无来源（如缝合占位）
    ])
    fund = next(n for n in nodes if n["data"]["label"] == "基金")
    bond = next(n for n in nodes if n["data"]["label"] == "债券")
    assert fund["data"]["source_document"] == "研报.txt"
    assert fund["data"]["source_chunk_index"] == 0
    assert fund["data"]["source_quote"] == "募集资金的投资计划"
    assert "source_document" not in bond["data"]


# ── API 契约（03 §8：模块码 schema_build + 项目角色）──


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

    user = User(username=f"{TAG}_u", role="user", is_active=True,
                hashed_password=bcrypt.hashpw(b"Passw0rd!123", bcrypt.gensalt()).decode())
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserModuleGrant(user_id=user.id, module_code="schema_build", allowed=1,
                           granted_by=user.id))
    db.add(UserModuleGrant(user_id=user.id, module_code="instance_build", allowed=1,
                           granted_by=user.id))
    db.commit()
    proj = Project(name=f"{TAG}_p", owner_id=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield {"user": user, "proj": proj, "headers": {}}
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


def test_api_schema_requires_chunks_and_validates_params(api_env):
    env = api_env
    env["headers"] = _login(env["user"].username)
    h = env["headers"]
    proj = env["proj"].id
    # 无切片 → 400 NO_CHUNKS
    r = client.post(f"/api/projects/{proj}/extraction/schema", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_CHUNKS"
    # 非法 parallelism → 400
    r = client.post(f"/api/projects/{proj}/extraction/schema", headers=h,
                    json={"parallelism": 99})
    assert r.status_code == 400


def test_api_task_not_found(api_env):
    proj = api_env["proj"].id
    h = api_env["headers"]
    r = client.get(f"/api/projects/{proj}/extraction/tasks/nonexistent-task", headers=h)
    assert r.status_code == 404


def test_api_instances_schema_version_contract(api_env, db):
    """框架复用（04 §9）：/instances 校验 schema_version_no（非法/不存在/合法但无切片）。"""
    proj = api_env["proj"].id
    h = api_env["headers"]
    # 非整数版本号 → 400（在切片检查之前）
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h,
                    json={"schema_version_no": "v1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_SCHEMA_VERSION"
    # 不存在的版本 → 404
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h,
                    json={"schema_version_no": 999})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SCHEMA_VERSION_NOT_FOUND"
    # 无画布骨架且未指定版本 → 400 NO_SCHEMA
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_SCHEMA"
    # 指定合法版本（先手动打版）可越过画布骨架门槛 → 走到切片检查（400 NO_CHUNKS，不会 .delay）
    from app.infrastructure.database import OntologyVersion, Project
    p = db.query(Project).filter(Project.id == proj).first()
    p.graph_data = {"nodes": [], "edges": []}  # 画布无骨架
    db.commit()
    ver = OntologyVersion(project_id=proj, version_no=1, kind="schema",
                          created_by=api_env["user"].id, label="框架复用测试版",
                          checksum="test-checksum")
    db.add(ver)
    db.commit()
    r = client.post(f"/api/projects/{proj}/extraction/instances", headers=h,
                    json={"schema_version_no": 1})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_CHUNKS", r.text


def test_api_requires_auth(api_env):
    proj = api_env["proj"].id
    r = client.post(f"/api/projects/{proj}/extraction/schema", json={})
    assert r.status_code in (401, 403)


def test_api_import_schema_from_project(api_env, db):
    """跨项目框架复用：把源项目框架导入目标项目（TBox 替换、实例保留、自动打版）。"""
    from app.infrastructure.database import OntologyVersion, Project
    user_id = api_env["user"].id
    src = Project(name=f"{TAG}_src", owner_id=user_id,
                  graph_data={"schema": {"classes": [{"label": "基金"}], "object_properties": []},
                              "nodes": [{"id": "cls_1", "data": {"label": "基金", "type": "Class"}},
                                        {"id": "cls_2", "data": {"label": "债券", "type": "Class"}},
                                        {"id": "inst_1", "data": {"label": "基金A", "type": "owl:NamedIndividual"}}],
                              "edges": [{"id": "e1", "source": "cls_1", "target": "cls_2", "data": {"relation": "投资"}}]})
    tgt = Project(name=f"{TAG}_tgt", owner_id=user_id,
                  graph_data={"nodes": [{"id": "inst_old", "data": {"label": "旧实例", "type": "owl:NamedIndividual"}}],
                              "edges": []})
    db.add_all([src, tgt])
    db.commit()
    db.refresh(src)
    db.refresh(tgt)
    try:
        h = api_env["headers"] = _login(api_env["user"].username)
        # 源=目标 → 400
        r = client.post(f"/api/projects/{tgt.id}/schema/import-from", headers=h,
                        json={"source_project_id": tgt.id})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_SOURCE_PROJECT"
        # 源不存在 → 404
        r = client.post(f"/api/projects/{tgt.id}/schema/import-from", headers=h,
                        json={"source_project_id": 999999})
        assert r.status_code == 404
        # 正常导入
        r = client.post(f"/api/projects/{tgt.id}/schema/import-from", headers=h,
                        json={"source_project_id": src.id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["imported_classes"] == 2 and body["imported_relations"] == 1
        assert body["version_no"] >= 1
        # 结束测试会话的读事务（REPEATABLE READ 快照），否则会读到导入前的旧数据
        db.commit()
        db.expire_all()
        t = db.query(Project).filter(Project.id == tgt.id).first()
        gd = t.graph_data
        types = sorted((n["data"]["type"] for n in gd["nodes"]))
        # 导入只替换框架层：目标原有实例 inst_old 保留，源实例不复制
        assert types == ["Class", "Class", "owl:NamedIndividual"]
        assert any(n["id"] == "inst_old" for n in gd["nodes"])
        assert gd["schema"]["classes"][0]["label"] == "基金"
        assert any(e["source"] == "cls_1" and e["target"] == "cls_2" for e in gd["edges"])
        vers = db.query(OntologyVersion).filter(OntologyVersion.project_id == tgt.id).all()
        assert len(vers) == 1 and vers[0].kind == "schema"
    finally:
        from app.core.config import settings
        from app.infrastructure.minio_client import get_minio_client
        for v in db.query(OntologyVersion).filter(OntologyVersion.project_id.in_([src.id, tgt.id])).all():
            if v.full_snapshot_key:
                try:
                    get_minio_client().client.remove_object(settings.MINIO_BUCKET_PARSED, v.full_snapshot_key)
                except Exception:  # noqa: BLE001
                    pass
        db.query(OntologyVersion).filter(OntologyVersion.project_id.in_([src.id, tgt.id])).delete(synchronize_session=False)
        db.delete(src)
        db.delete(tgt)
        db.commit()
