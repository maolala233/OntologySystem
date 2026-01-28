import React, { useState } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button } from 'antd';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import {
    HomeOutlined,
    UserOutlined,
    AppstoreOutlined,
    DatabaseOutlined,
    LogoutOutlined,
    SettingOutlined,
} from '@ant-design/icons';

const { Sider, Content } = Layout;

const AppLayout: React.FC = () => {
    const navigate = useNavigate();
    const location = useLocation();
    const [collapsed, setCollapsed] = useState(false);

    const user = JSON.parse(localStorage.getItem('user') || '{}');

    const handleLogout = () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        navigate('/login');
    };

    const userMenuItems = [
        {
            key: 'settings',
            icon: <SettingOutlined />,
            label: '设置',
        },
        {
            type: 'divider' as const,
        },
        {
            key: 'logout',
            icon: <LogoutOutlined />,
            label: '退出登录',
            onClick: handleLogout,
        },
    ];

    const menuItems = [
        {
            key: '/',
            icon: <HomeOutlined />,
            label: '首页',
        },
        {
            key: '/my-projects',
            icon: <UserOutlined />,
            label: '我的项目',
        },
        {
            key: '/ontology-builder',
            icon: <AppstoreOutlined />,
            label: '本体构建',
        },
        {
            key: '/asset-center',
            icon: <DatabaseOutlined />,
            label: '资产中心',
        },
    ];

    return (
        <Layout className="min-h-screen">
            {/* 深色侧边栏 */}
            <Sider
                collapsible
                collapsed={collapsed}
                onCollapse={setCollapsed}
                theme="dark"
                width={240}
                className="shadow-lg"
            >
                {/* Logo 区域 */}
                <div className="h-16 flex items-center justify-center bg-[#001529] border-b border-gray-700">
                    <div className="flex items-center space-x-2">
                        <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center">
                            <span className="text-white font-bold text-lg">O</span>
                        </div>
                        {!collapsed && (
                            <span className="text-white font-semibold text-lg">Ontology</span>
                        )}
                    </div>
                </div>

                {/* 导航菜单 */}
                <Menu
                    theme="dark"
                    mode="inline"
                    selectedKeys={[location.pathname]}
                    items={menuItems}
                    onClick={({ key }) => navigate(key)}
                    className="border-r-0"
                />

                {/* 用户信息 */}
                <div className="absolute bottom-4 left-0 right-0 px-4">
                    <Dropdown menu={{ items: userMenuItems }} placement="topRight">
                        <div className="flex items-center space-x-3 p-3 rounded-lg hover:bg-gray-700 cursor-pointer transition-colors">
                            <Avatar
                                size={collapsed ? 32 : 40}
                                icon={<UserOutlined />}
                                className="bg-gradient-to-br from-blue-500 to-purple-600"
                            />
                            {!collapsed && (
                                <div className="flex-1 overflow-hidden">
                                    <div className="text-white text-sm font-medium truncate">
                                        {user.username || '用户'}
                                    </div>
                                </div>
                            )}
                        </div>
                    </Dropdown>
                </div>
            </Sider>

            {/* 主内容区 */}
            <Layout>
                <Content className="bg-gray-50">
                    <Outlet />
                </Content>
            </Layout>
        </Layout>
    );
};

export default AppLayout;
