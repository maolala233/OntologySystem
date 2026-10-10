# tests/test_report_inference.py - 报告「推理衍生事实」附录（推理期 R7：报告接入推理结果）
# 覆盖：_inference_rows 取最新批次、附录 markdown 表格与来源标注、竖线防串列、
#       条数上限截断、PPT 附录页大纲、generate_report 端点接线（include_inferred
#       开/关 × report/ppt，LLM 与导出 stub 离线确定性）。
import uuid

import pytest

import app.api.reports as reports
from app.api.reports import (
    MAX_APPENDIX_ROWS,
    ReportRequest,
    _inference_appendix_md,
    _inference_appendix_slide,
    _inference_rows,
    generate_report,
)

RINF_TAG = f"rinf{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    from app.infrastructure.database import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def env(db):
    """临时项目 + 两个推理批次（旧批次 2 行 / 新批次 3 行，验证 latest-only）。"""
    from app.infrastructure.database import Entity, Project, ReasoningResult

    proj = Project(name=f"{RINF_TAG}_推理附录", owner_id=1, status="draft")
    db.add(proj)
    db.commit()
    db.refresh(proj)
    pid = proj.id

    def row(batch, i, source="owlrl", rule_name=None):
        return ReasoningResult(
            project_id=pid, batch_id=f"{RINF_TAG}-{batch}", source=source,
            rule_name=rule_name,
            subject_uri=f"ex:S{i}", subject_label=f"主体{i}",
            predicate_uri="rdf:type", predicate_label="类型",
            object_uri=f"ex:O{i}", object_label=f"客体{i}")

    db.add_all([row("b0", 1), row("b0", 2),                       # 旧批次（应被忽略）
                row("b1", 1), row("b1", 2, "rule", "持有即投资"), row("b1", 3)])
    # 报告端点有 entity_total 门：至少一个实体
    ent = Entity(project_id=pid, uri=f"urn:onto:{pid}:ent1", label="个人投资者",
                 label_normalized="个人投资者", class_label="投资者",
                 is_class_node=False, status="auto")
    db.add(ent)
    db.commit()
    yield {"pid": pid}

    db.query(ReasoningResult).filter(ReasoningResult.project_id == pid).delete(
        synchronize_session=False)
    db.query(Entity).filter(Entity.project_id == pid).delete(synchronize_session=False)
    p = db.query(Project).filter(Project.id == pid).first()
    if p:
        db.delete(p)
    db.commit()


# ---------------------------------------------------------------- 纯单测

def test_inference_rows_latest_batch_only(env, db):
    rows = _inference_rows(db, env["pid"])
    assert [r.object_label for r in rows] == ["客体1", "客体2", "客体3"]  # 新批次且升序


def test_inference_rows_empty(db):
    from app.infrastructure.database import Project
    proj = Project(name=f"{RINF_TAG}_空批次", owner_id=1, status="draft")
    db.add(proj)
    db.commit()
    db.refresh(proj)
    try:
        assert _inference_rows(db, proj.id) == []
    finally:
        db.delete(proj)
        db.commit()


def test_appendix_md_table_and_origin(env, db):
    rows = _inference_rows(db, env["pid"])
    md = _inference_appendix_md(rows)
    assert "## 附录：推理衍生事实" in md
    assert "非原始事实记载" in md
    assert "正文分析未使用本节内容" in md
    assert "| 1 | 主体1 | 类型 | 客体1 | 蕴含推理（owlrl） |" in md
    assert "| 2 | 主体2 | 类型 | 客体2 | 规则「持有即投资」 |" in md


def test_appendix_md_pipe_sanitized(db):
    from app.infrastructure.database import ReasoningResult
    r = ReasoningResult(project_id=0, batch_id="x", source="owlrl", rule_name=None,
                        subject_uri="ex:a", subject_label="a|b",
                        predicate_uri="p", predicate_label="p|q",
                        object_uri="ex:c", object_label="c")
    md = _inference_appendix_md([r])
    assert "a／b" in md and "p／q" in md
    assert "a|b" not in md.split("\n")[-1]


def test_appendix_md_cap(monkeypatch, db):
    from app.infrastructure.database import ReasoningResult
    monkeypatch.setattr(reports, "MAX_APPENDIX_ROWS", 2)
    rows = [ReasoningResult(project_id=0, batch_id="x", source="owlrl", rule_name=None,
                            subject_uri=f"ex:{i}", subject_label=f"s{i}",
                            predicate_uri="p", predicate_label="类型",
                            object_uri=f"ex:o{i}", object_label=f"o{i}")
            for i in range(5)]
    md = _inference_appendix_md(rows)
    assert "s3" not in md, "超过上限的行不应出现在表格中"
    assert "共 5 条推导结果" in md
    assert MAX_APPENDIX_ROWS == 50  # 常量本身未被 monkeypatch 污染（还原校验）


def test_appendix_slide(env, db):
    from app.infrastructure.database import ReasoningResult
    # 5 行触发"共 N 条，展示前 4"折叠分支
    # 规则行放最前（附录页仅展示前 4 条，需验证规则来源出现在页面上）
    rows = [_rule_row()] + [
        ReasoningResult(project_id=0, batch_id="x", source="owlrl", rule_name=None,
                        subject_uri=f"ex:{i}", subject_label=f"s{i}",
                        predicate_uri="p", predicate_label="类型",
                        object_uri=f"ex:o{i}", object_label=f"o{i}")
        for i in range(5)]
    slide = _inference_appendix_slide(rows)
    assert slide["title"] == "附录：推理衍生事实"
    assert "非原始事实记载" in slide["bullets"][0]
    assert any("规则「持有即投资」" in b for b in slide["bullets"])
    assert any("共 6 条" in b for b in slide["bullets"])


def _rule_row():
    from app.infrastructure.database import ReasoningResult
    return ReasoningResult(project_id=0, batch_id="x", source="rule",
                           rule_name="持有即投资", subject_uri="ex:s",
                           subject_label="s", predicate_uri="p",
                           predicate_label="投资", object_uri="ex:o", object_label="o")


# ---------------------------------------------------------------- 端点接线（stub LLM 与导出）

@pytest.fixture()
def stub_llm(monkeypatch):
    monkeypatch.setattr(reports, "_llm_call",
                        lambda llm, system, user: "# 测试报告\n\n正文结论。")


@pytest.fixture()
def stub_export(monkeypatch):
    monkeypatch.setattr(reports, "_upload_export",
                        lambda pid, name, data, ct: f"http://export/{name}")


def _admin(db):
    from app.infrastructure.database import User
    return db.query(User).filter(User.username == "admin").first()


def test_generate_report_appends_inference(env, db, stub_llm, stub_export, monkeypatch):
    from app.infrastructure.llm_client import LLMClient
    monkeypatch.setattr(reports, "build_legacy_llm_config",
                        lambda db_, pid: {"api_key": "k", "base_url": "http://x", "model": "m"})
    user = _admin(db)
    body = ReportRequest(kind="report", topic="测试", include_inferred=True)
    out = generate_report(env["pid"], body, db, user)
    assert "## 附录：推理衍生事实" in out["content"]
    assert "| 1 | 主体1 | 类型 | 客体1 | 蕴含推理（owlrl） |" in out["content"]
    assert "正文结论" in out["content"], "正文应保留且在附录之前"
    assert out["content"].index("正文结论") < out["content"].index("附录：推理衍生事实")


def test_generate_report_without_inference(env, db, stub_llm, stub_export, monkeypatch):
    from app.infrastructure.llm_client import LLMClient
    monkeypatch.setattr(reports, "build_legacy_llm_config",
                        lambda db_, pid: {"api_key": "k", "base_url": "http://x", "model": "m"})
    user = _admin(db)
    body = ReportRequest(kind="report", topic="测试", include_inferred=False)
    out = generate_report(env["pid"], body, db, user)
    assert "附录：推理衍生事实" not in out["content"]
    assert out["content"].endswith("正文结论。")


def test_generate_ppt_appends_inference_slide(env, db, stub_export, monkeypatch):
    from app.infrastructure.llm_client import LLMClient
    monkeypatch.setattr(reports, "build_legacy_llm_config",
                        lambda db_, pid: {"api_key": "k", "base_url": "http://x", "model": "m"})
    monkeypatch.setattr(reports, "_llm_call",
                        lambda llm, system, user:
                        '{"title":"演示","subtitle":"副标题","slides":'
                        '[{"title":"背景","bullets":["要点1","要点2"]}]}')
    monkeypatch.setattr(reports, "_build_pptx", lambda outline, project_name="": b"pptx")
    user = _admin(db)
    body = ReportRequest(kind="ppt", topic="测试", include_inferred=True)
    out = generate_report(env["pid"], body, db, user)
    titles = [s["title"] for s in out["outline"]["slides"]]
    assert titles[-1] == "附录：推理衍生事实"
    assert titles[0] == "背景"


def test_generate_report_no_inference_data(env, db, stub_llm, stub_export, monkeypatch):
    """项目无推理结果时开关打开也不报错、不附空附录。"""
    from app.infrastructure.database import Entity, Project
    from app.infrastructure.llm_client import LLMClient
    monkeypatch.setattr(reports, "build_legacy_llm_config",
                        lambda db_, pid: {"api_key": "k", "base_url": "http://x", "model": "m"})
    proj = Project(name=f"{RINF_TAG}_无推理", owner_id=1, status="draft")
    db.add(proj)
    db.commit()
    db.refresh(proj)
    db.add(Entity(project_id=proj.id, uri=f"urn:onto:{proj.id}:ent1", label="测试实体",
                  label_normalized="测试实体", class_label="测试类",
                  is_class_node=False, status="auto"))
    db.commit()
    try:
        user = _admin(db)
        body = ReportRequest(kind="report", topic="测试", include_inferred=True)
        out = generate_report(proj.id, body, db, user)
        assert "附录：推理衍生事实" not in out["content"]
    finally:
        db.query(Entity).filter(Entity.project_id == proj.id).delete(synchronize_session=False)
        db.delete(proj)
        db.commit()
