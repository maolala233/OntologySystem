import apiClient from './client';

export interface LoginRequest {
    username: string;
    password: string;
}

export interface RegisterRequest {
    username: string;
    password: string;
}

export interface AuthResponse {
    access_token: string;
    refresh_token?: string;
    token_type: string;
    user: {
        id: number;
        username: string;
    };
}

export interface MeResponse {
    id: number;
    username: string;
    role: 'admin' | 'user';
    display_name?: string | null;
    email?: string | null;
    locale: string;
    is_active: boolean;
    modules: string[];
}

export const authAPI = {
    login: async (data: LoginRequest): Promise<AuthResponse> => {
        const formData = new FormData();
        formData.append('username', data.username);
        formData.append('password', data.password);

        const response = await apiClient.post('/api/auth/login', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    register: async (data: RegisterRequest): Promise<AuthResponse> => {
        const response = await apiClient.post('/api/auth/register', data);
        return response.data;
    },

    getCurrentUser: async () => {
        const response = await apiClient.get('/api/auth/me');
        return response.data as MeResponse;
    },

    refresh: async (refresh_token: string) => {
        const response = await apiClient.post('/api/auth/refresh', { refresh_token });
        return response.data as AuthResponse;
    },

    updateMe: async (data: { display_name?: string; locale?: string }) => {
        const response = await apiClient.put('/api/auth/me', data);
        return response.data;
    },

    getSseTicket: async (taskId: string): Promise<{ ticket: string; expires_in: number }> => {
        const response = await apiClient.get('/api/auth/sse-ticket', { params: { task_id: taskId } });
        return response.data;
    },

    changePassword: async (data: { old_password: string; new_password: string }) => {
        const response = await apiClient.put('/api/auth/change-password', data);
        return response.data;
    },

    getUsers: async () => {
        const response = await apiClient.get('/api/auth/users');
        return response.data;
    },

    resetPassword: async (userId: number, new_password: string) => {
        const response = await apiClient.put(`/api/auth/users/${userId}/reset-password`, { new_password });
        return response.data;
    },
};
