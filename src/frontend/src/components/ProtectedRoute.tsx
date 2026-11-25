import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { Button, Result, Spin } from 'antd';
import { jwtDecode } from 'jwt-decode';
import { clearStoredSession, refreshSessionOnce } from '../api/client';
import { rememberReturnPath } from '../shared/auth/returnPath';

interface ProtectedRouteProps {
    children: React.ReactElement;
}

type CheckState = 'checking' | 'ok' | 'fail' | 'offline';

/**
 * 路由守卫（M1 改造，docs/design/05 §2）：
 * 存在 token 且未过期 → 放行；已过期 → 静默刷新一次；凭证失效 → 登录页。
 *
 * 2026-11-25：区分「凭证过期」与「网络/服务不可用」——后者保留登录态并给重试，
 * 不再因为后端重启/断网把用户踢回登录页；被踢时记住当前页面，登录后回跳。
 */
const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
    const [state, setState] = useState<CheckState>('checking');
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setState('checking');
            const token = localStorage.getItem('access_token');
            if (!token) {
                if (!cancelled) setState('fail');
                return;
            }
            try {
                const claims = jwtDecode<{ exp?: number }>(token);
                if (claims.exp && claims.exp * 1000 > Date.now() + 30_000) {
                    if (!cancelled) setState('ok');
                    return;
                }
            } catch {
                // 无法解析的 token 视为过期，走刷新
            }
            const outcome = await refreshSessionOnce();
            if (cancelled) return;
            if (outcome.ok) {
                setState('ok');
                return;
            }
            if (outcome.failure === 'expired') {
                clearStoredSession();
                rememberReturnPath();
                setState('fail');
            } else {
                setState('offline');
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [attempt]);

    if (state === 'checking') {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }
    if (state === 'offline') {
        return (
            <Result
                status="warning"
                title="无法连接服务器"
                subTitle="网络或服务暂时不可用，登录状态已保留。请检查网络后重试。"
                extra={<Button type="primary" onClick={() => setAttempt((n) => n + 1)}>重试</Button>}
            />
        );
    }
    if (state === 'fail') {
        return <Navigate to="/login" replace />;
    }
    return children;
};

export default ProtectedRoute;
