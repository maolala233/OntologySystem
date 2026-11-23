/**
 * 管理后台 · 用户管理（M1，docs/design/05 §6.10；R8 扩展：批量操作 + 名下项目查看）
 * 批量：行勾选 → 批量启用/停用/删除（逐个调用、汇总成败；自己行不可勾选，管理员停用跳过）。
 * 项目：操作列「项目」→ Drawer 列出该用户拥有与参与的项目。
 * 视觉（2026-11-23）：TechHero + 指标带 + 冷蓝表体；批量操作条吸顶，
 * 一次性密码用独立纸面卡片承载。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Form, Input, Modal, Popconfirm, Select, Space, Table, Typography } from 'antd';
import {
    CheckCircleOutlined, DeleteOutlined, FolderOpenOutlined, PlusOutlined,
    ReloadOutlined, SafetyCertificateOutlined, SearchOutlined, StopOutlined, TeamOutlined, UserOutlined,
} from '@ant-design/icons';
import { message, notifyError } from '../../shared/feedback';
import { adminApi, AdminUser } from '../../api/admin';
import { useAuthStore } from '../../shared/auth/authStore';
import { PROJECT_STATUS, statusChip } from '../../shared/projectStatus';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, StatBand, TkTag } from '../../shared/consoleUi';

const { Text } = Typography;

interface UserProject {
    id: number;
    name: string;
    status: string;
    is_published: boolean;
    role: 'owner' | 'editor' | 'viewer';
    created_at: string | null;
}

const ROLE_LABEL: Record<string, { text: string; tone: 'brand' | 'cyan' | 'quiet' }> = {
    owner: { text: '所有者', tone: 'brand' },
    editor: { text: '编辑者', tone: 'cyan' },
    viewer: { text: '查看者', tone: 'quiet' },
};

// 状态映射唯一权威在 shared/projectStatus（O1-F1）
const STATUS_LABEL = Object.fromEntries(
    Object.entries(PROJECT_STATUS).map(([k, v]) => [k, { text: v.text }]),
) as Record<string, { text: string }>;

const UserAdminPage: React.FC = () => {
    const me = useAuthStore((s) => s.user);
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [loading, setLoading] = useState(true);
    const [createOpen, setCreateOpen] = useState(false);
    const [form] = Form.useForm();
    const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
    const [oneTimePassword, setOneTimePassword] = useState<string>('');
    const [searchText, setSearchText] = useState('');

    // 批量操作
    const [selectedIds, setSelectedIds] = useState<React.Key[]>([]);
    const [batchBusy, setBatchBusy] = useState(false);

    // 项目抽屉
    const [projectsUser, setProjectsUser] = useState<AdminUser | null>(null);
    const [projects, setProjects] = useState<UserProject[]>([]);
    const [projectsLoading, setProjectsLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            setUsers(await adminApi.listUsers());
        } catch (e) {
            notifyError(e, '获取用户列表失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const handleCreate = async () => {
        let values: any;
        try {
            values = await form.validateFields();
        } catch {
            return; // 校验失败由字段下方的红字提示，这里不弹 message
        }
        try {
            await adminApi.createUser(values);
            message.success(`用户 ${values.username} 已创建`);
            setCreateOpen(false);
            form.resetFields();
            load();
        } catch (err: any) {
            notifyError(err, '创建失败');
        }
    };

    const handleToggleActive = async (u: AdminUser) => {
        try {
            await adminApi.patchUser(u.id, { is_active: !u.is_active });
            message.success(u.is_active ? `已停用 ${u.username}` : `已启用 ${u.username}`);
            load();
        } catch (err: any) {
            notifyError(err, '操作失败');
        }
    };

    const handleResetPassword = async (u: AdminUser) => {
        try {
            const resp = await adminApi.patchUser(u.id, { reset_password: true });
            setResetTarget(u);
            setOneTimePassword(resp.password || '');
            load();
        } catch (err: any) {
            notifyError(err, '重置失败');
        }
    };

    const handleDelete = async (u: AdminUser) => {
        try {
            await adminApi.deleteUser(u.id);
            message.success(`用户 ${u.username} 已删除`);
            load();
        } catch (err: any) {
            notifyError(err, '删除失败');
        }
    };

    // ── 批量操作：逐个调用并汇总（守卫在后端，逐用户报告成败原因）
    const runBatch = async (
        label: string,
        fn: (u: AdminUser) => Promise<unknown>,
        filter?: (u: AdminUser) => boolean,
    ) => {
        const targets = selectedIds
            .map((id) => users.find((u) => u.id === id))
            .filter((u): u is AdminUser => !!u && (!filter || filter(u)));
        if (targets.length === 0) return;
        setBatchBusy(true);
        const ok: string[] = [];
        const failed: string[] = [];
        for (const u of targets) {
            try {
                await fn(u);
                ok.push(u.username);
            } catch (err: any) {
                failed.push(`${u.username}（${err.response?.data?.error?.message || '失败'}）`);
            }
        }
        const skipped = selectedIds.length - targets.length;
        const skipMsg = skipped > 0 ? `，跳过 ${skipped} 个（管理员或不可操作）` : '';
        if (failed.length === 0) {
            message.success(`${label}完成：成功 ${ok.length} 个${skipMsg}`);
        } else {
            message.warning(`${label}完成：成功 ${ok.length} 个，失败 ${failed.length} 个：${failed.join('；')}`);
        }
        setSelectedIds([]);
        setBatchBusy(false);
        load();
    };

    const batchEnable = () => runBatch('批量启用', (u) => adminApi.patchUser(u.id, { is_active: true }));
    const batchDisable = () => runBatch(
        '批量停用',
        (u) => adminApi.patchUser(u.id, { is_active: false }),
        (u) => u.role !== 'admin', // 与单行一致：不停用管理员
    );
    const batchDelete = () => runBatch('批量删除', (u) => adminApi.deleteUser(u.id));

    // ── 名下项目
    const openProjects = async (u: AdminUser) => {
        setProjectsUser(u);
        setProjects([]);
        setProjectsLoading(true);
        try {
            const resp = await adminApi.listUserProjects(u.id);
            setProjects(resp.items);
        } catch (e) {
            notifyError(e, '获取用户项目失败');
        } finally {
            setProjectsLoading(false);
        }
    };

    const counts = useMemo(() => ({
        total: users.length,
        admins: users.filter((u) => u.role === 'admin').length,
        active: users.filter((u) => u.is_active).length,
        inactive: users.filter((u) => !u.is_active).length,
    }), [users]);

    const filteredUsers = useMemo(() => {
        const q = searchText.trim().toLowerCase();
        if (!q) return users;
        return users.filter((u) =>
            u.username.toLowerCase().includes(q)
            || (u.display_name ?? '').toLowerCase().includes(q)
            || (u.email ?? '').toLowerCase().includes(q)
            || (u.app_role_name ?? '').toLowerCase().includes(q));
    }, [users, searchText]);

    const columns = [
        {
            title: 'ID', dataIndex: 'id', width: 70,
            render: (id: number) => <span className="tk-mono text-xs text-[#8A94AB]">#{id}</span>,
        },
        {
            title: '用户', dataIndex: 'username', width: 220,
            render: (name: string, record: AdminUser) => (
                <div className="flex min-w-0 items-center gap-2.5">
                    <span
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold text-white"
                        style={{
                            background: record.role === 'admin'
                                ? 'linear-gradient(135deg, #E0A02A, #B8790F)'
                                : 'linear-gradient(135deg, #5B8DEF, #8B5CF6)',
                            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.28)',
                        }}
                    >
                        {name.slice(0, 1).toUpperCase()}
                    </span>
                    <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium text-[#1F2A44]">{name}</div>
                        {record.display_name && (
                            <div className="truncate text-[11px] text-[#8A94AB]">{record.display_name}</div>
                        )}
                    </div>
                </div>
            ),
        },
        {
            title: '角色', dataIndex: 'role', width: 170,
            render: (role: string, record: AdminUser) => (
                <Space size={4} className="!flex-wrap">
                    <TkTag tone={role === 'admin' ? 'amber' : 'brand'}>
                        {role === 'admin' ? '管理员' : '普通用户'}
                    </TkTag>
                    {record.app_role_name && (
                        <TkTag tone="violet"><span className="max-w-[150px] truncate">{record.app_role_name}</span></TkTag>
                    )}
                </Space>
            ),
        },
        {
            title: '邮箱', dataIndex: 'email',
            render: (v: string | null) => <span className="text-[12.5px] text-[#66738F]">{v || '—'}</span>,
        },
        {
            title: '状态', dataIndex: 'is_active', width: 96,
            render: (active: boolean) => (
                <span className="inline-flex items-center gap-1.5">
                    <Dot tone={active ? 'cyan' : 'quiet'} />
                    <span className={`text-[12.5px] ${active ? 'text-[#1B87AD]' : 'text-[#8A94AB]'}`}>
                        {active ? '正常' : '停用'}
                    </span>
                </span>
            ),
        },
        {
            title: '操作', key: 'action', width: 290,
            render: (_: any, record: AdminUser) => (
                <Space size={0}>
                    <Button size="small" type="link" icon={<FolderOpenOutlined />}
                        onClick={() => openProjects(record)}>项目</Button>
                    <Button size="small" type="link" onClick={() => handleResetPassword(record)}>重置密码</Button>
                    <Button size="small" type="link" danger={record.is_active}
                            disabled={record.role === 'admin'}
                            onClick={() => handleToggleActive(record)}>
                        {record.is_active ? '停用' : '启用'}
                    </Button>
                    <Popconfirm
                        title={`确定删除用户 ${record.username}？`}
                        description="将同时清除其模块授权与 MCP 令牌，不可恢复；名下有项目时会删除失败。"
                        okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                        onConfirm={() => handleDelete(record)}
                    >
                        <Button size="small" type="link" danger>删除</Button>
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={[
                { title: '首页', path: '/' },
                { title: '系统配置' },
                { title: '用户管理' },
            ]} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title="用户管理"
                    titleSuffix="USER DIRECTORY"
                    subtitle="管理平台账号、角色与状态；支持批量启用/停用/删除，并可查看用户名下项目"
                    showConstellation={users.length > 0}
                    actions={
                        <>
                            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
                            <Button type="primary" className="tk-primary-btn" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建用户</Button>
                        </>
                    }
                />

                <StatBand
                    className="mb-4"
                    items={[
                        { label: '用户总数', value: counts.total, tone: 'brand', icon: <TeamOutlined /> },
                        { label: '管理员', value: counts.admins, tone: 'amber', icon: <SafetyCertificateOutlined /> },
                        { label: '正常', value: counts.active, tone: 'cyan', icon: <CheckCircleOutlined /> },
                        { label: '停用', value: counts.inactive, tone: 'quiet', icon: <StopOutlined /> },
                    ]}
                />

                {/* 查询条 */}
                <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                    <div className="tk-controls">
                        <Input
                            allowClear
                            value={searchText}
                            onChange={e => { setSearchText(e.target.value); setSelectedIds([]); }}
                            placeholder="搜索用户名 / 显示名 / 邮箱 / 角色…"
                            prefix={<SearchOutlined className="text-[#5B8DEF]" />}
                            className="w-[300px]"
                        />
                    </div>
                    {searchText && (
                        <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[#66738F]">
                            <Dot tone="brand" />
                            匹配 <span className="tk-mono font-semibold text-[#2A57C6]">{filteredUsers.length}</span> 个用户
                        </span>
                    )}
                </div>

                {/* 批量操作条：吸顶，勾选后一直可见；用发光圆点 + 等宽计数回答"选了几个" */}
                {selectedIds.length > 0 && (
                    <div className="tk-save-bar mb-3 !bottom-auto top-0">
                        <Dot tone="brand" />
                        <span className="inline-flex items-baseline gap-1 text-[12.5px] text-[#3D4B66]">
                            已选
                            <span className="tk-mono text-[14px] font-semibold text-[#2A57C6]">{selectedIds.length}</span>
                            个用户
                        </span>
                        <Button size="small" disabled={batchBusy} onClick={batchEnable}>批量启用</Button>
                        <Button size="small" disabled={batchBusy} onClick={batchDisable}>批量停用</Button>
                        <Popconfirm
                            title={`确定批量删除 ${selectedIds.length} 个用户？`}
                            description="不可恢复；名下有项目的用户会删除失败并逐个报告原因。"
                            okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                            onConfirm={batchDelete}
                        >
                            <Button size="small" danger icon={<DeleteOutlined />} disabled={batchBusy}>批量删除</Button>
                        </Popconfirm>
                        <Button size="small" type="text" className="ml-auto" onClick={() => setSelectedIds([])}>取消选择</Button>
                    </div>
                )}

                <div className="surface-card tk-table">
                    <Table
                        rowKey="id"
                        size="middle"
                        loading={loading}
                        scroll={{ x: 1020 }}
                        columns={columns as any}
                        dataSource={filteredUsers}
                        locale={{
                            emptyText: (
                                <div className="flex flex-col items-center px-4 py-12">
                                    <span
                                        className="flex h-12 w-12 items-center justify-center rounded-2xl text-[20px] text-[#2F62D8]"
                                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                    >
                                        <UserOutlined />
                                    </span>
                                    <div className="mt-3 text-[13px] font-semibold text-[#0F172A]">
                                        {searchText ? '没有找到匹配的用户' : '暂无用户'}
                                    </div>
                                    <div className="mt-1 text-[12px] text-[#66738F]">
                                        {searchText ? '检查一下关键词，或清除搜索查看全部用户' : '新建用户后可在此管理账号与授权'}
                                    </div>
                                    {searchText && (
                                        <Button size="small" className="tk-soft-btn mt-3" onClick={() => setSearchText('')}>
                                            清除搜索
                                        </Button>
                                    )}
                                </div>
                            ),
                        }}
                        pagination={{
                            pageSize: 50,
                            pageSizeOptions: [50, 100, 200],
                            showSizeChanger: true,
                            showTotal: (t) => `共 ${t} 个用户（${counts.admins} 个管理员）`,
                        }}
                        rowSelection={{
                            selectedRowKeys: selectedIds,
                            onChange: (keys) => setSelectedIds(keys),
                            getCheckboxProps: (record: AdminUser) => ({
                                disabled: record.id === me?.id, // 自己不可批量操作（防自锁）
                            }),
                        }}
                    />
                </div>

                <Modal
                    title="新建用户"
                    open={createOpen}
                    onOk={handleCreate}
                    onCancel={() => setCreateOpen(false)}
                    destroyOnHidden
                    width={520}
                >
                    <Form form={form} layout="vertical" className="pt-1">
                        <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}>
                            <Input placeholder="2-64 字符" />
                        </Form.Item>
                        <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}>
                            <Input.Password placeholder="至少 6 位" />
                        </Form.Item>
                        <Form.Item name="role" label="角色" initialValue="user" rules={[{ required: true }]}>
                            <Select options={[{ value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }]} />
                        </Form.Item>
                        <Form.Item name="display_name" label="显示名">
                            <Input placeholder="可选" />
                        </Form.Item>
                        <Form.Item name="email" label="邮箱" rules={[{ type: 'email' }]} className="!mb-0">
                            <Input placeholder="可选" />
                        </Form.Item>
                    </Form>
                </Modal>

                <Modal
                    title={`重置密码 · ${resetTarget?.username ?? ''}`}
                    open={!!resetTarget}
                    onCancel={() => { setResetTarget(null); setOneTimePassword(''); }}
                    footer={<Button type="primary" onClick={() => { setResetTarget(null); setOneTimePassword(''); }}>我已保存</Button>}
                >
                    <div className="space-y-3">
                        <Note tone="amber">
                            请立即保存新密码：仅本次显示，关闭后将无法再次查看。
                        </Note>
                        <div className="rounded-xl border border-[rgba(91,141,239,0.16)] bg-[#FBFDFF] px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-[11.5px] text-[#8A94AB]">新密码</span>
                                <TkTag tone="amber">仅显示一次</TkTag>
                            </div>
                            <div className="mt-2">
                                <Text
                                    copyable
                                    code
                                    style={{
                                        display: 'inline-block',
                                        margin: 0,
                                        padding: '6px 10px',
                                        fontSize: 16,
                                        color: '#0F172A',
                                        background: '#EFF5FF',
                                        border: '1px solid rgba(91,141,239,0.20)',
                                        borderRadius: 8,
                                    }}
                                >
                                    {oneTimePassword}
                                </Text>
                            </div>
                        </div>
                        <div className="text-[12px] leading-[1.65] text-[#66738F]">
                            请告知该用户并提醒其尽快修改。
                        </div>
                    </div>
                </Modal>

                {/* 用户名下项目 */}
                <Drawer
                    title={`用户项目 · ${projectsUser?.username ?? ''}`}
                    open={!!projectsUser}
                    onClose={() => setProjectsUser(null)}
                    width={520}
                >
                    {projectsLoading ? (
                        <div className="py-10 text-center text-sm text-[#66738F]">
                            <Dot tone="brand" className="mr-2 inline-block align-middle" />
                            加载中…
                        </div>
                    ) : projects.length === 0 ? (
                        <div className="flex flex-col items-center px-4 py-12 text-center">
                            <span
                                className="flex h-12 w-12 items-center justify-center rounded-2xl text-[20px] text-[#2F62D8]"
                                style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                            >
                                <FolderOpenOutlined />
                            </span>
                            <div className="mt-3 text-[13px] font-semibold text-[#0F172A]">该用户暂无项目</div>
                            <div className="mt-1 text-[12px] text-[#66738F]">未拥有也未参与任何项目</div>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11.5px] text-[#66738F]">
                                <Dot tone="brand" />
                                共 <span className="tk-mono font-semibold text-[#1F2A44]">{projects.length}</span> 个项目
                                <TkTag tone="brand" mono>拥有 {projects.filter((p) => p.role === 'owner').length}</TkTag>
                                <TkTag tone="quiet" mono>参与 {projects.filter((p) => p.role !== 'owner').length}</TkTag>
                            </div>
                            {projects.map((p) => {
                                const role = ROLE_LABEL[p.role] ?? ROLE_LABEL.viewer;
                                const status = STATUS_LABEL[p.status] ?? STATUS_LABEL.draft;
                                return (
                                    <div key={p.id} className="flex items-center justify-between gap-3 rounded-xl border border-[#E7EEF9] bg-[rgba(251,253,255,0.7)] px-3 py-2.5 transition-colors hover:border-[rgba(91,141,239,0.34)] hover:bg-[rgba(91,141,239,0.05)]">
                                        <div className="min-w-0">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <span className="tk-mono text-xs text-[#66738F]">#{p.id}</span>
                                                <span className="truncate text-[13px] text-[#1F2A44]">{p.name}</span>
                                            </div>
                                            {p.created_at && (
                                                <div className="mt-0.5 text-[11px] text-[#8A94AB]">创建于 {p.created_at.slice(0, 10)}</div>
                                            )}
                                        </div>
                                        <Space size={4} className="shrink-0">
                                            <TkTag tone={role.tone}>{role.text}</TkTag>
                                            <span
                                                className="tk-tag"
                                                style={statusChip(p.status)}
                                            >
                                                {status.text}
                                            </span>
                                        </Space>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </Drawer>
            </div>
        </div>
    );
};

export default UserAdminPage;