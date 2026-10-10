/**
 * 推理期 R1/R3 API：蕴含推理（owlrl）+ 规则引擎。
 * 推理结果与事实分离：results 仅含推导三元组（source 区分 owlrl/rdfs/rule）。
 */
import apiClient from './client';

export interface ReasoningSummary {
    batch_id: string;
    profile: 'owlrl' | 'rdfs';
    fact_triples: number;
    inferred_entailment: number;
    inferred_rule: number;
    inferred_total: number;
    persisted: number;
    truncated: boolean;
    by_rule: Record<string, number>;
}

export interface ReasoningItem {
    id: number;
    source: string;           // 'owlrl' | 'rdfs' | 'rule'
    rule_name: string | null;
    subject_label: string;
    subject_uri: string;
    predicate_label: string;
    predicate_uri: string;
    object_label: string;
    object_uri: string;
}

export interface OntologyRule {
    id: number;
    name: string;
    if_subject_class: string;
    if_predicate: string;
    if_object_class: string;
    then_predicate: string;
    enabled: boolean;
    created_at: string | null;
}

export interface RuleBody {
    name: string;
    if_subject_class: string;
    if_predicate: string;
    if_object_class: string;
    then_predicate: string;
    enabled: boolean;
}

export const reasoningApi = {
    run: async (projectId: number, body?: { profile?: string; rule_ids?: number[] }): Promise<ReasoningSummary> => {
        const response = await apiClient.post(`/api/projects/${projectId}/reasoning/run`, body || {});
        return response.data;
    },
    getResults: async (projectId: number, params?: { limit?: number; source?: string }): Promise<{
        batch_id: string | null; total: number; items: ReasoningItem[];
    }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reasoning/results`, { params: params || {} });
        return response.data;
    },
    exportInferredUrl: (projectId: number): string =>
        `/api/projects/${projectId}/export?format=turtle&include_inferred=true`,

    listRules: async (projectId: number): Promise<{ items: OntologyRule[] }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reasoning/rules`);
        return response.data;
    },
    createRule: async (projectId: number, body: RuleBody): Promise<{ id: number }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/reasoning/rules`, body);
        return response.data;
    },
    updateRule: async (projectId: number, ruleId: number, body: RuleBody): Promise<void> => {
        await apiClient.put(`/api/projects/${projectId}/reasoning/rules/${ruleId}`, body);
    },
    deleteRule: async (projectId: number, ruleId: number): Promise<void> => {
        await apiClient.delete(`/api/projects/${projectId}/reasoning/rules/${ruleId}`);
    },
};
