/**
 * shared/auth/guards - 路由守卫组件（M1，docs/design/05 §2/§6.10）
 * RequireModule：模块码守卫 —— 未授权渲染 NoAccess（不是静默跳转）。
 * RequireAdmin：平台角色守卫 —— /admin/* 路由使用。
 */
import React, { useEffect } from 'react';
import { Result, Button, Spin } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
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

/**
 * ExpertGuard：专家模式（多 Tab 构建工作台）守卫。
 * 需 builder_pro 模块授权——未授权用户不渲染 403，直接跳回新版项目详情页
 * （新手形态：一键构建 + 结果查看，见 /projects/:id）。
 */
export const ExpertGuard: React.FC<{ children: React.ReactElement }> = ({ children }) => {
    const { loaded, hydrate } = useAuthStore();
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();

    useEffect(() => {
        if (!loaded && localStorage.getItem('access_token')) {
            hydrate();
        }
    }, [loaded, hydrate]);

    if (!loaded) {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }
    return <ExpertCheck projectId={projectId} onFallback={() => navigate(`/projects/${projectId}`, { replace: true })}>
        {children}
    </ExpertCheck>;
};

const ExpertCheck: React.FC<{ projectId?: string; onFallback: () => void; children: React.ReactElement }> = ({
    projectId, onFallback, children,
}) => {
    const { hasModule, user } = useAuthStore();
    const allowed = user?.role === 'admin' || hasModule('builder_pro');
    useEffect(() => {
        // admin 与已授权用户直通；未授权静默跳新版详情页
        if (projectId && !allowed) {
            onFallback();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, allowed]);
    if (projectId && !allowed) return null;
    return children;
};

export default RequireModule;
