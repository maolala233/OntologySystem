# app/services/tool_loop.py - 轻量 tool-loop（O3-B15，自研不引框架，docs/design/09 §0.1）
# 协议：约束 JSON 输出 {"action":"call"|"final", "tool":名, "args":{...}, "final_answer":串}，
# 每轮执行工具→观察回喂，最多 max_rounds 轮；解析失败/未知工具按错误观察回喂继续。
# llm 需支持 call_llm(json_schema=...)（llm_client 已有），无破坏性改动。
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

_ACTION_SCHEMA = {
    "name": "tool_loop_action",
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["call", "final"]},
            "tool": {"type": "string"},
            "args": {"type": "object"},
            "final_answer": {"type": "string"},
        },
        "required": ["action"],
    },
}


def iter_tool_loop(llm, system_prompt: str, user_prompt: str,
                   tools: dict[str, dict[str, Any]],
                   max_rounds: int = 2):
    """生成器版 tool-loop：边执行边 yield ("tool", payload) 步骤事件（B31/F16 实时展示），
    结束时 return {"answer", "rounds", "tool_calls"}（调用方用 yield from 捕获）。"""
    tools_desc = "\n".join(
        f"- {name}: {t.get('description', '')} 参数: {json.dumps(t.get('parameters') or {}, ensure_ascii=False)}"
        for name, t in tools.items())
    sys_full = (f"{system_prompt}\n\n可用工具：\n{tools_desc}\n\n"
                "每轮只能输出一个 JSON 动作：需要调用工具时 "
                '{"action":"call","tool":"工具名","args":{...}}；'
                '得到足够信息后 {"action":"final","final_answer":"结论"}。'
                "（尽量少调工具，信息够就立即 final）")
    transcript = user_prompt
    calls: list[dict] = []
    for round_no in range(1, max_rounds + 1):
        # 末轮强制收敛：不再允许 call，必须产出 final（防慢模型无限烧轮次）
        round_sys = sys_full if round_no < max_rounds else (
            f"{system_prompt}\n\n【最后一轮】工具阶段已结束，你必须立即输出 "
            '{"action":"final","final_answer":"基于以上已收集信息的完整结论"}，'
            "禁止再调用任何工具；信息不足也要给出当前可得的最优判断并说明缺口。")
        try:
            resp = llm.call_llm(system_prompt=round_sys, user_prompt=transcript,
                                stream=False, json_schema=_ACTION_SCHEMA)
            # 两种返回形态：{"content": str|dict}（json_schema 路径）或
            # 已解析的 action dict（json_object 降级路径，llm_client 直接 return json.loads 结果）
            if isinstance(resp, dict) and resp.get("content") is None \
                    and ("action" in resp or "final_answer" in resp):
                action = resp
            else:
                text = resp.get("content") if isinstance(resp, dict) else resp
                action = text if isinstance(text, dict) else json.loads(str(text))
        except Exception as exc:  # noqa: BLE001 —— 模型/解析失败直接终结
            logger.warning(f"[tool-loop] round {round_no} 模型输出失败：{exc}")
            return {"answer": "", "rounds": round_no, "tool_calls": calls,
                    "error": f"model_output_failed: {exc}"}
        if not isinstance(action, dict) or action.get("action") != "call":
            answer = str(action.get("final_answer") or "") if isinstance(action, dict) else ""
            if not answer:
                answer = json.dumps(action, ensure_ascii=False) if isinstance(action, dict) else str(action)
            return {"answer": answer, "rounds": round_no, "tool_calls": calls}
        tool_name = str(action.get("tool") or "")
        tool = tools.get(tool_name)
        args = action.get("args") or {}
        yield "tool", {"phase": "start", "tool": tool_name, "args": args,
                       "round": round_no}
        t_tool = time.time()
        if tool is None:
            observation = {"error": f"unknown_tool: {tool_name}",
                           "available": list(tools)}
        else:
            try:
                observation = tool["fn"](args) or {"ok": True}
            except Exception as exc:  # noqa: BLE001 —— 工具异常回喂，模型可改路
                observation = {"error": str(exc)[:500]}
        duration_ms = int((time.time() - t_tool) * 1000)
        yield "tool", {"phase": "end", "tool": tool_name, "args": args,
                       "round": round_no, "duration_ms": duration_ms,
                       "observation": observation}
        calls.append({"round": round_no, "tool": tool_name,
                      "args": args,
                      "duration_ms": duration_ms,
                      "observation_keys": sorted(observation) if isinstance(observation, dict) else [],
                      "observation": _compact_observation(observation)})
        transcript = (f"{transcript}\n\n【第 {round_no} 轮工具 {tool_name} 结果】\n"
                      f"{json.dumps(observation, ensure_ascii=False)[:4000]}")
    return {"answer": "", "rounds": max_rounds, "tool_calls": calls,
            "error": "max_rounds_exhausted"}


def run_tool_loop(llm, system_prompt: str, user_prompt: str,
                  tools: dict[str, dict[str, Any]],
                  max_rounds: int = 2) -> dict:
    """同步入口（兼容既有调用方）：消费 iter_tool_loop，丢弃事件、只取最终结果。"""
    gen = iter_tool_loop(llm, system_prompt, user_prompt, tools, max_rounds)
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value or {"answer": "", "rounds": 0, "tool_calls": []}


def _compact_observation(observation: Any, budget: int = 1500) -> Any:
    """落库/回放用的观察摘要：序列化超预算则截断（tool_calls 是 JSON 列，防膨胀）。"""
    try:
        text = json.dumps(observation, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return {"_unserializable": True}
    if len(text) <= budget:
        return observation
    return {"_truncated": True, "preview": text[:budget]}


__all__ = ["run_tool_loop", "iter_tool_loop"]
