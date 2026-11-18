/**
 * 知识域管理 API
 */
import apiClient, { sharedGet } from './client';

export interface KnowledgeDomain {
    id: number;
    name: string;
    description: string | null;
    created_at: string;
    updated_at: string;
    project_count?: number;  // 项目总数（含未发布），与删除校验口径一致
}

export interface KnowledgeDomainCreate {
    name: string;
    description?: string;
}

export interface KnowledgeDomainUpdate {
    name?: string;
    description?: string;
}

export interface ProjectInDomain {
    id: number;
    name: string;
    description: string | null;
    is_published: boolean;
    domain_id: number | null;
    current_domain_name: string;
    class_count: number;
    instance_count: number;
    edge_count: number;
}

export interface ProjectMigrationItem {
    project_id: number;
    target_domain_id: number;
}

export interface BatchMigrationRequest {
    items: ProjectMigrationItem[];
}

export interface MigrateProjectsRequest {
    project_ids: number[];
    target_domain_id: number;
}

export interface MigrateProjectsResponse {
    message: string;
    migrated_count: number;
    target_domain: {
        id: number;
        name: string;
    };
}

/**
 * 知识域是「慢变参考数据」：资产中心、我的项目（新建弹窗）、知识域管理
 * 三处都要它，但它几乎不变。以前走裸 apiClient，等于每进一次页面就打一次；
 * 而且 React StrictMode 下 effect 跑两遍，同一次进入会连发两个 GET（2026-11-18 反馈「资产中心进页面 3 个请求，/api/domains 两次」）。
 * 这里两件事一起做：
 *   1) sharedGet —— 同时在飞的同 URL GET 合并成一次；
 *   2) 60s TTL —— 跨页面/跨弹窗复用，写操作立即失效，避免看到自己刚改完的旧数据。
 */
const DOMAINS_TTL_MS = 60_000;
let domainsCache: { at: number; data: KnowledgeDomain[] } | null = null;

/** 写操作后调用：本端刚改过，下一次读取必须回源 */
export const invalidateDomains = () => {
    domainsCache = null;
};

/**
 * 获取所有知识域列表
 */
export const getDomains = async (): Promise<KnowledgeDomain[]> => {
    if (domainsCache && Date.now() - domainsCache.at < DOMAINS_TTL_MS) {
        return domainsCache.data;
    }
    const data = await sharedGet<KnowledgeDomain[]>('/api/domains');
    domainsCache = { at: Date.now(), data };
    return data;
};

/**
 * 创建知识域
 */
export const createDomain = async (data: KnowledgeDomainCreate): Promise<KnowledgeDomain> => {
    const response = await apiClient.post('/api/domains', data);
    invalidateDomains();
    return response.data;
};

/**
 * 更新知识域
 */
export const updateDomain = async (id: number, data: KnowledgeDomainUpdate): Promise<KnowledgeDomain> => {
    const response = await apiClient.put(`/api/domains/${id}`, data);
    invalidateDomains();
    return response.data;
};

/**
 * 获取知识域下的项目列表
 */
export const getDomainProjects = async (domainId: number): Promise<ProjectInDomain[]> => {
    const response = await apiClient.get(`/api/domains/${domainId}/projects`);
    return response.data;
};

/**
 * 迁移知识域中的项目到另一个知识域（所有项目迁移到同一目标）
 */
export const migrateProjects = async (
    domainId: number,
    data: MigrateProjectsRequest
): Promise<MigrateProjectsResponse> => {
    // 构建 query params 字符串，确保 project_ids 重复传递
    const params = new URLSearchParams();
    params.append('target_domain_id', data.target_domain_id.toString());
    data.project_ids.forEach(id => params.append('project_ids', id.toString()));
    
    const response = await apiClient.post(
        `/api/domains/${domainId}/migrate-projects?${params.toString()}`,
        null
    );
    // 项目归属变了 → 各域的 project_count 也变了
    invalidateDomains();
    return response.data;
};

/**
 * 批量迁移项目到不同的目标知识域
 */
export const migrateProjectsBatch = async (
    domainId: number,
    data: BatchMigrationRequest
): Promise<{
    message: string;
    migrated_count: number;
    migration_details: Array<{
        project_id: number;
        project_name: string;
        from_domain: string;
        to_domain: string;
    }>;
}> => {
    const response = await apiClient.post(
        `/api/domains/${domainId}/migrate-projects-batch`,
        data
    );
    invalidateDomains();
    return response.data;
};

/**
 * 删除知识域（可选指定迁移目标）
 */
export const deleteDomain = async (
    id: number,
    migrateToDomainId?: number
): Promise<{ message: string }> => {
    const params: Record<string, string | number> = {};
    if (migrateToDomainId !== undefined) {
        params.migrate_to_domain_id = migrateToDomainId;
    }
    const response = await apiClient.delete(`/api/domains/${id}`, { params });
    invalidateDomains();
    return response.data;
};
