/**
 * 管理后台 · 模块授权矩阵（M1，docs/design/05 §6.10 / 03 §4）
 * 行=用户、列=14 个模块码；单元格三态：默认（按 is_default_on）/ 允许 / 拒绝。
 * 保存 = 整表 PUT（显式行覆盖默认值）。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Table, Button, Tag, Radio, Select, message, Space, Typography, Tooltip } from 'antd';
import { ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import { adminApi, AdminModule, AdminUser, GrantRow } from '../../api/admin';

const { Text } = Typography;

type CellState = 'default' | 'allow' | 'deny';

const ModuleGrantsPage: React.FC = () => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [modules, setModules] = useState<AdminModule[]>([]);
    const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
    const [cells, setCells] = useState<Record<string, CellState>>({});
    const [dirty, setDirty] = useState(false);
    const [loading, setLoading] = useState(false);

    const userMap = useMemo(() => new Map(users.map((u) => [u.id, u])), [users]);

    const loadUsers = async () => {
        setUsers(await adminApi.listUsers());
    };

    const loadGrants = async (userId: number) => {
        setLoading(true);
        try {
            const { grants } = await adminApi.getGrants(userId);
            const next: Record<string, CellState> = {};
            for (const m of modules) next[m.code] = 'default';
            for (const g of grants as GrantRow[]) {
                next[g.module_code] = g.allowed ? 'allow' : 'deny';
            }
            setCells(next);
            setDirty(false);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        (async () => {
            try {
                const [us, ms] = await Promise.all([adminApi.listUsers(), adminApi.listModules()]);
                setUsers(us);
                setModules(ms);
                if (us.length > 0) {
                    const firstNormal = us.find((u) => u.role === 'user') ?? us[0];
                    setSelectedUserId(firstNormal.id);
                }
            } catch {
                message.error('加载授权数据失败');
            }
        })();
    }, []);

    useEffect(() => {
        if (selectedUserId != null) {
            loadGrants(selectedUserId).catch(() => message.error('加载用户授权失败'));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedUserId]);

    const handleSave = async () => {
        if (selectedUserId == null) return;
        const target = userMap.get(selectedUserId);
        if (target?.role === 'admin') {
            message.warning('管理员默认拥有全部模块，无需授权矩阵');
            return;
        }
        const grants = Object.entries(cells)
            .filter(([, state]) => state !== 'default')
            .map(([module_code, state]) => ({ module_code, allowed: state === 'allow' }));
        try {
            await adminApi.putGrants(selectedUserId, grants);
            message.success(`已保存 ${target?.username ?? ''} 的授权矩阵（显式 ${grants.length} 条）`);
            setDirty(false);
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '保存失败');
        }
    };

    const columns = [
        {
            title: '模块', dataIndex: 'name', width: 180,
            render: (name: string, record: AdminModule) => (
                <Tooltip title={record.description}>
                    <Space size={6}>
                        <span>{name}</span>
                        <Text code style={{ fontSize: 11 }}>{record.code}</Text>
                    </Space>
                </Tooltip>
            ),
        },
        {
            title: '默认开通', dataIndex: 'is_default_on', width: 100,
            render: (v: boolean) => (v ? <Tag color="cyan">默认开</Tag> : <Tag>默认关</Tag>),
        },
        {
            title: '开通人数', dataIndex: 'enabled_users', width: 90,
        },
        {
            title: '授权（所选用户）', key: 'cell',
            render: (_: any, record: AdminModule) => (
                <Radio.Group
                    size="small"
                    value={cells[record.code] ?? 'default'}
                    onChange={(e) => {
                        setCells((prev) => ({ ...prev, [record.code]: e.target.value }));
                        setDirty(true);
                    }}
                    options={[
                        { value: 'default', label: '默认' },
                        { value: 'allow', label: <span style={{ color: '#16A34A' }}>允许</span> },
                        { value: 'deny', label: <span style={{ color: '#DC2626' }}>拒绝</span> },
                    ]}
                    optionType="button"
                    buttonStyle="solid"
                />
            ),
        },
    ];

    const selectedUser = selectedUserId != null ? userMap.get(selectedUserId) : null;

    return (
        <div style={{ padding: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <Typography.Title level={4} style={{ margin: 0 }}>模块授权矩阵</Typography.Title>
                <Space>
                    <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 240 }}
                        placeholder="选择用户"
                        value={selectedUserId ?? undefined}
                        onChange={(v) => setSelectedUserId(v)}
                        options={users.map((u) => ({
                            value: u.id,
                            label: `${u.username}${u.role === 'admin' ? '（管理员）' : ''}`,
                        }))}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => selectedUserId != null && loadGrants(selectedUserId)}>刷新</Button>
                    <Button type="primary" icon={<SaveOutlined />} disabled={!dirty} onClick={handleSave}>保存</Button>
                </Space>
            </div>

            {selectedUser?.role === 'admin' && (
                <Tag color="gold" style={{ marginBottom: 12 }}>管理员默认拥有全部模块，矩阵不可配置</Tag>
            )}

            <Table
                rowKey="code"
                loading={loading}
                columns={columns as any}
                dataSource={modules}
                pagination={false}
                size="middle"
            />
        </div>
    );
};

export default ModuleGrantsPage;
