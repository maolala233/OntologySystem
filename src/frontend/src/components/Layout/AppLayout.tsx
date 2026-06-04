import React, { useState, useEffect } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button, Modal, Input, Tag, Spin, Table } from 'antd';
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
    MessageOutlined,
    SendOutlined,
    BookOutlined,
    CheckCircleOutlined,
    ClearOutlined,
    LoadingOutlined,
    LockOutlined,
    TeamOutlined,
    EyeOutlined,
} from '@ant-design/icons';
import { Form, message, Switch } from 'antd';
import { systemApi } from '../../api/system';
import { authAPI } from '../../api/auth';
import apiClient from '../../api/client';
import { getDomains, KnowledgeDomain } from '../../api/domains';
import { projectsApi } from '../../api/projects';
import type { ProjectData } from '../../types/ontology';

const { Sider, Content } = Layout;
const { TextArea } = Input;

const UserManagement: React.FC = () => {
    const [users, setUsers] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [resetModalVisible, setResetModalVisible] = useState(false);
    const [resetUserId, setResetUserId] = useState<number | null>(null);
    const [resetUsername, setResetUsername] = useState('');
    const [newPassword, setNewPassword] = useState('');

    const fetchUsers = async () => {
        setLoading(true);
        try {
            const data = await authAPI.getUsers();
            setUsers(data);
        } catch {
            message.error('获取用户列表失败');
        }
        setLoading(false);
    };

    useEffect(() => {
        fetchUsers();
    }, []);

    const handleResetPassword = async () => {
        if (!resetUserId || !newPassword) {
            message.error('请输入新密码');
            return;
        }
        if (newPassword.length < 6) {
            message.error('密码长度不能少于6位');
            return;
        }
        try {
            await authAPI.resetPassword(resetUserId, newPassword);
            message.success(`用户 ${resetUsername} 密码重置成功`);
            setResetModalVisible(false);
            setNewPassword('');
        } catch {
            message.error('密码重置失败');
        }
    };

    const columns = [
        { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
        { title: '用户名', dataIndex: 'username', key: 'username' },
        {
            title: '状态',
            dataIndex: 'is_active',
            key: 'is_active',
            render: (active: boolean) => (
                <Tag color={active ? 'green' : 'red'}>{active ? '正常' : '禁用'}</Tag>
            ),
        },
        {
            title: '操作',
            key: 'action',
            width: 120,
            render: (_: any, record: any) => (
                <Button
                    size="small"
                    type="link"
                    onClick={() => {
                        setResetUserId(record.id);
                        setResetUsername(record.username);
                        setNewPassword('');
                        setResetModalVisible(true);
                    }}
                >
                    重置密码
                </Button>
            ),
        },
    ];

    return (
        <div>
            <Table
                columns={columns}
                dataSource={users}
                rowKey="id"
                loading={loading}
                pagination={false}
                size="small"
            />
            <Modal
                title={`重置密码 - ${resetUsername}`}
                open={resetModalVisible}
                onCancel={() => setResetModalVisible(false)}
                onOk={handleResetPassword}
                okText="确认重置"
            >
                <div className="py-2">
                    <div className="mb-1 text-sm font-medium">新密码</div>
                    <Input.Password
                        value={newPassword}
                        onChange={e => setNewPassword(e.target.value)}
                        placeholder="请输入新密码（至少6位）"
                    />
                </div>
            </Modal>
        </div>
    );
};

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

        checkMobile();
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
    const [passwordModalVisible, setPasswordModalVisible] = useState(false);
    const [userManagementVisible, setUserManagementVisible] = useState(false);
    const [configLoading, setConfigLoading] = useState(false);
    const [configForm] = Form.useForm();

    // 测试连通性状态
    const [testingLLM, setTestingLLM] = useState(false);
    const [testingNeo4J, setTestingNeo4J] = useState(false);
    const [testingEmbedding, setTestingEmbedding] = useState(false);
    const [testingMilvus, setTestingMilvus] = useState(false);
    const [testingVL, setTestingVL] = useState(false);
    const [vlConfigured, setVlConfigured] = useState(false);

    // GraphRAG 问答相关状态
    const [isQAModalOpen, setIsQAModalOpen] = useState(false);
    const [qaQuestion, setQaQuestion] = useState('');
    const [qaAnswer, setQaAnswer] = useState('');
    const [qaReferences, setQaReferences] = useState<any[]>([]);
    const [isQALoading, setIsQALoading] = useState(false);
    const [selectedQADomains, setSelectedQADomains] = useState<number[]>([]);
    const [availableDomains, setAvailableDomains] = useState<KnowledgeDomain[]>([]);
    const [isDomainsLoading, setIsDomainsLoading] = useState(false);
    const [qaProjectId, setQaProjectId] = useState<number | null>(null);
    const [qaProjects, setQaProjects] = useState<ProjectData[]>([]);
    const [isQaProjectsLoading, setIsQaProjectsLoading] = useState(false);
    const [showProjectSelector, setShowProjectSelector] = useState(false);

    const openConfigModal = async () => {
        setConfigLoading(true);
        try {
            const config = await systemApi.getConfig('llm_config');
            configForm.setFieldsValue(config.value);
            try {
                const vlConfig = await systemApi.getConfig('vl_config');
                if (vlConfig?.value) {
                    configForm.setFieldsValue({
                        vl_base_url: vlConfig.value.vl_base_url || '',
                        vl_api_key: vlConfig.value.vl_api_key || '',
                        vl_model: vlConfig.value.vl_model || '',
                        vl_disable_think: vlConfig.value.vl_disable_think !== undefined ? vlConfig.value.vl_disable_think : true,
                    });
                }
            } catch { /* vl_config not found */ }
            try {
                const vlStatus = await apiClient.get('/api/system/vl-status');
                setVlConfigured(vlStatus.data.configured);
            } catch { setVlConfigured(false); }
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
            const configValues = {
                ...values,
                streaming_enabled: values.streaming_enabled === true,
                milvus_enabled: values.milvus_enabled === true,
                disable_think: values.disable_think === true,
                vl_enabled: values.vl_enabled === true,
                chunk_overlap: Number(values.chunk_overlap) || 10,
            };
            await systemApi.updateConfig('llm_config', configValues);

            const vlConfigValues = {
                vl_base_url: values.vl_base_url || '',
                vl_api_key: values.vl_api_key || '',
                vl_model: values.vl_model || '',
                vl_disable_think: values.vl_disable_think === true,
            };
            await systemApi.updateConfig('vl_config', vlConfigValues);

            try {
                const vlStatus = await apiClient.get('/api/system/vl-status');
                setVlConfigured(vlStatus.data.configured);
            } catch { setVlConfigured(false); }

            message.success('系统配置已保存');
            setIsConfigModalOpen(false);
        } catch (error) {
            console.error('保存配置失败:', error);
            message.error('保存配置失败，请检查输入');
        }
    };

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
            message.error(`大模型连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingLLM(false);
        }
    };

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
            message.error(`Neo4j 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingNeo4J(false);
        }
    };

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
            message.error(`Embedding 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingEmbedding(false);
        }
    };

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
            message.error(`Milvus 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingMilvus(false);
        }
    };

    const testVLConnectivity = async () => {
        setTestingVL(true);
        try {
            const values = await configForm.validateFields();
            const vlConfig = {
                vl_base_url: values.vl_base_url,
                vl_api_key: values.vl_api_key,
                vl_model: values.vl_model,
            };
            const response = await apiClient.post('/api/system/test-connectivity/vl', vlConfig);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`VL 视觉模型测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingVL(false);
        }
    };

    // ==================== GraphRAG 问答相关函数 ====================

    // 加载知识域列表（用于问答多选）
    const loadAvailableDomains = async () => {
        setIsDomainsLoading(true);
        try {
            const domains = await getDomains();
            setAvailableDomains(domains);
        } catch (error: any) {
            message.error('加载知识域列表失败');
        } finally {
            setIsDomainsLoading(false);
        }
    };

    // 加载用户项目列表
    const loadQaProjects = async () => {
        setIsQaProjectsLoading(true);
        try {
            const projects = await projectsApi.getMyProjects();
            setQaProjects(projects);
        } catch (error: any) {
            message.error('加载项目列表失败');
        } finally {
            setIsQaProjectsLoading(false);
        }
    };

    // 打开问答 Modal
    const handleOpenQAModal = () => {
        // 获取当前项目 ID（从 URL 路径）
        const pathParts = location.pathname.split('/');
        const lastPart = pathParts[pathParts.length - 1];
        const projectIdFromUrl = lastPart && !isNaN(Number(lastPart)) ? Number(lastPart) : null;
        
        if (projectIdFromUrl) {
            // 如果当前在项目页面，直接使用该项目
            setQaProjectId(projectIdFromUrl);
            setShowProjectSelector(false);
        } else {
            // 否则显示项目选择器
            loadQaProjects();
            setShowProjectSelector(true);
        }
        
        setIsQAModalOpen(true);
        loadAvailableDomains();
    };

    // 选择项目
    const handleSelectProject = (projectId: number) => {
        setQaProjectId(projectId);
        setShowProjectSelector(false);
    };

    // 发送问题
    const handleSendQuestion = async () => {
        if (!qaQuestion.trim()) {
            message.warning('请输入问题');
            return;
        }

        if (!qaProjectId) {
            message.warning('请先选择项目');
            return;
        }

        setIsQALoading(true);
        try {
            // 构建选中的知识域字符串（逗号分隔）
            const selectedDomainsStr = selectedQADomains.length > 0
                ? selectedQADomains.map(id => {
                    const domain = availableDomains.find(d => d.id === id);
                    return domain?.name || '';
                }).filter(Boolean).join(',')
                : undefined;

            const response = await projectsApi.qaQuery(qaProjectId, qaQuestion, {
                selected_domains: selectedDomainsStr,
                top_k: 5,
            });

            setQaAnswer(response.answer || '未生成回答');
            setQaReferences(response.references || []);
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`问答失败：${errorDetail}`);
            setQaAnswer('问答失败，请稍后重试');
        } finally {
            setIsQALoading(false);
        }
    };

    // 知识域多选切换
    const handleQADomainToggle = (domainId: number) => {
        setSelectedQADomains(prev => {
            if (prev.includes(domainId)) {
                return prev.filter(id => id !== domainId);
            } else {
                return [...prev, domainId];
            }
        });
    };

    // 清空问答状态
    const handleClearQA = () => {
        setQaQuestion('');
        setQaAnswer('');
        setQaReferences([]);
        setSelectedQADomains([]);
    };

    const userMenuItems = [
        {
            key: 'change-password',
            icon: <LockOutlined />,
            label: '修改密码',
            onClick: () => setPasswordModalVisible(true),
        },
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
        // 暂时隐藏
        // {
        //     key: 'question',
        //     icon: <QuestionCircleOutlined />,
        //     label: '问答',
        // },
        // {
        //     key: 'question-test',
        //     icon: <MessageOutlined />,
        //     label: '问答测试',
        // },
    ];

    // 管理员专属菜单项
    const adminMenuItems = [];
    
    if (user.username === 'admin') {
        adminMenuItems.push({
            key: '/domain-management',
            icon: <ClusterOutlined />,
            label: '知识域管理',
        });
        adminMenuItems.push({
            key: 'user-management',
            icon: <TeamOutlined />,
            label: '用户管理',
        });
        adminMenuItems.push({
            key: 'system-config-trigger',
            icon: <SettingOutlined />,
            label: '系统配置',
        });
    }
    
    // 合并菜单项
    const allMenuItems = [...menuItems, ...adminMenuItems];

    return (
        <Layout className="min-h-screen">
            {/* 深色侧边栏 */}
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
                        items={allMenuItems}
                        onClick={({ key }) => {
                            if (key === 'user-management') {
                                setUserManagementVisible(true);
                                return;
                            }
                            if (key === 'system-config-trigger') {
                                openConfigModal();
                            } else if (key === 'question') {
                                // 打开外部问答系统
                                window.open('http://28.4.185.69:7861', '_blank');
                            } else if (key === 'question-test') {
                                // 打开 GraphRAG 问答测试 Modal
                                handleOpenQAModal();
                            } else {
                                navigate(key);
                            }
                        }}
                        className="border-r-0"
                    />
                </div>

                {/* 用户信息区域 */}
                <div
                    className="px-4 pb-2"
                    style={{
                        marginTop: 'auto',
                        zIndex: 10,
                        position: 'relative',
                        background: 'transparent'
                    }}
                >
                    <Dropdown menu={{ items: userMenuItems }} placement="topRight">
                        <div className="flex items-center space-x-3 p-2 rounded-lg hover:bg-gray-700 cursor-pointer transition-colors mb-1">
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

            <Modal
                title="修改密码"
                open={passwordModalVisible}
                onCancel={() => setPasswordModalVisible(false)}
                onOk={async () => {
                    const oldPwd = (document.getElementById('old-password') as HTMLInputElement)?.value;
                    const newPwd = (document.getElementById('new-password') as HTMLInputElement)?.value;
                    const confirmPwd = (document.getElementById('confirm-password') as HTMLInputElement)?.value;
                    if (!oldPwd || !newPwd || !confirmPwd) {
                        message.error('请填写所有字段');
                        return;
                    }
                    if (newPwd !== confirmPwd) {
                        message.error('两次输入的新密码不一致');
                        return;
                    }
                    if (newPwd.length < 6) {
                        message.error('新密码长度不能少于6位');
                        return;
                    }
                    try {
                        await authAPI.changePassword({ old_password: oldPwd, new_password: newPwd });
                        message.success('密码修改成功');
                        setPasswordModalVisible(false);
                    } catch (error: any) {
                        message.error(error?.response?.data?.detail || '密码修改失败');
                    }
                }}
                okText="确认修改"
            >
                <div className="space-y-4 py-2">
                    <div>
                        <div className="mb-1 text-sm font-medium">旧密码</div>
                        <Input.Password id="old-password" placeholder="请输入旧密码" />
                    </div>
                    <div>
                        <div className="mb-1 text-sm font-medium">新密码</div>
                        <Input.Password id="new-password" placeholder="请输入新密码（至少6位）" />
                    </div>
                    <div>
                        <div className="mb-1 text-sm font-medium">确认新密码</div>
                        <Input.Password id="confirm-password" placeholder="请再次输入新密码" />
                    </div>
                </div>
            </Modal>

            <Modal
                title="用户管理"
                open={userManagementVisible}
                onCancel={() => setUserManagementVisible(false)}
                footer={null}
                width={700}
            >
                <UserManagement />
            </Modal>

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
                        chunk_overlap: 10,
                        request_interval: 2,
                        llm_timeout: 300,
                        streaming_enabled: false,
                        milvus_enabled: false,
                        disable_think: true,
                        neo4j_uri: 'bolt://localhost:7687',
                        neo4j_username: 'neo4j',
                        neo4j_password: 'password',
                        embedding_base_url: 'http://localhost:11434/v1',
                        embedding_model: 'nomic-embed-text:latest',
                        milvus_host: '127.0.0.1',
                        milvus_port: '19530',
                        vl_base_url: '',
                        vl_api_key: '',
                        vl_model: '',
                        vl_disable_think: true,
                        vl_enabled: false,
                    }}
                >
                    {/* 大语言模型配置 */}
                    <div className="mb-2">
                        <h4 className="font-medium text-blue-700 mb-3 text-sm flex items-center gap-2">
                            <CloudServerOutlined />
                            大语言模型 (LLM)
                            <span className="text-xs text-gray-400 font-normal">— 用于骨架/实例提取</span>
                        </h4>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <Form.Item
                            name="base_url"
                            label="API Endpoint (Base URL)"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 API 端点' }]}
                        >
                            <Input placeholder="例如：https://api.openai.com/v1" />
                        </Form.Item>

                        <Form.Item
                            name="api_key"
                            label="API Key"
                            className="col-span-2"
                            rules={[{ required: false, message: '请输入 API Key' }]}
                        >
                            <Input.Password placeholder="sk-..." />
                        </Form.Item>

                        <Form.Item
                            name="model"
                            label="模型名称"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入模型名称' }]}
                        >
                            <Input placeholder="例如：gpt-3.5-turbo 或 gpt-4" />
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
                            tooltip="相邻分块间的重叠百分比，0-50%"
                        >
                            <Input type="number" suffix="%" min={0} max={50} />
                        </Form.Item>

                        <Form.Item
                            name="request_interval"
                            label="请求间隔 (Interval)"
                        >
                            <Input type="number" suffix="秒" />
                        </Form.Item>

                        <Form.Item
                            name="llm_timeout"
                            label="LLM 调用超时 (Timeout)"
                            tooltip="设置 LLM API 调用的超时时间，超过该时间将自动终止请求"
                        >
                            <Input type="number" suffix="秒" placeholder="300" />
                        </Form.Item>

                        <Form.Item
                            name="streaming_enabled"
                            valuePropName="checked"
                            className="col-span-2"
                            label="流式输出"
                        >
                            <Switch checkedChildren="关闭" unCheckedChildren="开启" />
                        </Form.Item>
                        <Form.Item
                            name="disable_think"
                            valuePropName="checked"
                            className="col-span-2"
                            label="思考模式"
                            tooltip="关闭可提升响应速度（Qwen3/Gemma等思考模型生效，仅Ollama）"
                        >
                            <Switch checkedChildren="关闭" unCheckedChildren="开启" />
                        </Form.Item>
                    </div>

                    {/* VL 视觉模型配置 */}
                    <div className="mt-4 pt-4 border-t border-gray-200">
                        <h4 className="font-medium text-purple-700 mb-3 text-sm flex items-center gap-2">
                            <EyeOutlined />
                            VL 视觉模型
                            <Tag color={vlConfigured ? "green" : "orange"} className="text-xs">
                                {vlConfigured ? "已配置" : "未配置"}
                            </Tag>
                            <span className="text-xs text-gray-400 font-normal">— 用于文档图片解析</span>
                        </h4>
                        <div className="bg-purple-50 p-2 rounded text-xs text-purple-700 mb-3">
                            配置支持视觉能力的模型（如 Qwen3.5、GPT-4o 等），独立于上方 LLM，专门用于识别文档中的流程图、截图、表格等图片内容。
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <Form.Item
                                name="vl_base_url"
                                label="VL API 地址"
                                className="col-span-2"
                            >
                                <Input placeholder="例如：http://localhost:11434/v1" />
                            </Form.Item>

                            <Form.Item
                                name="vl_api_key"
                                label="VL API Key"
                                className="col-span-2"
                            >
                                <Input.Password placeholder="留空则无需认证（如 Ollama）" />
                            </Form.Item>

                            <Form.Item
                                name="vl_model"
                                label="VL 模型名称"
                                className="col-span-2"
                            >
                                <Input placeholder="例如：qwen3.5:9b（需支持视觉能力）" />
                            </Form.Item>

                            <Form.Item
                                name="vl_disable_think"
                                valuePropName="checked"
                                className="col-span-2"
                                label="VL 思考模式"
                                tooltip="关闭可提升 VL 模型响应速度（Qwen3/Gemma等思考模型生效，仅Ollama）"
                            >
                                <Switch checkedChildren="关闭" unCheckedChildren="开启" />
                            </Form.Item>

                            <Form.Item
                                name="vl_enabled"
                                valuePropName="checked"
                                className="col-span-2"
                                label="VL 视觉解析"
                                tooltip="开启后使用视觉模型识别文档中的图片内容。需先配置 VL 模型并测试通过"
                            >
                                <Switch checkedChildren="关闭" unCheckedChildren="开启" disabled={!vlConfigured} />
                            </Form.Item>
                            {!vlConfigured && (
                                <div className="col-span-2 text-xs text-orange-600 bg-orange-50 p-2 rounded mb-2">
                                    ⚠️ 未配置 VL 视觉模型，VL 解析功能不可用。请填写上方 VL 模型地址和名称后保存，再测试连通性。
                                </div>
                            )}
                        </div>
                    </div>

                    {/* 图数据库 & 向量存储 */}
                    <div className="mt-4 pt-4 border-t border-gray-200">
                        <h4 className="font-medium text-gray-700 mb-3 text-sm flex items-center gap-2">
                            <DatabaseOutlined />
                            图数据库 & 向量存储
                        </h4>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <Form.Item
                            name="neo4j_uri"
                            label="Neo4j URI"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Neo4j URI' }]}
                        >
                            <Input placeholder="例如：bolt://localhost:7687" />
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

                        <Form.Item
                            name="milvus_enabled"
                            valuePropName="checked"
                            className="col-span-2"
                        >
                            <Switch checkedChildren="关闭" unCheckedChildren="开启" />
                        </Form.Item>

                        <Form.Item
                            name="embedding_base_url"
                            label="Embedding API 地址"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Embedding API 地址' }]}
                        >
                            <Input placeholder="例如：http://localhost:11434/v1" />
                        </Form.Item>

                        <Form.Item
                            name="embedding_api_key"
                            label="Embedding API Key"
                            className="col-span-2"
                        >
                            <Input.Password placeholder="留空则无需认证" />
                        </Form.Item>

                        <Form.Item
                            name="embedding_model"
                            label="Embedding 模型"
                            className="col-span-2"
                            rules={[{ required: true, message: '请输入 Embedding 模型名称' }]}
                        >
                            <Input placeholder="例如：nomic-embed-text:latest" />
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

                    {/* 测试连通性区域 */}
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
                                测试 Neo4j 连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testEmbeddingConnectivity}
                                loading={testingEmbedding}
                                icon={<ApiOutlined />}
                            >
                                测试 Embedding 连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testMilvusConnectivity}
                                loading={testingMilvus}
                                icon={<ClusterOutlined />}
                            >
                                测试 Milvus 连通性
                            </Button>
                            <Button
                                type="default"
                                onClick={testVLConnectivity}
                                loading={testingVL}
                                icon={<EyeOutlined />}
                                className="col-span-2"
                            >
                                测试 VL 视觉模型连通性
                            </Button>
                        </div>
                    </div>
                </Form>
            </Modal>

            {/* GraphRAG 问答 Modal */}
            <Modal
                title={
                    <div className="flex items-center gap-2">
                        <MessageOutlined className="text-green-500" />
                        <span>GraphRAG 问答测试</span>
                    </div>
                }
                open={isQAModalOpen}
                onCancel={() => {
                    setIsQAModalOpen(false);
                    handleClearQA();
                    setShowProjectSelector(false);
                }}
                footer={null}
                width={700}
            >
                <div className="py-2">
                    {/* 项目选择区域 */}
                    {showProjectSelector ? (
                        <div className="mb-4">
                            <div className="flex items-center gap-2 mb-2">
                                <DatabaseOutlined className="text-blue-500" />
                                <span className="font-medium text-gray-700">选择项目</span>
                            </div>
                            <div className="text-xs text-gray-500 mb-2">
                                请选择要进行问答的项目
                            </div>
                            {isQaProjectsLoading ? (
                                <div className="flex justify-center py-8">
                                    <Spin size="large" />
                                </div>
                            ) : qaProjects.length === 0 ? (
                                <div className="text-center py-8 text-gray-400">
                                    <DatabaseOutlined className="text-4xl mb-2" />
                                    <p>暂无可用项目</p>
                                    <p className="text-sm mt-2">请先在"我的项目"中创建项目</p>
                                </div>
                            ) : (
                                <div className="max-h-64 overflow-auto space-y-2">
                                    {qaProjects.map((project) => (
                                        <div
                                            key={project.id}
                                            className="p-3 border border-gray-200 rounded-lg hover:border-blue-500 hover:bg-blue-50 cursor-pointer transition-all"
                                            onClick={() => handleSelectProject(project.id)}
                                        >
                                            <div className="flex items-center justify-between">
                                                <div>
                                                    <div className="font-medium text-gray-800">{project.name}</div>
                                                    <div className="text-xs text-gray-500 mt-1">
                                                        {project.description || '暂无描述'}
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <Tag color={project.is_published ? 'green' : 'orange'}>
                                                        {project.is_published ? '已发布' : '草稿'}
                                                    </Tag>
                                                    <Tag color="blue">{project.domain?.name || '未分类'}</Tag>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ) : (
                        <>
                    {/* 当前项目显示 */}
                    <div className="mb-3 p-2 bg-blue-50 border border-blue-200 rounded-lg flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <DatabaseOutlined className="text-blue-500" />
                            <span className="text-sm text-gray-600">当前项目 ID: {qaProjectId}</span>
                        </div>
                        <Button
                            size="small"
                            onClick={() => {
                                loadQaProjects();
                                setShowProjectSelector(true);
                            }}
                        >
                            更换项目
                        </Button>
                    </div>

                    {/* 知识域多选区域 */}
                    <div className="mb-4">
                        <div className="flex items-center gap-2 mb-2">
                            <BookOutlined className="text-indigo-500" />
                            <span className="font-medium text-gray-700">选择知识域（多选）</span>
                        </div>
                        <div className="text-xs text-gray-500 mb-2">
                            选择要检索的知识域范围，不选择则默认在所有知识域中检索
                        </div>
                        {isDomainsLoading ? (
                            <div className="flex justify-center py-4">
                                <Spin size="small" />
                            </div>
                        ) : availableDomains.length === 0 ? (
                            <div className="text-center py-4 text-gray-400 text-sm">
                                暂无可用知识域
                            </div>
                        ) : (
                            <div className="flex flex-wrap gap-2 max-h-32 overflow-auto p-2 border border-gray-200 rounded-lg bg-gray-50">
                                {availableDomains.map((domain) => (
                                    <Tag
                                        key={domain.id}
                                        color={selectedQADomains.includes(domain.id) ? 'blue' : 'default'}
                                        className={`cursor-pointer transition-all ${
                                            selectedQADomains.includes(domain.id)
                                                ? 'border-blue-500 text-blue-600'
                                                : 'border-gray-300 hover:border-gray-400'
                                        }`}
                                        onClick={() => handleQADomainToggle(domain.id)}
                                    >
                                        {domain.name}
                                        {selectedQADomains.includes(domain.id) && (
                                            <CheckCircleOutlined className="ml-1 text-blue-500" />
                                        )}
                                    </Tag>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* 问题输入区域 */}
                    <div className="mb-4">
                        <div className="flex items-center gap-2 mb-2">
                            <SendOutlined className="text-blue-500" />
                            <span className="font-medium text-gray-700">问题</span>
                        </div>
                        <TextArea
                            value={qaQuestion}
                            onChange={(e) => setQaQuestion(e.target.value)}
                            placeholder="请输入您的问题，例如：什么是本体论？"
                            rows={3}
                            disabled={isQALoading}
                            onPressEnter={(e) => {
                                if (!e.shiftKey) {
                                    e.preventDefault();
                                    handleSendQuestion();
                                }
                            }}
                        />
                        <div className="flex justify-end mt-2">
                            <Button
                                type="primary"
                                icon={isQALoading ? <LoadingOutlined spin /> : <SendOutlined />}
                                onClick={handleSendQuestion}
                                loading={isQALoading}
                                disabled={!qaQuestion.trim()}
                                className="bg-green-600 hover:bg-green-700"
                            >
                                {isQALoading ? '生成中...' : '发送问题'}
                            </Button>
                        </div>
                    </div>

                    {/* 答案显示区域 */}
                    {qaAnswer && (
                        <div className="mb-4">
                            <div className="flex items-center gap-2 mb-2">
                                <CheckCircleOutlined className="text-green-500" />
                                <span className="font-medium text-gray-700">答案</span>
                            </div>
                            <div className="p-3 bg-green-50 border border-green-200 rounded-lg">
                                <div className="text-sm text-gray-800 whitespace-pre-wrap">{qaAnswer}</div>
                            </div>
                        </div>
                    )}

                    {/* 溯源引用区域 */}
                    {qaReferences && qaReferences.length > 0 && (
                        <div>
                            <div className="flex items-center gap-2 mb-2">
                                <BookOutlined className="text-purple-500" />
                                <span className="font-medium text-gray-700">溯源引用 ({qaReferences.length})</span>
                            </div>
                            <div className="max-h-48 overflow-auto space-y-2">
                                {qaReferences.map((ref, index) => (
                                    <div
                                        key={ref.id}
                                        className="p-2 bg-gray-50 border border-gray-200 rounded-lg text-sm"
                                    >
                                        <div className="flex items-center gap-2 mb-1">
                                            <Tag color="purple" className="font-medium">[{index + 1}]</Tag>
                                            <span className="text-gray-600 font-medium">{ref.file}</span>
                                        </div>
                                        <div className="text-gray-500 pl-8 line-clamp-2">
                                            {ref.quote}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 空状态提示 */}
                    {!qaAnswer && !isQALoading && !showProjectSelector && (
                        <div className="text-center py-8 text-gray-400">
                            <MessageOutlined className="text-4xl mb-2" />
                            <p>请输入问题开始问答</p>
                            <p className="text-sm mt-1">支持基于知识图谱的 RAG 检索和溯源</p>
                        </div>
                    )}
                    </>
                    )}
                </div>

                {/* 底部操作区 */}
                <div className="flex justify-between items-center pt-4 border-t border-gray-200">
                    <Button
                        onClick={handleClearQA}
                        icon={<ClearOutlined />}
                        disabled={isQALoading || (!qaAnswer && qaReferences.length === 0)}
                    >
                        清空
                    </Button>
                    <Button onClick={() => {
                        setIsQAModalOpen(false);
                        handleClearQA();
                    }}>
                        关闭
                    </Button>
                </div>
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