#!/usr/bin/env python3
"""Smoke test for the Ontology MCP skill.

    python scripts/check.py
    python scripts/check.py --class 前端接口 --by 登记人

Checks handshake, tools, schema, query_facts, and optional group closure.
Exit codes: 0 ok, 2 closure failed, 3 MCP/transport error.
"""
from __future__ import annotations

import argparse
import json
import sys

from mcp_client import MCPClient, MCPError
from retrieve import fetch_entities


REQUIRED = {
    "graph_schema_get", "entity_type_list", "relation_type_list",
    "entity_find", "query_facts", "neighbors_get", "relations_between",
    "subgraph_expand", "paths_find", "shortest_path", "semantic_search",
    "ask",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint")
    p.add_argument("--token")
    p.add_argument("--class", dest="class_label")
    p.add_argument("--by", dest="group_by")
    ns = p.parse_args(argv)
    client = MCPClient(endpoint=ns.endpoint, token=ns.token)
    report: dict = {"ok": False, "endpoint": client.endpoint}
    try:
        init = client.initialize()
        report["server"] = (init.get("serverInfo") or {}).get("name")
        tools = {t.get("name") for t in client.tools()}
        missing = sorted(REQUIRED - tools)
        report["tools"] = len(tools)
        report["missing_tools"] = missing
        if missing:
            report["error"] = "缺少必需工具"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 3
        schema = client.call("graph_schema_get", {})
        report["classes"] = len((schema or {}).get("classes") or [])
        report["relations"] = len((schema or {}).get("objectProperties") or [])
        report["skill_resource"] = bool(client.resource(
            "ontology://skill/ontology-retrieval"))
        if ns.class_label:
            total, items = fetch_entities(
                client, {"class_label": ns.class_label, "include_classes": False})
            report["class"] = ns.class_label
            report["total"] = total
            report["fetched"] = len(items)
            if ns.group_by:
                from collections import Counter
                counts: Counter[str] = Counter()
                for it in items:
                    v = (it.get("props") or {}).get(ns.group_by)
                    counts[str(v).strip() if v is not None and str(v).strip()
                           else "未标注"] += 1
                summed = sum(counts.values())
                report["groups"] = dict(counts)
                report["closure"] = {"ok": len(items) == total and summed == total,
                                     "sum": summed, "total": total}
                if not report["closure"]["ok"]:
                    report["ok"] = False
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return 2
        report["ok"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except MCPError as e:
        report["error"] = str(e)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
