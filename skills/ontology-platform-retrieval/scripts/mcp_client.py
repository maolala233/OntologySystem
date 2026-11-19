#!/usr/bin/env python3
"""Minimal MCP Streamable HTTP client for the Ontology platform.

Only the Python standard library is used, so the skill works in a bare agent
runtime. Configuration comes from environment variables by default:

    MCP_ENDPOINT   default http://127.0.0.1:3001/mcp
    MCP_TOKEN      sk-mcp-* (required for anything except initialize)

The token is never printed. Prefer importing `MCPClient` from the other
scripts; run this file directly only for diagnosis:

    python scripts/mcp_client.py tools
    python scripts/mcp_client.py call graph_schema_get '{}'
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


class MCPError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPClient:
    def __init__(self, endpoint: str | None = None, token: str | None = None,
                 timeout: float = 30.0):
        self.endpoint = (endpoint or os.environ.get("MCP_ENDPOINT")
                         or "http://127.0.0.1:3001/mcp").strip()
        self.token = (token or os.environ.get("MCP_TOKEN")
                      or os.environ.get("MCP_BEARER") or "").strip()
        self.timeout = timeout
        self._id = 0

    # ── transport ────────────────────────────────────────────────────────
    def _rpc(self, method: str, params: dict | None = None,
             require_token: bool = True) -> dict:
        if require_token and not self.token:
            raise MCPError("未配置 MCP_TOKEN；只读查询也需要 Bearer 令牌")
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id,
                           "method": method, "params": params or {}},
                          ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.endpoint, data=body, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            err = payload.get("error") or {}
            raise MCPError(f"HTTP {e.code}: {err.get('message') or text[:300]}",
                           code=err.get("code"), data=payload) from e
        except urllib.error.URLError as e:
            raise MCPError(f"无法连接 MCP 端点 {self.endpoint}: {e.reason}") from e

        payload = self._parse_payload(raw)
        if payload.get("error"):
            err = payload["error"]
            raise MCPError(str(err.get("message") or err), code=err.get("code"),
                           data=err.get("data"))
        return payload.get("result") or {}

    @staticmethod
    def _parse_payload(raw: str) -> dict:
        text = raw.strip()
        if not text:
            raise MCPError("MCP 返回空响应")
        if text.startswith("event:") or text.startswith("data:"):
            # Streamable HTTP 的 SSE 帧：取最后一个 data: JSON
            chunks = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    chunks.append(line[5:].strip())
            if chunks:
                text = chunks[-1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise MCPError(f"网关响应不是合法 JSON: {text[:200]}") from e
        if not isinstance(payload, dict):
            raise MCPError("MCP 响应不是 JSON 对象")
        return payload

    # ── high level ───────────────────────────────────────────────────────
    def initialize(self) -> dict:
        return self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "ontology-skill", "version": "1.1.0"},
        }, require_token=False)

    def tools(self) -> list[dict]:
        return list((self._rpc("tools/list") or {}).get("tools") or [])

    def call(self, name: str, arguments: dict | None = None) -> Any:
        result = self._rpc("tools/call", {"name": name,
                                          "arguments": arguments or {}})
        content = (result or {}).get("content") or []
        if content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return result

    def resource(self, uri: str) -> str:
        result = self._rpc("resources/read", {"uri": uri})
        contents = (result or {}).get("contents") or []
        return str(contents[0].get("text") or "") if contents else ""


def main(argv: list[str]) -> int:
    client = MCPClient()
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    try:
        if cmd == "tools":
            print(json.dumps([t.get("name") for t in client.tools()],
                             ensure_ascii=False, indent=2))
            return 0
        if cmd == "call" and len(argv) >= 3:
            args = json.loads(argv[3]) if len(argv) >= 4 else {}
            print(json.dumps(client.call(argv[2], args), ensure_ascii=False, indent=2))
            return 0
        if cmd == "resource" and len(argv) >= 3:
            print(client.resource(argv[2]))
            return 0
        print(f"未知命令: {cmd}", file=sys.stderr)
        return 2
    except MCPError as e:
        print(f"MCP 错误: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
