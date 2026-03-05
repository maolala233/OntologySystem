import React, { useState, useEffect } from 'react';
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
    ApiOutlined,
    ClusterOutlined,
    QuestionCircleOutlined,
} from '@ant-design/icons';
import { Modal, Form, Input, message, Divider, Switch } from 'antd';
import { systemApi } from '../../api/system';
import apiClient from '../../api/client';

const { Sider, Content } = Layout;

const AppLayout: React.FC = () => {
    const navigate = useNavigate();
    const location = useLocation();
    const [collapsed, setCollapsed] = useState(false);
    const [isMobile, setIsMobile] = useState(false);

    // 响应式布局：检测屏幕宽度
    useEffect(() => {
        const checkMobile = () => {
            const mobile = window.innerWidth < 768;
            setIsMobile(mobile);
            if (mobile) {
                setCollapsed(true);
            }
        };

        // 初始化检查
        checkMobile();

        // 监听窗口大小变化
        window.addEventListener('resize', checkMobile);
        return () => window.removeEventListener('resize', checkMobile);
    }, []);

    const user = JSON.parse(localStorage.getItem('user') || '{}');

    const handleLogout = () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        navigate('/login');
    };

    const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
    const [configLoading, setConfigLoading] = useState(false);
    const [configForm] = Form.useForm();

    // 新增：测试连通性状态
    const [testingLLM, setTestingLLM] = useState(false);
    const [testingNeo4J, setTestingNeo4J] = useState(false);
    const [testingEmbedding, setTestingEmbedding] = useState(false);
    const [testingMilvus, setTestingMilvus] = useState(false);

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
            // 确保所有配置项都包含在values中
            const configValues = {
                ...values,
                // 确保布尔值正确转换
                streaming_enabled: values.streaming_enabled === true,
                milvus_enabled: values.milvus_enabled === true,
            };
            await systemApi.updateConfig('llm_config', configValues);
            message.success('系统配置已保存');
            setIsConfigModalOpen(false);
        } catch (error) {
            console.error('保存配置失败:', error);
            message.error('保存配置失败，请检查输入');
        }
    };

    // 新增：测试大模型连通性
    const testLLMConnectivity = async () => {
        setTestingLLM(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/llm', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`大模型连通性测试失败: ${error.response?.data?.message || error.message}`);
        } finally {
            setTestingLLM(false);
        }
    };

    // 新增：测试Neo4j连通性
    const testNeo4JConnectivity = async () => {
        setTestingNeo4J(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/neo4j', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Neo4j连通性测试失败: ${error.response?.data?.message || error.message}`);
        } finally {
            setTestingNeo4J(false);
        }
    };

    // 新增：测试Embedding连通性
    const testEmbeddingConnectivity = async () => {
        setTestingEmbedding(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/embedding', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Embedding连通性测试失败: ${error.response?.data?.message || error.message}`);
        } finally {
            setTestingEmbedding(false);
        }
    };

    // 新增：测试Milvus连通性
    const testMilvusConnectivity = async () => {
        setTestingMilvus(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/milvus', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Milvus连通性测试失败: ${error.response?.data?.message || error.message}`);
        } finally {
            setTestingMilvus(false);
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
        {
            key: 'question',
            icon: <QuestionCircleOutlined />,
            label: '问答',
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
            {/* 深色侧边栏 - 固定宽度 240px，确保一致布局 */}
            <Sider
                collapsible
                collapsed={collapsed}
                onCollapse={setCollapsed}
                theme="dark"
                width={240}
                className="sticky top-0 h-screen flex flex-col"
                style={{
                    position: 'sticky',
                    top: 0,
                    height: '100vh',
                    boxShadow: '4px 0 12px rgba(0, 0, 0, 0.15)',
                    zIndex: 10,
                }}
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
                <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
                    <Menu
                        theme="dark"
                        mode="inline"
                        selectedKeys={[location.pathname]}
                        items={menuItems}
                        onClick={({ key }) => {
                            if (key === 'system-config-trigger') {
                                openConfigModal();
                            } else if (key === 'question') {
                                window.open('http://28.4.185.69:7861', '_blank');
                            } else {
                                navigate(key);
                            }
                        }}
                        className="border-r-0"
                    />
                </div>

                {/* 用户信息 - 强制贴合在底部收起按钮上方 */}
                <div
                    className="px-4"
                    style={{
                        marginTop: 'auto',
                        paddingBottom: '48px', // 精确匹配 Ant Design Sider Trigger 的高度
                        zIndex: 10,
                        position: 'relative',
                        background: 'transparent'
                    }}
                >
                    <Dropdown menu={{ items: userMenuItems }} placement="topRight">
                        <div className="flex items-center space-x-3 p-2 rounded-lg hover:bg-gray-700 cursor-pointer transition-colors">
                            <Avatar
                                size={collapsed ? 32 : 40}
                                icon={<UserOutlined />}
                                className="bg-gradient-to-br from-blue-500 to-purple-600"
                            />
                            {!collapsed && (
                                <div className="flex-1">
                                    <div className="text-white text-sm font-medium">
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
                        request_interval: 2,
                        streaming_enabled: false,
                        milvus_enabled: false,
                        neo4j_uri: 'bolt://localhost:7687',
                        neo4j_username: 'neo4j',
                        neo4j_password: 'password',
                        embedding_base_url: 'http://localhost:11434/v1',
                        embedding_model: 'nomic-embed-text:latest',
                        milvus_host: '127.0.0.1',
                        milvus_port: '19530'
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

                        {/* 新增：大模型流式开关 */}
                        <Form.Item
                            name="streaming_enabled"
                            valuePropName="checked"
                            className="col-span-2"
                        >
                            <Switch checkedChildren="流式启用" unCheckedChildren="流式禁用" />
                        </Form.Item>

                        {/* 新增：Neo4j配置 */}
                        <Form.Item
                            name="neo4j_uri"
                            label="Neo4j URI"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Neo4j URI' }]}
                        >
                            <Input placeholder="例如: bolt://localhost:7687" />
                        </Form.Item>

                        <Form.Item
                            name="neo4j_username"
                            label="Neo4j 用户名"
                            className="col-span-1"
                            rules={[{ required: true, message: '请输入 Neo4j 用户名' }]}
                        >
                            <Input placeholder="neo4j" />
                        </Form.Item>

                        <Form.Item
                            name="neo4j_password"
                            label="Neo4j 密码"
                            className="col-span-1"
                            rules={[{ required: true, message: '请输入 Neo4j 密码' }]}
                        >
                            <Input.Password placeholder="password" />
                        </Form.Item>

                        {/* 新增：Milvus配置 */}
                        <Form.Item
                            name="milvus_enabled"
                            valuePropName="checked"
                            className="col-span-2"
                        >
                            <Switch checkedChildren="Milvus启用" unCheckedChildren="Milvus禁用" />
                        </Form.Item>

                        <Form.Item
                            name="embedding_base_url"
                            label="Embedding API 地址"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Embedding API 地址' }]}
                        >
                            <Input placeholder="例如: http://localhost:11434/v1" />
                        </Form.Item>

                        <Form.Item
                            name="embedding_model"
                            label="Embedding 模型"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Embedding 模型名称' }]}
                        >
                            <Input placeholder="例如: nomic-embed-text:latest" />
                        </Form.Item>

                        <Form.Item
                            name="milvus_host"
                            label="Milvus 主机"
                            className="col-span-1"
                            rules={[{ required: true, message: '请输入 Milvus 主机地址' }]}
                        >
                            <Input placeholder="127.0.0.1" />
                        </Form.Item>

                        <Form.Item
                            name="milvus_port"
                            label="Milvus 端口"
                            className="col-span-1"
                            rules={[{ required: true, message: '请输入 Milvus 端口' }]}
                        >
                            <Input placeholder="19530" />
                        </Form.Item>
                    </div>

                    {/* 新增：测试连通性区域 */}
                    <div className="mt-6 pt-4 border-t border-gray-200">
                        <h3 className="font-medium text-gray-700 mb-3">连通性测试</h3>
                        <div className="grid grid-cols-2 gap-3">
                            <Button
                                type="default"
                                onClick={testLLMConnectivity}
                                loading={testingLLM}
                                icon={<CloudServerOutlined />}
                            >
                                测试大模型连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testNeo4JConnectivity}
                                loading={testingNeo4J}
                                icon={<DatabaseOutlined />}
                            >
                                测试Neo4j连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testEmbeddingConnectivity}
                                loading={testingEmbedding}
                                icon={<ApiOutlined />}
                            >
                                测试Embedding连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testMilvusConnectivity}
                                loading={testingMilvus}
                                icon={<ClusterOutlined />}
                            >
                                测试Milvus连通性
                            </Button>
                        </div>
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
