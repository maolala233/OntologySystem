import apiClient from './client';

export interface SystemConfig {
    id: number;
    key: string;
    value: Record<string, any>;
    updated_at: string;
}

export const systemApi = {
    getConfig: async (key: string): Promise<SystemConfig> => {
        const response = await apiClient.get(`/api/system/config/${key}`);
        return response.data;
    },

    updateConfig: async (key: string, value: Record<string, any>): Promise<SystemConfig> => {
        const response = await apiClient.put(`/api/system/config/${key}`, { value });
        return response.data;
    },
};
