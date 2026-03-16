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
 * 获取知识域下的项目列表
 */
export const getDomainProjects = async (domainId: number): Promise<ProjectInDomain[]> => {
    const response = await apiClient.get(`/domains/${domainId}/projects`);
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
        `/domains/${domainId}/migrate-projects?${params.toString()}`,
        null
    );
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
        `/domains/${domainId}/migrate-projects-batch`,
        data
    );
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
    const response = await apiClient.delete(`/domains/${id}`, { params });
    return response.data;
};
