/**
 * M3-6 审核/版本/发布 API（03 §10-§12）——审核工作台与时间轴 Tab 数据源。
 */
import apiClient from './client';

// ── 审核 ──

export type ReviewItemType =
    | 'entity_merge' | 'new_class' | 'low_confidence_entity' | 'low_confidence_relation'
    | 'conflict_value' | 'conflict_type' | 'conflict_relationship' | 'missing_evidence'
    | 'conflict_axiom';
export type ReviewPriority = 'low' | 'medium' | 'high' | 'critical';
export type ReviewStatus = 'pending' | 'claimed' | 'approved' | 'rejected' | 'edited';

export interface ReviewItem {
    id: number;
    project_id: number;
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

export const reviewsApi = {
    listByProject: async (projectId: number, filters?: {
        status?: string; type?: string; priority?: string;
    }): Promise<{ items: ReviewItem[]; total: number }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/reviews`, { params: filters || {} });
        return response.data;
    },

    /** 跨项目队列（审核工作台）：自动过滤为可 view 项目 */
    listAll: async (filters?: { status?: string; type?: string }): Promise<{ items: ReviewItem[]; total: number }> => {
        const response = await apiClient.get('/api/reviews', { params: filters || {} });
        return response.data;
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
export interface DiffResult {
    classes: { added: string[]; removed: string[]; modified: { label: string; from: string; to: string }[] };
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
    publish: async (projectId: number, body?: { note?: string; label?: string }) => {
        const response = await apiClient.post(`/api/projects/${projectId}/publish`, body || {});
        return response.data as {
            publication_id: number; version_no: number; status: string; warnings: string[];
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
