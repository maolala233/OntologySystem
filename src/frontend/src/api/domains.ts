/**
 * 知识域管理 API
 */
import apiClient from './client';

export interface KnowledgeDomain {
    id: number;
    name: string;
    description: string | null;
    created_at: string;
    updated_at: string;
}

export interface KnowledgeDomainCreate {
    name: string;
    description?: string;
}

export interface KnowledgeDomainUpdate {
    name?: string;
    description?: string;
}

/**
 * 获取所有知识域列表
 */
export const getDomains = async (): Promise<KnowledgeDomain[]> => {
    const response = await apiClient.get('/domains');
    return response.data;
};

/**
 * 创建知识域
 */
export const createDomain = async (data: KnowledgeDomainCreate): Promise<KnowledgeDomain> => {
    const response = await apiClient.post('/domains', data);
    return response.data;
};

/**
 * 更新知识域
 */
export const updateDomain = async (id: number, data: KnowledgeDomainUpdate): Promise<KnowledgeDomain> => {
    const response = await apiClient.put(`/domains/${id}`, data);
    return response.data;
};

/**
 * 删除知识域
 */
export const deleteDomain = async (id: number): Promise<{ message: string }> => {
    const response = await apiClient.delete(`/domains/${id}`);
    return response.data;
};
