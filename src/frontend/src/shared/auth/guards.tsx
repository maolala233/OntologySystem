/**
 * shared/auth/guards - 路由守卫组件（M1，docs/design/05 §2/§6.10）
 * RequireModule：模块码守卫 —— 未授权渲染 NoAccess（不是静默跳转）。
 * RequireAdmin：平台角色守卫 —— /admin/* 路由使用。
 */
import React, { useEffect } from 'react';
import { Result, Button, Spin } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from './authStore';

const NoAccess: React.FC<{ onBack: () => void; reason: string }> = ({ onBack, reason }) => (
    <Result
        status="403"
        title="无访问权限"
        subTitle={reason}
        extra={<Button type="primary" onClick={onBack}>返回工作台</Button>}
    />
);

export const RequireModule: React.FC<{ module: string; children: React.ReactElement }> = ({
    module,
    children,
}) => {
    const { user, modules, loaded, hydrate } = useAuthStore();
    const navigate = useNavigate();

    useEffect(() => {
        if (!loaded && localStorage.getItem('access_token')) {
            hydrate();
        }
    }, [loaded, hydrate]);

    if (!loaded) {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }
    if (!user) {
        return <NoAccess reason="登录状态已失效" onBack={() => navigate('/login')} />;
    }
    if (user.role !== 'admin' && !modules.includes(module)) {
        return <NoAccess reason={`未开通模块「${module}」，请联系管理员授权`} onBack={() => navigate('/')} />;
    }
    return children;
};

export const RequireAdmin: React.FC<{ children: React.ReactElement }> = ({ children }) => {
    const { user, loaded, hydrate } = useAuthStore();
    const navigate = useNavigate();

    useEffect(() => {
        if (!loaded && localStorage.getItem('access_token')) {
            hydrate();
        }
    }, [loaded, hydrate]);

    if (!loaded) {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }
    if (!user || user.role !== 'admin') {
        return <NoAccess reason="需要管理员权限" onBack={() => navigate('/')} />;
    }
    return children;
};

export default RequireModule;
