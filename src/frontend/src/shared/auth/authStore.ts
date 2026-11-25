/**
 * shared/auth/authStore - 登录用户与模块授权状态（M1，docs/design/05 §4.3）
 * 数据源：GET /api/auth/me（user + modules[]）。
 * 兼容说明：localStorage 'user' 同步维护，供暂未迁移的旧组件（AppLayout、SchemaTab）读取。
 */
import { create } from 'zustand';
import { authAPI } from '../../api/auth';
import { clearStoredSession } from '../../api/client';

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
    /** /me 因网络或服务不可用而失败（不是会话失效），守卫据此显示重试 */
    loadError: boolean;
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
    loadError: false,

    hydrate: async () => {
        if (get().loading) return;
        set({ loading: true, loadError: false });
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
            set({ user, modules: me.modules ?? [], loaded: true, loadError: false });
        } catch (err: any) {
            if (err?.response?.status === 401) {
                // 会话确实失效（刷新失败时 401 拦截器会整页跳转，这里兜底清态）
                clearStoredSession();
                set({ user: null, modules: [], loaded: true, loadError: false });
            } else {
                // 网络 / 服务不可用：保留已有用户，页面显示「重试」而不是当成未登录
                set({ loaded: true, loadError: true });
            }
        } finally {
            set({ loading: false });
        }
    },

    setTokens: (access, refresh) => {
        localStorage.setItem('access_token', access);
        // 后端没给 refresh_token 时不要写入空串：空串会被当成「有 refresh token」
        if (refresh) localStorage.setItem('refresh_token', refresh);
        else localStorage.removeItem('refresh_token');
    },

    clear: () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
        set({ user: null, modules: [], loaded: false, loadError: false });
    },

    hasModule: (code) => get().modules.includes(code),
}));

/** 读取 localStorage 里缓存的用户（旧组件兼容）。
 *  脏数据 / 旧版本结构返回 null，避免渲染期 JSON.parse 抛错导致白屏（2026-11-25） */
export function readCachedUser(): Partial<AuthUser> | null {
    try {
        const raw = localStorage.getItem('user');
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? (parsed as Partial<AuthUser>) : null;
    } catch {
        return null;
    }
}
