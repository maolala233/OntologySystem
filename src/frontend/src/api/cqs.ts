/**
 * O3 前端数据源（09 §2 F8/F9）：CQ 资产 + 评测报告（B12）、发布门控（B14）、
 * 影响分析（B13）、失败轨迹进化（B16）。对应后端 app/api/cqs.py、publications.py、evolution.py。
 */
import apiClient from './client';

// ── Competency Questions（B12/F8） ──

export interface CqRow {
    id: number;
    project_id: number;
    question: string;
    answer_criteria: string | null;
    source: 'manual' | 'evolution';
    sort_order: number;
    enabled: boolean;
    created_at: string | null;
}

export interface CqResultRow {
    cq_id: number;
    question: string;
    answerable: boolean;
    source_count: number;
    coverage: number;
}

export interface CqEvaluationRow {
    id: number;
    project_id: number;
    trigger: string;
    mode: string;
    total_cq: number;
    answerable_count: number;
    coverage_rate: number;
    results: CqResultRow[];
    created_at: string | null;
}

export const cqApi = {
    list: async (projectId: number): Promise<{ items: CqRow[]; total: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/cqs`);
        return response.data;
    },

    /** 批量创建：items=[{question, answer_criteria?}]，单条也走 items */
    create: async (projectId: number, items: { question: string; answer_criteria?: string }[]):
        Promise<{ created: number; items: CqRow[] }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/cqs`, { items });
        return response.data;
    },

    update: async (projectId: number, cqId: number, body: {
        question?: string; answer_criteria?: string | null; enabled?: boolean; sort_order?: number;
    }): Promise<CqRow> => {
        const response = await apiClient.put(`/api/projects/${projectId}/cqs/${cqId}`, body);
        return response.data;
    },

    remove: async (projectId: number, cqId: number): Promise<{ deleted: number }> => {
        const response = await apiClient.delete(`/api/projects/${projectId}/cqs/${cqId}`);
        return response.data;
    },

    reorder: async (projectId: number, orderedIds: number[]): Promise<{ reordered: number }> => {
        const response = await apiClient.put(`/api/projects/${projectId}/cqs/reorder`, { ordered_ids: orderedIds });
        return response.data;
    },

    /** 手动触发评测（检索式跑通率/证据覆盖率） */
    evaluate: async (projectId: number): Promise<CqEvaluationRow> => {
        const response = await apiClient.post(`/api/projects/${projectId}/cqs/evaluate`, {});
        return response.data;
    },

    evaluations: async (projectId: number, limit = 10): Promise<{ items: CqEvaluationRow[]; total: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/cqs/evaluations`, { params: { limit } });
        return response.data;
    },
};

// ── 发布门控（B14/F9） ──

export interface GateResultRow {
    cq_id: number;
    question: string;
    /** 候选视角：win=候选胜 / loss=候选负 / tie=平 */
    verdict?: 'win' | 'loss' | 'tie';
    hard_failure?: boolean;
    // GT 模式
    baseline_score?: number;
    candidate_score?: number;
    delta?: number;
    // LLM Judge 模式
    judge_reason?: string;
}

export interface GateEvaluationRow {
    id: number;
    project_id: number;
    mode: 'gt' | 'llm_judge';
    baseline_version_id: number | null;
    candidate_version_id: number | null;
    wins: number;
    ties: number;
    losses: number;
    hard_failures: number;
    unacceptable_regressions: boolean;
    verdict: 'pass' | 'reject';
    results: GateResultRow[];
    created_at: string | null;
}

export const gateApi = {
    evaluations: async (projectId: number, limit = 20): Promise<{ items: GateEvaluationRow[]; total: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/gate/evaluations`, { params: { limit } });
        return response.data;
    },

    evaluation: async (projectId: number, evaluationId: number): Promise<GateEvaluationRow> => {
        const response = await apiClient.get(`/api/projects/${projectId}/gate/evaluations/${evaluationId}`);
        return response.data;
    },
};

// ── 发布影响分析（B13/F9） ──

export interface ImpactDiffCount { added: number; removed: number; modified: number }

export interface ImpactSummary {
    baseline_version_id: number | null;
    has_baseline: boolean;
    diff: Record<'classes' | 'properties' | 'instances' | 'relations', ImpactDiffCount>;
    affected: { instances: number; relations: number; pending_reviews: number };
    changed_classes: string[];
    changed_properties: string[];
    drift: { label: string; instance_count: number; samples: { node_label: string; quote: string; source_document: string }[] }[];
}

export const impactApi = {
    preview: async (projectId: number): Promise<ImpactSummary> => {
        const response = await apiClient.get(`/api/projects/${projectId}/publish/impact`);
        return response.data;
    },
};

// ── 失败轨迹进化（B16） ──

export interface QaFailureRow {
    id: number;
    question: string;
    error: string | null;
    repaired_rounds: number;
    created_at: string | null;
}

export const evolutionApi = {
    failures: async (projectId: number, limit = 50): Promise<{ items: QaFailureRow[]; total: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/evolution/failures`, { params: { limit } });
        return response.data;
    },

    analyze: async (projectId: number, body?: { min_failures?: number; window_days?: number }) => {
        const response = await apiClient.post(`/api/projects/${projectId}/evolution/analyze`, body || {});
        return response.data;
    },

    report: async (projectId: number): Promise<{ report: Record<string, any> | null }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/evolution/report`);
        return response.data;
    },
};
