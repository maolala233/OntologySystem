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


def test_api_requires_auth(api_env):
    proj = api_env["proj"].id
    r = client.post(f"/api/projects/{proj}/extraction/schema", json={})
    assert r.status_code in (401, 403)
