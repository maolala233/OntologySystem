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
};
