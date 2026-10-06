/**
 * 管理后台 API（M1，docs/design/03 §3–4）
 */
import apiClient from './client';

export interface AdminUser {
    id: number;
    username: string;
    role: 'admin' | 'user';
    email?: string | null;
    display_name?: string | null;
    locale: string;
    is_active: boolean;
    app_role_id?: number | null;    // R12：应用的角色预设
    app_role_name?: string | null;
}

export interface AdminModule {
    code: string;
    name: string;
    description?: string | null;
    is_default_on: boolean;
    sort_order: number;
    enabled_users: number;
}

export interface GrantRow {
    module_code: string;
    allowed: boolean;
}

export interface AdminRole {
    id: number;
    name: string;
    description?: string | null;
    module_codes: string[];
    is_builtin: boolean;
}

export const adminApi = {
    listUsers: async (keyword?: string): Promise<AdminUser[]> => {
        const resp = await apiClient.get('/api/admin/users', { params: keyword ? { keyword } : {} });
        return resp.data.items;
    },

    createUser: async (data: {
        username: string;
        password: string;
        role: 'admin' | 'user';
        email?: string;
        display_name?: string;
    }): Promise<AdminUser> => {
        const resp = await apiClient.post('/api/admin/users', data);
        return resp.data;
    },

    patchUser: async (
        userId: number,
        data: { role?: string; is_active?: boolean; display_name?: string; reset_password?: boolean }
    ): Promise<AdminUser & { password?: string }> => {
        const resp = await apiClient.patch(`/api/admin/users/${userId}`, data);
        return resp.data;
    },

    listModules: async (): Promise<AdminModule[]> => {
        const resp = await apiClient.get('/api/admin/modules');
        return resp.data.items;
    },

    getGrants: async (userId: number): Promise<{ user_id: number; grants: GrantRow[] }> => {
        const resp = await apiClient.get('/api/admin/modules/grants', { params: { user_id: userId } });
        return resp.data;
    },

    putGrants: async (userId: number, grants: GrantRow[]): Promise<void> => {
        await apiClient.put('/api/admin/modules/grants', { user_id: userId, grants });
    },

    /** 全量用户授权矩阵（平铺）：{user_id: GrantRow[]} */
    getAllGrants: async (): Promise<Record<string, GrantRow[]>> => {
        const resp = await apiClient.get('/api/admin/modules/grants-all');
        return resp.data.items ?? {};
    },

    deleteUser: async (userId: number): Promise<void> => {
        await apiClient.delete(`/api/admin/users/${userId}`);
    },

    /** 用户名下项目（拥有 + 参与，去重） */
    listUserProjects: async (userId: number): Promise<{
        items: {
            id: number; name: string; status: string; is_published: boolean;
            role: 'owner' | 'editor' | 'viewer'; created_at: string | null;
        }[];
        owned_count: number;
    }> => {
        const resp = await apiClient.get(`/api/admin/users/${userId}/projects`);
        return resp.data;
    },

    // ── 角色配置（R8）：角色 = 模块授权预设
    listRoles: async (): Promise<AdminRole[]> => {
        const resp = await apiClient.get('/api/admin/roles');
        return resp.data.items ?? [];
    },

    createRole: async (data: { name: string; description?: string; module_codes: string[] }): Promise<AdminRole> => {
        const resp = await apiClient.post('/api/admin/roles', data);
        return resp.data;
    },

    updateRole: async (roleId: number, data: { name: string; description?: string; module_codes: string[] }): Promise<AdminRole> => {
        const resp = await apiClient.patch(`/api/admin/roles/${roleId}`, data);
        return resp.data;
    },

    deleteRole: async (roleId: number): Promise<void> => {
        await apiClient.delete(`/api/admin/roles/${roleId}`);
    },

    /** 应用角色到所选用户（全量覆盖 grants；admin 自动跳过） */
    applyRole: async (roleId: number, userIds: number[]): Promise<{
        applied: string[];
        skipped: { user_id: number; reason: string }[];
        role: string;
    }> => {
        const resp = await apiClient.post(`/api/admin/roles/${roleId}/apply`, { user_ids: userIds });
        return resp.data;
    },
};
