# -*- coding: utf-8 -*-
"""M5 真实验证脚本：MCP 网关全链路（07 §3 / 03 §16）。

链路：admin 登录 → 签发 viewer/editor 令牌 → initialize → GET 405 → tools/list 权限矩阵
→ search_entities → query_graph 注入被拒 → viewer 拒写 → editor add_entity/add_relationship
（独立草稿项目，不污染业务数据）→ resources/read → 撤销令牌即失效。
断言全过打印 PASS 汇总；任何一步失败打印 FAIL 并退出码 1。
"""
import json
import os
import sys

import requests

BASE = "http://localhost:3001"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'✅ PASS' if ok else '❌ FAIL'} | {name}" + (f" | {detail}" if detail else ""))
    return ok


def main():
    # 1. admin 登录
    r = requests.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "cbil123456"}, timeout=10)
    assert r.status_code == 200, f"登录失败: {r.text}"
    admin_tok = r.json()["access_token"]
    ah = {"Authorization": f"Bearer {admin_tok}"}
    me = requests.get(f"{BASE}/api/auth/me", headers=ah, timeout=10).json()
    check("admin 登录", me.get("role") == "admin", f"user={me.get('username')}")

    # 2. 建独立验证项目（验证后删除）
    r = requests.post(f"{BASE}/api/projects", headers=ah, timeout=10,
                      json={"name": "M5-MCP-全链路验证", "description": "M5 验收用，验证后删除"})
    assert r.status_code in (200, 201), f"建项目失败: {r.text}"
    pid = r.json()["id"]
    print(f"   验证项目 id={pid}")

    try:
        # 3. 签发 viewer / editor 令牌
        rv = requests.post(f"{BASE}/api/admin/mcp-tokens", headers=ah, timeout=10,
                           json={"user_id": me["id"], "project_id": pid, "name": "M5验收-viewer", "can_write": False})
        re_ = requests.post(f"{BASE}/api/admin/mcp-tokens", headers=ah, timeout=10,
                            json={"user_id": me["id"], "project_id": pid, "name": "M5验收-editor", "can_write": True})
        check("令牌签发（明文仅返回一次）", rv.status_code == 201 and re_.status_code == 201)
        viewer_tok = rv.json()["token"]
        editor_tok = re_.json()["token"]
        check("令牌格式 sk-mcp-+40hex", viewer_tok.startswith("sk-mcp-") and len(viewer_tok) == 47)
        check("token_hint 脱敏", "***" in rv.json()["token_hint"] and viewer_tok[-4:] not in rv.json()["token_hint"])

        def rpc(method, params=None, token=None, rpc_id=1):
            body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
            if params is not None:
                body["params"] = params
            return requests.post(f"{BASE}/mcp", json=body,
                                 headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)

        # 4. initialize（无令牌）+ GET 405
        r = rpc("initialize", {"protocolVersion": "2025-03-26"})
        check("initialize 握手", r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "ontology-platform")
        r = requests.get(f"{BASE}/mcp", timeout=10)
        check("GET /mcp → 405", r.status_code == 405)

        # 5. tools/list 权限矩阵
        tools_v = [t["name"] for t in rpc("tools/list", token=viewer_tok).json()["result"]["tools"]]
        tools_e = [t["name"] for t in rpc("tools/list", token=editor_tok).json()["result"]["tools"]]
        check("viewer 8 只读工具", len(tools_v) == 8 and "add_entity" not in tools_v)
        check("editor 10 工具（含写）", len(tools_e) == 10 and {"add_entity", "add_relationship"} <= set(tools_e))

        # 6. 先放一个类进 TBox（写工具前置），再验证读工具
        gql = requests.get(f"{BASE}/api/projects/{pid}/graph", headers=ah, timeout=10)
        # 通过画布保存接口注入骨架类节点（项目草稿态，editor 权限 admin 直通）
        gd = {"nodes": [{"id": "cls_risk", "type": "custom", "position": {"x": 0, "y": 0},
                         "data": {"label": "风险类型", "type": "owl:Class", "properties": []}}],
              "edges": []}
        r = requests.put(f"{BASE}/api/projects/{pid}", headers=ah, timeout=10, json={"graph_data": gd})
        ok = r.status_code in (200, 204)
        check("预置 TBox 类节点", ok, r.text[:120] if not ok else "")

        # 7. search_entities（editor 建一个实体后检索）
        r = rpc("tools/call", {"name": "add_entity",
                               "arguments": {"label": "本金全部损失风险", "class_label": "风险类型",
                                             "properties": {"等级": "高"}}}, token=editor_tok, rpc_id=10)
        out = json.loads(r.json()["result"]["content"][0]["text"])
        check("editor add_entity 写入", out.get("created") is True and out.get("uri", "").startswith(f"urn:onto:{pid}:entity/mcp_"),
              json.dumps(out, ensure_ascii=False)[:160])

        r = rpc("tools/call", {"name": "search_entities", "arguments": {"query": "本金"}}, token=viewer_tok, rpc_id=11)
        ents = json.loads(r.json()["result"]["content"][0]["text"])["entities"]
        check("search_entities 检索命中", len(ents) >= 1 and ents[0]["label"] == "本金全部损失风险")

        # 8. query_graph 注入被拒
        r = rpc("tools/call", {"name": "query_graph",
                               "arguments": {"cypher": "MATCH (n) CREATE (m:Injected) RETURN m"}}, token=editor_tok, rpc_id=12)
        check("query_graph CREATE 注入被拒", r.json().get("error", {}).get("code") == -32002)
        r = rpc("tools/call", {"name": "query_graph",
                               "arguments": {"cypher": "CALL db.labels() YIELD label RETURN label"}}, token=editor_tok, rpc_id=13)
        check("query_graph CALL 注入被拒", r.json().get("error", {}).get("code") == -32002)

        # 9. viewer 拒写
        r = rpc("tools/call", {"name": "add_entity",
                               "arguments": {"label": "越权", "class_label": "风险类型"}}, token=viewer_tok, rpc_id=14)
        check("viewer 写工具被拒", r.json().get("error", {}).get("code") == -32002)

        # 10. add_relationship（本金全部损失风险 → 类节点不合适，用两个实例）
        rpc("tools/call", {"name": "add_entity",
                           "arguments": {"label": "市场波动风险", "class_label": "风险类型"}}, token=editor_tok, rpc_id=15)
        e1 = rpc("tools/call", {"name": "search_entities", "arguments": {"query": "本金全部"}}, token=editor_tok, rpc_id=16)
        e2 = rpc("tools/call", {"name": "search_entities", "arguments": {"query": "市场波动"}}, token=editor_tok, rpc_id=17)
        u1 = json.loads(e1.json()["result"]["content"][0]["text"])["entities"][0]["uri"]
        u2 = json.loads(e2.json()["result"]["content"][0]["text"])["entities"][0]["uri"]
        r = rpc("tools/call", {"name": "add_relationship",
                               "arguments": {"subject_uri": u1, "predicate": "同源风险", "object_uri": u2}}, token=editor_tok, rpc_id=18)
        out = json.loads(r.json()["result"]["content"][0]["text"])
        check("editor add_relationship 写入", out.get("created") is True)

        # 11. Resources
        r = rpc("resources/read", {"uri": "ontology://graph/summary"}, token=viewer_tok, rpc_id=19)
        summary = json.loads(r.json()["result"]["contents"][0]["text"])
        check("resources/read 图谱概要", summary["instances"] >= 2, f"instances={summary['instances']} entities={summary['entity_rows']}")

        # 12. 撤销令牌即失效
        tok_id = re_.json()["id"]
        requests.delete(f"{BASE}/api/admin/mcp-tokens/{tok_id}", headers=ah, timeout=10)
        r = rpc("tools/list", token=editor_tok, rpc_id=20)
        check("撤销后令牌失效", r.json().get("error", {}).get("code") == -32001)

        # 13. 审计留痕（audit_logs 无查询 API，直查库验证 07 §3.4）
        sys.path.insert(0, os.getcwd())
        from app.infrastructure.database import SessionLocal, AuditLog

        db = SessionLocal()
        mcp_logs = (
            db.query(AuditLog)
            .filter(AuditLog.resource_type == "mcp_tool", AuditLog.user_id == me["id"])
            .order_by(AuditLog.id.desc()).limit(30).all()
        )
        db.close()
        actions = {l.action for l in mcp_logs}
        blocked = any((l.detail or {}).get("blocked") for l in mcp_logs)
        digests = all((l.detail or {}).get("args_digest") for l in mcp_logs)
        check("审计留痕（mcp.read/mcp.write + args_digest）",
              len(mcp_logs) >= 6 and "mcp.read" in actions and "mcp.write" in actions
              and blocked and digests,
              f"条数={len(mcp_logs)} actions={sorted(actions)} 有blocked={blocked}")
    finally:
        # 14. 清理验证项目
        r = requests.delete(f"{BASE}/api/projects/{pid}", headers=ah, timeout=10)
        print(f"   清理验证项目 id={pid}: HTTP {r.status_code}")

    failed = [x for x in results if not x[1]]
    print(f"\n{'=' * 60}\nMCP 全链路验证：{len(results) - len(failed)}/{len(results)} PASS" +
          (f"，失败 {len(failed)} 项" if failed else "，全部通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
