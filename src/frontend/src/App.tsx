import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

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
import AssetCenterPage from './pages/AssetCenterPage';
import AssetDetailPage from './pages/AssetDetailPage';
import DomainManagementPage from './pages/DomainManagementPage';
import UserAdminPage from './features/admin/UserAdminPage';
import ModuleGrantsPage from './features/admin/ModuleGrantsPage';
import ModelConfigsPage from './features/admin/ModelConfigsPage';
import EnvConfigPage from './features/admin/EnvConfigPage';
import ReviewsPage from './features/reviews/ReviewsPage';
import { RequireModule } from './shared/auth/guards';

/** 旧路由 /ontology-builder/:projectId → /projects/:projectId/documents（05 §2 新信息架构） */
const RedirectBuilder: React.FC = () => {
    const { projectId } = useParams();
    return <Navigate to={`/projects/${projectId || ''}/documents`} replace />;
};

function App() {
    return (
        <ConfigProvider locale={zhCN}>
            <BrowserRouter>
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
                        <Route path="my-projects" element={<MyProjectsPage />} />
                        {/* M3-7 构建器四 Tab（05 §2）：URL 可分享 */}
                        <Route path="projects/:projectId/documents" element={<BuilderPage />} />
                        <Route path="projects/:projectId/schema" element={<BuilderPage />} />
                        <Route path="projects/:projectId/graph" element={<BuilderPage />} />
                        <Route path="projects/:projectId/timeline" element={<BuilderPage />} />
                        <Route path="projects/:projectId" element={<Navigate to="documents" replace />} />
                        {/* 旧路由兼容重定向 */}
                        <Route path="ontology-builder" element={<Navigate to="/my-projects" replace />} />
                        <Route path="ontology-builder/:projectId" element={<RedirectBuilder />} />
                        {/* M3-7 审核工作台（跨项目队列，05 §6.8） */}
                        <Route path="reviews" element={<RequireModule module="review"><ReviewsPage /></RequireModule>} />
                        <Route path="asset-center" element={<AssetCenterPage />} />
                        <Route path="asset-center/:projectId" element={<AssetDetailPage />} />
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
        </ConfigProvider>
    );
}

export default App;

