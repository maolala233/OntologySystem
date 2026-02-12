import apiClient from './client';
import { ProjectData } from '../types/ontology';

export interface CreateProjectRequest {
    name: string;
    description?: string;
}

export interface UpdateProjectRequest {
    name?: string;
    description?: string;
    graph_data?: {
        nodes: any[];
        edges: any[];
    };
}

export const projectsApi = {
    // 获取我的项目列表
    getMyProjects: async (): Promise<ProjectData[]> => {
        const response = await apiClient.get('/api/projects/my');
        return response.data;
    },

    // 获取公共已发布项目
    getPublicProjects: async (): Promise<ProjectData[]> => {
        const response = await apiClient.get('/api/projects/public');
        return response.data;
    },

    // 获取单个项目详情
    getProject: async (projectId: number): Promise<ProjectData> => {
        const response = await apiClient.get(`/api/projects/${projectId}`);
        return response.data;
    },

    // 创建新项目
    createProject: async (data: CreateProjectRequest): Promise<ProjectData> => {
        const response = await apiClient.post('/api/projects', data);
        return response.data;
    },

    // 更新项目（保存草稿）
    updateProject: async (projectId: number, data: UpdateProjectRequest): Promise<ProjectData> => {
        const response = await apiClient.put(`/api/projects/${projectId}`, data);
        return response.data;
    },

    // 发布项目
    publishProject: async (projectId: number): Promise<ProjectData> => {
        const response = await apiClient.post(`/api/projects/${projectId}/publish`);
        return response.data;
    },

    // 取消发布项目
    unpublishProject: async (projectId: number): Promise<ProjectData> => {
        const response = await apiClient.post(`/api/projects/${projectId}/unpublish`);
        return response.data;
    },

    // 删除项目
    deleteProject: async (projectId: number): Promise<void> => {
        await apiClient.delete(`/api/projects/${projectId}`);
    },

    // 上传文档并提取本体
    uploadDocument: async (projectId: number, file: File, params?: { scenario?: string; entities_df?: any }): Promise<any> => {
        const formData = new FormData();
        formData.append('file', file);

        // 添加场景描述（如果存在）
        if (params?.scenario && params.scenario.trim()) {
            formData.append('scenario', params.scenario);
        }

        // 添加结构化规则（如果存在）
        if (params?.entities_df) {
            // 将结构化数据转换为JSON字符串
            formData.append('entities_df', JSON.stringify(params.entities_df));
        }

        const response = await apiClient.post(`/api/projects/${projectId}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // 上传TTL文件并解析为前端展示要素
    uploadTTLFile: async (projectId: number, ttlFile: File): Promise<any> => {
        const formData = new FormData();
        formData.append('file', ttlFile);

        const response = await apiClient.post(`/api/projects/${projectId}/upload-ttl`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // 更新本体数据
    updateOntology: async (projectId: number, data: { nodes: any[], edges: any[] }): Promise<any> => {
        const response = await apiClient.post(`/api/projects/${projectId}/update-ontology`, data);
        return response.data;
    },

    // 下载TTL文件
    downloadTTL: async (projectId: number): Promise<Blob> => {
        const response = await apiClient.get(`/api/projects/${projectId}/download-ttl`, {
            responseType: 'blob'
        });

        // 创建下载链接
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `ontology_${projectId}.ttl`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);

        return response.data;
    },
};