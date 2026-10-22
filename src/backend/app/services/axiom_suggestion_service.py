# app/services/axiom_suggestion_service.py - 公理建议编排（O2-B8 + O4-B18 双通道）
# adapters 层纯函数产出候选（统计通道 axiom_suggestions / LLM 通道 axiom_llm）
# → 本服务层物化 review_items(item_type='axiom_suggestion')；
# 批准落地在 review_service._approve_axiom_suggestion（写回画布公理），
# 写入后由 B6 SHACL / B2 schema_checks 复查（既有机检在下次检测/发布时自动覆盖）。
# channel：statistical（仅统计）/ llm（仅 LLM）/ all（双通道，默认，向后兼容）。
from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.adapters.axiom_suggestions import suggest_axioms
from app.infrastructure.database import Entity, Project, Relation, ReviewItem

MAX_REVIEW_ITEMS = 50  # 单次物化上限（与其余审核物化同口径）
CHANNELS = ("statistical", "llm", "all")

# LLM 通道单次调用总超时（流式全程）。慢模型（如 glm-5-3-flash 单次 5-10 分钟）
# 曾被默认 300s 掐断导致通道恒空，放宽到 600s 对齐系统其他 LLM 调用；
# 环境变量 AXIOM_LLM_TIMEOUT 可覆盖。超时不重试（llm_client 对 TimeoutError 直接上抛）。
AXIOM_LLM_TIMEOUT = float(os.environ.get("AXIOM_LLM_TIMEOUT", "600"))


def _suggestion_key(cand: dict) -> tuple:
    """候选去重键：kind + 归一 targets；subclass_of 保序（[子类, 父类] 顺序即语义）。"""
    kind = cand.get("kind")
    if kind == "subclass_of":
        target = tuple(str(x) for x in (cand.get("classes") or []))
    else:
        target = tuple(sorted(cand.get("predicates") or cand.get("classes") or [])) or \
            (cand.get("predicate"),)
    return (kind, target)


def _pending_suggestion_key(item) -> tuple:
    return _suggestion_key(item.payload or {})


def _merge_evidence(base_ev: dict | None, llm_ev: dict | None) -> dict:
    """证据并列：统计字段保留，LLM 侧 llm_reason / examples 并入。"""
    ev = dict(base_ev or {})
    lev = llm_ev or {}
    if lev.get("llm_reason"):
        ev["llm_reason"] = lev["llm_reason"]
    if lev.get("examples"):
        ev["llm_examples"] = lev["examples"]
    return ev


def _merge_dual_channel(statistical: list[dict],
                        llm: list[dict]) -> tuple[list[dict], int]:
    """O4-B20 双通道同目标合并：source=both，证据两侧并列。

    返回 (合并后候选列表[统计 → both → LLM], both 合并数)。
    """
    stat_by_key: dict[tuple, dict] = {}
    stat_order: list[tuple] = []
    for c in statistical:
        k = _suggestion_key(c)
        if k not in stat_by_key:
            stat_by_key[k] = c
            stat_order.append(k)
    merged: list[dict] = []
    llm_rest: list[dict] = []
    for c in llm:
        k = _suggestion_key(c)
        base = stat_by_key.pop(k, None)
        if base is None:
            llm_rest.append(c)
            continue
        stat_order.remove(k)
        out = dict(base)
        out["source"] = "both"
        out["evidence"] = _merge_evidence(base.get("evidence"), c.get("evidence"))
        merged.append(out)
    return ([stat_by_key[k] for k in stat_order] + merged + llm_rest, len(merged))


def _upgrade_pending(item, cand: dict) -> bool:
    """跨 run 合并：既有待审项与互补通道候选同目标 → 升级 source=both 并列证据。

    返回是否发生升级；幂等（已 both 不再动）。
    """
    payload = item.payload or {}
    src, new_src = payload.get("source"), cand.get("source")
    if src == "both" or new_src == "both" or src == new_src or src not in ("statistical", "llm"):
        return False
    payload["source"] = "both"
    payload["evidence"] = _merge_evidence(payload.get("evidence"),
                                          cand.get("evidence"))
    item.payload = payload
    if hasattr(item, "_sa_instance_state"):  # 测试桩（SimpleNamespace）无此状态
        flag_modified(item, "payload")
    return True


def run_axiom_suggestions(db: Session, project_id: int,
                          channel: str = "all",
                          llm: Optional[Any] = None,
                          min_instances_note: str = "",
                          progress_cb: Optional[Any] = None,
                          is_cancelled_fn: Optional[Any] = None) -> dict:
    """公理候选扫描并物化审核项（同 kind+target 待审去重）。

    channel='llm' 或 'all' 时走 LLM 通道；llm 参数供测试注入 FakeLLM，
    为 None 时按项目抽取模型配置构造（无配置则 LLM 通道优雅跳过）。
    progress_cb(pct, msg)：任务框架的进度回调（S12 异步化）；is_cancelled_fn()：
    取消探测（阶段边界生效——LLM 单次调用本身不可中断，取消后不物化任何审核项）。
    """
    if channel not in CHANNELS:
        return {"candidates": 0, "review_created": 0, "deduped": 0,
                "skipped": f"未知 channel: {channel}"}
    _prog = progress_cb or (lambda pct, msg: None)
    _cancelled = is_cancelled_fn or (lambda: False)

    ents = db.query(Entity).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status != "merged",
    ).all()
    rels = db.query(Relation).filter(Relation.project_id == project_id).all()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"candidates": 0, "review_created": 0, "deduped": 0, "skipped": "project_not_found"}
    schema = (proj.graph_data or {}).get("schema") \
        if isinstance(proj.graph_data, dict) else None

    rel_dicts = [{"subject_id": r.subject_id, "predicate": r.predicate,
                  "object_id": r.object_id,
                  "subject_label": "", "object_label": ""}
                 for r in rels]
    # 客体/主体标签富化（suggest_inverse_of 用 label 对）
    index = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == project_id).all()}
    for r in rel_dicts:
        r["subject_label"] = (index.get(r["subject_id"]) or {}).label or ""
        r["object_label"] = (index.get(r["object_id"]) or {}).label or ""

    # ORM 行 → 归一 dict（build_materials / run_gates 均按 dict 形状消费）
    from app.adapters.conflicts import _as_ent

    ent_dicts = [_as_ent(e) for e in ents]

    stat_candidates: list[dict] = []
    if channel in ("statistical", "all"):
        _prog(20, "统计通道推断中…")
        stat_candidates = suggest_axioms(ent_dicts, rel_dicts, schema)

    llm_candidates: list[dict] = []
    llm_stats: dict | None = None
    if channel in ("llm", "all"):
        _prog(35, f"统计通道完成 {len(stat_candidates)} 条候选；AI 通道生成中"
                  "（单次调用约 3-10 分钟，可取消）…")
        if _cancelled():
            return {"candidates": 0, "review_created": 0, "deduped": 0,
                    "cancelled": True}
        llm_candidates, llm_stats = _llm_candidates(db, project_id, schema,
                                                    ent_dicts, rel_dicts, llm)
        _prog(70, f"AI 通道返回 {len(llm_candidates)} 条候选；合并与机检守门中…")
        if _cancelled():
            return {"candidates": 0, "review_created": 0, "deduped": 0,
                    "cancelled": True}

    # O4-B20 双通道合并（channel=statistical 时 llm 为空表，合并退化为透传）
    candidates, merged_both = _merge_dual_channel(stat_candidates, llm_candidates)

    existing = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "axiom_suggestion",
        ReviewItem.status.in_(("pending", "claimed")),
    ).all()
    existing_by_key: dict[tuple, ReviewItem] = {}
    for r in existing:
        existing_by_key.setdefault(_pending_suggestion_key(r), r)

    created = deduped = upgraded = 0
    seen_in_run: set[tuple] = set()
    _prog(85, f"候选合并完成 {len(candidates)} 条；物化审核项中…")
    if _cancelled():
        return {"candidates": len(candidates), "review_created": 0,
                "deduped": 0, "cancelled": True}
    for cand in candidates:
        key = _suggestion_key(cand)
        item = existing_by_key.get(key)
        if item is not None:
            if _upgrade_pending(item, cand):
                upgraded += 1
            deduped += 1
            continue
        if key in seen_in_run or created >= MAX_REVIEW_ITEMS:
            deduped += 1
            continue
        seen_in_run.add(key)
        db.add(ReviewItem(
            project_id=project_id,
            item_type="axiom_suggestion",
            payload=cand,
            reason=cand.get("summary") or f"公理建议（{cand.get('kind')}）",
            priority="medium",
            status="pending",
            suggested_action={"action": "declare_axiom", "kind": cand.get("kind")},
        ))
        created += 1
    db.commit()
    result = {"candidates": len(candidates), "review_created": created,
              "deduped": deduped, "merged_both": merged_both,
              "upgraded_pending": upgraded,
              "entities_scanned": len(ents),
              "relations_scanned": len(rels)}
    if llm_stats is not None:
        result["llm"] = llm_stats
    return result


def _llm_candidates(db: Session, project_id: int, schema: dict | None,
                    ents: list, rel_dicts: list[dict],
                    llm: Optional[Any]) -> tuple[list[dict], dict]:
    """LLM 通道：素材组装 → call_llm(json_schema) → 解析为统一结构候选。"""
    from app.adapters.axiom_llm import (
        AXIOM_OUTPUT_JSON_SCHEMA,
        build_materials,
        build_prompt,
        parse_axiom_suggestions,
    )

    if llm is None:
        llm = _build_llm(db, project_id)
    if llm is None:
        return [], {"skipped": "llm_unavailable"}

    materials = build_materials(schema, ents, rel_dicts)
    system_prompt, user_prompt = build_prompt(materials)
    try:
        resp = llm.call_llm(system_prompt=system_prompt, user_prompt=user_prompt,
                            json_schema=AXIOM_OUTPUT_JSON_SCHEMA,
                            timeout=AXIOM_LLM_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - LLM 失败不阻断统计通道
        return [], {"skipped": f"llm_call_failed (timeout={AXIOM_LLM_TIMEOUT}s): {e}"}

    # LLMClient.call_llm 实际返回「修复后的解析对象」（如 {"suggestions":[...]}），
    # 从不返回 {"content": ...}；兼容测试桩的 content 形状。
    if isinstance(resp, dict) and "suggestions" in resp:
        content: Any = resp
    else:
        content = (resp or {}).get("content") or ""
    candidates, rejected = parse_axiom_suggestions(content, schema)

    # O4-B19 机检守门：存在性 → 已声明冲突 → 影子 SHACL → 高风险 owlrl
    from app.adapters.axiom_gates import run_gates

    candidates, gate_stats = run_gates(candidates, schema, ents, rel_dicts)
    return candidates, {"parsed": len(candidates), "parse_rejected": len(rejected),
                        "gates": gate_stats,
                        "rejected_reasons": (gate_stats.get("rejected_reasons") or [])
                        + rejected[:5]}


def _build_llm(db: Session, project_id: int):
    """按项目抽取模型配置构造 LLMClient；未配置返回 None（通道优雅跳过）。

    默认位解析失败时兜底取任意 enabled 的 extract 配置（AI 扫描不强依赖
    is_default 标记——抽取主链路靠 config_id 显式选择，默认位常为空）。
    """
    try:
        from app.adapters.provider import build_legacy_llm_config
        from app.infrastructure.llm_client import LLMClient

        cfg = build_legacy_llm_config(db, project_id)
        return LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                         model=cfg.get("model"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.adapters.provider import decrypt_str
        from app.infrastructure.database import ModelConfig
        from app.infrastructure.llm_client import LLMClient

        q = db.query(ModelConfig).filter(ModelConfig.purpose == "extract",
                                         ModelConfig.enabled.is_(True))
        row = None
        if project_id is not None:
            row = q.filter(ModelConfig.scope == "project",
                           ModelConfig.project_id == project_id).first()
        if row is None:
            row = q.filter(ModelConfig.scope == "global")\
                .order_by(ModelConfig.updated_at.desc()).first()
        if row is None:
            return None
        api_key = decrypt_str(bytes(row.api_key_encrypted)) if row.api_key_encrypted else ""
        return LLMClient(api_key=api_key, base_url=row.base_url,
                         model=row.model_name)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["run_axiom_suggestions"]
