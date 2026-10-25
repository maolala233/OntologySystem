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

/** AI 规则挖掘候选（S9）：统计通道挖「带 p 必带 q」的物质化蕴含，LLM 通道从素材抽规则；
 * 双通道同键合并 source=both。人工采纳后生效。 */
export interface RuleSuggestion {
    name: string;
    if_subject_class: string;
    if_predicate: string;
    if_object_class: string;
    then_predicate: string;
    support: number | null;      // LLM 通道无统计支撑数
    confidence: number | null;   // LLM 通道置信依据在 llm_reason
    source?: 'co_occurrence' | 'llm' | 'both' | string;
    llm_reason?: string | null;
    examples?: Array<Record<string, unknown>> | null;
}

/** 公理声明（O4 后扩展：规则与公理统一管理）。key 为编辑/删除锚点。 */
export interface DeclaredAxiom {
    key: string;
    kind: 'functional' | 'inverse_of' | 'disjoint_with' | 'subclass_of'
        | 'equivalent_classes' | 'cardinality';
    predicate?: string;
    predicates?: string[];
    classes?: string[];
    class?: string;
    min?: number | null;
    max?: number | null;
    /** S16：公理来源四态 + 认证位（+可选证据锚点） */
    source_type?: string;
    certified?: boolean;
    evidence?: unknown;
}

export interface AxiomBody {
    kind: string;
    predicate?: string;
    predicates?: string[];
    classes?: string[];
    value?: string;
    min_card?: number;
    max_card?: number;
}

export const reasoningApi = {
    run: async (projectId: number, body?: { profile?: string; rule_ids?: number[] }): Promise<ReasoningSummary> => {
        const response = await apiClient.post(`/api/projects/${projectId}/reasoning/run`, body || {});
        return response.data;
    },
    getResults: async (projectId: number, params?: { limit?: number; offset?: number; source?: string }): Promise<{
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
    // AI 规则挖掘（S9）：统计（物质化蕴含）+ LLM（规则抽取）双通道候选，只读不落库
    getRuleSuggestions: async (projectId: number, channel: 'statistical' | 'llm' | 'all' = 'all'):
        Promise<{ items: RuleSuggestion[]; llm?: { parsed?: number; parse_rejected?: number; skipped?: string } }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reasoning/rule-suggestions`, { params: { channel } });
        return response.data;
    },

    listAxioms: async (projectId: number): Promise<{
        items: DeclaredAxiom[]; kinds: string[];
        class_labels: string[]; predicate_labels: string[];
    }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reasoning/axioms`);
        return response.data;
    },
    declareAxiom: async (projectId: number, body: AxiomBody): Promise<{ axiom_declared: string; edges_marked: number }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/reasoning/axioms`, body);
        return response.data;
    },
    removeAxiom: async (projectId: number, key: string): Promise<void> => {
        await apiClient.delete(`/api/projects/${projectId}/reasoning/axioms`, { params: { key } });
    },
    /** S16：公理一键认证/撤销（存量公理盘点用） */
    certifyAxiom: async (projectId: number, key: string, certified = true):
        Promise<{ key: string; certified: boolean; source_type: string }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/reasoning/axioms/certify`, { key, certified });
        return response.data;
    },
};
