# tests/test_qa_inference.py - 问答语义推理引用注入（推理期 R6：问答接入推理结果）
# 覆盖：_inference_sources 词项命中与名额上限、来源双标注（ref_type/doc_file）、
#       retrieve() 融合时推理引用垫底不挤占事实、use_inferred=False 关闭、
#       QaRequest 透传 use_inferred。HTTP 层沿用 test_qa_upgrade 的 stub 思路（离线确定性）。
import uuid

import pytest
from fastapi.testclient import TestClient

from app.adapters.retrieval import (
    MAX_INFERRED_SOURCES,
    MAX_SOURCES,
    RetrievalQuery,
    _inference_sources,
    retrieve,
)
from main import app

client = TestClient(app)

QINF_TAG = f"qinf{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    """临时项目 + 语义推理行（类型传播 / 规则推理各若干）+ 一个事实实体对。"""
    from app.infrastructure.database import (
        Entity, Project, ReasoningResult, Relation,
    )

    proj = Project(name=f"{QINF_TAG}_推理问答", owner_id=1, status="draft")
    db.add(proj)
    db.commit()
    db.refresh(proj)
    pid = proj.id

    rows = [
        ReasoningResult(project_id=pid, batch_id=f"{QINF_TAG}-b1", source="owlrl",
                        rule_name=None,
                        subject_uri="ex:I_1", subject_label="个人投资者",
                        predicate_uri="rdf:type", predicate_label="类型",
                        object_uri="ex:C_1", object_label="投资者"),
        ReasoningResult(project_id=pid, batch_id=f"{QINF_TAG}-b1", source="rule",
                        rule_name="持有即投资",
                        subject_uri="ex:I_1", subject_label="个人投资者",
                        predicate_uri="ex:prop_invest", predicate_label="投资",
                        object_uri="ex:I_2", object_label="理财产品"),
    ] + [
        ReasoningResult(project_id=pid, batch_id=f"{QINF_TAG}-b1", source="owlrl",
                        rule_name=None,
                        subject_uri=f"ex:I_{i}", subject_label=f"无关实体{i}",
                        predicate_uri="rdf:type", predicate_label="类型",
                        object_uri="ex:C_9", object_label="无关类")
        for i in range(10)
    ]
    db.add_all(rows)
    # 事实：个人投资者 —持有→ 理财产品（graph 扩展种子与命中）
    e1 = Entity(project_id=pid, uri=f"urn:onto:{pid}:entity/ent_s", label="个人投资者",
                label_normalized="个人投资者", class_label="投资者",
                is_class_node=False, status="auto")
    e2 = Entity(project_id=pid, uri=f"urn:onto:{pid}:entity/ent_o", label="理财产品",
                label_normalized="理财产品", class_label="理财产品",
                is_class_node=False, status="auto")
    db.add_all([e1, e2])
    db.commit()
    db.refresh(e1)
    db.refresh(e2)
    db.add(Relation(project_id=pid, subject_id=e1.id, predicate="持有",
                    object_id=e2.id, is_class_edge=False, status="auto"))
    db.commit()

    yield {"pid": pid}

    db.query(ReasoningResult).filter(ReasoningResult.project_id == pid).delete(synchronize_session=False)
    db.query(Relation).filter(Relation.project_id == pid).delete(synchronize_session=False)
    db.query(Entity).filter(Entity.project_id == pid).delete(synchronize_session=False)
    p2 = db.query(Project).filter(Project.id == pid).first()
    if p2:
        db.delete(p2)
    db.commit()


# ---------------------------------------------------------------- _inference_sources 纯单测

def test_inference_sources_match_and_annotate(env, db):
    """命中的推理行 → ref_type=inference + doc_file 双标注 + quote 带"语义推理产物"。"""
    q = RetrievalQuery(project_id=env["pid"], question="个人投资者属于什么类别？")
    out = _inference_sources(q, db)
    assert out, "应命中'个人投资者'相关推理行"
    assert all(s.ref_type == "inference" for s in out)
    assert all(s.doc_file == "语义推理（非原始事实）" for s in out)
    assert all("语义推理产物" in s.quote for s in out)
    # 排序：命中词项多者在前（"个人投资者…理财产品…投资"行命中更多）
    assert "投资" in out[0].quote or "类型" in out[0].quote
    assert len(out) <= MAX_INFERRED_SOURCES


def test_inference_sources_no_match(env, db):
    q = RetrievalQuery(project_id=env["pid"], question="完全不相干的问题量子芯片")
    assert _inference_sources(q, db) == []


def test_inference_sources_rule_origin_annotated(env, db):
    """规则推理行在 quote 里带规则名，蕴含推理行带档位来源。"""
    q = RetrievalQuery(project_id=env["pid"], question="个人投资者 投资 理财产品")
    out = _inference_sources(q, db)
    joined = " ".join(s.quote for s in out)
    assert "规则「持有即投资」" in joined
    assert "蕴含推理（owlrl）" in joined


# ---------------------------------------------------------------- retrieve() 融合

def test_retrieve_appends_inference_after_facts(env, db, monkeypatch):
    """事实引用在前、推理引用垫底补充；总量不超 MAX_SOURCES。"""
    from app.adapters import retrieval as r

    # 向量路径失败（离线）；关键词路径对临时项目无切片返回空——只留图事实 + 推理
    monkeypatch.setattr(r, "_vector_sources", lambda query: [])

    q = RetrievalQuery(project_id=env["pid"], question="个人投资者持有什么产品？")
    out = retrieve(q, db=db)
    assert out, "图事实 + 推理引用都应有产出"
    types = [s.ref_type for s in out]
    assert "graph_edge" in types, "事实引用应存在"
    assert "inference" in types, "推理引用应存在"
    # 事实引用全部位于推理引用之前（不混排）
    first_inf = types.index("inference")
    assert all(t != "graph_edge" for t in types[first_inf:])
    assert len(out) <= MAX_SOURCES


def test_retrieve_use_inferred_false_excludes(env, db, monkeypatch):
    from app.adapters import retrieval as r

    monkeypatch.setattr(r, "_vector_sources", lambda query: [])

    q = RetrievalQuery(project_id=env["pid"], question="个人投资者持有什么产品？",
                       use_inferred=False)
    out = retrieve(q, db=db)
    assert all(s.ref_type != "inference" for s in out)


def test_retrieve_inference_reserved_when_facts_full(env, db, monkeypatch):
    """事实引用满额时推理引用仍占保留名额出现（事实压缩让位），总量不超上限。"""
    from app.adapters import retrieval as r

    monkeypatch.setattr(r, "_vector_sources", lambda query: [])
    monkeypatch.setattr(r, "_keyword_sources", lambda query, db: [])

    def fake_expand(db_, query, seeds):
        return [{"subject": f"实体{i}", "predicate": "持有", "object": "理财产品",
                 "hop": 1, "score": 0.5, "entity_ids": []} for i in range(12)]

    monkeypatch.setattr(r, "_graph_expand", fake_expand)

    q = RetrievalQuery(project_id=env["pid"], question="个人投资者持有什么产品？")
    out = retrieve(q, db=db)
    types = [s.ref_type for s in out]
    assert "inference" in types, "事实满额时推理引用应占保留名额"
    assert len(out) <= MAX_SOURCES


# ---------------------------------------------------------------- QaRequest 透传

def test_qa_request_accepts_use_inferred(env):
    from app.api.qa import QaRequest

    body = QaRequest(question="个人投资者是投资者吗", use_inferred=False)
    assert body.use_inferred is False
    assert QaRequest(question="默认开").use_inferred is True
