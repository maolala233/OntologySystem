# app/services/extraction_repair.py - 抽取定向修复 agent（O3-B15②，docs/design/09 §2）
# 闸门违例率超阈值时，带工具的修复 agent（查违例/查原文/重跑单 chunk）替代"重试 1 次"；
# 只重跑受影响 chunk（违例最多的 ≤max_chunks 个），修复产物标注 _repaired 并回并结果。
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# 违例率 = 违例条数 / (通过 + 违例)；超过该值触发定向修复
REPAIR_VIOLATION_RATE = 0.4


def violation_rate(gate_stats: dict) -> float:
    total = int(gate_stats.get("passed_count") or 0) + int(gate_stats.get("violation_count") or 0)
    if total <= 0:
        return 0.0
    return int(gate_stats.get("violation_count") or 0) / total


def pick_worst_chunks(violations: list[dict], max_chunks: int = 3) -> list[int]:
    """违例最多的 chunk（violations: [{kind,label,detail:{chunk_index}}]）。"""
    counts: dict[int, int] = {}
    for v in violations or []:
        idx = ((v or {}).get("detail") or {}).get("chunk_index")
        if isinstance(idx, int):
            counts[idx] = counts.get(idx, 0) + 1
    return [i for i, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:max_chunks]]


def targeted_repair(chunks: list, violations: list[dict],
                    llm_call: Callable | None = None,
                    reextract_fn: Callable | None = None,
                    max_chunks: int = 3,
                    violation_threshold: float = REPAIR_VIOLATION_RATE,
                    passed_count: int = 0) -> dict:
    """对违例最重的 chunk 做带工具的定向重抽。

    reextract_fn(chunk_idx, chunk_text, feedback) -> list[dict]：单 chunk 重抽（可注入）；
    llm_call 不为 None 时走 tool-loop（工具：get_chunk_text / re_extract），
    否则退化为带违例反馈的直接重抽（确定性、可测试）。
    返回 {"triggered", "violation_rate", "repaired_chunks", "recovered_entities"}。
    """
    rate = violation_rate({"passed_count": passed_count,
                           "violation_count": len(violations or [])})
    out = {"triggered": False, "violation_rate": round(rate, 3),
           "repaired_chunks": [], "recovered_entities": []}
    if rate < violation_threshold or not violations:
        return out
    out["triggered"] = True
    targets = pick_worst_chunks(violations, max_chunks=max_chunks)
    chunk_map: dict[int, str] = {}
    for i, c in enumerate(chunks or []):
        if isinstance(c, tuple):
            chunk_map[i] = str(c[1] if len(c) > 1 else c[0])
        else:
            chunk_map[i] = str(c)
    for idx in targets:
        chunk_text = chunk_map.get(idx, "")
        chunk_violations = [{"kind": v.get("kind"), "label": v.get("label")}
                            for v in (violations or [])
                            if ((v or {}).get("detail") or {}).get("chunk_index") == idx]
        feedback = ("；".join(f"{v['kind']}:{v['label']}" for v in chunk_violations)
                    or "违例")
        if llm_call is not None:
            from app.services.tool_loop import run_tool_loop

            def _get_chunk(args: dict) -> dict:
                n = int(args.get("chunk_index") or idx)
                return {"chunk_index": n, "text": chunk_map.get(n, "")[:4000]}

            def _reextract(args: dict) -> dict:
                n = int(args.get("chunk_index") or idx)
                fb = str(args.get("feedback") or feedback)
                items = reextract_fn(n, chunk_map.get(n, ""), fb) if reextract_fn else []
                return {"entities": items}

            loop = run_tool_loop(
                llm_call,
                "你是抽取修复 agent。先查违例与原文，再决定重抽；最后给出恢复的实体清单。",
                f"chunk #{idx} 闸门违例：{feedback}。请修复该 chunk 的抽取。",
                {"get_chunk_text": {"description": "读取切片原文",
                                    "parameters": {"chunk_index": "int"},
                                    "fn": _get_chunk},
                 "re_extract": {"description": "带违例反馈重抽单 chunk，返回实体数组",
                                "parameters": {"chunk_index": "int", "feedback": "str"},
                                "fn": _reextract}},
                max_rounds=2)
            recovered = []
            for c in loop.get("tool_calls", []):
                if c.get("tool") == "re_extract":
                    obs = _reextract(c.get("args") or {})
                    recovered.extend(obs.get("entities") or [])
        else:
            items = reextract_fn(idx, chunk_text, feedback) if reextract_fn else []
            recovered = list(items or [])
        for it in recovered:
            if isinstance(it, dict):
                it["_repaired"] = True
                it.setdefault("_repaired_chunk", idx)
        out["repaired_chunks"].append({"chunk_index": idx,
                                       "violations": len(chunk_violations),
                                       "recovered": len(recovered)})
        out["recovered_entities"].extend(recovered)
    return out


__all__ = ["targeted_repair", "violation_rate", "pick_worst_chunks",
           "REPAIR_VIOLATION_RATE"]
