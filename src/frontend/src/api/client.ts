import axios from 'axios';
import { rememberReturnPath } from '../shared/auth/returnPath';

// 运行时动态获取API基础URL - 不依赖构建时的环境变量
const getApiBaseUrl = () => {
    // 使用当前页面的 host（推荐）
    const currentHost = window.location.host;

    // 容器化/反代同域部署（nginx 已把 /api、/mcp 反代到后端）：
    // 页面端口不是 3001（后端直出）时 → 相对路径（nginx 或 vite dev proxy 同域转发）
    const portMatch = currentHost.match(/:(\d+)$/);
    const port = portMatch ? Number(portMatch[1]) : 80;
    if (port !== 3001) {
        return '';
    }

    // 如果是 localhost 或 127.0.0.1，使用 localhost:3001（与后端端口一致）
    const protocol = window.location.protocol;
    if (currentHost === 'localhost' || currentHost === '127.0.0.1') {
        return `${protocol}//localhost:3001`;
    }

    // 对于远程访问，移除端口号后加上:3001；沿用页面协议，避免 HTTPS 页面拼出 http 直链（混合内容）
    const hostWithoutPort = currentHost.replace(/:\d+$/, '');
    return `${protocol}//${hostWithoutPort}:3001`;
};

const API_BASE_URL = getApiBaseUrl();

const apiClient = axios.create({
    baseURL: API_BASE_URL,
    // 默认 120s（2026-11-25：原为 10 分钟，接口挂起时用户要等太久）。
    // 上传、导出、推理等等待久的接口在调用处单独放宽（见 api/projects.ts / documents.ts / reasoning.ts）
    timeout: 120_000,
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
/**
 * 刷新结果（2026-11-25）：失败时区分「凭证过期」和「网络/服务不可用」。
 * 后端重启、断网、刷新超时、限流都不能把用户踢下线——只有凭证类 4xx 才算会话失效。
 */
export type RefreshOutcome =
    | { ok: true; token: string }
    | { ok: false; failure: 'expired' | 'network' };

const REFRESH_TIMEOUT_MS = 10_000;

async function performRefresh(): Promise<RefreshOutcome> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return { ok: false, failure: 'expired' };
    try {
        const resp = await axios.post(
            `${API_BASE_URL}/api/auth/refresh`,
            { refresh_token: refreshToken },
            { timeout: REFRESH_TIMEOUT_MS },
        );
        const { access_token, refresh_token } = resp.data || {};
        if (!access_token) return { ok: false, failure: 'network' };
        localStorage.setItem('access_token', access_token);
        if (refresh_token) localStorage.setItem('refresh_token', refresh_token);
        return { ok: true, token: access_token };
    } catch (err: any) {
        const status = err?.response?.status;
        // 400/401/403 = 凭证无效；无响应 / 超时 / 429 / 5xx 是服务问题，不能踢下线
        const expired = status === 400 || status === 401 || status === 403;
        return { ok: false, failure: expired ? 'expired' : 'network' };
    }
}

let refreshPromise: Promise<RefreshOutcome> | null = null;

/** 全站唯一的刷新入口：并发调用共享同一次请求（ProtectedRoute / 401 拦截器 / SSE 共用） */
export function refreshSessionOnce(): Promise<RefreshOutcome> {
    if (!refreshPromise) {
        refreshPromise = performRefresh().finally(() => {
            refreshPromise = null;
        });
    }
    return refreshPromise;
}

/** 清掉本地会话（不跳转）；forceLogout 与 ProtectedRoute 共用 */
export function clearStoredSession(): void {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
}

let loggingOut = false;

function forceLogout() {
    // 并发多个 401 会同时走到这里：只跳转一次
    if (loggingOut) return;
    loggingOut = true;
    clearStoredSession();
    rememberReturnPath();
    window.location.href = '/login';
}

// 403 不再在拦截器里弹提示（2026-11-20）：业务侧 catch 统一走 shared/feedback 的
// notifyError(e, '…')，由 errorText 按错误码给出可行动文案，避免同一失败弹两条。

// 响应拦截器 - 处理认证错误
apiClient.interceptors.response.use(
    (response) => response,
    async (error) => {
        const status = error.response?.status;
        const original = error.config || {};

        if (status === 401 && !original._retry) {
            original._retry = true;
            const outcome = await refreshSessionOnce();
            if (outcome.ok) {
                // 重放时请求拦截器会从 localStorage 重新取新 token，不必手动改 headers
                return apiClient(original);
            }
            if (outcome.failure === 'expired') forceLogout();
            // 网络/服务不可用：保留登录态，把原始 401 交给调用方提示，下个请求会再试
            return Promise.reject(error);
        } else if (status === 401) {
            forceLogout();
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
    // 带取消信号或自定义请求头的 GET 不合并：合并键只表达 params/responseType，
    // 信号一方 abort 会连带取消另一方，头不同则可能串结果
    if (config?.signal || config?.headers) return rawGet(url, config);
    // 一次性票据不合并：两个 EventSource 必须各拿各的 ticket
    if (url.includes('/api/auth/sse-ticket')) return rawGet(url, config);
    let paramsKey: string;
    try {
        paramsKey = JSON.stringify(config?.params ?? null);
    } catch {
        return rawGet(url, config); // 参数不可序列化（循环引用等）：不参与合并
    }
    const key = `${url}?${paramsKey}#${config?.responseType ?? ''}@${config?.timeout ?? ''}`;
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

export { forceLogout, API_BASE_URL };
export default apiClient;
