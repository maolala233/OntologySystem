/**
 * shared/auth/authStore - 登录用户与模块授权状态（M1，docs/design/05 §4.3）
 * 数据源：GET /api/auth/me（user + modules[]）。
 * 兼容说明：localStorage 'user' 同步维护，供暂未迁移的旧组件（OntologyBuilderPage 等）读取。
 */
import { create } from 'zustand';
import { authAPI } from '../../api/auth';

export interface AuthUser {
    id: number;
    username: string;
    role: 'admin' | 'user';
    display_name?: string | null;
    email?: string | null;
    locale: string;
}

interface AuthState {
    user: AuthUser | null;
    modules: string[];
    loaded: boolean;
    loading: boolean;
    /** 登录/注册后调用：用 token 拉取 /me 并填充 store */
    hydrate: () => Promise<void>;
    setTokens: (access: string, refresh: string) => void;
    clear: () => void;
    hasModule: (code: string) => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
    user: null,
    modules: [],
    loaded: false,
    loading: false,

    hydrate: async () => {
        if (get().loading) return;
        set({ loading: true });
        try {
            const me = await authAPI.getCurrentUser();
            const user: AuthUser = {
                id: me.id,
                username: me.username,
                role: me.role,
                display_name: me.display_name,
                email: me.email,
                locale: me.locale ?? 'zh-CN',
            };
            localStorage.setItem('user', JSON.stringify(user)); // 旧组件兼容
            set({ user, modules: me.modules ?? [], loaded: true });
        } catch {
            set({ user: null, modules: [], loaded: true });
        } finally {
            set({ loading: false });
        }
    },

    setTokens: (access, refresh) => {
        localStorage.setItem('access_token', access);
        localStorage.setItem('refresh_token', refresh);
    },

    clear: () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
        set({ user: null, modules: [], loaded: false });
    },

    hasModule: (code) => get().modules.includes(code),
}));
