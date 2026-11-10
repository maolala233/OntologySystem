# app/services/auto_adjudication.py - AI 自动裁决层（新手简化管线，2026-11）
# 规则表驱动：低风险审核项由系统直接 approve 落地（复用 review_service.decide_item
# 单一执行入口，全部留痕 audit_logs + result_ref.auto_rule 标记，前端据「AI 已自动处理」区
# 按 decided_by IS NULL + result_ref.auto_rule 识别）。
# 人只审 critical/high（与发布门禁 PUBLISH_BLOCKING_PRIORITIES 对齐）。
#
# 档位 AUTO_ADJUDICATION_LEVEL（环境变量）：
#   off          — 关闭（全部人审）
#   conservative — 稳妥档：仅规则自动裁决（entity_merge(sim≥0.85) / conflict_value /
#                  conflict_relationship / low_confidence_* / missing_evidence（low|medium）/
#                  axiom_suggestion（low|medium 过闸候选，逐条申报+一致性复验撤回））
#   llm          — 默认档：规则裁决之后，**剩余全部待审项由 LLM 逐条判断并直接落盘**
#                  （approve/reject/并入已有类/选定正确类别），全部留痕可撤销——
#                  用户不需要懂审核，构建完成即终态（2026-11-04 用户要求）
#
# 公理安全阀：每申报一条即重跑纯 TBox 可满足性检测（find_unsatisfiable_classes，确定性
# 多项式）；出现基线外的新不可满足类 → 自动 remove_axiom 撤回肇事公理，并物化
# critical 级 conflict_axiom 审核项说明原因——自动执行永远不会把本体推入不可满足状态。
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Optional

from sqlalchemy.orm import Session

AUTO_LEVEL_ENV = "AUTO_ADJUDICATION_LEVEL"
LEVELS = ("off", "conservative", "llm", "fast")
DEFAULT_LEVEL = "llm"

# entity_merge 自动合并阈值：对齐消解自动合并闸 AUTO_MERGE_THRESHOLD（0.85）。
# 原用 0.75（人审批量闸）——但 bge-m3 对共享后缀的无关短名实测常落 0.75-0.85，
# 自动执行必须用更严的 0.85（项目 245 实测：系统名簇 0.8288 曾险些自动合并）
AUTO_MERGE_MIN_SIMILARITY = 0.85

# result_ref 内的自动裁决标记键（前端「AI 已自动处理」区识别依据）
AUTO_RULE_KEY = "auto_rule"
AUTO_ACTOR_NAME = "system:auto"


class _SystemActor:
    """系统执行者：decide_item 只消费 user.id，None 合法（review_items.decided_by 可空），
    前端以 result_ref.auto_rule 区分「AI 自动处理」与人工裁决。"""

    id = None
    username = AUTO_ACTOR_NAME


SYSTEM_ACTOR = _SystemActor()


def resolve_level(explicit: Optional[str] = None) -> str:
    lvl = (explicit or os.environ.get(AUTO_LEVEL_ENV) or DEFAULT_LEVEL).strip().lower()
    return lvl if lvl in LEVELS else DEFAULT_LEVEL


# ── 规则表：item_type → 规则函数 ──
# 规则函数签名 f(item) -> ("approve", edited_payload|None, note) | None（None=留给人审）

def _rule_entity_merge(item):
    """消解聚类待审项：similarity ≥ 0.75 自动执行合并。

    payload 形如 {"cluster": {canonical_id, member_ids, similarity, ...}}；
    approve 落地需要 payload 顶层有 canonical_id/duplicate_ids（_approve_entity_merge
    消费），故经 edited_payload 显式补上——顺带修复人工批量通过同型项只留痕不落地的缺陷。
    """
    payload = item.payload or {}
    cluster = payload.get("cluster") or {}
    sim = float(payload.get("similarity") or cluster.get("similarity") or 0)
    layer = str(payload.get("layer") or cluster.get("layer") or "")
    # 拼音层证据弱（共享前/后缀即可过阈值，项目 238 实测 60 实例误并）——
    # 自动合并只认归一化同名（L1）与向量层（L3）证据，拼音层聚类留给人审
    if layer == "pinyin":
        return None
    if sim < AUTO_MERGE_MIN_SIMILARITY:
        return None
    canonical = cluster.get("canonical_id") or payload.get("canonical_id")
    members = cluster.get("member_ids") or payload.get("duplicate_ids") or []
    dups = [int(i) for i in members if canonical is not None and int(i) != int(canonical)]
    if canonical is None or not dups:
        return None
    edited = {"canonical_id": int(canonical), "duplicate_ids": dups}
    return ("approve", edited,
            f"AI 自动合并：{layer or '向量'}层相似度 {sim:.2f} ≥ {AUTO_MERGE_MIN_SIMILARITY}，"
            "已按高置信聚类执行（可在「AI 已自动处理」区撤销）")


def _rule_low_priority_close(item):
    """留痕关闭类（conflict_value/relationship、low_confidence_*、missing_evidence）：
    approve 本就不改图谱，low/medium 直接以现状关闭；critical/high 留人。"""
    if item.priority in ("critical", "high"):
        return None
    return ("approve", None, "AI 自动留痕关闭：低风险项，以现状为准（不改动图谱数据）")


def _rule_axiom_suggestion(item):
    """公理建议：候选在物化前已过五道机检闸（axiom_gates.run_gates），
    low/medium 自动申报；申报后一致性复验不通过会自动撤回（见 _adjudicate_axioms）。"""
    if item.priority in ("critical", "high"):
        return None
    return ("approve", None, "AI 自动申报公理：候选已过五道机检闸（可撤销）")


CONSERVATIVE_RULES: dict[str, Callable] = {
    "entity_merge": _rule_entity_merge,
    "conflict_value": _rule_low_priority_close,
    "conflict_relationship": _rule_low_priority_close,
    "low_confidence_entity": _rule_low_priority_close,
    "low_confidence_relation": _rule_low_priority_close,
    "missing_evidence": _rule_low_priority_close,
    "axiom_suggestion": _rule_axiom_suggestion,
}


def _mark_auto(item, rule: str) -> None:
    item.result_ref = {**(item.result_ref or {}), AUTO_RULE_KEY: rule}


# ── 公理自动申报 + 一致性复验撤回 ──

def _expected_axiom_key(kind: str, payload: dict) -> Optional[str]:
    """list_axioms key 口径预计算（cardinality 的序号后缀在申报后补算）。"""
    def _pair(a: str, b: str) -> str:
        return f"{min(a, b)}|{max(a, b)}"

    if kind == "functional":
        p = str(payload.get("predicate") or "").strip()
        return f"functional:{p}" if p else None
    if kind == "inverse_of":
        ps = [str(x or "").strip() for x in (payload.get("predicates") or [])[:2]]
        if len(ps) == 2 and ps[0] and ps[1] and ps[0] != ps[1]:
            return f"inverse_of:{_pair(ps[0], ps[1])}"
        return None
    if kind in ("disjoint_with", "equivalent_classes"):
        cs = [str(x or "").strip() for x in (payload.get("classes") or [])[:2]]
        if len(cs) == 2 and cs[0] and cs[1] and cs[0] != cs[1]:
            return f"{kind}:{_pair(cs[0], cs[1])}"
        return None
    if kind == "subclass_of":
        cs = [str(x or "").strip() for x in (payload.get("classes") or [])[:2]]
        if len(cs) == 2 and cs[0] and cs[1] and cs[0] != cs[1]:
            return f"subclass_of:{cs[0]}|{cs[1]}"  # 有序：子|父
        return None
    return None  # cardinality / domain_fix / range_fix 走申报后定位或快照回滚


def _axiom_undo_plan(db: Session, proj, payload: dict) -> tuple[Callable[[], None], Optional[str]]:
    """构造申报前的撤销闭包 + 可撤销 key（前端撤销按钮用，记入 result_ref.axiom_key）。

    可预计算 key 的种类走 remove_axiom；domain_fix/range_fix（list_axioms 不单列，
    remove_axiom 不支持）捕获旧值还原，返回 key=None（前端不显示撤销按钮）；
    cardinality 申报后按 restrictions 尾序号补 key。"""
    kind = str(payload.get("kind") or "")
    pre_key = _expected_axiom_key(kind, payload)
    old_domain = old_range = None
    card_label = str(payload.get("predicate") or "").strip()
    card_len_before = None
    if kind in ("domain_fix", "range_fix"):
        schema = (proj.graph_data or {}).get("schema") or {}
        op_ = next((o for o in schema.get("object_properties") or []
                    if isinstance(o, dict) and str(o.get("label") or "") == card_label), None)
        old_domain, old_range = (op_ or {}).get("domain"), (op_ or {}).get("range")
    if kind == "cardinality":
        schema = (proj.graph_data or {}).get("schema") or {}
        op_ = next((o for o in schema.get("object_properties") or []
                    if isinstance(o, dict) and str(o.get("label") or "") == card_label), None)
        card_len_before = len((op_ or {}).get("cardinality_restrictions") or [])

    def _undo() -> None:
        if kind in ("domain_fix", "range_fix"):
            from sqlalchemy.orm.attributes import flag_modified

            schema = (proj.graph_data or {}).get("schema") or {}
            op_ = next((o for o in schema.get("object_properties") or []
                        if isinstance(o, dict) and str(o.get("label") or "") == card_label), None)
            if op_ is not None:
                if kind == "domain_fix":
                    if old_domain is None:
                        op_.pop("domain", None)
                    else:
                        op_["domain"] = old_domain
                else:
                    if old_range is None:
                        op_.pop("range", None)
                    else:
                        op_["range"] = old_range
                proj.graph_data = {**(proj.graph_data or {}), "schema": schema}
                flag_modified(proj, "graph_data")
                db.flush()
            return
        key = pre_key
        if key is None and kind == "cardinality" and card_len_before is not None:
            key = f"cardinality:{card_label}#{card_len_before}"
        if key:
            from app.services.axiom_store import remove_axiom

            remove_axiom(db, proj, key)

    return _undo, (pre_key if pre_key else
                   (f"cardinality:{card_label}#{card_len_before}"
                    if kind == "cardinality" and card_len_before is not None else None))


def _adjudicate_axioms(db: Session, items: list, level: str,
                     budget_left_fn=None) -> dict:
    """公理建议逐条申报 + 双重复验：TBox 新不可满足类 / 数据反证矛盾 → 撤回并物化 critical 审核项。

    baseline 先取申报前的不可满足类名集合与数据矛盾键集合——项目本来就有
    矛盾时，自动申报只要不新增照常落地（不替既有问题买单，也不被既有问题卡死）。
    数据反证覆盖 owlrl 静默场景：函数性多值不产生 owl:Nothing，而是被 prp-fp
    推成 sameAs 错误合并（2026-11-04 项目 246 事故）。
    """
    from app.adapters.axiom_gates import find_data_contradictions
    from app.adapters.schema_checks import find_unsatisfiable_classes
    from app.infrastructure.database import Entity, Project, Relation, ReviewItem

    if not items:
        return {"approved": 0, "rolled_back": 0}
    proj = db.query(Project).filter(Project.id == items[0].project_id).first()
    if proj is None:
        return {"approved": 0, "rolled_back": 0, "skipped": "project_not_found"}

    def _schema() -> dict:
        gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
        return gd.get("schema") if isinstance(gd.get("schema"), dict) else {}

    def _ents_rels() -> tuple[list, list]:
        ents = db.query(Entity).filter(Entity.project_id == proj.id).all()
        rels = db.query(Relation).filter(Relation.project_id == proj.id).all()
        return ents, rels

    baseline = {u["class"] for u in find_unsatisfiable_classes(_schema())}
    base_contra_keys = {c["key"] for c in find_data_contradictions(_schema(), *_ents_rels())}
    approved = rolled_back = 0
    for item in items:
        if budget_left_fn is not None and budget_left_fn() <= 0:
            break  # 阶段预算耗尽：剩余公理项留待下次（已申报的照常收尾）
        payload = item.payload or {}
        rule = _rule_axiom_suggestion(item)
        if rule is None:
            continue
        _, _edited, note = rule
        undo, axiom_key = _axiom_undo_plan(db, proj, payload)
        try:
            decide = _decide_item_fn()
            decide(item, SYSTEM_ACTOR, "approve", note=note, db=db)
        except Exception:  # noqa: BLE001 —— 单条申报失败不阻断其余
            db.rollback()
            continue
        _mark_auto(item, "axiom_auto_declare")
        if axiom_key:
            item.result_ref = {**(item.result_ref or {}), "axiom_key": axiom_key}
        unsat_now = find_unsatisfiable_classes(_schema())
        new_unsat = [u for u in unsat_now if u["class"] not in baseline]
        new_contra = [c for c in find_data_contradictions(_schema(), *_ents_rels())
                      if c["key"] not in base_contra_keys]
        if new_unsat or new_contra:
            try:
                undo()
                db.commit()
            except Exception:  # noqa: BLE001 —— 撤回失败也要留下 critical 项让人介入
                db.rollback()
            rolled_back += 1
            if new_unsat:
                witness = "、".join(f"「{u['class']}」" for u in new_unsat[:3])
                reason_txt = f"引发不可满足类 {witness}"
            else:
                reason_txt = new_contra[0]["message"]
            item.result_ref = {**(item.result_ref or {}),
                               AUTO_RULE_KEY: "axiom_auto_declare",
                               "rolled_back": True,
                               "rollback_reason": reason_txt}
            db.add(ReviewItem(
                project_id=item.project_id,
                item_type="conflict_axiom",
                payload={"kind": payload.get("kind"), "axiom": payload,
                         "unsatisfiable": new_unsat,
                         "data_contradiction": (new_contra[0] if new_contra else None),
                         "auto_retracted": True,
                         "source_review_id": item.id},
                reason=(f"AI 自动申报的公理「{item.reason or payload.get('kind')}」"
                        f"{reason_txt}，已自动撤回，请人工判断是否保留"),
                priority="critical", status="pending"))
            db.commit()
        else:
            db.commit()
            approved += 1
    return {"approved": approved, "rolled_back": rolled_back}


def _decide_item_fn():
    from app.services.review_service import decide_item

    return decide_item


# ── LLM 裁决器（level=llm 默认档）：剩余待审项由 LLM 判断并直接落盘 ──

ADJUDICATOR_MAX_ITEMS = 40       # 单次 LLM 裁决条数上限（超出下轮/下次再裁）
ADJUDICATOR_TIMEOUT = 180.0      # 单次调用上限：复核 prompt 小（1-8k 字符），正常 5-60s 应回；600s×串行十几次会让"结果确认"小时级（2026-11-09 实测）
ADJUDICATE_WALL_BUDGET = 600.0   # 整阶段墙钟预算：超时停止复核，剩余项留待下次构建

_ADJUDICATOR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "待审核项 id"},
                    "action": {"type": "string", "enum": ["approve", "reject"]},
                    "reason": {"type": "string", "description": "一句话裁决理由"},
                    "merge_into_class": {"type": "string"},
                    "merge_into_predicate": {"type": "string"},
                    "chosen_class": {"type": "string"},
                },
                "required": ["id", "action"],
            },
        },
    },
    "required": ["verdicts"],
}

ADJUDICATOR_SYSTEM_PROMPT = (
    "你是本体质量审核员，代替不熟悉本体建模的用户做裁决。给定本体类/关系清单与一批待审核项，"
    "逐条给出裁决。判定原则：\n"
    "1. 实体合并（entity_merge）：成员称呼确实指向同一个事物才 approve；只是同类事物或名字相似"
    "（如不同的系统、不同的人）必须 reject。\n"
    "2. 新类型/新关系（new_class/new_predicate）：证据充分且不是已有类的同义概念才 approve；"
    "若有 candidates 且第一候选确属同一概念，填 merge_into_class/merge_into_predicate 并入；"
    "含糊或编造 → reject。\n"
    "3. 类型冲突（conflict_type）：按证据从候选中选定正确类别填 chosen_class；判断不了 → reject。\n"
    "4. 公理违例/逻辑冲突（conflict_axiom）：approve = 以现状留痕关闭（不改数据）；"
    "该违例是真实数据错误且需要保留警示时也 approve；噪音才 reject。\n"
    "5. 低置信/缺证据项：approve = 接受现状留痕；明显是抽取错误才 reject。\n"
    "每条给一句话 reason。只输出 JSON：{\"verdicts\":[...]}。"
)


def _llm_for_adjudication(db: Session, project_id: int):
    """裁决用 LLM（与公理扫描同源；无配置返回 None → 剩余项留人审）。"""
    try:
        from app.adapters.provider import build_legacy_llm_config
        from app.infrastructure.llm_client import LLMClient

        cfg = build_legacy_llm_config(db, project_id)
        # 与公理扫描同源接线 disable_think（glm-5 思考吞输出预算，长 prompt 空响应）
        return LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                         model=cfg.get("model"),
                         think_mode="disabled" if cfg.get("disable_think") else None)
    except Exception:  # noqa: BLE001
        return None


def _slim_item(item, reason_cap: int = 160) -> dict:
    """待审核项 → LLM 素材（轻量；引用标签不引用悬空 id）。"""
    payload = item.payload or {}
    slim: dict[str, Any] = {"id": item.id, "type": item.item_type,
                            "priority": item.priority,
                            "note": (item.reason or "")[:reason_cap]}
    if item.item_type == "entity_merge":
        cluster = payload.get("cluster") or {}
        slim["members"] = (cluster.get("member_labels")
                           or payload.get("duplicate_ids") or [])[:8]
        slim["similarity"] = cluster.get("similarity") or payload.get("similarity")
    elif item.item_type in ("new_class", "new_predicate"):
        slim["label"] = payload.get("label")
        cands = payload.get("candidates") or []
        slim["similar_existing"] = [c.get("label") for c in cands[:3]]
    elif item.item_type == "conflict_type":
        slim["sources"] = [{"entity": s.get("label"), "class": s.get("class")}
                           for s in (payload.get("sources") or [])[:5]]
    elif item.item_type == "conflict_axiom":
        slim["violation"] = (payload.get("message")
                             or "；".join(str(v) for v in payload.get("conflicting_values") or []))[:reason_cap]
    elif item.item_type == "axiom_suggestion":
        slim["axiom"] = {"kind": payload.get("kind"),
                         "summary": (payload.get("summary") or "")[:reason_cap]}
    return slim


def _llm_adjudicate(db: Session, project_id: int, items: list,
                    budget_left_fn=None) -> dict:
    """LLM 逐条裁决并直接落盘（decide_item 同一执行入口，留痕可撤销）。"""
    from app.infrastructure.database import Project

    if not items:
        return {"llm_adjudicated": 0, "llm_approved": 0, "llm_rejected": 0}
    llm = _llm_for_adjudication(db, project_id)
    if llm is None:
        return {"llm_adjudicated": 0, "skipped": "llm_unavailable"}
    proj = db.query(Project).filter(Project.id == project_id).first()
    schema = ((proj.graph_data or {}).get("schema") or {}) if proj else {}
    classes = [str(c.get("label")) for c in (schema.get("classes") or [])
               if isinstance(c, dict) and c.get("label")]
    props = [str(o.get("label")) for o in (schema.get("object_properties") or [])
             if isinstance(o, dict) and o.get("label")]

    decide = _decide_item_fn()
    approved = rejected = failed = 0
    by_type: dict[str, int] = {}
    item_by_id = {i.id: i for i in items}
    for batch_start in range(0, len(items), ADJUDICATOR_MAX_ITEMS):
        if budget_left_fn is not None and budget_left_fn() <= 0:
            break  # 阶段墙钟预算耗尽：剩余项留待下次构建复核
        batch = items[batch_start:batch_start + ADJUDICATOR_MAX_ITEMS]
        user_prompt = ("【本体类清单】" + "、".join(classes[:150]) +
                       "\n【关系清单】" + "、".join(props[:150]) +
                       "\n【待审核项】\n" +
                       json.dumps([_slim_item(i) for i in batch], ensure_ascii=False))
        verdicts = None
        try:
            resp = llm.call_llm(system_prompt=ADJUDICATOR_SYSTEM_PROMPT,
                                user_prompt=user_prompt,
                                json_schema=_ADJUDICATOR_JSON_SCHEMA,
                                max_retries=1, timeout=ADJUDICATOR_TIMEOUT)
            verdicts = resp.get("verdicts") if isinstance(resp, dict) else None
        except Exception:  # noqa: BLE001 —— 走文本通道兜底
            verdicts = None
        if not isinstance(verdicts, list):
            # json_object 降级场景（glm 等不支持 json_schema 的模型）：修复器可能返回
            # None（空回复）或键漂移（模型写 verdict 而非 action）——退文本通道宽容解析
            try:
                from app.adapters.axiom_llm import _coerce_json

                raw = llm.call_llm_text(system_prompt=ADJUDICATOR_SYSTEM_PROMPT,
                                        user_prompt=user_prompt,
                                        max_retries=1, timeout=ADJUDICATOR_TIMEOUT)
                content = raw.get("content") if isinstance(raw, dict) else raw
                data = _coerce_json(content)
                verdicts = data.get("verdicts") if isinstance(data, dict) else None
            except Exception:  # noqa: BLE001
                verdicts = None
        if not isinstance(verdicts, list):
            failed += len(batch)
            continue
        for v in verdicts:
            try:
                item = item_by_id.get(int(v.get("id")))
                if item is None or item.status != "pending":
                    continue
                # 键名容错：json_object 降级下模型常写 verdict/decision 代替 action
                action = str(v.get("action") or v.get("verdict")
                             or v.get("decision") or "").strip()
                if action not in ("approve", "reject"):
                    continue
                edited: dict[str, Any] = {}
                for k in ("merge_into_class", "merge_into_predicate", "chosen_class"):
                    if str(v.get(k) or "").strip():
                        edited[k] = str(v[k]).strip()
                reason = str(v.get("reason") or "").strip()[:200]
                decide(item, SYSTEM_ACTOR, action,
                       edited_payload=edited or None,
                       note=f"LLM 裁决：{reason}", db=db)
                _mark_auto(item, f"llm_review:{item.item_type}")
                item.result_ref = {**(item.result_ref or {}),
                                   "llm_reason": reason}
                db.commit()
                if action == "approve":
                    approved += 1
                else:
                    rejected += 1
                by_type[item.item_type] = by_type.get(item.item_type, 0) + 1
            except Exception:  # noqa: BLE001 —— 单条失败不断链
                db.rollback()
                failed += 1
    return {"llm_adjudicated": approved + rejected, "llm_approved": approved,
            "llm_rejected": rejected, "by_type": by_type,
            **({"failed": failed} if failed else {})}


def run_auto_adjudication(project_id: int, db: Optional[Session] = None,
                          level: Optional[str] = None) -> dict:
    """对项目 pending 审核项跑一遍自动裁决（claimed 不动——可能正被人看着）。

    返回 {"level", "scanned", "auto_approved", "rolled_back", "by_type": {...}}。
    """
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        import time as _time

        _t0 = _time.monotonic()

        def _budget_left() -> float:
            return ADJUDICATE_WALL_BUDGET - (_time.monotonic() - _t0)

        lvl = resolve_level(level)
        if lvl == "off":
            return {"level": lvl, "scanned": 0, "auto_approved": 0, "rolled_back": 0,
                    "skipped": "level_off"}
        from app.infrastructure.database import ReviewItem

        pending = (db.query(ReviewItem)
                   .filter(ReviewItem.project_id == project_id,
                           ReviewItem.status == "pending")
                   .all())
        rules = dict(CONSERVATIVE_RULES)

        # 公理项走申报+复验专用通道；其余按规则表
        axiom_items = [it for it in pending if it.item_type == "axiom_suggestion"]
        axiom_res = _adjudicate_axioms(db, axiom_items, lvl,
                                       budget_left_fn=_budget_left)

        by_type: dict[str, int] = {"axiom_suggestion": axiom_res["approved"]}
        auto_approved = axiom_res["approved"]
        rolled_back = axiom_res["rolled_back"]
        decide = _decide_item_fn()
        for item in pending:
            if item.item_type == "axiom_suggestion":
                continue
            rule = rules.get(item.item_type)
            if rule is None:
                continue
            try:
                verdict = rule(item)
            except Exception:  # noqa: BLE001 —— 规则异常按人审处理
                continue
            if verdict is None:
                continue
            action, edited, note = verdict
            try:
                decide(item, SYSTEM_ACTOR, action, edited_payload=edited,
                       note=note, db=db)
            except Exception:  # noqa: BLE001 —— 状态机/落地失败不阻断其余项
                db.rollback()
                continue
            _mark_auto(item, f"{item.item_type}:auto")
            db.commit()
            by_type[item.item_type] = by_type.get(item.item_type, 0) + 1
            auto_approved += 1
        # fast 档（2026-11-09 用户决策）：模型抽取产物不做 LLM 复核，剩余待审项
        # 直接默认通过——"结果确认"阶段从分钟级缩到秒级。唯一例外：低相似度
        # entity_merge 不默认并（拼音层误并 60 实例事故口径，AUTO_MERGE_MIN_-
        # SIMILARITY 之下的聚类留 pending）。
        fast_approved = 0
        if lvl == "fast":
            decide2 = _decide_item_fn()
            for item in pending:
                if item.status != "pending":
                    continue
                if item.item_type == "entity_merge":
                    payload = item.payload or {}
                    cluster = payload.get("cluster") or {}
                    sim = float(payload.get("similarity") or cluster.get("similarity") or 0)
                    layer = str(payload.get("layer") or cluster.get("layer") or "")
                    if sim < AUTO_MERGE_MIN_SIMILARITY or layer == "pinyin":
                        continue  # 证据不足的合并不默认执行
                try:
                    decide2(item, SYSTEM_ACTOR, "approve",
                            note="模型抽取结果默认通过（fast 档，可撤销）", db=db)
                    _mark_auto(item, f"{item.item_type}:fast")
                    db.commit()
                    fast_approved += 1
                    by_type[item.item_type] = by_type.get(item.item_type, 0) + 1
                except Exception:  # noqa: BLE001 —— 单项失败不阻断
                    db.rollback()
            auto_approved += fast_approved
            out = {"level": lvl, "scanned": len(pending),
                   "auto_approved": auto_approved,
                   "rolled_back": rolled_back, "by_type": by_type,
                   "fast_approved": fast_approved}
            if _budget_left() < 0:
                out["budget_exceeded"] = True
            return out
        # llm 档：规则裁决后剩余的待审项（new_class/new_predicate/conflict_type/
        # 悬置合并簇/公理违例等）由 LLM 逐条判断并直接落盘——用户不再需要懂审核
        llm_res: dict = {}
        if lvl == "llm":
            from app.infrastructure.database import ReviewItem as _RI

            remaining = (db.query(_RI)
                         .filter(_RI.project_id == project_id,
                                 _RI.status == "pending")
                         .all())
            llm_res = _llm_adjudicate(db, project_id, remaining,
                                      budget_left_fn=_budget_left)
            by_type.update(llm_res.pop("by_type", {}) or {})
        out = {"level": lvl, "scanned": len(pending), "auto_approved": auto_approved,
               "rolled_back": rolled_back, "by_type": by_type}
        if llm_res:
            out["llm"] = llm_res
        return out
    finally:
        if own:
            db.close()


__all__ = [
    "AUTO_LEVEL_ENV", "AUTO_MERGE_MIN_SIMILARITY", "AUTO_RULE_KEY", "LEVELS",
    "DEFAULT_LEVEL", "SYSTEM_ACTOR", "resolve_level", "run_auto_adjudication",
]
