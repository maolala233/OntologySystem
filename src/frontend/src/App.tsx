import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { FeedbackBridge } from './shared/feedback';

// 布局组件
import AppLayout from './components/Layout/AppLayout';
import ProtectedRoute from './components/ProtectedRoute';
import { RequireAdmin } from './shared/auth/guards';

// 页面组件
import LoginPage from './pages/LoginPage';
import HomePage from './pages/HomePage';
import MyProjectsPage from './pages/MyProjectsPage';
import QaPage from './pages/QaPage';
import ReportsPage from './pages/ReportsPage';
import McpConsolePage from './pages/McpConsolePage';
import BuilderPage from './features/builder/BuilderPage';
import ProjectOverviewPage from './pages/ProjectOverviewPage';
import BuildResultPage from './pages/BuildResultPage';
import AssetCenterPage from './pages/AssetCenterPage';
import AssetDetailPage from './pages/AssetDetailPage';
import ScorecardPage from './pages/ScorecardPage';
import DomainManagementPage from './pages/DomainManagementPage';
import UserAdminPage from './features/admin/UserAdminPage';
import ModuleGrantsPage from './features/admin/ModuleGrantsPage';
import ModelConfigsPage from './features/admin/ModelConfigsPage';
import EnvConfigPage from './features/admin/EnvConfigPage';
import { RequireModule, ExpertGuard } from './shared/auth/guards';
import { appTheme } from './theme';

/** 旧路由 /ontology-builder/:projectId → 新版详情页（一键构建形态） */
const RedirectBuilder: React.FC = () => {
    const { projectId } = useParams();
    return <Navigate to={`/projects/${projectId || ''}`} replace />;
};

function App() {
    return (
        <ConfigProvider locale={zhCN} theme={appTheme}>
            {/* AntdApp + FeedbackBridge：让 message/Modal 静态调用消费主题上下文（消 console 告警） */}
            <AntdApp>
                <FeedbackBridge />
            <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                <Routes>
                    {/* 公开路由 */}
                    <Route path="/login" element={<LoginPage />} />

                    {/* 受保护的路由 */}
                    <Route
                        path="/"
                        element={
                            <ProtectedRoute>
                                <AppLayout />
                            </ProtectedRoute>
                        }
                    >
                        <Route index element={<HomePage />} />
                        <Route path="my-projects" element={<RequireModule module="projects"><MyProjectsPage /></RequireModule>} />
                        {/* 新版详情页（默认）：后台构建状态 + 结果入口 + 发布/去问答 */}
                        <Route path="projects/:projectId" element={<ProjectOverviewPage />} />
                        <Route path="projects/:projectId/result" element={<BuildResultPage />} />
                        {/* 专家模式五 Tab（需 builder_pro 模块授权；未授权跳回新版详情页） */}
                        <Route path="projects/:projectId/documents" element={<ExpertGuard><BuilderPage /></ExpertGuard>} />
                        <Route path="projects/:projectId/schema" element={<ExpertGuard><BuilderPage /></ExpertGuard>} />
                        <Route path="projects/:projectId/graph" element={<ExpertGuard><BuilderPage /></ExpertGuard>} />
                        <Route path="projects/:projectId/timeline" element={<ExpertGuard><BuilderPage /></ExpertGuard>} />
                        {/* 构建器第五 Tab：项目内审核队列（用户在构建过程中查看审核情况） */}
                        <Route path="projects/:projectId/reviews" element={<ExpertGuard><BuilderPage /></ExpertGuard>} />
                        {/* O2-F7 本体健康记分卡（实时聚合机检与审核指标） */}
                        <Route path="projects/:projectId/scorecard" element={<ScorecardPage />} />
                        {/* 旧路由兼容重定向 */}
                        <Route path="ontology-builder" element={<Navigate to="/my-projects" replace />} />
                        <Route path="ontology-builder/:projectId" element={<RedirectBuilder />} />
                        <Route path="asset-center" element={<RequireModule module="asset_center"><AssetCenterPage /></RequireModule>} />
                        <Route path="asset-center/:projectId" element={<RequireModule module="asset_center"><AssetDetailPage /></RequireModule>} />
                        {/* M5 R8 本体问答独立页面（对话分组 + 模型选择） */}
                        <Route path="qa" element={<RequireModule module="qa"><QaPage /></RequireModule>} />
                        {/* R8 工具层：报告/PPT 生成 + MCP 服务控制台 */}
                        <Route path="tools/reports" element={<RequireModule module="report"><ReportsPage /></RequireModule>} />
                        <Route path="tools/ppt" element={<RequireModule module="ppt"><ReportsPage /></RequireModule>} />
                        <Route path="tools/mcp" element={<RequireModule module="mcp"><McpConsolePage /></RequireModule>} />
                        <Route path="domain-management" element={<DomainManagementPage />} />
                        {/* M1 管理后台（admin） */}
                        <Route path="admin/users" element={<RequireAdmin><UserAdminPage /></RequireAdmin>} />
                        <Route path="admin/modules" element={<RequireAdmin><ModuleGrantsPage /></RequireAdmin>} />
                        {/* M2 模型配置：admin 管全局配置；普通用户（项目 owner）可建项目级配置，页面内按角色自适应 */}
                        <Route path="admin/model-configs" element={<ModelConfigsPage />} />
                        {/* 环境配置（admin）：中间件连接参数 */}
                        <Route path="admin/env-configs" element={<RequireAdmin><EnvConfigPage /></RequireAdmin>} />
                    </Route>

                    {/* 404 重定向 */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </BrowserRouter>
            </AntdApp>
        </ConfigProvider>
    );
}

export default App;

