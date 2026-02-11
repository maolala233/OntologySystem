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
    CloudServerOutlined,
    InfoCircleOutlined,
} from '@ant-design/icons';
import { Modal, Form, Input, message, Divider } from 'antd';
import { systemApi } from '../../api/system';

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

    const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
    const [configLoading, setConfigLoading] = useState(false);
    const [configForm] = Form.useForm();

    const openConfigModal = async () => {
        setConfigLoading(true);
        try {
            const config = await systemApi.getConfig('llm_config');
            configForm.setFieldsValue(config.value);
            setIsConfigModalOpen(true);
        } catch (error) {
            message.error('获取配置失败');
        } finally {
            setConfigLoading(false);
        }
    };

    const handleSaveConfig = async () => {
        try {
            const values = await configForm.validateFields();
            await systemApi.updateConfig('llm_config', values);
            message.success('系统配置已保存');
            setIsConfigModalOpen(false);
        } catch (error) {
            console.error('保存配置失败:', error);
        }
    };

    const userMenuItems = [
        ...(user.username === 'admin' ? [{
            key: 'settings',
            icon: <SettingOutlined />,
            label: '设置',
            onClick: openConfigModal,
        }] : []),
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
            key: '/ontology-builder',
            icon: <AppstoreOutlined />,
            label: '本体构建',
        },
        {
            key: '/my-projects',
            icon: <UserOutlined />,
            label: '我的项目',
        },
        {
            key: '/asset-center',
            icon: <DatabaseOutlined />,
            label: '资产中心',
        },
    ];

    if (user.username === 'admin') {
        menuItems.push({
            key: 'system-config-trigger',
            icon: <SettingOutlined />,
            label: '系统配置',
        });
    }

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
                    onClick={({ key }) => {
                        if (key === 'system-config-trigger') {
                            openConfigModal();
                        } else {
                            navigate(key);
                        }
                    }}
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

            {/* 系统配置 Modal */}
            <Modal
                title={
                    <div className="flex items-center space-x-2">
                        <CloudServerOutlined style={{ color: '#1890ff' }} />
                        <span>模型服务配置 (仅管理员)</span>
                    </div>
                }
                open={isConfigModalOpen}
                onOk={handleSaveConfig}
                onCancel={() => setIsConfigModalOpen(false)}
                width={600}
                okText="保存配置"
                cancelText="取消"
                maskClosable={false}
                confirmLoading={configLoading}
            >
                <div className="bg-yellow-50 p-3 mb-4 rounded border border-yellow-100 flex items-start space-x-2">
                    <InfoCircleOutlined className="mt-1 text-yellow-600" />
                    <div className="text-yellow-800 text-sm">
                        此处的配置将覆盖环境变量中的默认设置。修改后将立即在自动提取和构建任务中生效。
                    </div>
                </div>

                <Form
                    form={configForm}
                    layout="vertical"
                    initialValues={{
                        api_key: '',
                        base_url: '',
                        model: '',
                        chunk_size: 15000,
                        chunk_overlap: 500,
                        request_interval: 2
                    }}
                >
                    <div className="grid grid-cols-2 gap-4">
                        <Form.Item
                            name="base_url"
                            label="API Endpoint (Base URL)"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 API 端点' }]}
                        >
                            <Input placeholder="例如: https://api.openai.com/v1" />
                        </Form.Item>

                        <Form.Item
                            name="api_key"
                            label="API Key"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 API Key' }]}
                        >
                            <Input.Password placeholder="sk-..." />
                        </Form.Item>

                        <Form.Item
                            name="model"
                            label="模型名称"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入模型名称' }]}
                        >
                            <Input placeholder="例如: gpt-3.5-turbo 或 gpt-4" />
                        </Form.Item>

                        <Form.Item
                            name="chunk_size"
                            label="提取分块大小 (Chunk Size)"
                        >
                            <Input type="number" suffix="字符" />
                        </Form.Item>

                        <Form.Item
                            name="chunk_overlap"
                            label="分块重叠 (Overlap)"
                        >
                            <Input type="number" suffix="字符" />
                        </Form.Item>

                        <Form.Item
                            name="request_interval"
                            label="请求间隔 (Interval)"
                        >
                            <Input type="number" suffix="秒" />
                        </Form.Item>
                    </div>
                </Form>
            </Modal>

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
