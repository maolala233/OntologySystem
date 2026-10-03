import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { Spin } from 'antd';
import { jwtDecode } from 'jwt-decode';
import { refreshAccessToken } from '../api/client';

interface ProtectedRouteProps {
    children: React.ReactElement;
}

/**
 * 路由守卫（M1 改造，docs/design/05 §2）：
 * 存在 token 且未过期 → 放行；已过期 → 静默刷新一次；失败 → 登录页。
 */
const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
    const [state, setState] = useState<'checking' | 'ok' | 'fail'>('checking');

    useEffect(() => {
        (async () => {
            const token = localStorage.getItem('access_token');
            if (!token) {
                setState('fail');
                return;
            }
            try {
                const claims = jwtDecode<{ exp?: number }>(token);
                if (claims.exp && claims.exp * 1000 > Date.now() + 30_000) {
                    setState('ok');
                    return;
                }
            } catch {
                // 无法解析的 token 视为过期，走刷新
            }
            const newToken = await refreshAccessToken();
            setState(newToken ? 'ok' : 'fail');
        })();
    }, []);

    if (state === 'checking') {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }
    if (state === 'fail') {
        return <Navigate to="/login" replace />;
    }
    return children;
};

export default ProtectedRoute;
