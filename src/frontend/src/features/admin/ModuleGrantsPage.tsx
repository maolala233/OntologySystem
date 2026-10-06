/**
 * 管理后台 · 模块授权（R8 重构：界面平铺 + 角色配置；R11 拆分工具授权清单）
 * Tab1 用户权限：业务模块平铺矩阵（行=全部用户、列=模块）；
 * Tab2 工具授权：工具类四模块（本体问答/报告生成/PPT生成/MCP 工具）单独清单；
 * 单元格三态点击循环：默认（按 is_default_on）→ 允许 → 拒绝 → 默认；保存按用户整表 PUT。
 * Tab3 角色配置：角色 = 模块授权预设（roles 表），应用角色即全量覆盖该用户 grants。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Button, Checkbox, Empty, Input, Modal, Popconfirm, Space, Tabs, Tag, Tooltip, Typography, message,
} from 'antd';
import {
    AppstoreOutlined, CheckOutlined, CloseOutlined, DeleteOutlined, EditOutlined, MinusOutlined,
    PlusOutlined, ReloadOutlined, SaveOutlined, TeamOutlined, ToolOutlined, UserSwitchOutlined,
} from '@ant-design/icons';
import { adminApi, AdminModule, AdminRole, AdminUser } from '../../api/admin';

type CellState = 'default' | 'allow' | 'deny';
type Matrix = Record<string, Record<string, CellState>>; // userId -> moduleCode -> state

const NEXT_STATE: Record<CellState, CellState> = { default: 'allow', allow: 'deny', deny: 'default' };
const STATE_META: Record<CellState, { label: string; icon: React.ReactNode; cls: string; tip: string }> = {
    default: { label: '默', icon: <MinusOutlined />, cls: 'bg-gray-100 text-gray-400 hover:bg-gray-200', tip: '默认（跟随模块开关）' },
    allow: { label: '许', icon: <CheckOutlined />, cls: 'bg-[#5B8DEF] text-white hover:bg-[#4a7de0]', tip: '允许（显式开通）' },
    deny: { label: '拒', icon: <CloseOutlined />, cls: 'bg-red-100 text-red-500 hover:bg-red-200', tip: '拒绝（显式关闭）' },
};

// 工具类模块码（R11）：在「工具授权」清单单独配置；侧栏「工具」子菜单与之对应
const TOOL_CODES = ['qa', 'report', 'ppt', 'mcp'];
const TOOL_LABELS: Record<string, string> = {
    qa: '本体问答', report: '报告生成', ppt: 'PPT生成', mcp: 'MCP 工具',
};

const ModuleGrantsPage: React.FC = () => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [modules, setModules] = useState<AdminModule[]>([]);
    const [roles, setRoles] = useState<AdminRole[]>([]);
    const [matrix, setMatrix] = useState<Matrix>({});
    const [dirtyUsers, setDirtyUsers] = useState<Set<number>>(new Set());
    const [saving, setSaving] = useState(false);
    const [loading, setLoading] = useState(false);

    // 角色编辑/应用弹窗
    const [roleModalOpen, setRoleModalOpen] = useState(false);
    const [editingRole, setEditingRole] = useState<AdminRole | null>(null);
    const [roleForm, setRoleForm] = useState({ name: '', description: '', module_codes: [] as string[] });
    const [applyRole, setApplyRole] = useState<AdminRole | null>(null);
    const [applyUserIds, setApplyUserIds] = useState<number[]>([]);
    const [applying, setApplying] = useState(false);

    const normalUsers = useMemo(() => users.filter((u) => u.role !== 'admin'), [users]);
    const moduleMap = useMemo(() => new Map(modules.map((m) => [m.code, m])), [modules]);
    const businessModules = useMemo(() => modules.filter((m) => !TOOL_CODES.includes(m.code)), [modules]);
    const toolModules = useMemo(
        () => TOOL_CODES.map((c) => moduleMap.get(c)).filter((m): m is AdminModule => !!m),
        [moduleMap],
    );

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [us, ms, rs, allGrants] = await Promise.all([
                adminApi.listUsers(), adminApi.listModules(), adminApi.listRoles(),
                adminApi.getAllGrants(),
            ]);
            setUsers(us);
            setModules(ms);
            setRoles(rs);
            const next: Matrix = {};
            for (const u of us) {
                next[u.id] = {};
                for (const m of ms) next[u.id][m.code] = 'default';
                if (u.role === 'admin') continue; // 管理员直通全模块，矩阵只读
                for (const g of allGrants[String(u.id)] ?? []) {
                    next[u.id][g.module_code] = g.allowed ? 'allow' : 'deny';
                }
            }
            setMatrix(next);
            setDirtyUsers(new Set());
        } catch {
            message.error('加载授权数据失败');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const cycleCell = (userId: number, code: string) => {
        const target = users.find((u) => u.id === userId);
        if (target?.role === 'admin') return;
        setMatrix((prev) => ({
            ...prev,
            [userId]: { ...prev[userId], [code]: NEXT_STATE[prev[userId]?.[code] ?? 'default'] },
        }));
        setDirtyUsers((prev) => new Set(prev).add(userId));
    };

    const resetRow = (userId: number) => {
        setMatrix((prev) => {
            const row = { ...prev[userId] };
            for (const m of modules) row[m.code] = 'default';
            return { ...prev, [userId]: row };
        });
        setDirtyUsers((prev) => {
            const next = new Set(prev);
            next.delete(userId);
            return next;
        });
    };

    const saveAll = async () => {
        const targets = [...dirtyUsers].filter((id) => users.find((u) => u.id === id)?.role !== 'admin');
        if (targets.length === 0) return;
        setSaving(true);
        try {
            await Promise.all(targets.map(async (userId) => {
                const grants = Object.entries(matrix[userId] ?? {})
                    .filter(([, state]) => state !== 'default')
                    .map(([module_code, state]) => ({ module_code, allowed: state === 'allow' }));
                await adminApi.putGrants(userId, grants);
            }));
            message.success(`已保存 ${targets.length} 个用户的授权`);
            setDirtyUsers(new Set());
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '保存失败');
        } finally {
            setSaving(false);
        }
    };

    // ── 角色编辑
    const openRoleModal = (role: AdminRole | null) => {
        setEditingRole(role);
        setRoleForm(role
            ? { name: role.name, description: role.description ?? '', module_codes: [...role.module_codes] }
            : { name: '', description: '', module_codes: [] });
        setRoleModalOpen(true);
    };

    const saveRole = async () => {
        if (!roleForm.name.trim()) { message.warning('请填写角色名称'); return; }
        try {
            if (editingRole) {
                await adminApi.updateRole(editingRole.id, {
                    name: roleForm.name.trim(), description: roleForm.description || undefined,
                    module_codes: roleForm.module_codes,
                });
                message.success('角色已更新');
            } else {
                await adminApi.createRole({
                    name: roleForm.name.trim(), description: roleForm.description || undefined,
                    module_codes: roleForm.module_codes,
                });
                message.success('角色已创建');
            }
            setRoleModalOpen(false);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '保存失败');
        }
    };

    const removeRole = async (role: AdminRole) => {
        try {
            await adminApi.deleteRole(role.id);
            message.success(`角色「${role.name}」已删除`);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '删除失败');
        }
    };

    const doApplyRole = async () => {
        if (!applyRole || applyUserIds.length === 0) { message.warning('请选择要应用的用户'); return; }
        setApplying(true);
        try {
            const resp = await adminApi.applyRole(applyRole.id, applyUserIds);
            const skippedMsg = resp.skipped?.length
                ? `，跳过 ${resp.skipped.length} 个（${resp.skipped.map((s) => s.reason).join('、')}）` : '';
            message.success(`角色「${resp.role}」已应用到 ${resp.applied.length} 个用户${skippedMsg}`);
            setApplyRole(null);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '应用失败');
        } finally {
            setApplying(false);
        }
    };

    const toggleRoleModule = (code: string, checked: boolean) => {
        setRoleForm((f) => ({
            ...f,
            module_codes: checked ? [...f.module_codes, code] : f.module_codes.filter((c) => c !== code),
        }));
    };

    /** 平铺授权矩阵（用户 × 模块），用户权限/工具授权两个清单共用 */
    const MatrixTable = ({ mods }: { mods: AdminModule[] }) => (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed' }}>
                <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                        <th className="text-left px-4 py-2 text-xs font-medium text-gray-500 sticky left-0 bg-gray-50 z-10" style={{ width: 200 }}>用户 / 模块</th>
                        {mods.map((m) => (
                            <th key={m.code} className="px-1 py-2 border-l border-gray-100 overflow-hidden">
                                <Tooltip title={`${m.description ?? ''}（${m.code}）`}>
                                    <div className="leading-tight overflow-hidden">
                                        <div className="text-[11px] text-gray-700 whitespace-nowrap text-ellipsis max-w-[62px] mx-auto">{m.name}</div>
                                        <div className={`text-[10px] ${m.is_default_on ? 'text-cyan-600' : 'text-gray-300'}`}>
                                            {m.is_default_on ? '默认开' : '默认关'}
                                        </div>
                                    </div>
                                </Tooltip>
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {users.map((u) => (
                        <tr key={u.id} className="border-b border-gray-50 hover:bg-gray-50/50">
                            <td className="px-4 py-2 sticky left-0 bg-white z-10">
                                <Space size={6}>
                                    <span className="w-6 h-6 rounded-full bg-[#5B8DEF] text-white text-xs flex items-center justify-center shrink-0">
                                        {u.username.slice(0, 1).toUpperCase()}
                                    </span>
                                    <span className="text-sm text-gray-800 whitespace-nowrap">{u.username}</span>
                                    {u.role === 'admin' && <Tag color="gold" className="!m-0">管理员</Tag>}
                                    {!u.is_active && <Tag className="!m-0">停用</Tag>}
                                    {dirtyUsers.has(u.id) && (
                                        <>
                                            <Tag color="blue" className="!m-0">未保存</Tag>
                                            <Button size="small" type="link" className="!px-0 !text-[11px]"
                                                onClick={() => resetRow(u.id)}>还原</Button>
                                        </>
                                    )}
                                </Space>
                            </td>
                            {mods.map((m) => {
                                if (u.role === 'admin') {
                                    return (
                                        <td key={m.code} className="text-center border-l border-gray-50">
                                            <Tooltip title="管理员默认拥有全部模块">
                                                <span className="text-gray-300 text-xs">全部</span>
                                            </Tooltip>
                                        </td>
                                    );
                                }
                                const state = matrix[u.id]?.[m.code] ?? 'default';
                                const meta = STATE_META[state];
                                return (
                                    <td key={m.code} className="text-center border-l border-gray-50 py-1.5">
                                        <Tooltip title={`${meta.tip}（点击切换）`}>
                                            <button type="button" onClick={() => cycleCell(u.id, m.code)}
                                                className={`w-7 h-7 rounded-md inline-flex items-center justify-center text-[11px] transition-colors ${meta.cls}`}>
                                                {meta.icon}
                                            </button>
                                        </Tooltip>
                                    </td>
                                );
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );

    const legendBar = (
        <div className="flex items-center justify-between mb-3">
            <Space size={16} className="!flex-wrap text-xs text-gray-500">
                <span className="flex items-center gap-1">
                    <span className="w-4 h-4 rounded bg-gray-100 inline-flex items-center justify-center text-gray-400 text-[10px]">默</span>跟随模块默认
                </span>
                <span className="flex items-center gap-1">
                    <span className="w-4 h-4 rounded bg-[#5B8DEF] inline-flex items-center justify-center text-white text-[10px]">许</span>允许
                </span>
                <span className="flex items-center gap-1">
                    <span className="w-4 h-4 rounded bg-red-100 inline-flex items-center justify-center text-red-500 text-[10px]">拒</span>拒绝
                </span>
                <Typography.Text type="secondary" className="!text-xs">点击单元格切换状态</Typography.Text>
            </Space>
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </div>
    );

    // ── 角色卡片
    const roleCards = (
        <div>
            <div className="flex items-center justify-between mb-4">
                <Typography.Text type="secondary" className="!text-xs">
                    角色 = 一组模块开通预设。「应用到用户」会全量覆盖所选用户的模块授权（允许列表内的模块开通，其余关闭）。
                </Typography.Text>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => openRoleModal(null)}>新建角色</Button>
            </div>
            {roles.length === 0 ? (
                <Empty description="暂无角色" />
            ) : (
                <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
                    {roles.map((r) => (
                        <div key={r.id} className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-md transition-shadow flex flex-col">
                            <div className="flex items-center justify-between">
                                <Space size={10}>
                                    <span className="w-8 h-8 rounded-lg bg-[#5B8DEF]/10 text-[#5B8DEF] inline-flex items-center justify-center text-base">
                                        <TeamOutlined />
                                    </span>
                                    <span className="font-medium text-gray-800">{r.name}</span>
                                    {r.is_builtin && <Tag color="blue" className="!m-0">内置</Tag>}
                                </Space>
                                <Space size={0}>
                                    <Tooltip title="编辑">
                                        <Button size="small" type="text" icon={<EditOutlined />}
                                            onClick={() => openRoleModal(r)} />
                                    </Tooltip>
                                    {!r.is_builtin && (
                                        <Popconfirm title={`确定删除角色「${r.name}」？`} onConfirm={() => removeRole(r)}>
                                            <Tooltip title="删除">
                                                <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                                            </Tooltip>
                                        </Popconfirm>
                                    )}
                                </Space>
                            </div>
                            <div className="text-xs text-gray-400 mt-2 min-h-[32px] leading-relaxed">
                                {r.description || '暂无描述'}
                                <span className="ml-2 whitespace-nowrap">· {r.module_codes.length} 个模块</span>
                            </div>
                            <div className="flex flex-wrap gap-1 mt-2 min-h-[46px] content-start flex-1">
                                {r.module_codes.map((c) => (
                                    <Tag key={c} color="blue" className="!m-0 !text-[11px]">{moduleMap.get(c)?.name ?? c}</Tag>
                                ))}
                            </div>
                            <div className="mt-3 pt-3 border-t border-gray-100">
                                <Button size="small" type="primary" ghost block icon={<UserSwitchOutlined />}
                                    onClick={() => { setApplyRole(r); setApplyUserIds([]); }}>
                                    应用到用户
                                </Button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );

    // 角色弹窗里的模块勾选分组（业务 / 工具）
    const roleModulePicker = (
        <div className="space-y-3">
            {[
                { title: '业务模块', mods: businessModules },
                { title: '工具模块', mods: toolModules },
            ].map((group) => (
                <div key={group.title}>
                    <div className="text-[11px] text-gray-400 mb-1.5 flex items-center gap-1">
                        {group.title === '工具模块' && <ToolOutlined />}
                        {group.title}
                    </div>
                    <div className="grid grid-cols-2 gap-y-2 gap-x-4">
                        {group.mods.map((m) => (
                            <Checkbox key={m.code} checked={roleForm.module_codes.includes(m.code)}
                                onChange={(e) => toggleRoleModule(m.code, e.target.checked)}>
                                <span className="text-sm">{group.title === '工具模块' ? (TOOL_LABELS[m.code] ?? m.name) : m.name}</span>
                                <span className="text-[10px] text-gray-400 ml-1">{m.code}</span>
                            </Checkbox>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );

    return (
        <div style={{ padding: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <Typography.Title level={4} style={{ margin: 0 }}>模块授权</Typography.Title>
                {dirtyUsers.size > 0 && (
                    <Space>
                        <Typography.Text type="warning" className="!text-xs">
                            {dirtyUsers.size} 个用户有未保存的修改
                        </Typography.Text>
                        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={saveAll}>
                            保存全部
                        </Button>
                    </Space>
                )}
            </div>

            <Tabs
                defaultActiveKey="users"
                items={[
                    {
                        key: 'users', label: '用户权限',
                        children: (
                            <div>
                                {legendBar}
                                <MatrixTable mods={businessModules} />
                            </div>
                        ),
                    },
                    {
                        key: 'tools', label: '工具授权',
                        children: (
                            <div>
                                <div className="flex items-center gap-2 mb-2">
                                    <ToolOutlined className="text-[#5B8DEF]" />
                                    <Typography.Text type="secondary" className="!text-xs">
                                        工具类功能单独授权：本体问答 / 报告生成 / PPT生成 / MCP 工具，分别控制「工具」子菜单中各入口的可见与可用。
                                    </Typography.Text>
                                </div>
                                {legendBar}
                                <MatrixTable mods={toolModules} />
                            </div>
                        ),
                    },
                    { key: 'roles', label: '角色配置', children: roleCards },
                ]}
            />

            {/* 新建/编辑角色 */}
            <Modal
                title={editingRole ? `编辑角色 · ${editingRole.name}` : '新建角色'}
                open={roleModalOpen} onOk={saveRole} onCancel={() => setRoleModalOpen(false)}
                okText="保存" destroyOnClose
            >
                <div className="space-y-3 py-1">
                    <div>
                        <div className="text-xs text-gray-500 mb-1">角色名称</div>
                        <Input value={roleForm.name} maxLength={64} placeholder="如：建模员"
                            onChange={(e) => setRoleForm((f) => ({ ...f, name: e.target.value }))} />
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 mb-1">描述（可选）</div>
                        <Input value={roleForm.description} maxLength={255} placeholder="该角色的职责说明"
                            onChange={(e) => setRoleForm((f) => ({ ...f, description: e.target.value }))} />
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 mb-2">
                            开通模块（勾选 = 允许；应用角色时未勾选模块将显式关闭）
                        </div>
                        {roleModulePicker}
                    </div>
                </div>
            </Modal>

            {/* 应用角色到用户 */}
            <Modal
                title={`应用角色「${applyRole?.name ?? ''}」到用户`}
                open={!!applyRole} onOk={doApplyRole} onCancel={() => setApplyRole(null)}
                okText={`应用到 ${applyUserIds.length} 个用户`} confirmLoading={applying}
            >
                <div className="space-y-3 py-1">
                    <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-700">
                        应用后所选用户的模块授权将被该角色预设<b>全量覆盖</b>（允许列表内开通、其余关闭）。管理员默认全模块，已自动排除。
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-xs text-gray-500">选择用户（{normalUsers.length} 个可选）</span>
                        <Button size="small" type="link"
                            onClick={() => setApplyUserIds(applyUserIds.length === normalUsers.length ? [] : normalUsers.map((u) => u.id))}>
                            {applyUserIds.length === normalUsers.length ? '取消全选' : '全选'}
                        </Button>
                    </div>
                    <div className="border border-gray-200 rounded-lg divide-y divide-gray-50 max-h-64 overflow-auto">
                        {normalUsers.map((u) => (
                            <label key={u.id} className="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 cursor-pointer">
                                <Checkbox
                                    checked={applyUserIds.includes(u.id)}
                                    onChange={(e) => setApplyUserIds((prev) => e.target.checked
                                        ? [...prev, u.id]
                                        : prev.filter((id) => id !== u.id))}
                                />
                                <span className="text-sm text-gray-800">{u.username}</span>
                                {!u.is_active && <Tag className="!m-0 !text-[10px]">停用</Tag>}
                            </label>
                        ))}
                        {normalUsers.length === 0 && (
                            <div className="px-3 py-6 text-center text-xs text-gray-300">暂无普通用户</div>
                        )}
                    </div>
                </div>
            </Modal>
        </div>
    );
};

export default ModuleGrantsPage;
