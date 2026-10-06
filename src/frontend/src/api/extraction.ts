/**
 * M3-4/M3-5/M3-6 抽取任务 API（03 §8）——前端唯一抽取入口（缺陷 #12 收编：旧同步端点已删除）。
 * 发起 → task_id → 轮询 GET tasks/{id} 或 SSE（ticket 鉴权）→ stats。
 */
import apiClient from './client';
import { authAPI } from './auth';

export interface ExtractTaskProgress {
    task_id?: string;
    status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
    progress?: number;
    message?: string;
    detail?: string;
    stage?: string;
    stats?: Record<string, any>;
    error?: string;
    result?: Record<string, any>;
}

export const extractionApi = {
    /** 发起 Schema 抽取（extract 队列）：document_ids 缺省=全部已解析切片；
     *  guidance 为用户注入的抽取引导纯文本（规则表单组装），随 prompt 进缓存键 */
    runSchema: async (projectId: number, options?: {
        document_ids?: number[];
        parallelism?: number;
        guidance?: string;
    }): Promise<{ task_id: string; chunks_total: number; parallelism: number }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/extraction/schema`, options || {});
        return response.data;
    },

    /** 发起实例抽取（严格闸门 + ABox 双写行表）；schema_version_no 指定历史框架版本，缺省用当前画布框架 */
    runInstances: async (projectId: number, options?: {
        document_ids?: number[];
        parallelism?: number;
        promote_policy?: 'review' | 'discard' | 'auto';
        strict_gate?: boolean;
        schema_version_no?: number;
    }): Promise<{ task_id: string; chunks_total: number; parallelism: number; schema_version_no?: number }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/extraction/instances`, options || {});
        return response.data;
    },

    /** 轮询任务进度 */
    getTask: async (projectId: number, taskId: string): Promise<ExtractTaskProgress> => {
        const response = await apiClient.get(`/api/projects/${projectId}/extraction/tasks/${taskId}`);
        return response.data;
    },

    /** 取消任务（软 revoke + 取消标记） */
    cancelTask: async (projectId: number, taskId: string): Promise<void> => {
        await apiClient.post(`/api/projects/${projectId}/extraction/tasks/${taskId}/cancel`);
    },

    /** SSE 事件流 URL（ticket 一次性鉴权，EventSource 不支持 header） */
    eventsUrl: async (projectId: number, taskId: string): Promise<string> => {
        const { ticket } = await authAPI.getSseTicket(taskId);
        const base = apiClient.defaults.baseURL || '';
        return `${base}/api/projects/${projectId}/extraction/tasks/${taskId}/events?ticket=${encodeURIComponent(ticket)}`;
    },
};
