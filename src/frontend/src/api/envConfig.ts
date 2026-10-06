/**
 * 环境配置 API（中间件连接参数，admin）
 * 后端：/api/admin/env-configs（读/写/测试连通）
 */
import apiClient from './client';

export interface EnvFieldRow {
    key: string;
    label: string;
    kind: 'text' | 'password' | 'switch';
    secret: boolean;
    help: string;
    /** secret 字段恒为掩码 '••••••••'，空则 '' */
    value: string | boolean;
    has_value: boolean;
    source: 'db' | 'env';
}

export interface EnvConfigResponse {
    services: Record<string, EnvFieldRow[]>;
    notes: string[];
}

export interface EnvTestResult {
    service: string;
    status: 'ok' | 'down';
    latency_ms?: number;
    error?: string;
}

export interface EnvUpdateResult {
    message: string;
    changed: number;
    skipped: number;
    applied: { neo4j: boolean; minio: boolean };
}

export const envConfigApi = {
    get: () =>
        apiClient.get<EnvConfigResponse>('/api/admin/env-configs').then((r) => r.data),
    update: (updates: Record<string, string | boolean>) =>
        apiClient.put<EnvUpdateResult>('/api/admin/env-configs', { updates }).then((r) => r.data),
    test: (service: string) =>
        apiClient
            .post<EnvTestResult>('/api/admin/env-configs/test', { service })
            .then((r) => r.data),
};
