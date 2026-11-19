#!/usr/bin/env python3
"""Stable, read-only retrieval commands for the Ontology MCP skill.

Examples:
    python scripts/retrieve.py schema
    python scripts/retrieve.py types
    python scripts/retrieve.py count --class 前端接口
    python scripts/retrieve.py group --class 前端接口 --by 登记人
    python scripts/retrieve.py find --class 前端接口 --filter "登记人='张茂莲'"
    python scripts/retrieve.py semantic --query "合同查询接口" --top-k 10
    python scripts/retrieve.py neighbors --id C2504051
    python scripts/retrieve.py path --from C2504051 --to C2504052
    python scripts/retrieve.py ask --question "前端接口有多少个？"

The scripts paginate query_facts, so counts are based on the full total, not the
first page. `group` exits with code 2 when group counts do not close to total.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from mcp_client import MCPClient, MCPError


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fetch_entities(client: MCPClient, args: dict, page_size: int = 500,
                   max_pages: int = 100) -> tuple[int, list[dict]]:
    """按 next_offset 取全量；返回 (total, items)。"""
    items: list[dict] = []
    offset = 0
    total = 0
    for _ in range(max_pages):
        payload = client.call("query_facts", {
            "kind": "entities",
            **args,
            "limit": page_size,
            "offset": offset,
        })
        if not isinstance(payload, dict):
            raise RuntimeError("query_facts 返回格式异常")
        total = int(payload.get("total") or 0)
        items.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            break
        nxt = payload.get("next_offset")
        if nxt is None:
            break
        offset = int(nxt)
    return total, items


def entity_args(ns: argparse.Namespace) -> dict:
    out: dict[str, Any] = {"include_classes": False}
    if ns.class_label:
        out["class_label"] = ns.class_label
    if getattr(ns, "filter", None):
        out["label_contains"] = ns.filter
    return out


def cmd_count(client: MCPClient, ns: argparse.Namespace) -> int:
    payload = client.call("query_facts", {
        "kind": "entities", "class_label": ns.class_label,
        "include_classes": False, "limit": ns.limit, "offset": 0,
    })
    total = int(payload.get("total") or 0)
    emit({"ok": True, "class": ns.class_label, "total": total,
          "returned": payload.get("returned"),
          "has_more": payload.get("has_more"),
          "items": payload.get("items") or []})
    return 0


def cmd_group(client: MCPClient, ns: argparse.Namespace) -> int:
    total, items = fetch_entities(client, entity_args(ns))
    counts: Counter[str] = Counter()
    for item in items:
        props = item.get("props") or {}
        value = props.get(ns.by)
        key = str(value).strip() if value is not None else ""
        counts[key or "未标注"] += 1
    grouped = [{"value": k, "count": v}
               for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    summed = sum(counts.values())
    ok = len(items) == total and summed == total
    emit({
        "ok": ok,
        "class": ns.class_label,
        "group_by": ns.by,
        "total": total,
        "fetched": len(items),
        "groups": grouped,
        "closure": {"ok": ok, "sum": summed, "diff": total - summed},
    })
    if not ok:
        print(f"分组不闭合: total={total}, fetched={len(items)}, sum={summed}",
              file=sys.stderr)
        return 2
    return 0


def cmd_find(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("entity_find", {
        "typeNameEn": ns.class_label,
        "filter": ns.filter,
        "limit": ns.limit,
    }))
    return 0


def cmd_semantic(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("semantic_search", {
        "queryText": ns.query, "topK": ns.top_k,
    }))
    return 0


def cmd_neighbors(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("neighbors_get", {
        "entityDataId": ns.id, "direction": ns.direction, "limit": ns.limit,
    }))
    return 0


def cmd_path(client: MCPClient, ns: argparse.Namespace) -> int:
    if ns.mode == "exists":
        emit(client.call("path_exists", {"fromDataId": ns.from_id,
                                          "toDataId": ns.to_id}))
    elif ns.mode == "all":
        emit(client.call("paths_find", {"fromDataId": ns.from_id,
                                         "toDataId": ns.to_id,
                                         "maxDepth": ns.max_depth,
                                         "limit": ns.limit}))
    else:
        emit(client.call("shortest_path", {"fromDataId": ns.from_id,
                                             "toDataId": ns.to_id}))
    return 0


def cmd_schema(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("graph_schema_get", {}))
    return 0


def cmd_types(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("entity_type_list", {}))
    return 0


def cmd_relation_types(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("relation_type_list", {}))
    return 0


def cmd_relations(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("relations_between", {
        "fromDataId": ns.from_id, "toDataId": ns.to_id,
        "direction": ns.direction,
    }))
    return 0


def cmd_subgraph(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("subgraph_expand", {
        "anchorDataIds": ns.ids, "hops": ns.hops,
        "maxNodes": ns.max_nodes, "direction": ns.direction,
    }))
    return 0


def cmd_ask(client: MCPClient, ns: argparse.Namespace) -> int:
    emit(client.call("ask", {"question": ns.question, "top_k": ns.top_k}))
    return 0


def cmd_resource(client: MCPClient, ns: argparse.Namespace) -> int:
    print(client.resource(ns.uri))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--endpoint", default=None,
                        help="MCP 端点；默认读 MCP_ENDPOINT")
    common.add_argument("--token", default=None,
                        help="MCP 令牌；默认读 MCP_TOKEN（不建议写在命令历史）")
    p = argparse.ArgumentParser(description="Ontology MCP 稳定检索脚本")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("schema", parents=[common]); s.set_defaults(fn=cmd_schema)
    s = sub.add_parser("types", parents=[common]); s.set_defaults(fn=cmd_types)
    s = sub.add_parser("relation-types", parents=[common]); s.set_defaults(fn=cmd_relation_types)

    s = sub.add_parser("count", parents=[common])
    s.add_argument("--class", dest="class_label", required=True)
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_count)

    s = sub.add_parser("group", parents=[common])
    s.add_argument("--class", dest="class_label", required=True)
    s.add_argument("--by", required=True, help="属性名，例如 登记人")
    s.set_defaults(fn=cmd_group)

    s = sub.add_parser("find", parents=[common])
    s.add_argument("--class", dest="class_label", required=True)
    s.add_argument("--filter", default=None,
                   help="SQL 风格表达式，例如 登记人='张茂莲'")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("semantic", parents=[common])
    s.add_argument("--query", required=True)
    s.add_argument("--top-k", type=int, default=10)
    s.set_defaults(fn=cmd_semantic)

    s = sub.add_parser("neighbors", parents=[common])
    s.add_argument("--id", required=True)
    s.add_argument("--direction", choices=["OUT", "IN", "BOTH"], default="BOTH")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_neighbors)

    s = sub.add_parser("relations", parents=[common])
    s.add_argument("--from", dest="from_id", required=True)
    s.add_argument("--to", dest="to_id", required=True)
    s.add_argument("--direction", choices=["OUT", "IN", "BOTH"], default="BOTH")
    s.set_defaults(fn=cmd_relations)

    s = sub.add_parser("subgraph", parents=[common])
    s.add_argument("--id", dest="ids", action="append", required=True)
    s.add_argument("--hops", type=int, default=1)
    s.add_argument("--max-nodes", type=int, default=100)
    s.add_argument("--direction", choices=["OUT", "IN", "BOTH"], default="BOTH")
    s.set_defaults(fn=cmd_subgraph)

    s = sub.add_parser("path", parents=[common])
    s.add_argument("--from", dest="from_id", required=True)
    s.add_argument("--to", dest="to_id", required=True)
    s.add_argument("--mode", choices=["shortest", "exists", "all"],
                   default="shortest")
    s.add_argument("--max-depth", type=int, default=4)
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_path)

    s = sub.add_parser("ask", parents=[common])
    s.add_argument("--question", required=True)
    s.add_argument("--top-k", type=int, default=12)
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("resource", parents=[common])
    s.add_argument("--uri", default="ontology://skill/ontology-retrieval")
    s.set_defaults(fn=cmd_resource)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    client = MCPClient(endpoint=ns.endpoint, token=ns.token)
    try:
        client.initialize()
        return int(ns.fn(client, ns) or 0)
    except MCPError as e:
        print(f"MCP 错误: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
