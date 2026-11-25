/**
 * 会话被踢下线时的回跳地址（2026-11-25）。
 * 只存站内路径，放 sessionStorage：同一标签页内刷新/重新登录后仍有效。
 */
const KEY = 'auth_return_to';

/** 记住当前页面（登录后回跳）。登录页自身不记，避免循环 */
export function rememberReturnPath(): void {
    const { pathname, search, hash } = window.location;
    if (pathname === '/login') return;
    try {
        sessionStorage.setItem(KEY, pathname + search + hash);
    } catch {
        // 隐私模式/禁用存储：放弃回跳，不影响登录
    }
}

/** 取出并清空回跳地址；只接受站内绝对路径，防开放重定向 */
export function consumeReturnPath(): string | null {
    try {
        const saved = sessionStorage.getItem(KEY);
        sessionStorage.removeItem(KEY);
        if (!saved) return null;
        return saved.startsWith('/') && !saved.startsWith('//') ? saved : null;
    } catch {
        return null;
    }
}

/** 主动退出登录时清掉历史回跳，避免下次登录被带到旧页面 */
export function clearReturnPath(): void {
    try {
        sessionStorage.removeItem(KEY);
    } catch {
        // 同上
    }
}
