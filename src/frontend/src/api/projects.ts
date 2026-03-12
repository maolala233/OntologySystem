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

// 阶段 1: Schema 提取请求参数
export interface SchemaExtractionRequest {
    file: File | File[];
    user_intent?: string;
    chunk_size?: number;
    chunk_overlap?: number;
    request_interval?: number;
}

// 阶段 2: 实例提取请求参数
export interface InstanceExtractionRequest {
    text_content: string;
    schema_graph: {
        classes: any[];
        object_properties: any[];
    };
    chunk_size?: number;
    chunk_overlap?: number;
    request_interval?: number;
    product_code?: string;
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

    // 上传文档并提取本体（旧版兼容接口）
    uploadDocument: async (projectId: number, file: File, params?: { scenario?: string; entities_df?: any }): Promise<any> => {
        const formData = new FormData();
        formData.append('file', file);

        // 添加场景描述（如果存在）
        if (params?.scenario && params.scenario.trim()) {
            formData.append('scenario', params.scenario);
        }

        // 添加结构化规则（如果存在）
        if (params?.entities_df) {
            // 将结构化数据转换为 JSON 字符串
            formData.append('entities_df', JSON.stringify(params.entities_df));
        }

        const response = await apiClient.post(`/api/projects/${projectId}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // 上传 TTL 文件并解析为前端展示要素
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

    // 下载 TTL 文件
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

    // ==================== 两阶段提取 API ====================

    // 阶段 1: 提取 Schema（骨架提取）- 支持多文件
    extractSchema: async (
        projectId: number,
        files: File | File[],
        options?: {
            user_intent?: string;
            chunk_size?: number;
            chunk_overlap?: number;
            request_interval?: number;
            async_mode?: boolean;
            save_documents?: boolean;
        }
    ): Promise<any> => {
        const formData = new FormData();
        
        // 支持单个或多个文件
        const fileArray = Array.isArray(files) ? files : [files];
        fileArray.forEach((file) => {
            formData.append('files', file);
        });
        
        if (options?.user_intent) {
            formData.append('user_intent', options.user_intent);
        }
        if (options?.chunk_size) {
            formData.append('chunk_size', String(options.chunk_size));
        }
        if (options?.chunk_overlap) {
            formData.append('chunk_overlap', String(options.chunk_overlap));
        }
        if (options?.request_interval) {
            formData.append('request_interval', String(options.request_interval));
        }
        if (options?.async_mode !== undefined) {
            formData.append('async_mode', String(options.async_mode));
        }
        if (options?.save_documents !== undefined) {
            formData.append('save_documents', String(options.save_documents));
        }

        const response = await apiClient.post(
            `/api/projects/${projectId}/extract-schema`,
            formData,
            {
                headers: { 'Content-Type': 'multipart/form-data' },
            }
        );
        return response.data;
    },

    // 阶段 2: 提取实例（带 Schema 约束）
    extractInstances: async (
        projectId: number,
        data: {
            text_content: string;
            schema_graph: {
                classes: any[];
                object_properties: any[];
            };
            chunk_size?: number;
            chunk_overlap?: number;
            request_interval?: number;
            product_code?: string;
            async_mode?: boolean;
        }
    ): Promise<any> => {
        const formData = new FormData();
        formData.append('async_mode', String(data.async_mode || false));
        formData.append('request_body', JSON.stringify({
            text_content: data.text_content,
            schema_graph: data.schema_graph,
            chunk_size: data.chunk_size,
            chunk_overlap: data.chunk_overlap,
            request_interval: data.request_interval,
            product_code: data.product_code,
        }));

        const response = await apiClient.post(
            `/api/projects/${projectId}/extract-instances`,
            formData,
            {
                headers: { 'Content-Type': 'multipart/form-data' },
            }
        );
        return response.data;
    },

    // 获取任务进度
    getTaskProgress: async (projectId: number, taskId: string): Promise<any> => {
        const response = await apiClient.get(`/api/projects/${projectId}/task/${taskId}/progress`);
        return response.data;
    },

    // 取消任务
    cancelTask: async (projectId: number, taskId: string): Promise<any> => {
        const response = await apiClient.post(`/api/projects/${projectId}/task/${taskId}/cancel`);
        return response.data;
    },

    // 解析文件获取文本内容（不提取 schema）- 支持多文件
    parseFiles: async (
        projectId: number,
        files: File | File[]
    ): Promise<any> => {
        const formData = new FormData();
        
        // 支持单个或多个文件
        const fileArray = Array.isArray(files) ? files : [files];
        fileArray.forEach((file) => {
            formData.append('files', file);
        });

        const response = await apiClient.post(
            `/api/projects/${projectId}/parse-files`,
            formData,
            {
                headers: { 'Content-Type': 'multipart/form-data' },
            }
        );
        // response.data 已经是后端返回的数据对象
        return response.data;
    },

    // 获取项目已上传的文档列表
    getDocuments: async (projectId: number): Promise<any> => {
        const response = await apiClient.get(`/api/projects/${projectId}/documents`);
        return response.data;
    },

    // 删除项目文档
    deleteDocument: async (projectId: number, docId: number): Promise<any> => {
        const response = await apiClient.delete(`/api/projects/${projectId}/documents/${docId}`);
        return response.data;
    },

    // 清空项目所有文档
    clearAllDocuments: async (projectId: number): Promise<any> => {
        const response = await apiClient.post(`/api/projects/${projectId}/documents/clear-all`);
        return response.data;
    },

    // 基于已上传文档 ID 进行骨架提取
    extractSchemaFromDocuments: async (
        projectId: number,
        documentIds: number[],
        options?: {
            user_intent?: string;
            chunk_size?: number;
            chunk_overlap?: number;
            request_interval?: number;
            async_mode?: boolean;
        }
    ): Promise<any> => {
        const formData = new FormData();
        
        // 传递文档 ID 列表（逗号分隔）
        formData.append('document_ids', documentIds.join(','));
        
        if (options?.user_intent) {
            formData.append('user_intent', options.user_intent);
        }
        if (options?.chunk_size) {
            formData.append('chunk_size', String(options.chunk_size));
        }
        if (options?.chunk_overlap) {
            formData.append('chunk_overlap', String(options.chunk_overlap));
        }
        if (options?.request_interval) {
            formData.append('request_interval', String(options.request_interval));
        }
        if (options?.async_mode !== undefined) {
            formData.append('async_mode', String(options.async_mode));
        }

        const response = await apiClient.post(
            `/api/projects/${projectId}/extract-schema-from-documents`,
            formData,
            {
                headers: { 'Content-Type': 'multipart/form-data' },
            }
        );
        return response.data;
    },

    // 基于已上传文档 ID 进行实例提取
    extractInstancesFromDocuments: async (
        projectId: number,
        documentIds: number[],
        options?: {
            chunk_size?: number;
            chunk_overlap?: number;
            request_interval?: number;
            async_mode?: boolean;
        }
    ): Promise<any> => {
        const formData = new FormData();
        
        // 传递文档 ID 列表（逗号分隔）
        formData.append('document_ids', documentIds.join(','));
        
        if (options?.chunk_size) {
            formData.append('chunk_size', String(options.chunk_size));
        }
        if (options?.chunk_overlap) {
            formData.append('chunk_overlap', String(options.chunk_overlap));
        }
        if (options?.request_interval) {
            formData.append('request_interval', String(options.request_interval));
        }
        if (options?.async_mode !== undefined) {
            formData.append('async_mode', String(options.async_mode));
        }

        const response = await apiClient.post(
            `/api/projects/${projectId}/extract-instances-from-documents`,
            formData,
            {
                headers: { 'Content-Type': 'multipart/form-data' },
            }
        );
        return response.data;
    },
};
