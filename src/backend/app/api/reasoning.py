# app/api/reasoning.py — 蕴含推理与规则引擎 API（推理期 R1/R3）
# 运行推理/读结果：resolution 模块（与冲突检测同守卫）；规则 CRUD：另需项目 editor 角色。

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_module, require_project_role
from app.infrastructure.database import OntologyRule, Project, User, get_db
from app.services import reasoning_service
from app.services.axiom_store import AXIOM_KINDS, certify_axiom, declare_axiom, list_axioms, remove_axiom

router = APIRouter(prefix="/api/projects/{project_id}", tags=["reasoning"])


def _load_project_or_404(project_id: int, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# ── 推理运行与结果 ──

class ReasoningRunBody(BaseModel):
    profile: str = "owlrl"          # 'owlrl'（OWL-RL+RDFS）| 'rdfs'
    rule_ids: list[int] | None = None  # 缺省=全部启用规则


@router.post("/reasoning/run", dependencies=[Depends(require_module("resolution"))])
def run_reasoning_endpoint(
    project_id: int,
    body: ReasoningRunBody | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """执行推理批次：owlrl 语义闭包（蕴含）+ 启用规则（产生式）。

    结果只落 reasoning_results（source 区分 'owlrl'/'rdfs'/'rule'），不回写事实表。
    """
    _load_project_or_404(project_id, db)
    body = body or ReasoningRunBody()
    if body.profile not in ("owlrl", "rdfs"):
        raise HTTPException(status_code=400, detail="profile 仅支持 'owlrl' 或 'rdfs'")
    project = db.query(Project).filter(Project.id == project_id).first()
    summary = reasoning_service.run_reasoning(db, project, profile=body.profile,
                                              rule_ids=body.rule_ids)
    return summary


@router.get("/reasoning/results", dependencies=[Depends(require_module("resolution"))])
def get_reasoning_results(
    project_id: int,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None, description="过滤：owlrl/rdfs/rule，支持逗号分隔多值（如 owlrl,rdfs=全部蕴含）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """最新批次推理结果（仅推导三元组；原始事实不在此表）。"""
    _load_project_or_404(project_id, db)
    return reasoning_service.get_latest_results(db, project_id, limit=limit,
                                                offset=offset, source=source)


@router.get("/reasoning/rule-suggestions", dependencies=[Depends(require_module("resolution"))])
def get_rule_suggestions(
    project_id: int,
    channel: str = Query("all", description="挖掘通道：statistical/llm/all（默认双通道）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI 规则挖掘（S9）：统计通道（物质化蕴含）+ LLM 通道（规则抽取）双通道候选，
    只读不落库，采纳走 POST /reasoning/rules 人工门禁（与公理建议同哲学）。"""
    _load_project_or_404(project_id, db)
    if channel not in ("statistical", "llm", "all"):
        raise HTTPException(status_code=400, detail="channel 仅支持 statistical/llm/all")
    project = db.query(Project).filter(Project.id == project_id).first()
    stat: list[dict] = []
    if channel in ("statistical", "all"):
        stat = reasoning_service.mine_rule_suggestions(db, project)
    llm_items: list[dict] = []
    llm_stats: dict | None = None
    if channel in ("llm", "all"):
        llm_items, llm_stats = reasoning_service.llm_rule_suggestions(db, project)
    items = (stat if channel == "statistical"
             else llm_items if channel == "llm"
             else reasoning_service.merge_rule_suggestions(stat, llm_items))
    out = {"items": items}
    if llm_stats is not None:
        out["llm"] = llm_stats
    return out


# ── 推理规则 CRUD ──

class RuleBody(BaseModel):
    name: str
    if_subject_class: str
    if_predicate: str
    if_object_class: str
    then_predicate: str
    enabled: bool = True


def _load_rule_or_404(project_id: int, rule_id: int, db: Session) -> OntologyRule:
    rule = db.query(OntologyRule).filter(
        OntologyRule.id == rule_id, OntologyRule.project_id == project_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.get("/reasoning/rules", dependencies=[Depends(require_module("resolution"))])
def list_rules(project_id: int, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    _load_project_or_404(project_id, db)
    rules = db.query(OntologyRule).filter(
        OntologyRule.project_id == project_id).order_by(OntologyRule.id).all()
    return {"items": [
        {"id": r.id, "name": r.name, "if_subject_class": r.if_subject_class,
         "if_predicate": r.if_predicate, "if_object_class": r.if_object_class,
         "then_predicate": r.then_predicate, "enabled": bool(r.enabled),
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rules
    ]}


@router.post("/reasoning/rules", dependencies=[Depends(require_module("resolution"))])
def create_rule(project_id: int, body: RuleBody,
                current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """新建规则。"""
    require_project_role(project_id, "editor", current_user, db)
    _load_project_or_404(project_id, db)
    if not body.name.strip() or not body.then_predicate.strip():
        raise HTTPException(status_code=400, detail="规则名称与结论谓词不能为空")
    rule = OntologyRule(project_id=project_id, **body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"id": rule.id, "name": rule.name}


@router.put("/reasoning/rules/{rule_id}", dependencies=[Depends(require_module("resolution"))])
def update_rule(project_id: int, rule_id: int, body: RuleBody,
                current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """更新规则（含启停）。"""
    require_project_role(project_id, "editor", current_user, db)
    rule = _load_rule_or_404(project_id, rule_id, db)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    db.commit()
    return {"id": rule.id, "name": rule.name, "enabled": bool(rule.enabled)}


@router.delete("/reasoning/rules/{rule_id}", dependencies=[Depends(require_module("resolution"))])
def delete_rule(project_id: int, rule_id: int,
                current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除规则。"""
    require_project_role(project_id, "editor", current_user, db)
    rule = _load_rule_or_404(project_id, rule_id, db)
    db.delete(rule)
    db.commit()
    return {"deleted": True, "id": rule_id}


# ── 公理声明 CRUD（O4 后扩展：规则与公理统一管理）──
# 与审核批准共用 axiom_store.declare_axiom 写入路径（schema 权威键 + 画布双写）；
# 列表/删除按 axiom key 定位。domain_fix/range_fix 与原生 domain/range 不可区分，
# 不在本 CRUD 暴露（经画布属性面板调整）。


class AxiomBody(BaseModel):
    kind: str
    predicate: str | None = None
    predicates: list[str] | None = None
    classes: list[str] | None = None
    value: str | None = None
    min_card: int | None = None
    max_card: int | None = None
    # S16：证据锚点（chunk 全量序号 → 审批/声明时固化为锚点；或直接传锚点 dict）
    chunk_indices: list[int] | None = None
    evidence: dict | list | None = None


class AxiomCertifyBody(BaseModel):
    key: str
    certified: bool = True


@router.get("/reasoning/axioms", dependencies=[Depends(require_module("resolution"))])
def list_axioms_endpoint(project_id: int, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """已声明公理列表（来自画布 graph_data，审核批准/手工新增同源）。

    附带 schema 类/谓词清单（前端公理表单下拉用）。
    """
    proj = _load_project_or_404(project_id, db)
    require_project_role(project_id, "viewer", current_user, db)
    schema = (proj.graph_data or {}).get("schema") or {}
    return {
        "items": list_axioms(proj),
        "kinds": list(AXIOM_KINDS),
        "class_labels": sorted({str(c.get("label")) for c in schema.get("classes") or []
                                if isinstance(c, dict) and c.get("label")}),
        "predicate_labels": sorted({str(op_.get("label")) for op_ in schema.get("object_properties") or []
                                    if isinstance(op_, dict) and op_.get("label")}),
    }


@router.post("/reasoning/axioms", dependencies=[Depends(require_module("resolution"))])
def declare_axiom_endpoint(project_id: int, body: AxiomBody,
                           current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """手工声明公理（编辑走 delete + 重新 declare）。

    S16：带 chunk_indices 时固化为证据锚点（steward_specified + certified 语义不变）。
    """
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    payload = body.model_dump()
    if payload.get("chunk_indices") and not payload.get("evidence"):
        from app.services.review_service import _evidence_anchor

        anchor = _evidence_anchor(db, project_id, payload["chunk_indices"], "")
        if anchor:
            payload["evidence"] = anchor
    result = declare_axiom(db, proj, payload)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result


@router.delete("/reasoning/axioms", dependencies=[Depends(require_module("resolution"))])
def remove_axiom_endpoint(project_id: int, key: str = Query(..., min_length=3),
                          current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """按 key 撤销一条公理声明（同时清理 schema 权威键与画布标记）。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    result = remove_axiom(db, proj, key)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result


@router.post("/reasoning/axioms/certify", dependencies=[Depends(require_module("resolution"))])
def certify_axiom_endpoint(project_id: int, body: AxiomCertifyBody,
                           current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """S16：公理一键认证/撤销（存量公理盘点用；写 axiom_provenance，list 即时可见）。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    result = certify_axiom(db, proj, body.key, certified=body.certified,
                           decided_by=current_user.id)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result


# ─────────────── S24：治理指标（docs/15 / GovernedMetric） ───────────────

class MetricBody(BaseModel):
    label: str
    definition: str | None = None
    expression: str | None = None      # 口径（如 "单笔等值 ≥ 50 万美元须大额审批"）
    unit: str | None = None
    applies_to: list[str] | None = None  # 适用类
    severity: str | None = None        # block | warn
    chunk_indices: list[int] | None = None
    evidence: dict | None = None


@router.get("/reasoning/metrics", dependencies=[Depends(require_module("resolution"))])
def list_metrics_endpoint(project_id: int, current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """治理指标列表（口径的单一权威来源）。"""
    require_project_role(project_id, "viewer", current_user, db)
    proj = _load_project_or_404(project_id, db)
    from app.services.metrics_store import list_metrics

    return {"items": list_metrics(proj)}


@router.post("/reasoning/metrics", dependencies=[Depends(require_module("resolution"))])
def declare_metric_endpoint(project_id: int, body: MetricBody,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """声明/更新指标（按 label 幂等；人工路径默认 steward_specified+certified）。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    payload = body.model_dump()
    if payload.get("chunk_indices") and not payload.get("evidence"):
        from app.services.review_service import _evidence_anchor

        anchor = _evidence_anchor(db, project_id, payload["chunk_indices"], "")
        if anchor:
            payload["evidence"] = anchor
    from app.services.metrics_store import declare_metric

    result = declare_metric(db, proj, payload)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result


@router.delete("/reasoning/metrics", dependencies=[Depends(require_module("resolution"))])
def remove_metric_endpoint(project_id: int, label: str = Query(..., min_length=1),
                           current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    from app.services.metrics_store import remove_metric

    result = remove_metric(db, proj, label)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result


@router.post("/reasoning/metrics/certify", dependencies=[Depends(require_module("resolution"))])
def certify_metric_endpoint(project_id: int, body: dict,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """指标一键认证/撤销（存量盘点）。"""
    require_project_role(project_id, "editor", current_user, db)
    proj = _load_project_or_404(project_id, db)
    from app.services.metrics_store import certify_metric

    result = certify_metric(db, proj, str(body.get("label") or ""),
                            certified=bool(body.get("certified", True)),
                            decided_by=current_user.id)
    if "skipped" in result:
        raise HTTPException(status_code=400, detail=result["skipped"])
    db.commit()
    return result
