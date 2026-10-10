// src/api/qa.ts - 本体问答 API（M5，docs/design/03 §15 / 07 §4）
// SSE 流式（POST fetch + ReadableStream 解析 token/sources/done/error 事件）
// + 历史 / 引用溯源详情端点。鉴权与 401 刷新复用 client.ts 的 token 约定。
import { API_BASE_URL, refreshAccessToken, forceLogout } from './client';

// ───────────────────────── 类型（03 §15 契约）

export interface QaSource {
    doc_file: string;
    quote: string;
    char_start: number | null;
    char_end: number | null;
    ref_type: 'vector_chunk' | 'graph_edge' | 'graph_node' | 'keyword_chunk' | 'inference';
    score: number;
    source_document_id: number | null;
    /** 溯源详情端点附加字段 */
    doc_name?: string;
    doc_url?: string;
}

export interface QaDonePayload {
    answer: string;
    confidence: number;
    reasoning_path: string[];
    model: string | null;
    latency_ms: number;
    history_id: number;
    conversation_id?: string;
}

export interface QaHistoryItem {
    id: number;
    question: string;
    answer: string;
    sources: QaSource[];
    model: string | null;
    latency_ms: number;
    confidence: number | null;
    created_at: string | null;
}

// ───────────────────────── 鉴权头（含单次刷新）

async function authHeaders(): Promise<HeadersInit> {
    const token = localStorage.getItem('access_token');
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
}

// ───────────────────────── SSE 流式问答（07 §4：token → sources → done）

export interface AskStreamHandlers {
    onMeta?: (meta: { source_count: number }) => void;
    onToken: (delta: string) => void;
    onSources?: (sources: QaSource[]) => void;
    onDone?: (payload: QaDonePayload) => void;
    onError?: (message: string) => void;
}

export interface AskOptions {
    top_k?: number;
    use_graph?: boolean;
    use_inferred?: boolean;
    knowledge_domain?: string | null;
    conversation_id?: string | null;
    model_config_id?: number | null;
    signal?: AbortSignal;
}

export async function askQuestionStream(
    projectId: number,
    question: string,
    handlers: AskStreamHandlers,
    options?: AskOptions,
): Promise<void> {
    const resp = await fetch(`${API_BASE_URL}/api/projects/${projectId}/qa`, {
        method: 'POST',
        headers: await authHeaders(),
        body: JSON.stringify({
            question,
            stream: true,
            top_k: options?.top_k ?? 12,
            use_graph: options?.use_graph ?? true,
            use_inferred: options?.use_inferred ?? true,
            ...(options?.knowledge_domain ? { knowledge_domain: options.knowledge_domain } : {}),
            ...(options?.conversation_id ? { conversation_id: options.conversation_id } : {}),
            ...(options?.model_config_id ? { model_config_id: options.model_config_id } : {}),
        }),
        signal: options?.signal,
    });

    if (resp.status === 401) {
        const newToken = await refreshAccessToken();
        if (newToken) {
            return askQuestionStream(projectId, question, handlers, options);
        }
        forceLogout();
        return;
    }
    if (resp.status === 403) {
        handlers.onError?.('未开通问答模块或无该项目访问权限');
        return;
    }
    if (!resp.ok || !resp.body) {
        let detail = `HTTP ${resp.status}`;
        try {
            const err = await resp.json();
            detail = err?.error?.message || err?.detail || detail;
        } catch { /* 保留 HTTP 状态码 */ }
        handlers.onError?.(detail);
        return;
    }

    // 解析 text/event-stream：事件以空行分隔，行格式 `event: <name>` / `data: <json>`
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() ?? '';
        for (const block of blocks) {
            const parsed = parseSseBlock(block);
            if (!parsed) continue;
            const { event, data } = parsed;
            if (event === 'token') handlers.onToken(data.delta ?? '');
            else if (event === 'meta') handlers.onMeta?.(data);
            else if (event === 'sources') handlers.onSources?.(data.sources ?? []);
            else if (event === 'done') handlers.onDone?.(data);
            else if (event === 'error') handlers.onError?.(data.message ?? '问答服务异常');
        }
    }
}

function parseSseBlock(block: string): { event: string; data: any } | null {
    let event = '';
    let dataRaw = '';
    for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        else if (line.startsWith('data: ')) dataRaw += line.slice(6);
        else if (line.startsWith('data:')) dataRaw += line.slice(5);
    }
    if (!event) return null;
    try {
        return { event, data: JSON.parse(dataRaw) };
    } catch {
        return { event, data: {} };
    }
}

// ───────────────────────── 历史 / 溯源详情（非流式走 axios 拦截器）

import apiClient from './client';

export async function getQaHistory(projectId: number, limit = 20): Promise<QaHistoryItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/qa/history`, { params: { limit } });
    return resp.data.items ?? [];
}

export async function askQuestionJson(
    projectId: number,
    question: string,
    options?: AskOptions,
): Promise<{ answer: string; sources: QaSource[]; confidence: number; reasoning_path: string[]; model: string | null; latency_ms: number; history_id: number }> {
    const resp = await apiClient.post(`/api/projects/${projectId}/qa`, {
        question,
        stream: false,
        top_k: options?.top_k ?? 12,
        use_graph: options?.use_graph ?? true,
        use_inferred: options?.use_inferred ?? true,
        ...(options?.knowledge_domain ? { knowledge_domain: options.knowledge_domain } : {}),
    });
    return resp.data;
}

export interface QaSourceDetail {
    id: number;
    question: string;
    answer: string;
    sources: QaSource[];
    created_at: string | null;
}

export async function getQaSourceDetail(projectId: number, refId: number): Promise<QaSourceDetail> {
    const resp = await apiClient.get(`/api/projects/${projectId}/qa/sources/${refId}`);
    return resp.data;
}

// ───────────────────────── 对话分组与模型选择（R8：QA 独立页面）

export interface QaConversationItem {
    id: string;
    title: string;
    message_count: number;
    first_time: string | null;
    last_time: string | null;
    last_answer: string;
}

export interface QaConversationDetail {
    id: string;
    title: string;
    messages: QaHistoryItem[];
}

export interface QaModelItem {
    id: number;
    name: string;
    provider: string;
    model_name: string;
    scope: 'global' | 'project';
    is_default: boolean;
}

export async function listQaConversations(projectId: number, limit = 50): Promise<QaConversationItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/qa/conversations`, { params: { limit } });
    return resp.data.items ?? [];
}

export async function getQaConversation(projectId: number, conversationId: string): Promise<QaConversationDetail> {
    const resp = await apiClient.get(`/api/projects/${projectId}/qa/conversations/${conversationId}`);
    return resp.data;
}

export async function deleteQaConversation(projectId: number, conversationId: string): Promise<void> {
    await apiClient.delete(`/api/projects/${projectId}/qa/conversations/${conversationId}`);
}

export async function listQaModels(projectId: number): Promise<QaModelItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/qa/models`);
    return resp.data.items ?? [];
}
