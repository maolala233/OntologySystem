/**
 * 管理后台 · 用户管理（M1，docs/design/05 §6.10）
 */
import React, { useEffect, useState } from 'react';
import { Table, Button, Tag, Modal, Form, Input, Select, message, Space, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { adminApi, AdminUser } from '../../api/admin';

const { Text } = Typography;

const UserAdminPage: React.FC = () => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [loading, setLoading] = useState(false);
    const [createOpen, setCreateOpen] = useState(false);
    const [form] = Form.useForm();
    const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
    const [oneTimePassword, setOneTimePassword] = useState<string>('');

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

    const columns = [
        { title: 'ID', dataIndex: 'id', width: 60 },
        { title: '用户名', dataIndex: 'username' },
        {
            title: '角色', dataIndex: 'role', width: 90,
            render: (role: string) => (
                <Tag color={role === 'admin' ? 'gold' : 'blue'}>{role === 'admin' ? '管理员' : '普通用户'}</Tag>
            ),
        },
        { title: '显示名', dataIndex: 'display_name', render: (v: string | null) => v || '-' },
        { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v || '-' },
        {
            title: '状态', dataIndex: 'is_active', width: 80,
            render: (active: boolean) => <Tag color={active ? 'green' : 'red'}>{active ? '正常' : '停用'}</Tag>,
        },
        {
            title: '操作', key: 'action', width: 200,
            render: (_: any, record: AdminUser) => (
                <Space>
                    <Button size="small" type="link" onClick={() => handleResetPassword(record)}>重置密码</Button>
                    <Button size="small" type="link" danger={record.is_active}
                            disabled={record.role === 'admin'}
                            onClick={() => handleToggleActive(record)}>
                        {record.is_active ? '停用' : '启用'}
                    </Button>
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

            <Table
                rowKey="id"
                loading={loading}
                columns={columns as any}
                dataSource={users}
                pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 个用户` }}
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
        </div>
    );
};

export default UserAdminPage;
