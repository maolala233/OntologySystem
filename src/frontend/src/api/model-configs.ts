/**
 * 模型配置 API（M2，docs/design/03 §5）
 */
import apiClient from './client';

export interface ModelConfigRow {
    id: number;
    scope: 'global' | 'project';
    project_id?: number | null;
    purpose: 'chat' | 'extract' | 'embedding' | 'vl';
    name: string;
    provider: string;
    base_url: string;
    model_name: string;
    api_key_masked: string;
    api_key_set: boolean;
    params: Record<string, any>;
    dims?: number | null;
    is_default: boolean;
    enabled: boolean;
    last_test_at?: string | null;
    last_test_ok?: boolean | null;
}

export interface ProviderMeta {
    id: string;
    label: string;
    needs_key: boolean;
    default_base_url: string;
    hint: string;
}

export interface TestResult {
    ok: boolean;
    latency_ms: number;
    message: string;
    dims?: number;
    sample_output?: string;
}

/** 用户个人模型选择（R14，/me/pick）：普通用户点选的全局抽取模型 */
export interface ModelPickState {
    purpose: string;
    config_id: number | null;
    config: ModelConfigRow | null;
}

export const modelConfigsApi = {
    listProviders: async (): Promise<ProviderMeta[]> => {
        const resp = await apiClient.get('/api/model-configs/providers');
        return resp.data.items;
    },

    list: async (params?: { purpose?: string; scope?: string; project_id?: number }): Promise<ModelConfigRow[]> => {
        const resp = await apiClient.get('/api/model-configs', { params });
        return resp.data.items;
    },

    create: async (data: Partial<ModelConfigRow> & {
        purpose: string; name: string; provider: string; base_url: string;
        model_name: string; api_key?: string; dims?: number | null;
    }): Promise<ModelConfigRow> => {
        const resp = await apiClient.post('/api/model-configs', data);
        return resp.data;
    },

    patch: async (id: number, data: Record<string, any>): Promise<ModelConfigRow> => {
        const resp = await apiClient.patch(`/api/model-configs/${id}`, data);
        return resp.data;
    },

    remove: async (id: number): Promise<void> => {
        await apiClient.delete(`/api/model-configs/${id}`);
    },

    setDefault: async (id: number): Promise<ModelConfigRow> => {
        const resp = await apiClient.put(`/api/model-configs/${id}/default`);
        return resp.data;
    },

    // ---- R14 个人模型选择（仅全局抽取配置可选，对所有项目生效）----
    getMyPick: async (purpose: string = 'extract'): Promise<ModelPickState> => {
        const resp = await apiClient.get('/api/model-configs/me/pick', { params: { purpose } });
        return resp.data;
    },

    setMyPick: async (configId: number, purpose: string = 'extract'): Promise<ModelPickState> => {
        const resp = await apiClient.put('/api/model-configs/me/pick', { purpose, config_id: configId });
        return resp.data;
    },

    clearMyPick: async (purpose: string = 'extract'): Promise<ModelPickState> => {
        const resp = await apiClient.delete('/api/model-configs/me/pick', { params: { purpose } });
        return resp.data;
    },

    test: async (data: {
        purpose: string; provider: string; base_url: string; model_name: string;
        api_key?: string; dims?: number | null; config_id?: number | null;
    }): Promise<TestResult> => {
        const resp = await apiClient.post('/api/model-configs/test', data);
        return resp.data;
    },
};
