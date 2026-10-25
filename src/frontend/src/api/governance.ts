/**
 * M3-6 审核/版本/发布 API（03 §10-§12）——审核工作台与时间轴 Tab 数据源。
 */
import apiClient from './client';

// ── 审核 ──

export type ReviewItemType =
    | 'entity_merge' | 'new_class' | 'low_confidence_entity' | 'low_confidence_relation'
    | 'conflict_value' | 'conflict_type' | 'conflict_relationship' | 'missing_evidence'
    | 'conflict_axiom' | 'axiom_suggestion';
export type ReviewPriority = 'low' | 'medium' | 'high' | 'critical';
export type ReviewStatus = 'pending' | 'claimed' | 'approved' | 'rejected' | 'edited';

export interface ReviewItem {
    id: number;
    project_id: number;
    /** 所隶属项目名称（列表/详情返回；项目已删除时为 null，展示处回退 #id） */
    project_name?: string | null;
    item_type: ReviewItemType;
    status: ReviewStatus;
    priority: ReviewPriority;
    reason: string | null;
    payload: Record<string, any>;
    suggested_action: Record<string, any> | null;
    assigned_to: number | null;
    decided_by: number | null;
    decided_at: string | null;
    result_ref: Record<string, any> | null;
    created_at: string | null;
}

const MAX_REVIEW_FETCH = 2000;

export const reviewsApi = {
    listByProject: async (projectId: number, filters?: {
        status?: string; type?: string; priority?: string;
        limit?: number; offset?: number;
    }): Promise<{ items: ReviewItem[]; total: number; limit?: number; offset?: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reviews`, { params: filters || {} });
        return response.data;
    },

    /** 跨项目队列（审核工作台）：自动过滤为可 view 项目；响应携带 projects（项目筛选下拉数据源） */
    listAll: async (filters?: { status?: string; type?: string; project_id?: string; limit?: number; offset?: number }): Promise<{ items: ReviewItem[]; total: number; limit?: number; offset?: number; projects?: Array<{ id: number; name: string | null }> }> => {
        const response = await apiClient.get('/api/reviews', { params: filters || {} });
        return response.data;
    },

    /** 审核工作台全量加载：按 500/页循环拉到真实 total（后端单页上限 2000，超过 2000 截断并告警） */
    listAllPaged: async (filters?: { status?: string; type?: string; project_id?: string }): Promise<{ items: ReviewItem[]; total: number; truncated: boolean; projects: Array<{ id: number; name: string | null }> }> => {
        const PAGE = 500;
        const first = await reviewsApi.listAll({ ...(filters || {}), limit: PAGE, offset: 0 });
        const items = [...(first.items || [])];
        const total = first.total ?? items.length;
        while (items.length < Math.min(total, MAX_REVIEW_FETCH)) {
            const page = await reviewsApi.listAll({ ...(filters || {}), limit: PAGE, offset: items.length });
            if (!page.items?.length) break; // 防御：游标异常时不死循环
            items.push(...page.items);
        }
        return { items, total, truncated: total > MAX_REVIEW_FETCH, projects: first.projects || [] };
    },

    detail: async (reviewId: number): Promise<ReviewItem> => {
        const response = await apiClient.get(`/api/reviews/${reviewId}`);
        return response.data;
    },

    claim: async (reviewId: number): Promise<{ id: number; status: string }> => {
        const response = await apiClient.post(`/api/reviews/${reviewId}/claim`);
        return response.data;
    },

    decide: async (reviewId: number, body: {
        action: 'approve' | 'reject' | 'edit';
        edited_payload?: Record<string, any>;
        note?: string;
    }): Promise<{ id: number; status: string; effect: Record<string, any> }> => {
        const response = await apiClient.post(`/api/reviews/${reviewId}/decide`, body);
        return response.data;
    },

    batchDecide: async (projectId: number, ids: number[], action: 'approve' | 'reject' | 'edit', note?: string) => {
        const response = await apiClient.post(`/api/projects/${projectId}/reviews/batch-decide`,
            { ids, action, note });
        return response.data;
    },
};

// ── 版本/时间轴 ──

export interface VersionRow {
    id: number;
    version_no: number;
    label: string | null;
    kind: 'schema' | 'full' | 'publication' | 'rollback';
    stats: Record<string, any> | null;
    checksum: string;
    parent_version_id: number | null;
    created_by: number;
    description: string | null;
    created_at: string | null;
    is_current: boolean;
}

export interface InstanceBrief { id: string; label: string; class_label: string | null }
export interface InstanceModified extends InstanceBrief {
    class_from: string | null;
    class_to: string | null;
    props_added: string[];
    props_removed: string[];
    props_changed: { key: string; from: any; to: any }[];
}
export interface RelationRow {
    subject: string; subject_label: string; subject_class: string;
    predicate: string;
    object: string; object_label: string; object_class: string;
}
/** 类字段级变化（versioning.diff_graphs）：属性增删/类型变化/父类调整/定义调整 */
export interface ClassModified {
    label: string;
    from: string; to: string;
    props_added?: { name: string; data_type: string }[];
    props_removed?: { name: string; data_type: string }[];
    props_changed?: { name: string; from: string; to: string }[];
    parent_from?: string | null; parent_to?: string | null;
    definition_changed?: boolean;
}
export interface DiffResult {
    classes: { added: string[]; removed: string[]; modified: ClassModified[] };
    properties: { added: string[]; removed: string[]; modified: { label: string; from: string; to: string }[] };
    instances: { added: InstanceBrief[]; removed: InstanceBrief[]; modified: InstanceModified[] };
    relations: { added: RelationRow[]; removed: RelationRow[]; modified: any[] };
}

export const versionsApi = {
    list: async (projectId: number): Promise<{ items: VersionRow[]; total: number; current_version_id: number | null }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/versions`);
        return response.data;
    },

    diff: async (projectId: number, a: number, b: number): Promise<DiffResult> => {
        const response = await apiClient.get(`/api/projects/${projectId}/versions/${a}/diff/${b}`);
        return response.data;
    },

    snapshotUrl: async (projectId: number, vid: number): Promise<{ url: string; checksum: string }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/versions/${vid}/snapshot`);
        return response.data;
    },

    restore: async (projectId: number, vid: number): Promise<{
        restored_from: number; new_version_no: number; pre_rollback_key: string;
    }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/versions/${vid}/restore`, {});
        return response.data;
    },

    /** 手动打版（框架复用：把调整后的框架存为可回滚/可选用的快照） */
    create: async (projectId: number, body?: { label?: string; description?: string; kind?: 'schema' | 'full' }): Promise<VersionRow> => {
        const response = await apiClient.post(`/api/projects/${projectId}/versions`, body || {});
        return response.data;
    },

    timeline: async (projectId: number, options?: {
        entity_uri?: string; time_axis?: 'valid' | 'transaction' | 'both';
    }): Promise<{ events: Record<string, any>[]; time_axis: string }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/timeline`, { params: options || {} });
        return response.data;
    },

    entityHistory: async (projectId: number, entityId: number) => {
        const response = await apiClient.get(`/api/projects/${projectId}/entities/${entityId}/history`);
        return response.data;
    },
};

// ── 发布/公共区 ──

export const publishApi = {
    publish: async (projectId: number, body?: { note?: string; label?: string; unacceptable_regressions?: boolean; force?: boolean }) => {
        const response = await apiClient.post(`/api/projects/${projectId}/publish`, body || {});
        return response.data as {
            publication_id: number; version_no: number; status: string; warnings: string[];
            /** S17-1：true=同内容幂等返回既有发布（未新建版本） */
            idempotent?: boolean;
        };
    },

    /**
     * S21：进化触发器检查（新轨迹 ≥N 或距 checkpoint ≥M 天 → due；幂等）。
     */
    evolutionTriggerCheck: async (projectId: number) => {
        const response = await apiClient.get(`/api/projects/${projectId}/evolution/trigger/check`);
        return response.data as {
            evolution_due: boolean; new_trajectories: number; days_since_checkpoint: number;
            reason: string | null; checkpoint_time: string;
            thresholds: { min_new_trajectories: number; min_days: number };
        };
    },

    /** S21：手动推进进化 checkpoint（本批轨迹看过/暂不进化；发布成功会自动推进） */
    evolutionTriggerAdvance: async (projectId: number) => {
        const response = await apiClient.post(`/api/projects/${projectId}/evolution/trigger/advance`, {});
        return response.data;
    },

    /**
     * S17-3（FE3 向导 Step1）：只读发布门禁报告——五道检查逐项结果 + 幂等信号。
     * check.level: block=阻断（任一失败 passed=false）| info=提示；warnings=非阻断告警。
     */
    gateReport: async (projectId: number) => {
        const response = await apiClient.get(`/api/projects/${projectId}/publish/gate-report`);
        return response.data as {
            passed: boolean;
            checks: { code: string; name: string; level: 'block' | 'warn' | 'info';
                      passed: boolean; detail: any }[];
            warnings: string[];
            content_changed: boolean;
            checksum: string;
            last_publication: { publication_id: number; version_no: number } | null;
        };
    },

    unpublish: async (projectId: number) => {
        const response = await apiClient.post(`/api/projects/${projectId}/unpublish`, {});
        return response.data;
    },

    assets: async (): Promise<{ groups: { domain: string; items: any[] }[]; total: number }> => {
        const response = await apiClient.get('/api/assets');
        return response.data;
    },

    assetDetail: async (projectId: number) => {
        const response = await apiClient.get(`/api/assets/${projectId}`);
        return response.data;
    },
};

// ── 本体健康记分卡（O2-F7，口径见 docs/design/09 §3） ──

export interface ScorecardTrendPoint {
    month: string;
    approved: number;
    rejected: number;
    pass_rate: number | null;
}

export interface Scorecard {
    consistency_violations: number;
    cycles: number;
    dangling_refs: number;
    dangling_domain_range: string[];
    naming_consistency: number;
    naming_bad: string[];
    evidence_closure_rate: number;
    evidence_dangling: string[];
    evidence_missing_count: number;
    instance_coverage: number;
    orphan_classes: string[];
    orphan_count: number;
    schema_checks: { passed: boolean; error_count: number; warning_count: number };
    review_trend: ScorecardTrendPoint[];
    pending_reviews: number;
    class_count: number;
    instance_count: number;
}

export const scorecardApi = {
    get: async (projectId: number): Promise<Scorecard> => {
        const response = await apiClient.get(`/api/projects/${projectId}/scorecard`);
        return response.data;
    },
};
