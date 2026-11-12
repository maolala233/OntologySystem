import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Avatar, Breadcrumb, Dropdown, Input, Modal, Tag } from 'antd';
import {
    DownOutlined, LockOutlined, LogoutOutlined, SettingOutlined, UserOutlined,
} from '@ant-design/icons';
import { message, confirmModal } from '../../shared/feedback';
import { authAPI } from '../../api/auth';
import { useAuthStore } from '../../shared/auth/authStore';
import SiderToggle from './SiderToggle';

interface NavbarProps {
    breadcrumbs?: { title: string; path?: string }[];
}

/**
 * 全站顶栏：左侧面包屑，右侧当前登录用户（头像/姓名/角色/邮箱 + 账号菜单）。
 * 用户信息 2026-11-11 从左侧菜单底部迁到这里——账号相关操作属于全局，放在顶栏更符合常规习惯。
 */
const Navbar: React.FC<NavbarProps> = ({ breadcrumbs = [] }) => {
    const navigate = useNavigate();
    const { user: storeUser, clear: clearAuth } = useAuthStore();
    // store 尚未 hydrate 时用 localStorage 兜底（与 AppLayout 同源），避免首帧空白
    const user = (storeUser || JSON.parse(localStorage.getItem('user') || '{}')) as {
        username?: string;
        role?: 'admin' | 'user';
        display_name?: string | null;
        email?: string | null;
    };

    const [pwdOpen, setPwdOpen] = useState(false);
    const [pwdSaving, setPwdSaving] = useState(false);
    const [oldPwd, setOldPwd] = useState('');
    const [newPwd, setNewPwd] = useState('');
    const [confirmPwd, setConfirmPwd] = useState('');

    const closePwdModal = () => {
        setPwdOpen(false);
        setOldPwd('');
        setNewPwd('');
        setConfirmPwd('');
    };

    const submitPwd = async () => {
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
        setPwdSaving(true);
        try {
            await authAPI.changePassword({ old_password: oldPwd, new_password: newPwd });
            message.success('密码修改成功');
            closePwdModal();
        } catch (error: any) {
            message.error(error?.response?.data?.detail || '密码修改失败');
        } finally {
            setPwdSaving(false);
        }
    };

    const handleLogout = () => {
        confirmModal({
            title: '退出登录',
            content: '确定要退出当前账号吗？',
            okText: '退出登录',
            cancelText: '取消',
            onOk: () => {
                clearAuth();
                navigate('/login');
            },
        });
    };

    const accountMenu = {
        items: [
            {
                key: 'change-password',
                icon: <LockOutlined />,
                label: '修改密码',
                onClick: () => setPwdOpen(true),
            },
            ...(user.role === 'admin' ? [{
                key: 'settings',
                icon: <SettingOutlined />,
                label: '设置',
                onClick: () => navigate('/admin/model-configs'),
            }] : []),
            { type: 'divider' as const },
            {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '退出登录',
                onClick: handleLogout,
            },
        ],
    };

    return (
        <div className="shrink-0 h-14 app-topbar px-4 sm:px-6 flex items-center justify-between">
            {/* 左侧：菜单折叠开关 + 面包屑 */}
            <div className="flex items-center gap-1 flex-1 min-w-0">
                <SiderToggle />
                <Breadcrumb
                    items={breadcrumbs.map((item) => ({
                        // 用路由 Link 做站内跳转：href 会触发整页刷新（白屏闪烁），SPA 内切换才平滑
                        title: item.path ? <Link to={item.path}>{item.title}</Link> : item.title,
                    }))}
                />
            </div>

            {/* 右侧：当前用户 */}
            <div className="flex items-center flex-shrink-0">
                <Dropdown menu={accountMenu} placement="bottomRight" trigger={['click']}>
                    <div className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-100 cursor-pointer transition-colors">
                        <Avatar
                            size={32}
                            icon={<UserOutlined />}
                            style={{ background: 'linear-gradient(135deg, #5B8DEF, #8B5CF6)' }}
                        />
                        <div className="hidden sm:block leading-tight min-w-0">
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-gray-800 truncate max-w-[140px]">
                                    {user.display_name || user.username || '用户'}
                                </span>
                                <Tag
                                    color={user.role === 'admin' ? 'gold' : 'blue'}
                                    style={{ marginInlineEnd: 0, lineHeight: '16px', fontSize: 11, flexShrink: 0 }}
                                >
                                    {user.role === 'admin' ? '管理员' : '普通用户'}
                                </Tag>
                            </div>
                            {user.email && (
                                <div className="text-[11px] text-gray-400 truncate max-w-[180px]" title={user.email}>
                                    {user.email}
                                </div>
                            )}
                        </div>
                        <DownOutlined className="text-gray-400 text-[10px]" />
                    </div>
                </Dropdown>
            </div>

            <Modal
                title="修改密码"
                open={pwdOpen}
                onCancel={closePwdModal}
                onOk={submitPwd}
                okText="确认修改"
                confirmLoading={pwdSaving}
            >
                <div className="space-y-4 py-2">
                    <div>
                        <div className="mb-1 text-sm font-medium">旧密码</div>
                        <Input.Password value={oldPwd} onChange={e => setOldPwd(e.target.value)} placeholder="请输入旧密码" />
                    </div>
                    <div>
                        <div className="mb-1 text-sm font-medium">新密码</div>
                        <Input.Password value={newPwd} onChange={e => setNewPwd(e.target.value)} placeholder="请输入新密码（至少6位）" />
                    </div>
                    <div>
                        <div className="mb-1 text-sm font-medium">确认新密码</div>
                        <Input.Password value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)} placeholder="请再次输入新密码" />
                    </div>
                </div>
            </Modal>
        </div>
    );
};

export default Navbar;
