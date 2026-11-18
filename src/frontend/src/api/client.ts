import axios from 'axios';
import { message } from 'antd';

// 运行时动态获取API基础URL - 不依赖构建时的环境变量
const getApiBaseUrl = () => {
    // 方法 1: 从 URL 参数获取（用于测试）
    const urlParams = new URLSearchParams(window.location.search);
    const apiHost = urlParams.get('api_host');
    if (apiHost) {
        return `http://${apiHost}:8000`;
    }

    // 方法 2: 使用当前页面的 host（推荐）
    const currentHost = window.location.host;

    // 容器化/反代同域部署（nginx 已把 /api、/mcp 反代到后端）：
    // 页面端口不是 3001（后端直出）时 → 相对路径（nginx 或 vite dev proxy 同域转发）
    const portMatch = currentHost.match(/:(\d+)$/);
    const port = portMatch ? Number(portMatch[1]) : 80;
    if (port !== 3001) {
        return '';
    }

    // 如果是 localhost 或 127.0.0.1，使用 localhost:3001（与后端端口一致）
    if (currentHost === 'localhost' || currentHost === '127.0.0.1') {
        return 'http://localhost:3001';
    }

    // 对于远程访问，移除端口号后加上:3001
    const hostWithoutPort = currentHost.replace(/:\d+$/, '');
    return `http://${hostWithoutPort}:3001`;
};

const API_BASE_URL = getApiBaseUrl();

console.log('API Base URL:', API_BASE_URL); // 调试用

const apiClient = axios.create({
    baseURL: API_BASE_URL,
    timeout: 600000,
});

// 请求拦截器 - 添加认证token
apiClient.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('access_token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

// ---- M1：401 时用 refresh token 单飞刷新后重试（docs/design/05 §4.1）----
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return null;
    try {
        const resp = await axios.post(`${API_BASE_URL}/api/auth/refresh`, {
            refresh_token: refreshToken,
        });
        const { access_token, refresh_token } = resp.data;
        if (!access_token) return null;
        localStorage.setItem('access_token', access_token);
        if (refresh_token) localStorage.setItem('refresh_token', refresh_token);
        return access_token;
    } catch {
        return null;
    }
}

function forceLogout() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    window.location.href = '/login';
}

function refreshInFlight(): Promise<string | null> {
    if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => {
            refreshPromise = null;
        });
    }
    return refreshPromise;
}

// ---- M1：403 按错误码分支提示（docs/design/03 §2.3）----
function handle403(code: string | undefined) {
    if (code === 'MODULE_NOT_GRANTED') {
        message.error('未开通该功能模块，请联系管理员授权');
    } else if (code === 'ADMIN_REQUIRED') {
        message.error('该操作需要管理员权限');
    } else if (code === 'PUBLISHED_READONLY') {
        message.warning('公共资产为只读快照，如需修改请取消发布或另起新版本');
    } else if (code === 'PROJECT_FORBIDDEN') {
        message.error('无该项目访问权限');
    }
}

// 响应拦截器 - 处理认证错误
apiClient.interceptors.response.use(
    (response) => response,
    async (error) => {
        const status = error.response?.status;
        const errCode: string | undefined = error.response?.data?.error?.code;
        const original = error.config || {};

        if (status === 401 && !original._retry) {
            original._retry = true;
            const newToken = await refreshInFlight();
            if (newToken) {
                original.headers = { ...(original.headers || {}), Authorization: `Bearer ${newToken}` };
                return apiClient(original);
            }
            forceLogout();
        } else if (status === 401) {
            forceLogout();
        } else if (status === 403) {
            handle403(errCode);
        }
        return Promise.reject(error);
    }
);

// ---- 并发 GET 合并（统一入口）----
// 同一份数据经常被两处同时要：React StrictMode（仅开发模式）会把挂载 effect 跑两遍，
// 一个页面里的两个组件也可能各取一次。以前只有少数接口走 sharedGet，其余照样双发
// （2026-11-18：资产中心进页面 3 个请求，/api/domains 两次）。
// 现在直接在 axios 实例的 get 上合并：同 URL + 同参数、且在极短窗口内（同一轮挂载，
// 250ms）同时在飞的请求只发一次。不做结果缓存——请求结束即释放，之后再来照常回源，
// 删除/修改后的刷新不会被先前的旧响应污染。
const INFLIGHT_WINDOW_MS = 250;
const inflightGets = new Map<string, { at: number; p: Promise<unknown> }>();
const rawGet = apiClient.get.bind(apiClient) as typeof apiClient.get;

apiClient.get = ((url: string, config?: Parameters<typeof rawGet>[1]) => {
    // 带取消信号的 GET 不合并：合并后一方 abort 会连带取消另一方的请求
    if (config?.signal) return rawGet(url, config);
    // 一次性票据不合并：两个 EventSource 必须各拿各的 ticket
    if (url.includes('/api/auth/sse-ticket')) return rawGet(url, config);
    let paramsKey: string;
    try {
        paramsKey = JSON.stringify(config?.params ?? null);
    } catch {
        return rawGet(url, config); // 参数不可序列化（循环引用等）：不参与合并
    }
    const key = `${url}?${paramsKey}#${config?.responseType ?? ''}`;
    const hit = inflightGets.get(key);
    if (hit && Date.now() - hit.at < INFLIGHT_WINDOW_MS) return hit.p;
    const p = rawGet(url, config);
    const entry = { at: Date.now(), p: p as Promise<unknown> };
    inflightGets.set(key, entry);
    // 成功/失败都释放：失败不会被后续调用粘住
    const release = () => {
        if (inflightGets.get(key) === entry) inflightGets.delete(key);
    };
    p.then(release, release);
    return p;
}) as typeof apiClient.get;

/** 取响应体（同样享受并发合并）：等价于 apiClient.get(...).then(r => r.data) */
export function sharedGet<T = unknown>(url: string, config?: { params?: unknown }): Promise<T> {
    return apiClient.get<T>(url, config as any).then((r) => r.data);
}

export { refreshAccessToken, forceLogout, API_BASE_URL };
export default apiClient;
