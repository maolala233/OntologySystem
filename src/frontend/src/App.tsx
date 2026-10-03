import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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
import OntologyBuilderPage from './pages/OntologyBuilderPage';
import AssetCenterPage from './pages/AssetCenterPage';
import AssetDetailPage from './pages/AssetDetailPage';
import DomainManagementPage from './pages/DomainManagementPage';
import UserAdminPage from './features/admin/UserAdminPage';
import ModuleGrantsPage from './features/admin/ModuleGrantsPage';
import ModelConfigsPage from './features/admin/ModelConfigsPage';

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
                        <Route path="ontology-builder" element={<OntologyBuilderPage />} />
                        <Route path="ontology-builder/:projectId" element={<OntologyBuilderPage />} />
                        <Route path="asset-center" element={<AssetCenterPage />} />
                        <Route path="asset-center/:projectId" element={<AssetDetailPage />} />
                        <Route path="domain-management" element={<DomainManagementPage />} />
                        {/* M1 管理后台（admin） */}
                        <Route path="admin/users" element={<RequireAdmin><UserAdminPage /></RequireAdmin>} />
                        <Route path="admin/modules" element={<RequireAdmin><ModuleGrantsPage /></RequireAdmin>} />
                        {/* M2 模型配置（admin） */}
                        <Route path="admin/model-configs" element={<RequireAdmin><ModelConfigsPage /></RequireAdmin>} />
                    </Route>

                    {/* 404 重定向 */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </BrowserRouter>
        </ConfigProvider>
    );
}

export default App;

