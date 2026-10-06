import React, { useState, useEffect } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button, Modal, Input, Tag, Spin, Table } from 'antd';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import {
    HomeOutlined,
    AuditOutlined,
    UserOutlined,
    BookOutlined,

    DatabaseOutlined,
    LogoutOutlined,
    SettingOutlined,
    CloudServerOutlined,
    InfoCircleOutlined,
    ApiOutlined,
    ClusterOutlined,
    FileImageOutlined,
    FileTextOutlined,
    ToolOutlined,
    QuestionCircleOutlined,
    LockOutlined,
    TeamOutlined,
    EyeOutlined,
    RobotOutlined,
    SafetyOutlined,
} from '@ant-design/icons';
import { Form, message, Switch } from 'antd';
import { authAPI } from '../../api/auth';
import apiClient from '../../api/client';
import { CONNECTIVITY_FIELDS, describeConnectivityError } from '../../utils/connectivity';
import { projectsApi } from '../../api/projects';
import { useAuthStore } from '../../shared/auth/authStore';
import { confirmLeaveUnsaved } from '../../utils/unsavedGuard';

const { Sider, Content } = Layout;

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

    // 子菜单收纳（R8）：进入子页自动展开其所在分组；用户可手动收起/展开（onOpenChange 受控）
    const parentOfPath: Record<string, string> = {
        '/qa': 'tools', '/tools/reports': 'tools', '/tools/ppt': 'tools', '/tools/mcp': 'tools',
        '/domain-management': 'system-config',
        '/admin/users': 'system-config', '/admin/modules': 'system-config',
        '/admin/model-configs': 'system-config', '/admin/env-configs': 'system-config',
    };
    const [openMenus, setOpenMenus] = useState<string[]>(() => {
        const parent = parentOfPath[location.pathname];
        return parent ? [parent] : [];
    });
    useEffect(() => {
        const parent = parentOfPath[location.pathname];
        if (parent) setOpenMenus(prev => (prev.includes(parent) ? prev : [...prev, parent]));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [location.pathname]);
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
    // M3-7：构建器不再有独立菜单入口（从「我的项目」进入四 Tab）；新增审核工作台
    const menuItems = [
        {
            key: '/',
            icon: <HomeOutlined />,
            label: '工作台',
            module: 'dashboard',
        },
        {
            key: '/my-projects',
            icon: <UserOutlined />,
            label: '我的项目',
            module: 'projects',
        },
        {
            key: '/reviews',
            icon: <AuditOutlined />,
            label: '审核工作台',
            module: 'review',
        },
        {
            key: '/asset-center',
            icon: <DatabaseOutlined />,
            label: '资产中心',
            module: 'asset_center',
        },
    ].filter((item) => hasModule(item.module));

    // R8 工具分组（可折叠子菜单）：本体问答（qa）/ 报告生成（report，独立模块码）/ MCP 服务（mcp）
    const toolChildren = [
        ...(hasModule('qa') ? [
            { key: '/qa', icon: <BookOutlined />, label: '本体问答' },
        ] : []),
        ...(hasModule('report') ? [
            { key: '/tools/reports', icon: <FileTextOutlined />, label: '报告生成' },
        ] : []),
        ...(hasModule('ppt') ? [
            { key: '/tools/ppt', icon: <FileImageOutlined />, label: 'PPT生成' },
        ] : []),
        ...(hasModule('mcp') ? [
            { key: '/tools/mcp', icon: <ApiOutlined />, label: 'MCP 服务' },
        ] : []),
    ];
    const toolsMenu = toolChildren.length > 0 ? [{
        key: 'tools',
        icon: <ToolOutlined />,
        label: '工具',
        children: toolChildren,
    }] : [];

    // 管理员专属菜单（03 §3–4）：收纳进「系统配置」子菜单（抽屉式，进入子页自动展开所在组）
    const adminMenuItems = [];

    if (user.role === 'admin') {
        adminMenuItems.push({
            key: 'system-config',
            icon: <SettingOutlined />,
            label: '系统配置',
            children: [
                { key: '/domain-management', icon: <ClusterOutlined />, label: '知识域管理' },
                { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' },
                { key: '/admin/modules', icon: <SafetyOutlined />, label: '模块授权' },
                { key: '/admin/model-configs', icon: <RobotOutlined />, label: '模型配置' },
                { key: '/admin/env-configs', icon: <CloudServerOutlined />, label: '环境配置' },
            ],
        });
    }
    
    // 合并菜单项
    const allMenuItems = [...menuItems, ...toolsMenu, ...adminMenuItems];

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
                        openKeys={openMenus}
                        onOpenChange={keys => setOpenMenus(keys as string[])}
                        items={allMenuItems}
                        onClick={({ key }) => {
                            if (key === 'user-management') {
                                setUserManagementVisible(true);
                                return;
                            }
                            if (key === location.pathname) return;
                            confirmLeaveUnsaved(() => navigate(key));
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
            {/* 本体问答已改为独立页面 /qa（R8）；QaDrawer 组件保留供其他入口复用 */}

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