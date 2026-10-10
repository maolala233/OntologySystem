# app/api/reasoning.py — 蕴含推理与规则引擎 API（推理期 R1/R3）
# 运行推理/读结果：resolution 模块（与冲突检测同守卫）；规则 CRUD：另需项目 editor 角色。

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_module, require_project_role
from app.infrastructure.database import OntologyRule, Project, ReasoningResult, User, get_db
from app.services import reasoning_service

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
    source: str | None = Query(None, description="过滤：owlrl/rdfs/rule"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """最新批次推理结果（仅推导三元组；原始事实不在此表）。"""
    _load_project_or_404(project_id, db)
    return reasoning_service.get_latest_results(db, project_id, limit=limit, source=source)


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
