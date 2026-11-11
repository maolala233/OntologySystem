import React, { useState, useEffect } from 'react';
import { Layout, Menu } from 'antd';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import {
    HomeOutlined,
    UserOutlined,
    BookOutlined,

    DatabaseOutlined,
    SettingOutlined,
    CloudServerOutlined,
    InfoCircleOutlined,
    ApiOutlined,
    ClusterOutlined,
    FileImageOutlined,
    FileTextOutlined,
    ToolOutlined,
    QuestionCircleOutlined,
    TeamOutlined,
    EyeOutlined,
    RobotOutlined,
    SafetyOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '../../shared/auth/authStore';
import { confirmLeaveUnsaved } from '../../utils/unsavedGuard';
import CopilotWidget from '../Copilot/CopilotWidget';
import BrandMark from '../BrandMark';
import { SiderCollapseContext } from './siderCollapse';

const { Sider, Content } = Layout;

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
        email?: string | null;
    };

    useEffect(() => {
        if (!loaded && localStorage.getItem('access_token')) {
            hydrate();
        }
    }, [loaded, hydrate]);

    // 菜单可见性：admin 全量；普通用户按授权矩阵；矩阵未加载完成前放行基础项（避免闪烁）
    const hasModule = (code: string) =>
        user.role === 'admin' ? true : modules.length > 0 ? modules.includes(code) : true;

    // M1：菜单按授权矩阵过滤（模块码见 docs/design/README §4.1）
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

    // 菜单选中态：板块下的子页面仍归属到板块本身——
    // 项目概览/构建/文档/图谱等 /projects/:id/* 页面继续高亮「我的项目」，
    // 否则进入项目后左侧菜单失去高亮（2026-11-11 实测）
    const selectedMenuKey = (() => {
        const path = location.pathname;
        if (path === '/my-projects' || path.startsWith('/projects/') || path.startsWith('/ontology-builder/')) {
            return '/my-projects';
        }
        return path;
    })();

    return (
        <SiderCollapseContext.Provider value={{ collapsed, toggle: () => setCollapsed(c => !c) }}>
        <Layout className="h-screen overflow-hidden">
            {/* 深色侧边栏 */}
            {/* 2026-11-11：关掉 Sider 底部自带的折叠箭头（trigger=null），
                改由顶栏左侧的 SiderToggle 控制折叠 */}
            <Sider
                collapsible
                collapsed={collapsed}
                onCollapse={setCollapsed}
                trigger={null}
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
                {/* Logo 区域：标题随侧栏宽度一起「展开/收起」（max-width 过渡），
                    不能按 collapsed 直接显隐——展开动画途中侧栏还窄，文字会先折成两行再变一行 */}
                <div className="h-16 flex items-center justify-center bg-[#001529] border-b border-gray-700 overflow-hidden">
                    <div className="flex items-center min-w-0">
                        <div className="w-8 h-8 shrink-0 rounded-lg flex items-center justify-center"
                             style={{ background: 'linear-gradient(135deg, #3b82f6, #9333ea)' }}>
                            <BrandMark size={19} strokeWidth={4} />
                        </div>
                        <span
                            className={`text-white font-semibold text-lg whitespace-nowrap overflow-hidden transition-all duration-200 ease-out ${
                                collapsed ? 'max-w-0 opacity-0 ml-0' : 'max-w-[6rem] opacity-100 ml-2'
                            }`}
                        >
                            本体平台
                        </span>
                    </div>
                </div>

                {/* 导航菜单 */}
                <div className="sidebar-scroll" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
                    <Menu
                        theme="dark"
                        mode="inline"
                        selectedKeys={[selectedMenuKey]}
                        openKeys={openMenus}
                        onOpenChange={keys => setOpenMenus(keys as string[])}
                        items={allMenuItems}
                        onClick={({ key }) => {
                            if (key === location.pathname) return;
                            confirmLeaveUnsaved(() => navigate(key));
                        }}
                        className="border-r-0"
                    />
                </div>

            </Sider>

            {/* 本体问答已改为独立页面 /qa（R8）；QaDrawer 组件保留供其他入口复用 */}

            {/* 主内容区 */}
            <Layout>
                <Content className="bg-gray-50 h-screen overflow-hidden">
                    <Outlet />
                </Content>
            </Layout>

            {/* O5-F12：Copilot 小窗始终挂载保持单例（会话状态不随路由丢）；
                可见范围（仅构建器五 Tab）由组件内部按路由门控 */}
            <CopilotWidget />
        </Layout>
        </SiderCollapseContext.Provider>
    );
};

export default AppLayout;