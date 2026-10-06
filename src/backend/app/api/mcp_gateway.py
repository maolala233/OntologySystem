# app/api/mcp_gateway.py - 平台 MCP 网关（M5，docs/design/07 §3 / 03 §16）
# HTTP Streamable 传输（POST /mcp，JSON-RPC 2.0；无会话状态的无状态实现，
# GET/SSE 生命周期返回 405——规范允许服务端不提供 server-initiated 流）。
# 令牌 sk-mcp-*（SHA-256 落库 mcp_tokens，user+project+读写范围 绑定）；
# 工具走平台语义：写操作改 graph_data blob（S1 权威）→ commit 触发行表双写 + outbox；
# 只读 Cypher 白名单；audit(mcp.read/mcp.write)；Redis 限流（读 60/分、写 20/分/令牌）。
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.adapters.provider import build_legacy_llm_config
from app.adapters.retrieval import RetrievalQuery, query_with_reasoning
from app.core.config import settings
from app.core.deps import get_db
from app.core.exceptions import APIError
from app.infrastructure.database import (
    Entity,
    McpToken,
    OntologyVersion,
    Project,
    ProvenanceRecord,
    UploadedDocument,
    User,
)
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.neo4j_client import neo4j_client
from app.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

MCP_PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "ontology-platform", "version": "0.7.0"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 07 §3.3：单响应 ≤ 2MB

RATE_LIMIT_READ = 60   # 次/分/令牌（07 §3.4）
RATE_LIMIT_WRITE = 20

# JSON-RPC 标准错误码 + 平台自定义段
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_UNAUTHORIZED = -32001
ERR_FORBIDDEN = -32002
ERR_RATE_LIMITED = -32029

# 只读 Cypher 白名单（07 §3.3：禁 CALL/CREATE/DELETE/SET/MERGE 等）
_CYPHER_FORBIDDEN = re.compile(
    r"(?is)\b(call|create|delete|detach|set|merge|remove|drop|load|foreach|index|constraint)\b"
)
_CYPHER_FORBIDDEN_PREFIX = (";", "--", "/*")

WRITE_TOOLS = {"add_entity", "add_relationship"}


class McpContext:
    """每次 tools/call 解析一次的令牌上下文（07 §3.2：MCP 无跨请求状态）。"""

    def __init__(self, token: McpToken, user: User, project: Project):
        self.token = token
        self.user = user
        self.project = project

    @property
    def can_write(self) -> bool:
        return bool(self.token.can_write)


# ---------------------------------------------------------------- 令牌鉴权

def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _auth_token(request: Request, db: Session) -> McpContext:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _rpc_auth_error("缺少 Authorization: Bearer sk-mcp-* 令牌")
    plaintext = auth[7:].strip()
    if not plaintext.startswith("sk-mcp-"):
        raise _rpc_auth_error("令牌格式非法（须为 sk-mcp- 前缀）")
    tok = (
        db.query(McpToken)
        .filter(McpToken.token_hash == _hash_token(plaintext))
        .first()
    )
    if tok is None or tok.revoked_at is not None:
        raise _rpc_auth_error("令牌无效或已撤销")
    if tok.expires_at is not None:
        expires = tok.expires_at if tok.expires_at.tzinfo else tok.expires_at.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise _rpc_auth_error("令牌已过期")
    user = db.query(User).filter(User.id == tok.user_id).first()
    if user is None or not user.is_active:
        raise _rpc_auth_error("令牌绑定用户不可用")
    project = db.query(Project).filter(Project.id == tok.project_id).first()
    if project is None:
        raise _rpc_auth_error("令牌绑定项目不存在")
    tok.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return McpContext(tok, user, project)


def _rate_limit(ctx: McpContext, tool: str | None) -> None:
    """Redis 计数限流：读 60/分、写 20/分/令牌（07 §3.4）。Redis 不可用时放行（可用性优先）。"""
    try:
        import redis
        is_write = tool in WRITE_TOOLS
        bucket = int(time.time() // 60)
        key = f"mcp:rl:{ctx.token.id}:{'w' if is_write else 'r'}:{bucket}"
        from app.services.env_config_service import redis_url
        client = redis.Redis.from_url(redis_url(), decode_responses=True, socket_connect_timeout=1)
        count = client.incr(key)
        if count == 1:
            client.expire(key, 120)
        if count > (RATE_LIMIT_WRITE if is_write else RATE_LIMIT_READ):
            raise _rpc_error(ERR_RATE_LIMITED, "触发 MCP 限流（读 60/分、写 20/分/令牌）")
    except _RpcError:
        raise
    except Exception as e:
        logger.warning(f"[mcp] 限流计数失败（放行）：{e}")


# ---------------------------------------------------------------- JSON-RPC 骨架

class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _rpc_error(code: int, message: str, data: Any = None) -> _RpcError:
    return _RpcError(code, message, data)


def _rpc_auth_error(message: str) -> _RpcError:
    return _RpcError(ERR_UNAUTHORIZED, message)


def _rpc_result(rpc_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_fail(rpc_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


def _text_content(payload: Any) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        # 07 §3.3：超限截断 + next_cursor 提示
        text = text[: MAX_RESPONSE_BYTES - 64] + f'","truncated": true, "next_cursor": "refresh-with-filters"'
    return {"content": [{"type": "text", "text": text}], "isError": False}


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    """MCP HTTP Streamable 端点（03 §16）。

    令牌校验失败仍返回 200 + JSON-RPC error（认证语义在协议层；
    同时对未授权请求回 401 头便于客户端诊断——采用规范惯例：HTTP 401 + JSON body）。
    """
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_response(_rpc_fail(None, ERR_PARSE, "请求体不是合法 JSON"), status=400)

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _jsonrpc_response(_rpc_fail(body.get("id") if isinstance(body, dict) else None,
                                           ERR_INVALID_REQUEST, "缺少 jsonrpc:2.0 标识"), status=400)

    method = body.get("method")
    rpc_id = body.get("id")
    is_notification = rpc_id is None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": (body.get("params") or {}).get("protocolVersion", MCP_PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
            return _jsonrpc_response(_rpc_result(rpc_id, result))

        if method in ("notifications/initialized", "notifications/cancelled"):
            return Response(status_code=202)

        # initialize 之外的调用需要令牌
        ctx = _auth_token(request, db)

        if method == "ping":
            return _jsonrpc_response(_rpc_result(rpc_id, {}))

        if method == "tools/list":
            return _jsonrpc_response(_rpc_result(rpc_id, {"tools": _tool_specs(ctx)}))

        if method == "tools/call":
            params = body.get("params") or {}
            tool = params.get("name", "")
            args = params.get("arguments") or {}
            return _jsonrpc_response(_rpc_result(rpc_id, _dispatch_tool(ctx, db, tool, args)))

        if method == "resources/list":
            return _jsonrpc_response(_rpc_result(rpc_id, {"resources": _resource_specs(ctx)}))

        if method == "resources/read":
            uri = (body.get("params") or {}).get("uri", "")
            return _jsonrpc_response(_rpc_result(rpc_id, _read_resource(ctx, db, uri)))

        if is_notification:
            return Response(status_code=202)
        return _jsonrpc_response(_rpc_fail(rpc_id, ERR_METHOD_NOT_FOUND, f"未知方法: {method}"))
    except _RpcError as e:
        if is_notification:
            return Response(status_code=202)
        return _jsonrpc_response(_rpc_fail(rpc_id, e.code, e.message, e.data), status=401 if e.code == ERR_UNAUTHORIZED else 200)
    except Exception as e:
        logger.exception("[mcp] 网关内部错误")
        if is_notification:
            return Response(status_code=202)
        return _jsonrpc_response(_rpc_fail(rpc_id, ERR_INTERNAL, f"内部错误：{e}"))


@router.get("/mcp")
async def mcp_get():
    """无状态实现不提供 server-initiated SSE 流（规范允许 405）。"""
    return Response(status_code=405, content="该 MCP 端点为无状态 POST-only 实现，请使用 POST /mcp",
                    media_type="text/plain")


def _jsonrpc_response(payload: dict, status: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str),
        media_type="application/json",
        status_code=status,
    )


# ---------------------------------------------------------------- 工具清单（07 §3.3 MVP 10 个）

def _tool_specs(ctx: McpContext) -> list[dict]:
    specs: list[dict] = [
        {
            "name": "search_entities",
            "description": "按关键词检索项目内实体（label/别名 子串匹配），返回实体 uri/类/属性/置信度",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词"},
                    "class_label": {"type": "string", "description": "按所属类过滤（可选）"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_entity",
            "description": "按 uri 获取实体详情：属性 + 溯源摘要（evidence/文档/char 定位）",
            "inputSchema": {
                "type": "object",
                "properties": {"uri": {"type": "string", "description": "实体 uri 或行表 id"}},
                "required": ["uri"],
            },
        },
        {
            "name": "get_neighbors",
            "description": "获取实体 1~2 跳邻域（Neo4j 只读；图库不可用时回退行表）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "hops": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                },
                "required": ["uri"],
            },
        },
        {
            "name": "query_graph",
            "description": "执行只读 Cypher（白名单：禁 CREATE/DELETE/SET/MERGE/CALL 等，超时 5s）；自动限定 project_id",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cypher": {"type": "string", "description": "只读 Cypher，可用 $project_id 参数"},
                    "params": {"type": "object", "default": {}},
                },
                "required": ["cypher"],
            },
        },
        {
            "name": "get_ontology_schema",
            "description": "获取项目 TBox：类清单 + 属性 + 关系声明（graph_data 快照）",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ask",
            "description": "基于本体的 GraphRAG 问答（向量 + 图扩展 + 溯源引用，非流式聚合）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                },
                "required": ["question"],
            },
        },
        {
            "name": "export_graph",
            "description": "导出图谱：format=turtle（TTL 内容）或 jsonld（graph_data JSON）",
            "inputSchema": {
                "type": "object",
                "properties": {"format": {"type": "string", "enum": ["turtle", "jsonld"], "default": "turtle"}},
            },
        },
        {
            "name": "list_versions",
            "description": "列出项目本体版本（版本号/标签/时间/统计）",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    if ctx.can_write:
        specs += [
            {
                "name": "add_entity",
                "description": "新增实体（editor 令牌）：label + 所属类；写入画布 blob 并自动同步行表/图库，低置信进审核队列",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "class_label": {"type": "string", "description": "所属类名称（须已在 TBox 中）"},
                        "properties": {"type": "object", "default": {}},
                    },
                    "required": ["label", "class_label"],
                },
            },
            {
                "name": "add_relationship",
                "description": "新增关系（editor 令牌）：subject/object 实体 uri + 谓词；自动同步行表/图库",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "subject_uri": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object_uri": {"type": "string"},
                        "properties": {"type": "object", "default": {}},
                    },
                    "required": ["subject_uri", "predicate", "object_uri"],
                },
            },
        ]
    return specs


def _resource_specs(ctx: McpContext) -> list[dict]:
    return [
        {"uri": "ontology://graph/summary", "name": f"项目 {ctx.project.id} 图谱规模统计",
         "description": "节点/边/类/实例计数与知识域", "mimeType": "application/json"},
        {"uri": "ontology://schema/info", "name": f"项目 {ctx.project.id} TBox（TTL）",
         "description": "本体骨架 turtle 文本", "mimeType": "text/turtle"},
    ]


# ---------------------------------------------------------------- 工具实现

def _resolve_entity(db: Session, project_id: int, uri: str) -> Entity | None:
    """uri 兼容三种形态：完整 urn、graph_data 节点 id、行表数字 id。"""
    if uri is None:
        return None
    s = str(uri).strip()
    ent = db.query(Entity).filter(Entity.project_id == project_id, Entity.uri == s).first()
    if ent:
        return ent
    node_id = s.rsplit("/", 1)[-1] if s.startswith("urn:") else s
    ent = db.query(Entity).filter(Entity.project_id == project_id, Entity.uri == f"urn:onto:{project_id}:entity/{node_id}").first()
    if ent:
        return ent
    if s.isdigit():
        return db.query(Entity).filter(Entity.project_id == project_id, Entity.id == int(s)).first()
    return None


def _t_search_entities(ctx: McpContext, db: Session, args: dict) -> dict:
    q = str(args.get("query", "")).strip()
    if not q:
        raise _rpc_error(ERR_INVALID_PARAMS, "query 不能为空")
    limit = int(args.get("limit") or 10)
    rows = (
        db.query(Entity)
        .filter(
            Entity.project_id == ctx.project.id,
            Entity.is_class_node.is_(False),
            Entity.status != "rejected",
            Entity.label_normalized.contains(q.lower()),
        )
        .limit(min(max(limit, 1), 50))
        .all()
    )
    # label 未命中时再扫别名
    if len(rows) < limit:
        alias_hit = [
            e for e in db.query(Entity)
            .filter(Entity.project_id == ctx.project.id, Entity.is_class_node.is_(False))
            .limit(500).all()
            if any(q.lower() in str(a).lower() for a in (e.aliases or []))
        ]
        seen = {e.id for e in rows}
        rows += [e for e in alias_hit if e.id not in seen]
    class_filter = args.get("class_label")
    out = []
    for e in rows[: min(max(limit, 1), 50)]:
        if class_filter and e.class_label != class_filter:
            continue
        out.append({"uri": e.uri, "label": e.label, "class_label": e.class_label,
                    "aliases": e.aliases, "confidence": float(e.confidence) if e.confidence is not None else None,
                    "status": e.status})
    return _text_content({"count": len(out), "entities": out})


def _t_get_entity(ctx: McpContext, db: Session, args: dict) -> dict:
    ent = _resolve_entity(db, ctx.project.id, args.get("uri"))
    if ent is None:
        raise _rpc_error(ERR_INVALID_PARAMS, f"实体不存在: {args.get('uri')}")
    provs = (
        db.query(ProvenanceRecord, UploadedDocument.filename)
        .join(UploadedDocument, ProvenanceRecord.source_document_id == UploadedDocument.id)
        .filter(ProvenanceRecord.project_id == ctx.project.id,
                ProvenanceRecord.target_type == "entity", ProvenanceRecord.target_id == ent.id)
        .limit(5).all()
    )
    provenance = [
        {"evidence": p.evidence_text, "document": fname, "chunk_index": p.chunk_index,
         "char_start": p.char_start, "char_end": p.char_end, "method": p.extraction_method}
        for p, fname in provs
    ]
    return _text_content({
        "uri": ent.uri, "label": ent.label, "class_label": ent.class_label,
        "aliases": ent.aliases, "properties": ent.props or {},
        "confidence": float(ent.confidence) if ent.confidence is not None else None,
        "status": ent.status, "provenance": provenance,
    })


def _t_get_neighbors(ctx: McpContext, db: Session, args: dict) -> dict:
    ent = _resolve_entity(db, ctx.project.id, args.get("uri"))
    if ent is None:
        raise _rpc_error(ERR_INVALID_PARAMS, f"实体不存在: {args.get('uri')}")
    hops = min(int(args.get("hops") or 1), 2)
    limit = min(int(args.get("limit") or 30), 100)
    node_id = ent.uri.rsplit("/", 1)[-1]
    rows: list[dict] = []
    if neo4j_client.driver is not None:
        try:
            with neo4j_client.driver.session() as session:
                tx = session.begin_transaction(timeout=5.0)
                result = tx.run(
                    f"MATCH (n {{project_id: $pid, id: $nid}})-[r*1..{hops}]-(m {{project_id: $pid}}) "
                    "RETURN n.id AS source, [rel IN r | type(rel)] AS predicates, "
                    "m.id AS target_id, m.label AS target_label LIMIT $lim",
                    {"pid": ctx.project.id, "nid": node_id, "lim": limit},
                )
                rows = result.data()
                tx.commit()
        except Exception as e:
            logger.warning(f"[mcp] Neo4j 邻域查询失败，回退行表：{e}")
    if not rows:  # 行表兜底（Neo4j 投影不可用时，事实源 relations 行表直查）
        from app.infrastructure.database import Relation
        rel_rows = (
            db.query(Relation, Entity)
            .join(Entity, (Relation.subject_id == Entity.id) | (Relation.object_id == Entity.id))
            .filter(
                Relation.project_id == ctx.project.id,
                (Relation.subject_id == ent.id) | (Relation.object_id == ent.id),
            )
            .limit(limit)
            .all()
        )
        for rel, other in rel_rows:
            if other.id == ent.id:
                continue
            rows.append({"target_id": other.uri, "target_label": other.label,
                         "predicates": [rel.predicate]})
    return _text_content({"uri": ent.uri, "label": ent.label, "hops": hops, "neighbors": rows})


def _t_query_graph(ctx: McpContext, db: Session, args: dict) -> dict:
    cypher = str(args.get("cypher", "")).strip()
    if not cypher:
        raise _rpc_error(ERR_INVALID_PARAMS, "cypher 不能为空")
    if _CYPHER_FORBIDDEN.search(cypher) or any(cypher.startswith(p) for p in _CYPHER_FORBIDDEN_PREFIX):
        _audit(ctx, db, "mcp.read", "query_graph", args, blocked=True)
        raise _rpc_error(ERR_FORBIDDEN, "只允许只读 Cypher（禁 CALL/CREATE/DELETE/SET/MERGE/REMOVE/DROP/LOAD 等）")
    params = dict(args.get("params") or {})
    params["project_id"] = ctx.project.id  # 强制项目限定，覆盖调用方同名参数
    if neo4j_client.driver is None:
        raise _rpc_error(ERR_INTERNAL, "Neo4j 不可用")
    try:
        with neo4j_client.driver.session() as session:
            tx = session.begin_transaction(timeout=5.0)
            rows = tx.run(cypher, params).data()
            tx.commit()
    except _RpcError:
        raise
    except Exception as e:
        raise _rpc_error(ERR_INVALID_PARAMS, f"Cypher 执行失败：{e}")
    _audit(ctx, db, "mcp.read", "query_graph", args)
    return _text_content({"rows": rows[:200], "row_count": len(rows)})


def _t_get_ontology_schema(ctx: McpContext, db: Session, args: dict) -> dict:
    gd = ctx.project.graph_data or {}
    classes = []
    for n in gd.get("nodes", []):
        data = n.get("data") or {}
        if data.get("type") in ("owl:Class", "Class"):
            classes.append({
                "id": n.get("id"), "label": data.get("label"),
                "raw_id": data.get("raw_id"), "properties": data.get("properties") or [],
            })
    relations = [
        {"source": e.get("source"), "target": e.get("target"),
         "predicate": (e.get("data") or {}).get("relation") or (e.get("data") or {}).get("label")}
        for e in gd.get("edges", [])
    ]
    return _text_content({"class_count": len(classes), "classes": classes, "relations": relations[:300]})


def _t_ask(ctx: McpContext, db: Session, args: dict) -> dict:
    question = str(args.get("question", "")).strip()
    if not question:
        raise _rpc_error(ERR_INVALID_PARAMS, "question 不能为空")
    cfg = build_legacy_llm_config(db, ctx.project.id)
    llm = LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"), model=cfg.get("model"))
    query = RetrievalQuery(
        project_id=ctx.project.id, question=question,
        top_k=max(1, min(int(args.get("top_k") or 12), 30)),
    )
    result = query_with_reasoning(query, db=db, llm=llm)
    _audit(ctx, db, "mcp.read", "ask", args)
    return _text_content({
        "answer": result.answer, "confidence": result.confidence,
        "sources": [s.model_dump() for s in result.sources],
        "reasoning_path": result.reasoning_path, "latency_ms": result.latency_ms,
    })


def _t_export_graph(ctx: McpContext, db: Session, args: dict) -> dict:
    fmt = str(args.get("format") or "turtle").lower()
    if fmt == "turtle":
        ttl = ctx.project.ttl_content
        if not ttl:
            raise _rpc_error(ERR_INVALID_PARAMS, "项目尚无 TTL 内容（未同步过 TTL）")
        return _text_content({"format": "turtle", "content": ttl})
    if fmt == "jsonld":
        gd = ctx.project.graph_data or {}
        return _text_content({"format": "jsonld",
                              "@context": {"onto": f"urn:onto:{ctx.project.id}:"},
                              "@graph": gd.get("nodes", [])})
    raise _rpc_error(ERR_INVALID_PARAMS, "format 仅支持 turtle|jsonld")


def _t_list_versions(ctx: McpContext, db: Session, args: dict) -> dict:
    from app.infrastructure.database import GraphSnapshot
    vers = (
        db.query(OntologyVersion)
        .filter(OntologyVersion.project_id == ctx.project.id)
        .order_by(OntologyVersion.version_no.desc())
        .limit(50).all()
    )
    return _text_content({"versions": [
        {"version_no": v.version_no, "label": v.label,
         "kind": getattr(v, "kind", None), "created_at": str(v.created_at),
         "stats": v.stats if hasattr(v, "stats") else None}
        for v in vers
    ]})


def _entity_payload(ent: Entity) -> dict:
    return {"uri": ent.uri, "label": ent.label, "class_label": ent.class_label,
            "status": ent.status}


def _t_add_entity(ctx: McpContext, db: Session, args: dict) -> dict:
    label = str(args.get("label", "")).strip()
    class_label = str(args.get("class_label", "")).strip()
    if not label or not class_label:
        raise _rpc_error(ERR_INVALID_PARAMS, "label 与 class_label 必填")
    gd = ctx.project.graph_data or {"nodes": [], "edges": []}
    nodes = gd.setdefault("nodes", [])
    # 类必须已在 TBox（防悬空类型）
    cls_node = next((n for n in nodes
                     if (n.get("data") or {}).get("label") == class_label
                     and (n.get("data") or {}).get("type") in ("owl:Class", "Class")), None)
    if cls_node is None:
        raise _rpc_error(ERR_INVALID_PARAMS, f"类「{class_label}」不在本项目 TBox 中")
    # 重复判重：同 label 同类直接返回已有实体
    dup = next((n for n in nodes
                if (n.get("data") or {}).get("label") == label
                and (n.get("data") or {}).get("class_label") == class_label
                and (n.get("data") or {}).get("type") == "owl:NamedIndividual"), None)
    if dup is not None:
        return _text_content({"created": False, "reason": "同名同类实体已存在", **_entity_payload_from_node(dup, ctx.project.id)})
    node_id = f"mcp_{secrets.token_hex(6)}"
    props = dict(args.get("properties") or {})
    node = {
        "id": node_id, "type": "custom",
        "position": {"x": 60.0 + len(nodes) * 24 % 480, "y": 60.0 + len(nodes) * 32 % 360},
        "data": {"label": label, "type": "owl:NamedIndividual", "class_label": class_label,
                 "properties": props, "_source": "mcp", "_created_by": ctx.user.username},
    }
    edge = {
        "id": f"edge_{secrets.token_hex(6)}", "source": node_id, "target": cls_node.get("id"),
        "data": {"label": "rdf:type", "relation": "instance_of"},
    }
    nodes.append(node)
    gd.setdefault("edges", []).append(edge)
    ctx.project.graph_data = gd
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(ctx.project, "graph_data")
    db.commit()  # commit 触发 M3-3 监听器：行表双写 + outbox project.graph_rebuilt
    _audit(ctx, db, "mcp.write", "add_entity", args)
    return _text_content({"created": True, "uri": f"urn:onto:{ctx.project.id}:entity/{node_id}",
                          "label": label, "class_label": class_label,
                          "note": "实体经行表双写自动同步；如为低置信/缺证据将进入审核队列"})


def _entity_payload_from_node(node: dict, project_id: int) -> dict:
    return {"uri": f"urn:onto:{project_id}:entity/{node.get('id')}",
            "label": (node.get("data") or {}).get("label"),
            "class_label": (node.get("data") or {}).get("class_label")}


def _t_add_relationship(ctx: McpContext, db: Session, args: dict) -> dict:
    subj = _resolve_entity(db, ctx.project.id, args.get("subject_uri"))
    obj = _resolve_entity(db, ctx.project.id, args.get("object_uri"))
    predicate = str(args.get("predicate", "")).strip()
    if subj is None or obj is None:
        raise _rpc_error(ERR_INVALID_PARAMS, "subject_uri/object_uri 对应实体不存在")
    if not predicate:
        raise _rpc_error(ERR_INVALID_PARAMS, "predicate 必填")
    gd = ctx.project.graph_data or {"nodes": [], "edges": []}
    edges = gd.setdefault("edges", [])
    subject_node_id = subj.uri.rsplit("/", 1)[-1]
    object_node_id = obj.uri.rsplit("/", 1)[-1]
    dup = next((e for e in edges if e.get("source") == subject_node_id
                and e.get("target") == object_node_id
                and ((e.get("data") or {}).get("relation") == predicate
                     or (e.get("data") or {}).get("label") == predicate)), None)
    if dup is not None:
        return _text_content({"created": False, "reason": "同三元组关系已存在"})
    edge = {
        "id": f"edge_{secrets.token_hex(6)}", "source": subject_node_id, "target": object_node_id,
        "data": {"label": predicate, "relation": predicate},
    }
    edges.append(edge)
    ctx.project.graph_data = gd
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(ctx.project, "graph_data")
    subj_uri, obj_uri = subj.uri, obj.uri  # commit 触发行表重建，commit 后不可再访问旧 ORM 实例
    db.commit()
    _audit(ctx, db, "mcp.write", "add_relationship", args)
    return _text_content({"created": True, "subject": subj_uri, "predicate": predicate,
                          "object": obj_uri, "note": "关系经行表双写自动同步"})


_TOOL_TABLE = {
    "search_entities": _t_search_entities,
    "get_entity": _t_get_entity,
    "get_neighbors": _t_get_neighbors,
    "query_graph": _t_query_graph,
    "get_ontology_schema": _t_get_ontology_schema,
    "ask": _t_ask,
    "export_graph": _t_export_graph,
    "list_versions": _t_list_versions,
    "add_entity": _t_add_entity,
    "add_relationship": _t_add_relationship,
}


def _dispatch_tool(ctx: McpContext, db: Session, tool: str, args: dict) -> dict:
    if tool not in _TOOL_TABLE:
        raise _rpc_error(ERR_METHOD_NOT_FOUND, f"未知工具: {tool}")
    if tool in WRITE_TOOLS:
        if not ctx.can_write:
            _audit(ctx, db, "mcp.read", tool, args, blocked=True)
            raise _rpc_error(ERR_FORBIDDEN, "该令牌为只读（viewer），无权调用写工具")
        if ctx.project.status == "published":
            _audit(ctx, db, "mcp.read", tool, args, blocked=True)
            raise _rpc_error(ERR_FORBIDDEN, "项目已发布，MCP 写操作被只读拦截（PUBLISHED_READONLY）")
    _rate_limit(ctx, tool)
    return _TOOL_TABLE[tool](ctx, db, args)


def _audit(ctx: McpContext, db: Session, action: str, tool: str, args: dict,
           blocked: bool = False) -> None:
    digest = hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
                            .encode("utf-8")).hexdigest()[:16]
    log_action(db, ctx.user.id, action, resource_type="mcp_tool",
               resource_id=tool,
               detail={"tool": tool, "args_digest": digest, "project_id": ctx.project.id,
                       "token_id": ctx.token.id, "blocked": blocked})
    db.commit()  # log_action 为同事务写入，请求会话 close 不 commit，须在此落盘


# ---------------------------------------------------------------- Resources

def _read_resource(ctx: McpContext, db: Session, uri: str) -> dict:
    if uri == "ontology://graph/summary":
        gd = ctx.project.graph_data or {}
        nodes = gd.get("nodes", [])
        edges = gd.get("edges", [])
        class_count = sum(1 for n in nodes
                          if (n.get("data") or {}).get("type") in ("owl:Class", "Class"))
        instance_count = sum(1 for n in nodes
                             if (n.get("data") or {}).get("type") == "owl:NamedIndividual")
        ent_rows = (db.query(Entity)
                    .filter(Entity.project_id == ctx.project.id, Entity.is_class_node.is_(False))
                    .count())
        return {"contents": [{
            "uri": uri, "mimeType": "application/json",
            "text": json.dumps({
                "project": {"id": ctx.project.id, "name": ctx.project.name,
                            "status": ctx.project.status},
                "knowledge_domain": ctx.project.domains,
                "nodes": len(nodes), "edges": len(edges),
                "classes": class_count, "instances": instance_count,
                "entity_rows": ent_rows,
            }, ensure_ascii=False),
        }]}
    if uri == "ontology://schema/info":
        ttl = ctx.project.ttl_content or ""
        schema_result = json.loads(_t_get_ontology_schema(ctx, db, {})["content"][0]["text"])
        if not ttl:
            ttl = f"# 项目 {ctx.project.id} 尚无同步 TTL；TBox 概要：{schema_result['class_count']} 类"
        return {"contents": [{"uri": uri, "mimeType": "text/turtle", "text": ttl[:MAX_RESPONSE_BYTES]}]}
    raise _rpc_error(ERR_INVALID_PARAMS, f"未知资源: {uri}")
