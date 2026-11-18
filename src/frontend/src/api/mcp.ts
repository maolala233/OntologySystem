// src/api/mcp.ts - MCP 控制台（R8）：令牌管理 + 真实 JSON-RPC 调用（POST /mcp 网关）
import apiClient, { API_BASE_URL } from './client';

// ───────────────────────── 令牌管理（admin 端 /api/admin/mcp-tokens + 自助端 /api/mcp-tokens，
// ───────────────────────── 明文仅签发时返回一次）

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

// 自助端：登录用户只能看到/签发/撤销自己的令牌（持有人固定为当前用户）

export async function listMyMcpTokens(): Promise<McpTokenItem[]> {
    const resp = await apiClient.get('/api/mcp-tokens');
    return resp.data.items ?? [];
}

export async function createMyMcpToken(body: {
    project_id: number;
    name: string;
    can_write: boolean;
    expires_days?: number | null;
}): Promise<{ token: string } & McpTokenItem> {
    const resp = await apiClient.post('/api/mcp-tokens', body);
    return resp.data;
}

export async function revokeMyMcpToken(tokenId: number): Promise<void> {
    await apiClient.delete(`/api/mcp-tokens/${tokenId}`);
}

// ───────────────────────── MCP 端点（默认按页面地址自动推导；可被 localStorage 覆盖，
// ───────────────────────── 适配容器化/反代部署：端口映射改变或同域代理时在控制台改一次即可）

// ─────── 明文令牌（后端只存 SHA-256，明文仅在签发时返回一次）
// 存 sessionStorage：只活在当前标签页生命周期内，关掉标签页即消失。
// 不用 localStorage——它对任何 XSS 都是全暴露的，而明文等价于该项目的 MCP 访问权；
// 旧版本残留的 localStorage 副本在读写时顺手清掉（2026-11-18）。
const plainKey = (tokenId: number) => `mcp_plain_${tokenId}`;

export function savePlainToken(tokenId: number, plain: string): void {
    try {
        sessionStorage.setItem(plainKey(tokenId), plain);
        localStorage.removeItem(plainKey(tokenId)); // 迁移旧数据
    } catch { /* 隐私模式等存储不可用：退化为本页不可恢复明文 */ }
}

export function getPlainToken(tokenId: number): string {
    try {
        const current = sessionStorage.getItem(plainKey(tokenId));
        if (current) return current;
        // 旧版本把明文存在 localStorage：读到就搬进 sessionStorage 并删掉旧副本
        const legacy = localStorage.getItem(plainKey(tokenId));
        if (legacy) {
            sessionStorage.setItem(plainKey(tokenId), legacy);
            localStorage.removeItem(plainKey(tokenId));
            return legacy;
        }
        return '';
    } catch {
        return '';
    }
}

export function dropPlainToken(tokenId: number): void {
    try {
        sessionStorage.removeItem(plainKey(tokenId));
        localStorage.removeItem(plainKey(tokenId));
    } catch { /* ignore */ }
}
export const MCP_ENDPOINT_KEY = 'mcp_endpoint';

/** 默认端点：host 随浏览器地址栏（部署 IP/域名）。同域部署时 API_BASE_URL 为空，
 *  取页面 origin 补全为绝对地址——相对路径 /mcp 在本页可用，但外部 MCP 客户端需要完整 URL。 */
export function getDefaultMcpEndpoint(): string {
    if (API_BASE_URL) {
        return `${API_BASE_URL}/mcp`;
    }
    return `${window.location.origin}/mcp`;
}

/** 实际生效端点：localStorage 覆盖值 > 默认推导；历史保存的相对路径同样补全为绝对地址 */
export function getMcpEndpoint(): string {
    const saved = localStorage.getItem(MCP_ENDPOINT_KEY)?.trim();
    if (saved) {
        return saved.startsWith('/') ? `${window.location.origin}${saved}` : saved;
    }
    return getDefaultMcpEndpoint();
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

/** 调用 MCP 网关（initialize / ping / tools/list / tools/call / resources/list）。
 *  超时：网关无响应时 20s 中止，避免连接按钮一直挂着。
 *  401 的语义：网关只认 sk-mcp-* 令牌，401 表示**这个 MCP 令牌**无效/过期/被撤销，
 *  与平台登录态无关——不能 forceLogout，否则令牌一过期整站被踢到登录页（2026-11-18）。
 *  错误按 JSON-RPC 的 error 原样抛出，由调用方展示。 */
export async function mcpRpc(
    bearerToken: string,
    method: string,
    params?: Record<string, unknown>,
    endpoint?: string,
    timeoutMs = 20_000,
): Promise<RpcResponse['result']> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let resp: Response;
    try {
        resp = await fetch(endpoint ?? getMcpEndpoint(), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${bearerToken}` },
            body: JSON.stringify({ jsonrpc: '2.0', id: rpcIdCounter++, method, params: params ?? {} }),
            signal: controller.signal,
        });
    } catch (e: any) {
        if (e?.name === 'AbortError') throw new McpRpcError(-32000, `网关 ${Math.round(timeoutMs / 1000)}s 内无响应`);
        throw new McpRpcError(-32000, `无法连接 MCP 网关：${e?.message || e}`);
    } finally {
        clearTimeout(timer);
    }
    const data = await resp.json().catch(() => null);
    if (!data) throw new McpRpcError(-32700, `网关响应不是合法 JSON（HTTP ${resp.status}）`);
    if (data.error) throw new McpRpcError(data.error.code, data.error.message, data.error.data);
    if (!resp.ok) throw new McpRpcError(-32000, `网关返回 HTTP ${resp.status}`);
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
