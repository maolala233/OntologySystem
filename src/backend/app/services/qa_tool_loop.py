# app/services/qa_tool_loop.py - QA 工具规划（2026-11-19）
# 目的：清单/计数类问题不再靠“上下文里可见的前 N 条”猜全集。QA 先做一次
# 确定性预取（问题里有接口编码时直接 query_facts），预取不完整时让模型通过
# JSON tool-loop 追加查询；工具结果带 total/next_offset，最终答案必须说明总数。
#
# 只接一个只读工具 query_facts；最多 2 轮（1 次工具 + 1 次收敛），避免慢模型
# 在问答链路上多烧轮次。
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.database import Entity
from app.services.fact_query import query_facts
from app.services.tool_loop import iter_tool_loop

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,5}\d{4,}[A-Za-z]?)(?![A-Za-z0-9])")
_LIST_MARKERS = re.compile(
    r"(哪些|哪个|多少|几个|几项|清单|列表|列出|字段|输入项|输出项|成员|"
    r"包含|包括|所有|全部|枚举|统计|数量|有哪些)"
)

_QUERY_FACTS_PARAMS = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["entities", "relations"],
                 "default": "relations"},
        "entity_label": {"type": "string",
                         "description": "主体或宾语命中该标签（双向，最常用）"},
        "class_label": {"type": "string", "description": "实体类过滤（entities）"},
        "label_contains": {"type": "string", "description": "实体标签子串（entities）"},
        "predicate": {"type": "string", "description": "谓词精确过滤（relations）"},
        "subject_label": {"type": "string", "description": "主体标签过滤（relations）"},
        "object_label": {"type": "string", "description": "宾语标签过滤（relations）"},
        "include_structural": {"type": "boolean", "default": False},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
    },
}


def should_use_tools(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    return bool(_LIST_MARKERS.search(q) or _CODE_RE.search(q))


def _prefetch_args(question: str) -> dict | None:
    """明确编码的清单题先确定性预取，避免模型漏调工具。"""
    m = _CODE_RE.search(question or "")
    if not m:
        return None
    return {"kind": "relations", "entity_label": m.group(1),
            "limit": 200, "offset": 0}


def _planner_system() -> str:
    return (
        "你是问答检索规划器。用户的问题可能需要精确的清单/计数，而现有检索上下文"
        "可能只包含前几条。你有一个只读工具 query_facts，可以按实体、类、谓词过滤，"
        "返回 total（全集计数）、returned、has_more、next_offset、items。\n"
        "规则：\n"
        "1. 已有预取结果且 total == returned 时，不要重复调用，直接 final。\n"
        "2. 预取缺失、total > returned 或用户问题有更精确的过滤条件时，调用一次 "
        "query_facts 补全；args 用 entity_label/class_label/predicate 等字段。\n"
        "3. 不要编造工具结果；工具报错就 final，让最终回答说明缺口。\n"
        "4. 字段/关系类问题（“某接口/某实体有哪些字段/输入项”）→ kind=relations + "
        "entity_label=接口编码或实体名。\n"
        "5. 类清单/计数类问题（“某类有多少个/有哪些”）→ kind=entities + "
        "class_label=类名；不要把类名填到 predicate。\n"
        "6. 最后只输出 JSON：{\"action\":\"call\",\"tool\":\"query_facts\",\"args\":{...}} "
        "或 {\"action\":\"final\"}。"
    )


def _normalize_qa_args(args: dict | None) -> dict:
    """QA 口径纠偏：类清单只数实例，绝不能把类节点自身算进去。"""
    safe = dict(args or {})
    if str(safe.get("kind") or "relations").lower() in (
            "entity", "entities", "实体"):
        safe["include_classes"] = False
    return safe


def run_qa_tool_loop(db: Session, project_id: int, question: str, llm,
                     max_rounds: int = 2) -> dict:
    """返回 {observations, calls, prefetch_complete}；失败不阻断问答主链路。"""
    observations: list[dict] = []
    calls: list[dict] = []
    prefetch = _prefetch_args(question)
    prefetch_complete = False

    def _exec(args: dict) -> dict:
        try:
            # QA 问“某类有多少个”指的是实例数，不能把类节点自己也算进去。
            # 规划器偶尔会幻觉出 include_classes=true（151 实例 + 1 个类 = 152），
            # 这里强制关掉，保证问答口径与图谱实例统计一致。
            safe = _normalize_qa_args(args)
            return query_facts(db, project_id, safe)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qa-tool] query_facts 失败 args={args}: {e}")
            return {"error": str(e)[:300]}

    if prefetch:
        res = _exec(prefetch)
        observations.append({"tool": "query_facts", "args": prefetch, "result": res})
        total = int(res.get("total") or 0)
        returned = int(res.get("returned") or 0)
        prefetch_complete = total > 0 and total == returned

    if prefetch_complete:
        return {"observations": observations, "calls": calls,
                "prefetch_complete": True}

    tools = {
        "query_facts": {
            "description": "确定性清单/计数查询（分页）：返回 total/returned/"
                           "has_more/next_offset/items；清单题用它取全量或补页。",
            "parameters": _QUERY_FACTS_PARAMS,
            "fn": _exec,
        }
    }
    user_prompt = (
        f"用户问题：{question}\n\n"
        + (f"已预取 query_facts({json.dumps(prefetch, ensure_ascii=False)})："
           f"total={observations[0]['result'].get('total')}, "
           f"returned={observations[0]['result'].get('returned')}。\n"
           if observations else "尚无预取结果。\n")
        + "请判断是否需要调用 query_facts 补全清单/计数；不需要就 final。"
    )
    try:
        gen = iter_tool_loop(llm, _planner_system(), user_prompt, tools,
                             max_rounds=max_rounds)
        for kind, payload in gen:
            if kind != "tool":
                continue
            calls.append(payload)
            if payload.get("phase") == "end":
                obs = payload.get("observation") or {}
                # 预取已经覆盖同一调用时不重复注入
                if obs and obs != (observations[0]["result"] if observations else None):
                    observations.append({"tool": payload.get("tool"),
                                         "args": _normalize_qa_args(payload.get("args") or {}),
                                         "result": obs})
    except Exception as e:  # noqa: BLE001 —— 规划失败只保留预取结果
        logger.warning(f"[qa-tool] 规划器失败，保留预取结果：{e}")

    # 规划器可能把类名误填成 predicate（实测“前端接口有多少个”→ predicate=前端接口，
    # total=0）。这里做一次确定性纠偏：问题里逐字命中的类名 → entities/class_label。
    if not any(int((o.get("result") or {}).get("total") or 0) > 0
               for o in observations):
        cls = _likely_class_label(db, project_id, question)
        if cls:
            args = {"kind": "entities", "class_label": cls, "limit": 200, "offset": 0}
            res = _exec(args)
            observations.append({"tool": "query_facts", "args": args, "result": res})
    return {"observations": observations, "calls": calls,
            "prefetch_complete": False}


def _likely_class_label(db: Session, project_id: int, question: str) -> str | None:
    """问题逐字包含的类名（最长匹配）；用于纠正模型的 class/predicate 误用。"""
    q = question or ""
    if not q:
        return None
    rows = (db.query(Entity.label, Entity.aliases)
            .filter(Entity.project_id == project_id,
                    Entity.is_class_node.is_(True),
                    Entity.status != "rejected")
            .all())
    best = ""
    for label, aliases in rows:
        names = [str(label or "")] + [str(a or "") for a in (aliases or [])]
        for name in names:
            if len(name) >= 2 and name in q and len(name) > len(best):
                best = name
    return best or None


__all__ = ["should_use_tools", "run_qa_tool_loop"]
