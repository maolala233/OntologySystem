// src/api/mcp.ts - MCP 控制台（R8）：令牌管理 + 真实 JSON-RPC 调用（POST /mcp 网关）
import apiClient, { API_BASE_URL, forceLogout } from './client';

// ───────────────────────── 令牌管理（admin，明文仅签发时返回一次）

export interface McpTokenItem {
    id: number;
    user_id: number;
    name: string;
    token_hint: string;
    project_id: number;
    can_write: boolean;
    expires_at: string | null;
    revoked_at: string | null;
    last_used_at: string | null;
    created_at: string | null;
}

export async function listMcpTokens(): Promise<McpTokenItem[]> {
    const resp = await apiClient.get('/api/admin/mcp-tokens');
    return resp.data.items ?? [];
}

export async function createMcpToken(body: {
    user_id: number;
    project_id: number;
    name: string;
    can_write: boolean;
    expires_days?: number | null;
}): Promise<{ token: string } & McpTokenItem> {
    const resp = await apiClient.post('/api/admin/mcp-tokens', body);
    return resp.data;
}

export async function revokeMcpToken(tokenId: number): Promise<void> {
    await apiClient.delete(`/api/admin/mcp-tokens/${tokenId}`);
}

// ───────────────────────── JSON-RPC 客户端（MCP HTTP Streamable，无状态 POST）

export interface RpcResponse {
    result?: Record<string, unknown>;
    error?: { code: number; message: string; data?: unknown } | null;
}

export class McpRpcError extends Error {
    code: number;
    data?: unknown;
    constructor(code: number, message: string, data?: unknown) {
        super(message);
        this.code = code;
        this.data = data;
    }
}

export interface McpToolSpec {
    name: string;
    description?: string;
    inputSchema?: {
        type?: string;
        properties?: Record<string, {
            type?: string;
            description?: string;
            enum?: string[];
            default?: unknown;
        }>;
        required?: string[];
    };
}

let rpcIdCounter = 1;

/** 调用 MCP 网关（initialize / ping / tools/list / tools/call / resources/list）。 */
export async function mcpRpc(
    bearerToken: string,
    method: string,
    params?: Record<string, unknown>,
): Promise<RpcResponse['result']> {
    const resp = await fetch(`${API_BASE_URL}/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${bearerToken}` },
        body: JSON.stringify({ jsonrpc: '2.0', id: rpcIdCounter++, method, params: params ?? {} }),
    });
    if (resp.status === 401) {
        forceLogout();
        throw new McpRpcError(-32000, '登录态失效，请重新登录');
    }
    const data = await resp.json().catch(() => null);
    if (!data) throw new McpRpcError(-32700, `网关响应不是合法 JSON（HTTP ${resp.status}）`);
    if (data.error) throw new McpRpcError(data.error.code, data.error.message, data.error.data);
    return data.result ?? {};
}

/** tools/list 返回的工具清单 */
export function toolsFromResult(result: Record<string, unknown> | undefined): McpToolSpec[] {
    return (result?.tools as McpToolSpec[]) ?? [];
}

/** tools/call 返回的 content[] 中提取文本 */
export function textFromCallResult(result: Record<string, unknown> | undefined): string {
    const content = (result?.content as { type?: string; text?: string }[]) ?? [];
    return content.filter(c => c.type === 'text').map(c => c.text ?? '').join('\n') || JSON.stringify(result, null, 2);
}
