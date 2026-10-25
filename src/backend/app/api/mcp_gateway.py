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
    Relation,
    Project,
    ProvenanceRecord,
    ReasoningResult,
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
            "description": "执行只读 Cypher（白名单：禁 CREATE/DELETE/SET/MERGE/CALL 等，超时 5s）；自动限定 project_id。include_inferred=true 时附带语义推理衍生三元组（独立 inferred 字段，非 Cypher 查询结果）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cypher": {"type": "string", "description": "只读 Cypher，可用 $project_id 参数"},
                    "params": {"type": "object", "default": {}},
                    "include_inferred": {"type": "boolean", "default": False,
                                         "description": "附带最新推理批次的衍生三元组（语义推理产物，标注推导方式，至多 50 条）"},
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
        {
            "name": "resolve_semantics",
            "description": "S19 语义解析（运行时可信核心）：按提及词解析概念，返回实体+适用公理/约束+溯源证据；"
                           "每条带 coverage_status（supported/ambiguous/unavailable），消歧失败返回候选绝不猜；"
                           "硬上限 mentions<=5、禁全量枚举。先 read ontology://session-manifest 再用本工具",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mentions": {"type": "array", "items": {"type": "string"}, "maxItems": 5,
                                 "description": "实体/术语提及词列表（最多 5 个）"},
                    "context": {"type": "string", "description": "当前任务上下文（可选，辅助消歧）"},
                },
                "required": ["mentions"],
            },
        },
        {
            "name": "precheck_action",
            "description": "S24 执行前合规校验（金融复杂操作可信执行）：提交拟执行的业务操作"
                           "（add_fact/add_entity），平台用 TBox/公理/指标口径做确定性机检，"
                           "返回 verdict（allow/warn/block）+ 命中清单（含指标限额口径）。"
                           "block=不得执行；写工具 add_* 内置同源检查不可绕过",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["add_fact", "add_entity"]},
                    "subject_label": {"type": "string", "description": "add_fact 主语（实例/类名）"},
                    "predicate": {"type": "string", "description": "add_fact 谓词"},
                    "object_label": {"type": "string", "description": "add_fact 宾语"},
                    "class_label": {"type": "string", "description": "add_entity 所属类"},
                    "properties": {"type": "object", "description": "add_entity 属性（可选）"},
                },
                "required": ["kind"],
            },
        },
        {
            "name": "check_answerable",
            "description": "S19 答案性预检（确定性、无 LLM）：问题术语与本体词汇的命中覆盖分级 "
                           "（no/partial/likely）+ 命中清单；覆盖不足时不要硬答，改走澄清或提示构建",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string", "minLength": 1},
                               "context": {"type": "string"}},
                "required": ["question"],
            },
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
        {"uri": "ontology://session-manifest", "name": f"项目 {ctx.project.id} 会话清单（S19）",
         "description": "会话初始化只注入本清单：版本+计数+认证统计+工具签名+使用指引；"
                        "具体语义按需 resolve_semantics", "mimeType": "application/json"},
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
    # S23 数据级授权：受限令牌按类黑名单裁剪（被裁条目留审计）
    from app.services.data_auth import audit_trim, scope_for

    scope = scope_for(db, ctx.user)
    if scope.active:
        before = len(rows)
        rows = [e for e in rows if scope.class_allowed(e.class_label)]
        if len(rows) != before:
            audit_trim(db, ctx.user.id, "mcp.search_entities",
                       {"trimmed": before - len(rows)})
            db.commit()
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
    from app.services.data_auth import scope_for

    _scope = scope_for(db, ctx.user)
    return _text_content({
        "uri": ent.uri, "label": ent.label, "class_label": ent.class_label,
        "aliases": ent.aliases, "properties": _scope.filter_props(ent.props),
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
    out: dict = {"rows": rows[:200], "row_count": len(rows)}
    # 推理衍生三元组（可选）：独立 inferred 字段返回，不与 Cypher 查询结果混排——
    # 语义推理产物不得冒充事实，供调用方在答案中显式区分来源
    if args.get("include_inferred"):
        try:
            from app.api.reports import _inference_origin, _inference_rows

            inferred_rows = _inference_rows(db, ctx.project.id)
            out["inferred"] = [
                {"subject": r.subject_label, "predicate": r.predicate_label,
                 "object": r.object_label, "origin": _inference_origin(r)}
                for r in inferred_rows[:50]
            ]
            out["inferred_count"] = len(inferred_rows)
            out["inference_note"] = "inferred 为语义推理产物（本体公理/规则推导），非原始事实记载"
        except Exception as e:
            logger.warning(f"[mcp] 推理三元组附带失败，忽略：{e}")
    return _text_content(out)


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
    # S24：执行前同源合规校验（不可绕过）
    from app.services.action_gate import precheck_action

    _pre = precheck_action(db, ctx.project, {
        "kind": "add_entity",
        "class_label": str(args.get("class_label") or ""),
        "properties": args.get("properties") or {},
    })
    if _pre["verdict"] == "block":
        _audit(ctx, db, "mcp.write", "add_entity",
               {"blocked": True, "hits": _pre["hits"][:5]}, blocked=True)
        raise _rpc_error(ERR_FORBIDDEN,
                         "操作被合规校验拦截（block）：" + "；".join(
                             h.get("message", "") for h in _pre["hits"][:3]))
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
    # S24：执行前同源合规校验（不可绕过——与 precheck_action 同一实现）
    from app.services.action_gate import precheck_action

    _pre = precheck_action(db, ctx.project, {
        "kind": "add_fact",
        "subject_label": str(args.get("subject_uri") or ""),
        "predicate": str(args.get("predicate") or ""),
        "object_label": str(args.get("object_uri") or ""),
    })
    if _pre["verdict"] == "block":
        _audit(ctx, db, "mcp.write", "add_relationship",
               {"blocked": True, "hits": _pre["hits"][:5]}, blocked=True)
        raise _rpc_error(ERR_FORBIDDEN,
                         "操作被合规校验拦截（block）：" + "；".join(
                             h.get("message", "") for h in _pre["hits"][:3]))
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



# ---------------------------------------------------------------- S19 语义服务面
# 匹配/公理/证据实现抽取到 app/services/semantic_index.py（S20-0：MCP 与 QA Tier0 共用）
from app.services.semantic_index import (  # noqa: E402
    AXIOM_CAP as _AXIOM_CAP,
    EVIDENCE_CAP as _EVIDENCE_CAP,
    MENTION_MAX as _MENTION_MAX,
    build_index as _semantic_index,
    entity_axioms as _entity_axioms,
    entity_evidence as _entity_evidence,
    grade_coverage as grade_coverage,
    match_mention as _match_mention,
)

_SEMANTIC_TOOL_NAMES = ["resolve_semantics", "check_answerable", "ask", "get_ontology_schema"]


def _t_resolve_semantics(ctx: McpContext, db: Session, args: dict) -> dict:
    """S19-2：语义解析——按提及词返回实体+适用公理+证据，带诚实信号与硬上限。"""
    mentions = args.get("mentions")
    if not isinstance(mentions, list) or not mentions:
        raise _rpc_error(ERR_INVALID_PARAMS, "mentions 必须是非空数组（<=5）")
    context = str(args.get("context") or "")
    mentions = [str(m) for m in mentions][:_MENTION_MAX]
    skipped = [str(m) for m in (args.get("mentions") or [])[len(mentions):]]

    index = _semantic_index(db, ctx.project.id)
    gd = ctx.project.graph_data if isinstance(ctx.project.graph_data, dict) else {}
    # S23：受限令牌——类黑名单内的概念按 unavailable 呈现（不暴露其存在）
    from app.services.data_auth import scope_for

    _scope = scope_for(db, ctx.user)
    if _scope.active:
        index = [e for e in index if _scope.class_allowed(e.class_label)]
    # S24：指标名（label/别名不存在，label 精确）优先于实体解析——口径的单一权威来源
    from app.services.metrics_store import find_metrics_for

    metric_by_label = {str(x.get("label") or ""): x for x in find_metrics_for(gd)}
    results = []
    for m in mentions:
        if m.strip().lower() in {k.lower() for k in metric_by_label}:
            hit = next(v for k, v in metric_by_label.items()
                       if k.lower() == m.strip().lower())
            results.append({
                "mention": m, "coverage_status": "supported", "matched_via": "exact_metric",
                "metric": {"label": hit.get("label"),
                           "definition": hit.get("definition") or "",
                           "expression": hit.get("expression") or "",
                           "unit": hit.get("unit") or "",
                           "applies_to": hit.get("applies_to") or [],
                           "severity": hit.get("severity") or "warn",
                           "certified": bool(hit.get("certified"))},
                "axioms": [], "evidence": [],
                "note": "治理指标：口径的单一权威来源；执行涉及其适用类的操作前先过 precheck_action",
            })
            continue
        match = _match_mention(index, m, context)
        if match["status"] == "unavailable":
            results.append({"mention": m, "coverage_status": "unavailable",
                            "note": "本本体未覆盖该概念：不要编造，改走澄清或提示构建"})
            continue
        cands = match["candidates"]
        if match["status"] == "ambiguous":
            results.append({"mention": m, "coverage_status": "ambiguous",
                            "candidates": [{"uri": e.uri, "label": e.label,
                                            "kind": "class" if e.is_class_node else "instance",
                                            "class_label": e.class_label} for e in cands],
                            "note": "多个候选，请根据 context 指定；语义服务不替你猜"})
            continue
        e = cands[0]
        canvas_id = e.uri.rsplit("/", 1)[-1] if e.uri.startswith("urn:") else str(e.id)
        results.append({
            "mention": m, "coverage_status": match["status"], "matched_via": match["via"],
            "entity": {"uri": e.uri, "label": e.label,
                       "kind": "class" if e.is_class_node else "instance",
                       "class_label": e.class_label, "aliases": e.aliases,
                       "source_type": e.source_type, "certified": bool(e.certified),
                       "confidence": float(e.confidence) if e.confidence is not None else None},
            "axioms": _entity_axioms(gd, canvas_id, e.label),
            "evidence": _entity_evidence(db, ctx.project.id, e.id),
        })
    payload = {"results": results, "skipped": skipped,
               "note": "resolved 语义是引导不是最终答案：执行动作前须按返回的公理/约束校验；"
                       "unavailable/ambiguous 时不要编造。"}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
        payload = {"results": [], "skipped": skipped, "note": "响应超限，请减少 mentions"}
    return _text_content(payload)


def _t_check_answerable(ctx: McpContext, db: Session, args: dict) -> dict:
    """S19-3：答案性预检（确定性、无 LLM）——问题与本体词汇命中分级，诚实不超claim。"""
    question = str(args.get("question") or "").strip()
    if not question:
        raise _rpc_error(ERR_INVALID_PARAMS, "question 不能为空")
    index = _semantic_index(db, ctx.project.id)
    gd = ctx.project.graph_data if isinstance(ctx.project.graph_data, dict) else {}
    grade = grade_coverage(index, gd, question)
    matched = grade["matched"]
    n = grade["matched_count"]
    coverage = grade["coverage"]
    return _text_content({
        "coverage": coverage, "matched_entities": matched[:10], "matched_count": n,
        "note": "确定性预检只覆盖术语命中；实际可答性以检索与生成结果为准。"
                "coverage=no 时不要硬答：改走澄清或提示补建本体。",
    })


def _t_precheck_action(ctx: McpContext, db: Session, args: dict) -> dict:
    """S24-3：执行前合规校验（确定性、无 LLM、只读不落库）。"""
    from app.services.action_gate import precheck_action

    result = precheck_action(db, ctx.project, args)
    _audit(ctx, db, "mcp.precheck", "precheck_action",
           {"kind": args.get("kind"), "verdict": result.get("verdict")})
    return _text_content(result)


def _session_manifest(ctx: McpContext, db: Session) -> dict:
    """S19-1 会话清单：会话初始化只注入摘要，具体语义按需 resolve。"""
    from app.services.axiom_store import list_axioms

    pid = ctx.project.id
    classes = (db.query(Entity).filter(Entity.project_id == pid,
                                       Entity.is_class_node.is_(True)).count())
    instances = (db.query(Entity).filter(Entity.project_id == pid,
                                         Entity.is_class_node.is_(False)).count())
    relations = (db.query(Relation).filter(Relation.project_id == pid).count())
    certified_ents = (db.query(Entity).filter(Entity.project_id == pid,
                                              Entity.certified.is_(True)).count())
    axioms: list = []
    try:
        axioms = list_axioms(ctx.project)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[mcp] manifest 公理统计失败：{exc}")
    rules = 0
    try:
        from app.infrastructure.database import OntologyRule

        rules = (db.query(OntologyRule).filter(OntologyRule.project_id == pid).count())
    except Exception:  # noqa: BLE001
        pass
    from app.adapters.versioning import latest_official_version

    ver = latest_official_version(db, pid)
    return {
        "project": {"id": pid, "name": ctx.project.name, "status": ctx.project.status},
        "ontology_version": {"version_no": ver.version_no if ver else None,
                             "published": ctx.project.status == "published"},
        "counts": {"classes": classes, "instances": instances,
                   "relations": relations, "axioms": len(axioms),
                   "axioms_certified": sum(1 for a in axioms if a.get("certified")),
                   "rules": rules, "entities_certified": certified_ents},
        "tools": _SEMANTIC_TOOL_NAMES,
        "recommended_sequence": ["resources/read session-manifest（本清单）",
                                 "resolve_semantics（按需取语义）",
                                 "check_answerable（执行前确认覆盖）",
                                 "执行动作前按公理/约束校验"],
        "guidance": "resolved 语义是引导不是最终答案：执行动作前须按公理/约束校验；"
                    "unavailable/ambiguous 时不要编造，改走澄清；"
                    "本清单只含摘要，定义与证据按需检索。",
    }



# S19：语义工具注册（定义于上方，动态补入工具表）
_TOOL_TABLE["resolve_semantics"] = _t_resolve_semantics
_TOOL_TABLE["check_answerable"] = _t_check_answerable
_TOOL_TABLE["precheck_action"] = _t_precheck_action


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
    if uri == "ontology://session-manifest":
        return {"contents": [{
            "uri": uri, "mimeType": "application/json",
            "text": json.dumps(_session_manifest(ctx, db), ensure_ascii=False),
        }]}
    if uri == "ontology://schema/info":
        ttl = ctx.project.ttl_content or ""
        schema_result = json.loads(_t_get_ontology_schema(ctx, db, {})["content"][0]["text"])
        if not ttl:
            ttl = f"# 项目 {ctx.project.id} 尚无同步 TTL；TBox 概要：{schema_result['class_count']} 类"
        return {"contents": [{"uri": uri, "mimeType": "text/turtle", "text": ttl[:MAX_RESPONSE_BYTES]}]}
    raise _rpc_error(ERR_INVALID_PARAMS, f"未知资源: {uri}")
