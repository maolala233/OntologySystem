/**
 * 管理后台 · 用户管理（M1，docs/design/05 §6.10；R8 扩展：批量操作 + 名下项目查看）
 * 批量：行勾选 → 批量启用/停用/删除（逐个调用、汇总成败；自己行不可勾选，管理员停用跳过）。
 * 项目：操作列「项目」→ Drawer 列出该用户拥有与参与的项目。
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
    Button, Drawer, Empty, Modal, Form, Input, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd';
import { DeleteOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { adminApi, AdminUser } from '../../api/admin';
import { useAuthStore } from '../../shared/auth/authStore';

const { Text } = Typography;

interface UserProject {
    id: number;
    name: string;
    status: string;
    is_published: boolean;
    role: 'owner' | 'editor' | 'viewer';
    created_at: string | null;
}

const ROLE_LABEL: Record<string, { text: string; color: string }> = {
    owner: { text: '所有者', color: 'blue' },
    editor: { text: '编辑者', color: 'cyan' },
    viewer: { text: '查看者', color: 'default' },
};

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
    draft: { text: '草稿', color: 'default' },
    building: { text: '构建中', color: 'processing' },
    ready: { text: '就绪', color: 'success' },
    published: { text: '已发布', color: 'purple' },
};

const UserAdminPage: React.FC = () => {
    const me = useAuthStore((s) => s.user);
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [loading, setLoading] = useState(false);
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
        } catch {
            message.error('获取用户列表失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const handleCreate = async () => {
        const values = await form.validateFields();
        try {
            await adminApi.createUser(values);
            message.success(`用户 ${values.username} 创建成功`);
            setCreateOpen(false);
            form.resetFields();
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '创建失败');
        }
    };

    const handleToggleActive = async (u: AdminUser) => {
        try {
            await adminApi.patchUser(u.id, { is_active: !u.is_active });
            message.success(u.is_active ? `已停用 ${u.username}` : `已启用 ${u.username}`);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '操作失败');
        }
    };

    const handleResetPassword = async (u: AdminUser) => {
        try {
            const resp = await adminApi.patchUser(u.id, { reset_password: true });
            setResetTarget(u);
            setOneTimePassword(resp.password || '');
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '重置失败');
        }
    };

    const handleDelete = async (u: AdminUser) => {
        try {
            await adminApi.deleteUser(u.id);
            message.success(`用户 ${u.username} 已删除`);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '删除失败');
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
        } catch {
            message.error('获取用户项目失败');
        } finally {
            setProjectsLoading(false);
        }
    };

    const ownedCount = useMemo(
        () => users.filter((u) => u.role === 'admin').length,
        [users],
    );

    const columns = [
        { title: 'ID', dataIndex: 'id', width: 70 },
        { title: '用户名', dataIndex: 'username' },
        {
            title: '角色', dataIndex: 'role', width: 130,
            render: (role: string, record: AdminUser) => (
                <Space size={4} className="!flex-wrap">
                    <Tag color={role === 'admin' ? 'gold' : 'blue'} className="!m-0">
                        {role === 'admin' ? '管理员' : '普通用户'}
                    </Tag>
                    {record.app_role_name && <Tag color="geekblue" className="!m-0">{record.app_role_name}</Tag>}
                </Space>
            ),
        },
        { title: '显示名', dataIndex: 'display_name', render: (v: string | null) => v || '-' },
        { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v || '-' },
        {
            title: '状态', dataIndex: 'is_active', width: 90,
            render: (active: boolean) => <Tag color={active ? 'green' : 'red'}>{active ? '正常' : '停用'}</Tag>,
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
        <div style={{ padding: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <Typography.Title level={4} style={{ margin: 0 }}>用户管理</Typography.Title>
                <Space>
                    <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建用户</Button>
                </Space>
            </div>

            {/* 批量操作栏 */}
            {selectedIds.length > 0 && (
                <div className="mb-3 flex items-center gap-3 rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-2">
                    <span className="text-sm text-gray-700">
                        已选 <b className="text-[#5B8DEF]">{selectedIds.length}</b> 个用户
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
                    <Button size="small" type="text" onClick={() => setSelectedIds([])}>取消选择</Button>
                </div>
            )}

            <Table
                rowKey="id"
                loading={loading}
                columns={columns as any}
                dataSource={users}
                pagination={{
                    pageSize: 50,
                    pageSizeOptions: [50, 100, 200],
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 个用户（${ownedCount} 个管理员）`,
                }}
                rowSelection={{
                    selectedRowKeys: selectedIds,
                    onChange: (keys) => setSelectedIds(keys),
                    getCheckboxProps: (record: AdminUser) => ({
                        disabled: record.id === me?.id, // 自己不可批量操作（防自锁）
                    }),
                }}
            />

            <Modal
                title="新建用户"
                open={createOpen}
                onOk={handleCreate}
                onCancel={() => setCreateOpen(false)}
                destroyOnClose
            >
                <Form form={form} layout="vertical">
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
                    <Form.Item name="email" label="邮箱" rules={[{ type: 'email' }]}>
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
                <p>请立即保存新密码（仅本次显示）：</p>
                <Text copyable code style={{ fontSize: 16 }}>{oneTimePassword}</Text>
                <p style={{ color: '#999', marginTop: 12 }}>关闭后将无法再次查看，请告知该用户并提醒其尽快修改。</p>
            </Modal>

            {/* 用户名下项目 */}
            <Drawer
                title={`用户项目 · ${projectsUser?.username ?? ''}`}
                open={!!projectsUser}
                onClose={() => setProjectsUser(null)}
                width={480}
            >
                {projectsLoading ? (
                    <div className="py-10 text-center text-sm text-gray-400">加载中…</div>
                ) : projects.length === 0 ? (
                    <Empty description="该用户暂无项目（未拥有也未参与任何项目）" />
                ) : (
                    <div className="space-y-2">
                        <div className="text-xs text-gray-400 mb-2">
                            共 {projects.length} 个项目（拥有 {projects.filter((p) => p.role === 'owner').length} 个，参与 {projects.filter((p) => p.role !== 'owner').length} 个）
                        </div>
                        {projects.map((p) => {
                            const role = ROLE_LABEL[p.role] ?? ROLE_LABEL.viewer;
                            const status = STATUS_LABEL[p.status] ?? STATUS_LABEL.draft;
                            return (
                                <div key={p.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2.5 hover:border-blue-200 hover:bg-blue-50/30 transition-colors">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span className="font-mono text-xs text-gray-400">#{p.id}</span>
                                            <span className="text-sm text-gray-800 truncate">{p.name}</span>
                                        </div>
                                        {p.created_at && (
                                            <div className="text-[11px] text-gray-400 mt-0.5">创建于 {p.created_at.slice(0, 10)}</div>
                                        )}
                                    </div>
                                    <Space size={4}>
                                        <Tag color={role.color} className="!m-0">{role.text}</Tag>
                                        <Tag color={status.color} className="!m-0">{status.text}</Tag>
                                    </Space>
                                </div>
                            );
                        })}
                    </div>
                )}
            </Drawer>
        </div>
    );
};

export default UserAdminPage;
