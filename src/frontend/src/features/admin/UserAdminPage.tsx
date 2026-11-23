/**
 * 管理后台 · 用户管理（M1，docs/design/05 §6.10；R8 扩展：批量操作 + 名下项目查看）
 * 批量：行勾选 → 批量启用/停用/删除（逐个调用、汇总成败；自己行不可勾选，管理员停用跳过）。
 * 项目：操作列「项目」→ Drawer 列出该用户拥有与参与的项目。
 * 视觉（2026-11-23）：TechHero + 指标带 + 冷蓝表体；批量操作条吸顶，
 * 一次性密码用独立纸面卡片承载。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Form, Input, Modal, Popconfirm, Select, Space, Table } from 'antd';
import {
    CheckCircleOutlined, CopyOutlined, DeleteOutlined, FolderOpenOutlined, PlusOutlined,
    ReloadOutlined, SafetyCertificateOutlined, StopOutlined, TeamOutlined, UserOutlined,
    WarningOutlined,
} from '@ant-design/icons';
import { confirmModal, message, notifyError } from '../../shared/feedback';
import { adminApi, AdminUser } from '../../api/admin';
import { useAuthStore } from '../../shared/auth/authStore';
import { PROJECT_STATUS, statusChip } from '../../shared/projectStatus';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, StatBand, TkTag } from '../../shared/consoleUi';
import Tip from '../../shared/Tip';

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
    // 刷新按钮只在手动点击时转圈；首屏由表格 loading 表达
    const [refreshing, setRefreshing] = useState(false);
    const [createOpen, setCreateOpen] = useState(false);
    const [form] = Form.useForm();
    const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
    const [oneTimePassword, setOneTimePassword] = useState<string>('');

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

    const refresh = async () => {
        setRefreshing(true);
        try {
            await load();
        } finally {
            setRefreshing(false);
        }
    };

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

    const handleResetPassword = (u: AdminUser) => {
        // 重置会立即让旧密码失效，属于敏感操作：先确认，再调接口
        confirmModal({
            title: `重置密码 · ${u.username}`,
            content: '重置后该用户的旧密码立即失效，系统会生成一个一次性新密码（仅本次显示）。确定要重置吗？',
            okText: '确认重置',
            cancelText: '取消',
            onOk: async () => {
                try {
                    const resp = await adminApi.patchUser(u.id, { reset_password: true });
                    setResetTarget(u);
                    setOneTimePassword(resp.password || '');
                    load();
                } catch (err: any) {
                    notifyError(err, '重置失败');
                }
            },
        });
    };

    const copyOneTimePassword = async () => {
        if (!oneTimePassword) return;
        try {
            await navigator.clipboard.writeText(oneTimePassword);
            message.success('新密码已复制');
        } catch {
            message.warning('复制失败，请手动选择密码复制');
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

    const columns = [
        {
            title: 'ID', dataIndex: 'id', width: 60,
            render: (id: number) => <span className="tk-mono text-xs text-[#8A94AB]">#{id}</span>,
        },
        {
            title: '用户', dataIndex: 'username', width: 210,
            render: (name: string) => (
                <Tip text={name}>
                    <span className="block truncate text-[13px] font-medium text-[#1F2A44]">{name}</span>
                </Tip>
            ),
        },
        {
            title: '角色', dataIndex: 'role', width: 150,
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
            title: '显示名', dataIndex: 'display_name', width: 140,
            render: (v: string | null) => (
                <Tip text={v || undefined}>
                    <span className="block truncate text-[12.5px] text-[#66738F]">{v || '—'}</span>
                </Tip>
            ),
        },
        {
            title: '邮箱', dataIndex: 'email', width: 190,
            render: (v: string | null) => <span className="text-[12.5px] text-[#66738F]">{v || '—'}</span>,
        },
        {
            title: '状态', dataIndex: 'is_active', width: 84,
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
            title: '操作', key: 'action', width: 270,
            render: (_: any, record: AdminUser) => (
                <Space size={0}>
                    <Button size="small" type="link" icon={<FolderOpenOutlined />}
                        onClick={() => openProjects(record)}>项目</Button>
                    <Button size="small" type="link" onClick={() => handleResetPassword(record)}>重置密码</Button>
                    <Popconfirm
                        title={record.is_active
                            ? `确定停用用户 ${record.username}？`
                            : `确定启用用户 ${record.username}？`}
                        description={record.is_active
                            ? '停用后该用户将无法登录，可随时重新启用。'
                            : '启用后该用户可以重新登录。'}
                        okText={record.is_active ? '停用' : '启用'}
                        cancelText="取消"
                        okButtonProps={{ danger: record.is_active }}
                        onConfirm={() => handleToggleActive(record)}
                        disabled={record.role === 'admin'}
                    >
                        <Button size="small" type="link" danger={record.is_active}
                                disabled={record.role === 'admin'}>
                            {record.is_active ? '停用' : '启用'}
                        </Button>
                    </Popconfirm>
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
                            <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>刷新</Button>
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

                {/* 批量操作条：不再套长条卡片，直接一条左对齐的普通行；
                    用发光圆点 + 等宽计数回答"选了几个" */}
                {selectedIds.length > 0 && (
                    <div className="mb-3 flex flex-wrap items-center gap-1.5">
                        <Dot tone="brand" />
                        <span className="inline-flex items-baseline gap-1 text-[12.5px] text-[#3D4B66]">
                            已选
                            <span className="tk-mono text-[14px] font-semibold text-[#2A57C6]">{selectedIds.length}</span>
                            个用户
                        </span>
                        <span className="mx-0.5 h-4 w-px shrink-0 bg-[#E4EBF7]" aria-hidden />
                        <Popconfirm
                            title={`确定批量启用选中的 ${selectedIds.length} 个用户？`}
                            description="启用后这些用户可以重新登录；管理员会被跳过。"
                            okText="启用" cancelText="取消"
                            onConfirm={batchEnable}
                        >
                            <Button size="small" className="tk-soft-btn" icon={<CheckCircleOutlined />} disabled={batchBusy}>批量启用</Button>
                        </Popconfirm>
                        <Popconfirm
                            title={`确定批量停用选中的 ${selectedIds.length} 个用户？`}
                            description="停用后这些用户将无法登录；管理员会被跳过，可随时重新启用。"
                            okText="停用" cancelText="取消" okButtonProps={{ danger: true }}
                            onConfirm={batchDisable}
                        >
                            <Button size="small" className="tk-warn-soft-btn" icon={<StopOutlined />} disabled={batchBusy}>批量停用</Button>
                        </Popconfirm>
                        <Popconfirm
                            title={`确定批量删除 ${selectedIds.length} 个用户？`}
                            description="不可恢复；名下有项目的用户会删除失败并逐个报告原因。"
                            okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                            onConfirm={batchDelete}
                        >
                            <Button size="small" className="tk-danger-soft-btn" icon={<DeleteOutlined />} disabled={batchBusy}>批量删除</Button>
                        </Popconfirm>
                        <Button size="small" type="link" className="!px-1 text-[#66738F]" onClick={() => setSelectedIds([])}>取消选择</Button>
                    </div>
                )}

                <div className="surface-card tk-table">
                    {/* 与知识域管理同一套加载方式：表头始终是真实表头，
                        数据未到时用 Table 自己的 loading 遮罩，不再先出现一套骨架表头。
                        列宽全部显式给出 + fixed 布局，表头从第一帧起就是确定的位置。 */}
                    <Table
                        rowKey="id"
                        size="middle"
                        loading={loading}
                        tableLayout="fixed"
                        scroll={{ x: 1150 }}
                        columns={columns as any}
                        dataSource={users}
                        locale={{
                            emptyText: (
                                <div className="flex flex-col items-center px-4 py-12">
                                    <span
                                        className="flex h-12 w-12 items-center justify-center rounded-2xl text-[20px] text-[#2F62D8]"
                                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                    >
                                        <UserOutlined />
                                    </span>
                                    <div className="mt-3 text-[13px] font-semibold text-[#0F172A]">暂无用户</div>
                                    <div className="mt-1 text-[12px] text-[#66738F]">新建用户后可在此管理账号与授权</div>
                                </div>
                            ),
                        }}
                        pagination={{
                            pageSize: 50,
                            showSizeChanger: false,
                            showTotal: (t) => `共 ${t} 个用户（${counts.admins} 个管理员）`,
                        }}
                        rowSelection={{
                            selectedRowKeys: selectedIds,
                            // 显式给宽度：避免多选框列在首次挂载时由内容测量出宽度再回填
                            columnWidth: 40,
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
                    className="tk-form-compact"
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
                    maskClosable={false}
                    width={460}
                    footer={<Button type="primary" onClick={() => { setResetTarget(null); setOneTimePassword(''); }}>我已保存</Button>}
                >
                    <div className="space-y-3">
                        <div className="flex items-start gap-2 text-[12.5px] leading-[1.65] text-[#52607A]">
                            <WarningOutlined className="mt-0.5 shrink-0 text-[14px] text-[#B8790F]" />
                            <span>关闭后无法再次查看，请立即保存并告知该用户。</span>
                        </div>

                        {/* 密码展示做成一块只读字段（和弹窗里的输入框同一支浅冷蓝），
                            不再套一层白色小卡，避免「卡片包卡片」 */}
                        <div className="rounded-xl border border-[rgba(91,141,239,0.20)] bg-[#EFF5FF] px-3.5 py-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="tk-field-label !mb-0">一次性新密码</span>
                                <TkTag tone="amber">仅显示一次</TkTag>
                            </div>
                            <div className="mt-2 flex items-center gap-3">
                                <span className="tk-mono min-w-0 flex-1 select-all break-all text-[20px] font-semibold leading-7 tracking-[0.04em] text-[#0F172A]">
                                    {oneTimePassword}
                                </span>
                                <Button size="small" className="shrink-0" icon={<CopyOutlined />} onClick={copyOneTimePassword}>
                                    复制
                                </Button>
                            </div>
                        </div>

                        <div className="text-[11.5px] text-[#8A94AB]">建议提醒用户首次登录后立即修改密码。</div>
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