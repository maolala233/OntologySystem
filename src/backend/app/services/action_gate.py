# app/services/action_gate.py - 执行前合规校验（S24-3，docs/15 对外服务优化核心）
# 智能体拟执行业务操作（加事实/加实例）→ 平台用 TBox/公理/指标口径做确定性机检：
#   verdict: allow | warn | block；hits: [{kind, severity, message, metric?}]
# 双挂载保证"架构上不可绕过"：①对外 MCP 工具 precheck_action（Agent 主动问）；
# ②MCP 写工具（add_entity/add_relationship）执行前同源调用（被动拦）。
from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, Project, Relation


def _class_of_label(gd: dict, label: str) -> dict | None:
    for c in (gd.get("schema") or {}).get("classes") or []:
        if isinstance(c, dict) and str(c.get("label") or "") == label:
            return c
    return None


def precheck_action(db: Session, proj: Project, action: dict) -> dict:
    """确定性机检（无 LLM）：

    action 形态：
      {"kind": "add_fact", "subject_label", "predicate", "object_label"}
      {"kind": "add_entity", "class_label", "properties": {...}}
    检查项：
      ① domain/range 违例（block）
      ② disjoint_with 类对（block）
      ③ 函数性谓词已有值（warn，替换语义须确认）
      ④ 指标口径命中（限额/审批线类指标 → 按指标 severity 提示；金额超限判定留给
         调用方读 expression，平台不下断言数值——诚实不越界）
    """
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") or {}
    ops = {str(o.get("label") or ""): o for o in (schema.get("object_properties") or [])
           if isinstance(o, dict)}
    # 类索引 = schema.classes ∪ 画布类节点（画布优先流：类可先于 schema 存在）
    classes = {str(c.get("label") or ""): c for c in (schema.get("classes") or [])
               if isinstance(c, dict)}
    for n in (gd.get("nodes") or []):
        d = n.get("data") or {}
        if d.get("type") in ("owl:Class", "Class") and d.get("label"):
            classes.setdefault(str(d["label"]), {"label": str(d["label"])})
    hits: list[dict] = []

    kind = str(action.get("kind") or "").strip()

    if kind == "add_fact":
        subj = str(action.get("subject_label") or "").strip()
        pred = str(action.get("predicate") or "").strip()
        obj = str(action.get("object_label") or "").strip()
        if not (subj and pred and obj):
            return {"verdict": "block",
                    "hits": [{"kind": "bad_args", "severity": "block",
                              "message": "add_fact 需要 subject_label/predicate/object_label"}]}
        # ① domain/range（subject/object 的类 vs 谓词声明）
        subj_cls = _instance_class(db, proj.id, subj) or (subj if subj in classes else None)
        obj_cls = _instance_class(db, proj.id, obj) or (obj if obj in classes else None)
        op = ops.get(pred)
        if op is not None:
            dom, rng = str(op.get("domain") or ""), str(op.get("range") or "")
            if dom and subj_cls and subj_cls != dom:
                hits.append({"kind": "domain_violation", "severity": "block",
                             "message": f"谓词「{pred}」的 domain 是「{dom}」，主语「{subj}」属于「{subj_cls}」"})
            if rng and obj_cls and obj_cls != rng:
                hits.append({"kind": "range_violation", "severity": "block",
                             "message": f"谓词「{pred}」的 range 是「{rng}」，宾语「{obj}」属于「{obj_cls}」"})
        # ② disjoint（subject 类与 object 类互斥 → 该事实制造矛盾）
        if subj_cls and obj_cls:
            sc, oc = classes.get(subj_cls), classes.get(obj_cls)
            if sc and oc and obj_cls in [str(x) for x in (sc.get("disjoint_with") or [])]:
                hits.append({"kind": "disjoint_violation", "severity": "block",
                             "message": f"「{subj_cls}」与「{obj_cls}」互斥（owl:disjointWith），该事实将制造逻辑矛盾"})
        # ③ 函数性谓词已有值
        if op is not None and op.get("functional"):
            s_ent = _entity_by_label(db, proj.id, subj)
            if s_ent is not None:
                dup = (db.query(Relation)
                       .join(Entity, Relation.object_id == Entity.id)
                       .filter(Relation.project_id == proj.id,
                               Relation.subject_id == s_ent.id,
                               Relation.predicate == pred).count())
                if dup > 0:
                    hits.append({"kind": "functional_duplicate", "severity": "warn",
                                 "message": f"「{pred}」是函数性谓词且「{subj}」已有值（{dup} 条）——新增将覆盖口径，须确认"})
        # ④ 指标口径命中（涉及该谓词或该类）
        _metric_hits(gd, hits, classes_for=(subj_cls, obj_cls), predicate=pred)

    elif kind == "add_entity":
        cls = str(action.get("class_label") or "").strip()
        if not cls:
            return {"verdict": "block",
                    "hits": [{"kind": "bad_args", "severity": "block",
                              "message": "add_entity 需要 class_label"}]}
        if cls not in classes:
            hits.append({"kind": "unknown_class", "severity": "block",
                         "message": f"类「{cls}」不在 TBox 中"})
        else:
            _metric_hits(gd, hits, classes_for=(cls,), predicate=None)
    else:
        return {"verdict": "block",
                "hits": [{"kind": "bad_args", "severity": "block",
                          "message": "kind 取值限 add_fact | add_entity"}]}

    if any(h["severity"] == "block" for h in hits):
        verdict = "block"
    elif hits:
        verdict = "warn"
    else:
        verdict = "allow"
    return {"verdict": verdict, "hits": hits,
            "note": "确定性机检（无 LLM）：block=不得执行；warn=须确认后执行；"
                    "指标命中含口径表达式与 severity，数值判断由调用方按口径执行。"}


def _instance_class(db: Session, project_id: int, label: str) -> str | None:
    e = _entity_by_label(db, project_id, label)
    return e.class_label if e else None


def _entity_by_label(db: Session, project_id: int, label: str) -> Entity | None:
    return (db.query(Entity)
            .filter(Entity.project_id == project_id,
                    Entity.label == label).first())


def _metric_hits(gd: dict, hits: list, classes_for: tuple, predicate: str | None) -> None:
    from app.services.metrics_store import find_metrics_for

    seen: set[str] = set()
    for cls in classes_for:
        if not cls:
            continue
        for m in find_metrics_for(gd, class_label=cls):
            if m["label"] in seen:
                continue
            seen.add(m["label"])
            hits.append({
                "kind": "metric", "severity": m.get("severity") or "warn",
                "metric": {"label": m["label"], "expression": m.get("expression") or "",
                           "unit": m.get("unit") or "", "applies_to": m.get("applies_to"),
                           "certified": bool(m.get("certified"))},
                "message": f"命中指标口径「{m['label']}」：{m.get('expression') or '（见指标定义）'}"
                           f"{'（人工认证）' if m.get('certified') else ''}",
            })
