/**
 * 一键构建管线 API（新手简化 2026-11）：run/status/cancel + 撤销（合并拆分）。
 * 公理撤销复用 reasoningApi.removeAxiom（api/reasoning.ts）。
 */
import apiClient from './client';

export type PipelineStageKey = 'parse' | 'schema' | 'instances' | 'resolution'
    | 'conflicts' | 'axioms' | 'adjudicate';

export interface PipelineStageState {
    key: PipelineStageKey;
    label: string;
    status: 'pending' | 'running' | 'done' | 'skipped' | 'failed';
    detail: string;
    task_id: string | null;
}

export interface PipelineReviews {
    pending_by_priority: Record<string, number>;
    pending_total: number;
    need_human: number;
    auto_handled: number;
}

export type BuildDepth = 'fast' | 'standard' | 'deep';

export const DEPTH_OPTIONS = [
    { value: 'fast', label: '快速（自检 1 轮，跳过公理扫描）' },
    { value: 'standard', label: '标准（自检 2 轮，含公理扫描）' },
    { value: 'deep', label: '深度（自检 3 轮，含公理扫描，最慢）' },
] as const;

export interface PipelineStatus {
    status: 'idle' | 'running' | 'queued' | 'scheduled' | 'completed' | 'failed' | 'cancelled';
    task_id: string | null;
    stage?: string;
    percent?: number;
    message?: string;
    stages: PipelineStageState[];
    reviews: PipelineReviews;
    scheduled_at?: string;
    stats?: Record<string, any>;
}

export interface ClassSummary {
    label: string;
    definition: string;
    aliases: string[];
    parents: string[];
    /** 类目属性（结构化抽取：表格列名=属性名） */
    properties?: Array<{ name: string; data_type: string; description: string }>;
}

export interface ClassRelation {
    predicate: string;
    domain: string | null;
    range: string | null;
    functional: boolean;
    transitive: boolean;
    symmetric: boolean;
    inverse_of: string | null;
}

export interface InstanceItem {
    id: number;
    label: string;
    class_label: string;
    props: Record<string, any>;
}

export interface InstanceRelation {
    subject_id: number;
    object_id: number;
    predicate: string;
    subject_label: string;
    object_label: string;
}

/** 公理申报留痕：从哪来（document/statistical/derived/schema/manual）+ 依据什么 */
export interface AxiomItem {
    key: string;
    kind: string;
    origin?: string;
    evidence?: Record<string, any>;
    summary?: string;
    derived_from?: string[];
    declared_at?: string;
    predicate?: string;
    predicates?: string[];
    classes?: string[];
    value?: string;
    min?: number | null;
    max?: number | null;
    [k: string]: any;
}

export interface BuildSummary {
    schema: {
        classes_total: number;
        object_properties_total: number;
        datatype_properties_total: number;
        classes: ClassSummary[];
        class_relations: ClassRelation[];
    };
    instances: {
        total: number;
        by_class: Record<string, number>;
        items: InstanceItem[];
    };
    instance_relations: InstanceRelation[];
    relations_total: number;
    axioms: { total: number; items: AxiomItem[] };
    reviews: PipelineReviews;
    checks: { passed: boolean; error_count: number; warning_count: number; issues: Array<Record<string, any>> };
    pipeline: {
        task_id: string | null; status: string; message: string;
        depth?: string; selfcheck?: Record<string, any>; stages?: PipelineStageState[];
    } | null;
    is_published: boolean;
}

export const pipelineApi = {
    /** 发起一键管线（幂等：进行中返回其 task_id，already_running=true）
     *  options.depth: fast|standard|deep；options.schedule_at: ISO 未来时间=定时构建；options.restart: 重头开始 */
    run: async (projectId: number, options?: Record<string, any>): Promise<{
        mode: string; task_id: string | null; already_running?: boolean;
        already_scheduled?: boolean; scheduled_at?: string; error?: string;
    }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/pipeline/run`,
            options ? { options } : {});
        return response.data;
    },

    /** 管线进度聚合（stages 实时计数 + 审核统计）；taskId 缺省读项目当前管线 */
    status: async (projectId: number, taskId?: string): Promise<PipelineStatus> => {
        const response = await apiClient.get(`/api/projects/${projectId}/pipeline/status`, {
            params: taskId ? { task_id: taskId } : {},
        });
        return response.data;
    },

    cancel: async (projectId: number): Promise<{ cancelled: boolean; kind?: string }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/pipeline/cancel`);
        return response.data;
    },

    /** 构建结果页数据源：骨架/实例/公理/审核四区 + 机检结论（零 LLM 调用） */
    buildSummary: async (projectId: number): Promise<BuildSummary> => {
        const response = await apiClient.get(`/api/projects/${projectId}/pipeline/build/summary`);
        return response.data;
    },

    /** 撤销 AI 自动合并（resolution/split：被并成员拆回独立实体） */
    undoMerge: async (projectId: number, canonicalId: number, splitIds: number[]): Promise<void> => {
        await apiClient.post(`/api/projects/${projectId}/resolution/split`,
            { canonical_id: canonicalId, split_ids: splitIds });
    },
};
