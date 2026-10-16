// src/api/copilot.ts - Copilot 人机协同 API（O5-B22/F12，docs/design/14 §5 S1）
// SSE 流式（POST fetch + ReadableStream 解析 stage/token/done/error 事件）
// + 会话列表/回放/删除 + 模型选择。鉴权与 401 刷新复用 client.ts 约定。
import { API_BASE_URL, refreshAccessToken, forceLogout } from './client';

// ───────────────────────── 类型

export interface CopilotToolCall {
    round: number;
    tool: string;
    args: Record<string, unknown>;
    /** B31：工具执行耗时（毫秒），回放/实时步骤展示用 */
    duration_ms?: number | null;
    observation_keys?: string[];
    /** B24：generate_proposal 调用携带提案 observation（F13 回放对齐状态用） */
    observation?: Record<string, unknown>;
}

/** B31/F16：工具步骤 SSE 事件（phase=start 开始执行 / end 带耗时与观察输出） */
export interface CopilotToolEvent {
    phase: 'start' | 'end' | string;
    tool: string;
    args?: Record<string, unknown>;
    round?: number;
    duration_ms?: number | null;
    observation?: Record<string, unknown>;
}

export interface CopilotStage {
    stage: 'context' | 'tool' | 'answer' | string;
    detail: string;
}

/** diff 条目（服务端确定性生成，op: remove/add/update/merge_into） */
export interface CopilotDiffEntry {
    op: 'remove' | 'add' | 'update' | 'merge_into' | string;
    type?: string;
    label?: string;
    detail?: string;
    cascade?: { instances?: number; relations?: number };
}

/** 编辑提案（B24 生成 / B25 执行 / F13 渲染，docs/design/14 §3.1 契约） */
export interface CopilotProposalItem {
    proposal_id: string;
    kind: string;
    targets: Record<string, unknown>;
    diff_preview: CopilotDiffEntry[] | null;
    impact: { instances: number; relations: number; review_items: number } | null;
    llm_reason: string;
    evidence: string[];
    risk_tier: 'low' | 'high' | string;
    status: 'pending' | 'applied' | 'rejected' | 'expired' | string;
    /** S8：校验警告（同谓词多定义/同方向语义重复/属性类型为建议值等），不阻断应用 */
    warnings?: string[] | null;
    base_version?: number | null;
    result?: Record<string, unknown> | null;
    session_id?: number | null;
}

/** 澄清轮（B26/F14）：SSE clarify 事件与 done.clarify 的统一形态 */
export interface CopilotClarify {
    question: string;
    options: string[];
    round: number;
    exhausted?: boolean;
    trigger?: string;
}

export interface CopilotDonePayload {
    answer: string;
    tool_calls: CopilotToolCall[];
    latency_ms: number;
    rounds: number;
    intent?: string;
    /** B24：编辑意图时随 done 返回的提案（F13 渲染提案卡） */
    proposal?: CopilotProposalItem | null;
    proposal_errors?: string[];
    /** B26：澄清态随 done 返回（F14 渲染选项按钮组） */
    clarify?: CopilotClarify | null;
    /** S6：只读项目编辑诉求 + 用户有取消发布权 → 前端渲染「取消发布并调整」按钮 */
    unpublish_offer?: boolean;
    /** B30：只读项目编辑诉求的确定性指引 */
    readonly?: boolean;
    /** B28：verify 意图随 done 返回的验证报告（F15 渲染三段报告卡） */
    verify?: CopilotVerifyReport | null;
}

/** B28：验证报告（三段：CQ 评测 / 一致性 / 抽样溯源 + 与上一版对比） */
export interface CopilotVerifyReport {
    overall: 'pass' | 'warn' | 'fail' | string;
    generated_from?: string;
    cq: {
        total?: number; answerable?: number; coverage_rate?: number | null;
        failed?: { question: string; source_count: number }[];
        note?: string; error?: string;
    };
    consistency: {
        owlrl_errors?: number; error_samples?: Record<string, any>[];
        inferred_count?: number; shacl_violations?: number | null;
        shacl_by_kind?: Record<string, number>; shacl_error?: string;
    };
    sample: {
        sampled?: number; supported?: number; partial?: number; unsupported?: number;
        support_rate?: number | null; note?: string;
        samples?: { type: string; target: string; verdict: string; reason: string;
                    evidence_count: number; quote?: string | null }[];
    };
    comparison?: {
        metrics?: Record<string, { previous: number; current: number; delta: number }>;
        newly_failed_cqs?: string[];
        resolved_cqs?: string[];
    };
    archived_version_no?: number | null;
}

export interface CopilotMessageItem {
    id: number;
    role: 'user' | 'assistant';
    kind: string;
    content: string;
    stages: CopilotStage[];
    tool_calls: CopilotToolCall[];
    report?: CopilotVerifyReport | null;
    status: string;
    latency_ms: number | null;
    created_at: string | null;
}

export interface CopilotSessionItem {
    id: number;
    title: string;
    message_count: number;
    model_config_id: number | null;
    created_at: string | null;
    updated_at: string | null;
}

export interface CopilotModelItem {
    id: number;
    name: string;
    provider: string;
    model_name: string;
    scope: 'global' | 'project';
    is_default: boolean;
}

/** 画布选中态（F12：构建器内选中节点/边随消息携带） */
export interface CopilotSelection {
    nodes: { id: string; label: string }[];
    edges: { id: string; label: string }[];
}

async function authHeaders(): Promise<HeadersInit> {
    const token = localStorage.getItem('access_token');
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
}

// ───────────────────────── SSE 流式对话

export interface CopilotStreamHandlers {
    onMeta?: (meta: { session_id: number; project_id: number; project_name: string }) => void;
    onStage?: (stage: CopilotStage) => void;
    onToken: (delta: string) => void;
    /** B31/F16：工具调用步骤（实时） */
    onTool?: (ev: CopilotToolEvent) => void;
    onDone?: (payload: CopilotDonePayload) => void;
    onError?: (message: string) => void;
}

export interface CopilotChatOptions {
    session_id?: number | null;
    model_config_id?: number | null;
    selected?: CopilotSelection | null;
    signal?: AbortSignal;
}

export async function sendCopilotMessage(
    projectId: number,
    message: string,
    handlers: CopilotStreamHandlers,
    options?: CopilotChatOptions,
): Promise<void> {
    const resp = await fetch(`${API_BASE_URL}/api/projects/${projectId}/copilot/chat`, {
        method: 'POST',
        headers: await authHeaders(),
        body: JSON.stringify({
            message,
            ...(options?.session_id ? { session_id: options.session_id } : {}),
            ...(options?.model_config_id ? { model_config_id: options.model_config_id } : {}),
            ...(options?.selected ? { selected: options.selected } : {}),
        }),
        signal: options?.signal,
    });

    if (resp.status === 401) {
        const newToken = await refreshAccessToken();
        if (newToken) {
            return sendCopilotMessage(projectId, message, handlers, options);
        }
        forceLogout();
        return;
    }
    if (resp.status === 403) {
        handlers.onError?.('未开通「实体消解与冲突」模块或无该项目访问权限');
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
            if (event === 'meta') handlers.onMeta?.(data);
            else if (event === 'stage') handlers.onStage?.(data);
            else if (event === 'tool') handlers.onTool?.(data);
            else if (event === 'token') handlers.onToken(data.delta ?? '');
            else if (event === 'done') handlers.onDone?.(data);
            else if (event === 'error') handlers.onError?.(data.message ?? 'Copilot 服务异常');
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

// ───────────────────────── 会话与模型（非流式走 axios）

import apiClient from './client';

export async function listCopilotSessions(projectId: number, limit = 50): Promise<CopilotSessionItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/sessions`, { params: { limit } });
    return resp.data.items ?? [];
}

export interface CopilotSessionDetail {
    id: number;
    title: string;
    model_config_id: number | null;
    messages: CopilotMessageItem[];
}

export async function getCopilotSession(projectId: number, sessionId: number): Promise<CopilotSessionDetail> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/sessions/${sessionId}`);
    return resp.data;
}

export async function deleteCopilotSession(projectId: number, sessionId: number): Promise<void> {
    await apiClient.delete(`/api/projects/${projectId}/copilot/sessions/${sessionId}`);
}

export async function listCopilotModels(projectId: number): Promise<CopilotModelItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/models`);
    return resp.data.items ?? [];
}

// ───────────────────────── 编辑提案 API（O5-B25/F13）

/** 应用提案：高风险须 confirm=true；返回 {applied, need_confirm?, conflict?, healed?, message} */
export async function applyCopilotProposal(
    projectId: number, proposalId: string, confirm: boolean,
): Promise<Record<string, any>> {
    const resp = await apiClient.post(
        `/api/projects/${projectId}/copilot/proposals/${proposalId}/apply`,
        { confirm });
    return resp.data;
}

/** 忽略提案（无画布副作用） */
export async function rejectCopilotProposal(projectId: number, proposalId: string): Promise<void> {
    await apiClient.post(`/api/projects/${projectId}/copilot/proposals/${proposalId}/reject`);
}

/** 会话内提案列表（回放时按 proposal_id 对齐当前状态） */
export async function listCopilotProposals(
    projectId: number, sessionId?: number | null, limit = 100,
): Promise<CopilotProposalItem[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/proposals`, {
        params: { ...(sessionId ? { session_id: sessionId } : {}), limit },
    });
    return resp.data.items ?? [];
}

// ───────────────────────── 首轮诊断建议卡（O5-B27/F14）

export interface CopilotSuggestionItem {
    proposal_id: string;
    kind: string;
    targets: Record<string, unknown>;
    llm_reason: string;
    evidence: string[];
    status: 'pending' | 'applied' | 'rejected' | 'expired' | string;
    result?: Record<string, unknown> | null;
    created_at: string | null;
    decided_at: string | null;
}

/** 建议卡列表（构建器顶栏"N 条建议"入口按 pending_count 计数） */
export async function listCopilotSuggestions(
    projectId: number, status?: string | null, limit = 100,
): Promise<{ pending_count: number; items: CopilotSuggestionItem[] }> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/suggestions`, {
        params: { ...(status ? { status } : {}), limit },
    });
    return { pending_count: resp.data.pending_count ?? 0, items: resp.data.items ?? [] };
}

/** 建议卡"转审核项"：物化 review_items 走既有裁决流，返回审核项 id */
export async function convertCopilotSuggestion(
    projectId: number, proposalId: string,
): Promise<{ converted: boolean; review_item_id: number }> {
    const resp = await apiClient.post(
        `/api/projects/${projectId}/copilot/suggestions/${proposalId}/convert`);
    return resp.data;
}

// ───────────────────────── 验证报告与审计（O5-B28/B29/F15）

/** 验证报告存档到版本（打 schema 快照版本，description 带报告摘要） */
export async function archiveCopilotVerify(
    projectId: number, messageId: number, label?: string,
): Promise<{ archived: boolean; version_no: number }> {
    const resp = await apiClient.post(
        `/api/projects/${projectId}/copilot/verify/${messageId}/archive`,
        label ? { label } : {});
    return resp.data;
}

/** AI 协同操作历史（B29：copilot.* 审计流水，actor/会话/提案/diff 摘要） */
export async function listCopilotAudit(
    projectId: number, limit = 200,
): Promise<{ id: number; action: string; actor_id: number | null; actor: string | null;
             detail: Record<string, any>; created_at: string | null }[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/copilot/audit`, {
        params: { limit },
    });
    return resp.data.items ?? [];
}
