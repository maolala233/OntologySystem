# app/services/tool_loop.py - 轻量 tool-loop（O3-B15，自研不引框架，docs/design/09 §0.1）
# 协议：约束 JSON 输出 {"action":"call"|"final", "tool":名, "args":{...}, "final_answer":串}，
# 每轮执行工具→观察回喂，最多 max_rounds 轮；解析失败/未知工具按错误观察回喂继续。
# llm 需支持 call_llm(json_schema=...)（llm_client 已有），无破坏性改动。
from __future__ import annotations

import json
import logging
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


def run_tool_loop(llm, system_prompt: str, user_prompt: str,
                  tools: dict[str, dict[str, Any]],
                  max_rounds: int = 2) -> dict:
    """执行 tool-loop，返回 {"answer": str, "rounds": int, "tool_calls": [...]}。

    tools: {name: {"description": str, "parameters": dict(json schema), "fn": callable(args)->dict}}
    fn 抛异常视为工具失败，错误文本作为观察回喂（模型可换路）。
    """
    tools_desc = "\n".join(
        f"- {name}: {t.get('description', '')} 参数: {json.dumps(t.get('parameters') or {}, ensure_ascii=False)}"
        for name, t in tools.items())
    sys_full = (f"{system_prompt}\n\n可用工具：\n{tools_desc}\n\n"
                "每轮只能输出一个 JSON 动作：需要调用工具时 "
                '{"action":"call","tool":"工具名","args":{...}}；'
                '得到足够信息后 {"action":"final","final_answer":"结论"}。')
    transcript = user_prompt
    calls: list[dict] = []
    for round_no in range(1, max_rounds + 1):
        try:
            resp = llm.call_llm(system_prompt=sys_full, user_prompt=transcript,
                                stream=False, json_schema=_ACTION_SCHEMA)
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
        if tool is None:
            observation = {"error": f"unknown_tool: {tool_name}",
                           "available": list(tools)}
        else:
            try:
                observation = tool["fn"](action.get("args") or {}) or {"ok": True}
            except Exception as exc:  # noqa: BLE001 —— 工具异常回喂，模型可改路
                observation = {"error": str(exc)[:500]}
        calls.append({"round": round_no, "tool": tool_name,
                      "args": action.get("args") or {},
                      "observation_keys": sorted(observation) if isinstance(observation, dict) else []})
        transcript = (f"{transcript}\n\n【第 {round_no} 轮工具 {tool_name} 结果】\n"
                      f"{json.dumps(observation, ensure_ascii=False)[:4000]}")
    return {"answer": "", "rounds": max_rounds, "tool_calls": calls,
            "error": "max_rounds_exhausted"}


__all__ = ["run_tool_loop"]
