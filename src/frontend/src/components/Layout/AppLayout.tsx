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
    SafetyOutlined,
} from '@ant-design/icons';
import { Form, message, Switch } from 'antd';
import { authAPI } from '../../api/auth';
import apiClient from '../../api/client';
import { getDomains, KnowledgeDomain } from '../../api/domains';
import { CONNECTIVITY_FIELDS, describeConnectivityError } from '../../utils/connectivity';
import { projectsApi } from '../../api/projects';
import { useAuthStore } from '../../shared/auth/authStore';
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

    // M1：用户与模块授权状态（authStore 为源，localStorage 兜底供旧组件读取）
    const { user: storeUser, modules, loaded, hydrate, clear: clearAuth } = useAuthStore();
    const user = (storeUser || JSON.parse(localStorage.getItem('user') || '{}')) as {
        id?: number;
        username: string;
        role?: 'admin' | 'user';
        display_name?: string | null;
    };

    useEffect(() => {
        if (!loaded && localStorage.getItem('access_token')) {
            hydrate();
        }
    }, [loaded, hydrate]);

    // 菜单可见性：admin 全量；普通用户按授权矩阵；矩阵未加载完成前放行基础项（避免闪烁）
    const hasModule = (code: string) =>
        user.role === 'admin' ? true : modules.length > 0 ? modules.includes(code) : true;

    const handleLogout = () => {
        clearAuth();
        navigate('/login');
    };

    const [passwordModalVisible, setPasswordModalVisible] = useState(false);
    const [userManagementVisible, setUserManagementVisible] = useState(false);

    // 测试连通性状态

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
        ...(user.role === 'admin' ? [{
            key: 'settings',
            icon: <SettingOutlined />,
            label: '设置',
            onClick: () => navigate('/admin/model-configs'),
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

    // M1：菜单按授权矩阵过滤（模块码见 docs/design/README §4.1）
    const menuItems = [
        {
            key: '/',
            icon: <HomeOutlined />,
            label: '工作台',
            module: 'dashboard',
        },
        {
            key: '/ontology-builder',
            icon: <AppstoreOutlined />,
            label: '本体构建',
            module: 'projects',
        },
        {
            key: '/my-projects',
            icon: <UserOutlined />,
            label: '我的项目',
            module: 'projects',
        },
        {
            key: '/asset-center',
            icon: <DatabaseOutlined />,
            label: '资产中心',
            module: 'asset_center',
        },
    ].filter((item) => hasModule(item.module));

    // 管理员专属菜单项（03 §3–4：用户管理/模块授权为 /admin/* 页面）
    const adminMenuItems = [];

    if (user.role === 'admin') {
        adminMenuItems.push({
            key: '/domain-management',
            icon: <ClusterOutlined />,
            label: '知识域管理',
        });
        adminMenuItems.push({
            key: '/admin/users',
            icon: <TeamOutlined />,
            label: '用户管理',
        });
        adminMenuItems.push({
            key: '/admin/modules',
            icon: <SafetyOutlined />,
            label: '模块授权',
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
                                navigate('/admin/model-configs');
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