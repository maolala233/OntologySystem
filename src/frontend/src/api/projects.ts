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

export const projectsAPI = {
    // 获取我的项目列表
    getMyProjects: async (): Promise<ProjectData[]> => {
        const response = await apiClient.get('/projects/my');
        return response.data;
    },

    // 获取公共已发布项目
    getPublicProjects: async (): Promise<ProjectData[]> => {
        const response = await apiClient.get('/projects/public');
        return response.data;
    },

    // 获取单个项目详情
    getProject: async (projectId: number): Promise<ProjectData> => {
        const response = await apiClient.get(`/projects/${projectId}`);
        return response.data;
    },

    // 创建新项目
    createProject: async (data: CreateProjectRequest): Promise<ProjectData> => {
        const response = await apiClient.post('/projects', data);
        return response.data;
    },

    // 更新项目（保存草稿）
    updateProject: async (projectId: number, data: UpdateProjectRequest): Promise<ProjectData> => {
        const response = await apiClient.put(`/projects/${projectId}`, data);
        return response.data;
    },

    // 发布项目
    publishProject: async (projectId: number): Promise<ProjectData> => {
        const response = await apiClient.post(`/projects/${projectId}/publish`);
        return response.data;
    },

    // 删除项目
    deleteProject: async (projectId: number): Promise<void> => {
        await apiClient.delete(`/projects/${projectId}`);
    },

    // 上传文档并提取本体
    uploadDocument: async (projectId: number, file: File): Promise<any> => {
        const formData = new FormData();
        formData.append('file', file);

        const response = await apiClient.post(`/projects/${projectId}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },
};
