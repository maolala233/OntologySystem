# app/services/pipeline_selfcheck.py - 本体质量自检修复环（新手简化管线 2026-11）
# 准出标准 = TBox 机检无 error（类层次环/悬空父类/自父/不可满足类）。
# 循环：run_schema_checks → 有 error 则把问题清单喂给 LLM 定向修复 → 重跑机检，
# 最多 rounds 轮；轮次耗尽仍有硬伤 → 物化 critical 审核项（conflict_axiom），
# 发布门禁自然拦下——自动修复永不把坏结果静默放行。
#
# 修复动作约束（尊重原文档，最小干预）：只允许 删除肇事类 / 断开坏 is-a 边 /
# 撤销引发矛盾的互斥或基数 / 把重复概念并为已有类别名——禁止改写类定义、
# 禁止发明新类新关系、禁止批量改动。LLM 只在「哪条最小动作能消掉哪些硬伤」上做判断。
from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from sqlalchemy.orm import Session

# 修复 prompt 素材预算（对齐 axiom_llm 口径：LLM 不做全量输入）
MAX_CLASSES_IN_PROMPT = 150
MAX_PROMPT_CHARS = 12000
MAX_ROUNDS_CAP = 5

REPAIR_ACTIONS = ("remove_class", "remove_parent", "merge_alias",
                  "remove_disjoint", "remove_cardinality")

_REPAIR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": list(REPAIR_ACTIONS)},
                    "label": {"type": "string"},
                    "parent": {"type": "string"},
                    "into": {"type": "string"},
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "predicate": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    "required": ["actions"],
}

SYSTEM_PROMPT = (
    "你是本体质量修复器。给定 TBox 机检发现的硬伤清单（类层次环/悬空父类/自父引用/"
    "不可满足类）和当前类层次，请输出**最小修复动作**消除硬伤。"
    "硬性约束：只允许 remove_class（删肇事类）、remove_parent（断开某类的某条 is-a）、"
    "merge_alias（把概念并为已有类别名，删该类）、remove_disjoint（撤销互斥声明）、"
    "remove_cardinality（撤销 min>max 基数）；禁止发明新类/新关系，禁止改写类定义，"
    "动作数量尽量少（宁少勿多，拿不准就不动）。输出 {\"actions\": [...]}。"
)


def _build_llm(db: Session, project_id: int, model_config_id: Optional[int] = None):
    """修复用 LLM：与公理扫描同源（extract 配置；缺配置返回 None → 跳过修复）。"""
    try:
        from app.adapters.provider import build_legacy_llm_config
        from app.infrastructure.llm_client import LLMClient

        cfg = build_legacy_llm_config(db, project_id)
        return LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                         model=cfg.get("model"))
    except Exception:  # noqa: BLE001
        return None


def _repair_material(schema: dict, issues: list[dict]) -> str:
    """修复素材：硬伤清单 + 相关类的层次/互斥/基数声明（预算内截断）。"""
    involved: set[str] = set()
    for it in issues:
        involved.update(it.get("classes") or [])
    rows = []
    chars = 0
    for c in schema.get("classes") or []:
        label = str((c or {}).get("label") or "")
        if not label:
            continue
        if involved and label not in involved and len(rows) >= 30:
            continue  # 无关类只带前 30 个占位，保预算
        entry = {"label": label}
        for k in ("parent_classes", "disjoint_with", "equivalent_to", "aliases"):
            if c.get(k):
                entry[k] = c[k]
        line = str(entry)
        if chars + len(line) > MAX_PROMPT_CHARS or len(rows) >= MAX_CLASSES_IN_PROMPT:
            rows.append({"note": "（素材截断）"})
            break
        rows.append(entry)
        chars += len(line)
    cards = []
    for op in schema.get("object_properties") or []:
        for i, r in enumerate((op or {}).get("cardinality_restrictions") or []):
            if (r or {}).get("min") is not None and (r or {}).get("max") is not None \
                    and r["min"] > r["max"]:
                cards.append({"predicate": op.get("label"), "min": r["min"],
                              "max": r["max"], "index": i})
    return "【机检硬伤】\n" + "\n".join(
        f"- [{it['rule']}] {it['message']}" for it in issues
    ) + "\n\n【相关类声明】\n" + "\n".join(str(r) for r in rows) + \
        (("\n\n【min>max 基数】\n" + "\n".join(str(c) for c in cards)) if cards else "")


def _apply_action(schema: dict, act: dict) -> Optional[str]:
    """校验并应用单条修复动作（最小干预；返回人话描述，非法返回 None）。"""
    action = str(act.get("action") or "")
    classes_by_label = {str(c.get("label") or ""): c
                        for c in schema.get("classes") or [] if isinstance(c, dict)}

    def _drop_class(label: str) -> bool:
        c = classes_by_label.get(label)
        if c is None:
            return False
        schema["classes"] = [x for x in schema["classes"] if x is not c]
        # 引用它的 parent_classes 一并清掉（悬空防御）
        for x in schema["classes"]:
            pc = x.get("parent_classes") or []
            if label in pc:
                x["parent_classes"] = [p for p in pc if p != label]
        return True

    if action == "remove_class":
        label = str(act.get("label") or "").strip()
        return f"删除类「{label}」" if label and _drop_class(label) else None
    if action == "merge_alias":
        into = str(act.get("into") or "").strip()
        label = str(act.get("label") or "").strip()
        target = classes_by_label.get(into)
        if target is None or not label or label == into or classes_by_label.get(label) is None:
            return None
        if not _drop_class(label):
            return None
        aliases = target.setdefault("aliases", [])
        if label not in aliases:
            aliases.append(label)
        return f"把「{label}」并入「{into}」作别名"
    if action == "remove_parent":
        c = classes_by_label.get(str(act.get("label") or "").strip())
        parent = str(act.get("parent") or "").strip()
        if c is None or not parent or parent not in (c.get("parent_classes") or []):
            return None
        c["parent_classes"] = [p for p in c["parent_classes"] if p != parent]
        return f"断开「{c.get('label')}」→「{parent}」的 is-a"
    if action == "remove_disjoint":
        a = str(act.get("a") or "").strip()
        b = str(act.get("b") or "").strip()
        removed = 0
        for lbl, peer in ((a, b), (b, a)):
            c = classes_by_label.get(lbl)
            if c is not None and peer in (c.get("disjoint_with") or []):
                c["disjoint_with"].remove(peer)
                if not c["disjoint_with"]:
                    c.pop("disjoint_with", None)
                removed += 1
        return f"撤销互斥「{a}⊥{b}」" if removed else None
    if action == "remove_cardinality":
        op = next((o for o in schema.get("object_properties") or []
                   if isinstance(o, dict) and str(o.get("label") or "") == str(act.get("predicate") or "")), None)
        if op is None:
            return None
        restrs = op.get("cardinality_restrictions") or []
        bad = [i for i, r in enumerate(restrs)
               if (r or {}).get("min") is not None and (r or {}).get("max") is not None
               and r["min"] > r["max"]]
        if not bad:
            return None
        for i in reversed(bad):
            restrs.pop(i)
        return f"撤销「{op.get('label')}」的 min>max 基数"
    return None


def _materialize_remaining_errors(db: Session, project_id: int,
                                  issues: list[dict]) -> None:
    """修复轮次耗尽仍有硬伤 → critical 审核项（发布门禁拦下；同规则 pending 去重）。"""
    from sqlalchemy.orm.attributes import flag_modified  # noqa: F401

    from app.infrastructure.database import ReviewItem

    existing = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "conflict_axiom",
        ReviewItem.status == "pending").all()
    have = {(r.payload or {}).get("rule") for r in existing}
    for it in issues:
        if it["rule"] in have:
            continue
        db.add(ReviewItem(
            project_id=project_id, item_type="conflict_axiom",
            payload={"rule": it["rule"], "message": it["message"],
                     "classes": it.get("classes") or [], "source": "pipeline_selfcheck"},
            reason=f"自检未修掉的硬伤：{it['message'][:200]}",
            priority="critical", status="pending"))


def run_selfcheck_repair(project_id: int, db: Optional[Session] = None,
                         rounds: int = 2, model_config_id: Optional[int] = None,
                         progress_cb: Optional[Callable[[str, float], None]] = None,
                         cancel_fn: Optional[Callable[[], bool]] = None) -> dict:
    """自检修复环主入口：机检 → LLM 最小修复 → 重跑，直至无硬伤或轮次耗尽。"""
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        from sqlalchemy.orm.attributes import flag_modified

        from app.adapters.schema_checks import run_schema_checks
        from app.infrastructure.database import Project

        _prog = progress_cb or (lambda m, f: None)

        def _cancelled() -> bool:
            try:
                return bool(cancel_fn and cancel_fn())
            except Exception:  # noqa: BLE001
                return False

        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj is None:
            return {"rounds": 0, "fixed": 0, "remaining": 0, "skipped": "project_not_found"}
        rounds = max(1, min(int(rounds or 1), MAX_ROUNDS_CAP))

        def _schema() -> dict:
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            s = gd.get("schema")
            return s if isinstance(s, dict) else {}

        report = run_schema_checks(_schema())
        errors = [i.model_dump() for i in report.issues if i.severity == "error"]
        if not errors:
            return {"rounds": 0, "fixed": 0, "remaining": 0}
        llm = _build_llm(db, project_id, model_config_id)

        fixed_total = 0
        used_rounds = 0
        for r in range(rounds):
            if _cancelled():
                break
            used_rounds += 1
            _prog(f"第 {r + 1} 轮修复中（{len(errors)} 处硬伤）…", r / max(rounds, 1))
            actions: list[dict] = []
            if llm is not None:
                try:
                    resp = llm.call_llm(system_prompt=SYSTEM_PROMPT,
                                        user_prompt=_repair_material(_schema(), errors),
                                        json_schema=_REPAIR_JSON_SCHEMA)
                    actions = (resp or {}).get("actions") or [] \
                        if isinstance(resp, dict) else []
                except Exception:  # noqa: BLE001 —— LLM 失败本轮跳过修复只重检
                    actions = []
            if not actions:
                # 无动作可施（LLM 缺席/拒答）：重检一次确认状态，防死循环
                report = run_schema_checks(_schema())
                errors = [i.model_dump() for i in report.issues if i.severity == "error"]
                if not errors:
                    break
                continue
            schema = _schema()
            applied = 0
            for act in actions[:20]:  # 单轮动作上限：最小干预
                if _cancelled():
                    break
                desc = _apply_action(schema, act)
                if desc:
                    applied += 1
            if applied:
                fixed_total += applied
                gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
                gd["schema"] = schema
                proj.graph_data = gd
                flag_modified(proj, "graph_data")
                db.commit()
            report = run_schema_checks(_schema())
            errors = [i.model_dump() for i in report.issues if i.severity == "error"]
            if not errors:
                break

        if errors:
            _materialize_remaining_errors(db, project_id, errors)
            db.commit()
        return {"rounds": used_rounds, "fixed": fixed_total, "remaining": len(errors),
                "errors": errors[:10]}
    finally:
        if own:
            db.close()


__all__ = ["run_selfcheck_repair", "REPAIR_ACTIONS", "MAX_ROUNDS_CAP"]
