/**
 * 管理后台 · 模块授权（R8 重构：界面平铺 + 角色配置；R11 拆分工具授权清单）
 * Tab1 用户权限：业务模块平铺矩阵（行=全部用户、列=模块）；
 * Tab2 工具授权：工具类四模块（本体问答/报告生成/PPT生成/MCP 工具）单独清单；
 * 单元格三态点击循环：默认（按 is_default_on）→ 允许 → 拒绝 → 默认；保存按用户整表 PUT。
 * Tab3 角色配置：角色 = 模块授权预设（roles 表），应用角色即全量覆盖该用户 grants。
 * 视觉（2026-11-23）：TechHero + 指标带；矩阵换成冷蓝表体，
 * 未保存行带左侧品牌竖条；改动后吸底保存条一直可见，不必滚回页头。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Checkbox, Input, Modal, Popconfirm, Space, Spin, Tabs, Tooltip } from 'antd';
import { message, notifyError } from '../../shared/feedback';
import {
    CheckOutlined, CloseOutlined, DeleteOutlined, EditOutlined, MinusOutlined, PlusOutlined,
    ReloadOutlined, SafetyOutlined, SaveOutlined, SearchOutlined, TeamOutlined,
    ToolOutlined, UndoOutlined, UserSwitchOutlined,
} from '@ant-design/icons';
import { adminApi, AdminModule, AdminRole, AdminUser } from '../../api/admin';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, SectionHead, StatBand, TkTag } from '../../shared/consoleUi';

type CellState = 'default' | 'allow' | 'deny';
type Matrix = Record<string, Record<string, CellState>>; // userId -> moduleCode -> state

const NEXT_STATE: Record<CellState, CellState> = { default: 'allow', allow: 'deny', deny: 'default' };
const STATE_META: Record<CellState, { label: string; icon: React.ReactNode; cls: string; tip: string }> = {
    default: { label: '默', icon: <MinusOutlined />, cls: 'mg-cell--default', tip: '默认（跟随模块开关）' },
    allow: { label: '许', icon: <CheckOutlined />, cls: 'mg-cell--allow', tip: '允许（显式开通）' },
    deny: { label: '拒', icon: <CloseOutlined />, cls: 'mg-cell--deny', tip: '拒绝（显式关闭）' },
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
    // 服务器上的授权快照：行内「还原」回到这份，而不是清成默认（默认 ≠ 已保存值）
    const [savedMatrix, setSavedMatrix] = useState<Matrix>({});
    const [dirtyUsers, setDirtyUsers] = useState<Set<number>>(new Set());
    const [saving, setSaving] = useState(false);
    const [loading, setLoading] = useState(true);
    // 刷新按钮只在手动点击时转圈；首屏由骨架屏表达
    const [refreshing, setRefreshing] = useState(false);

    // 角色编辑/应用弹窗
    const [roleModalOpen, setRoleModalOpen] = useState(false);
    const [editingRole, setEditingRole] = useState<AdminRole | null>(null);
    const [roleForm, setRoleForm] = useState({ name: '', description: '', module_codes: [] as string[] });
    const [applyRole, setApplyRole] = useState<AdminRole | null>(null);
    const [applyUserIds, setApplyUserIds] = useState<number[]>([]);
    const [applySearch, setApplySearch] = useState('');
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
            setSavedMatrix(next);
            setDirtyUsers(new Set());
        } catch (e) {
            notifyError(e, '加载授权数据失败');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const refresh = useCallback(async () => {
        setRefreshing(true);
        try {
            await load();
        } finally {
            setRefreshing(false);
        }
    }, [load]);

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
        setMatrix((prev) => ({
            ...prev,
            [userId]: { ...(savedMatrix[userId] ?? {}) },
        }));
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
            // 保存成功后把快照推进到当前矩阵：之后的行内「还原」应回到刚保存的状态
            setSavedMatrix((prev) => {
                const next = { ...prev };
                for (const userId of targets) next[userId] = { ...(matrix[userId] ?? {}) };
                return next;
            });
            setDirtyUsers(new Set());
        } catch (err: any) {
            notifyError(err, '保存失败');
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
            notifyError(err, '保存失败');
        }
    };

    const removeRole = async (role: AdminRole) => {
        try {
            await adminApi.deleteRole(role.id);
            message.success(`角色「${role.name}」已删除`);
            load();
        } catch (err: any) {
            notifyError(err, '删除失败');
        }
    };

    const openApplyRole = (role: AdminRole) => {
        setApplyRole(role);
        setApplyUserIds([]);
        setApplySearch('');
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
            notifyError(err, '应用失败');
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

    /** 平铺授权矩阵（用户 × 模块），用户权限/工具授权两个清单共用。
     *  用 render 函数而不是内联组件：内联组件每次 render 都是新函数身份，
     *  点完单元格会整表重挂载、焦点丢失。 */
    const renderMatrix = (mods: AdminModule[]) => (
        <div className="surface-card tk-table mg-matrix-wrap is-scrollable">
            {/* 模块多时（当前 13 个业务模块）在窄屏会挤成一条：给表格一个下限宽度，横向滚动而不是压缩列名 */}
            <table className="mg-matrix" style={{ minWidth: 200 + mods.length * 76 }}>
                <thead>
                    <tr>
                        <th className="mg-user-col text-left" style={{ width: 220 }}>用户 / 模块</th>
                        {mods.map((m) => (
                            <th key={m.code} className="mg-module-col">
                                <Tooltip title={`${m.name}${m.description ? `｜${m.description}` : ''}（${m.code}）${m.enabled_users ? ` · ${m.enabled_users} 人已开通` : ''}`}>
                                    <div className="leading-tight overflow-hidden">
                                        {/* 列宽固定（76px）时「实体消解与冲突」这类 6 字名称单行会被截断，
                                            去掉 nowrap 让它占满单元格、过长时折成第二行，而不是省略号 */}
                                        <div className="text-[11px] leading-[15px] text-[#3D4B66] break-all [text-wrap:balance]">{m.name}</div>
                                        <div className={`mt-0.5 inline-flex items-center gap-1 text-[10px] leading-4 ${m.is_default_on ? 'text-[#1B87AD]' : 'text-[#A9B4C9]'}`}>
                                            <span
                                                className="inline-block h-[5px] w-[5px] flex-none rounded-full"
                                                style={m.is_default_on
                                                    ? { background: '#1B87AD', boxShadow: '0 0 0 2px rgba(27,135,173,0.14), 0 0 6px rgba(27,135,173,0.55)' }
                                                    : { background: '#C3CCDD' }}
                                            />
                                            {m.is_default_on ? '默认开' : '默认关'}
                                        </div>
                                    </div>
                                </Tooltip>
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {users.map((u) => {
                        const dirty = dirtyUsers.has(u.id);
                        return (
                            <tr key={u.id} className={dirty ? 'mg-row mg-row--dirty' : 'mg-row'}>
                                <td className="mg-user-col">
                                    <Space size={6} wrap>
                                        <span
                                            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-white"
                                            style={{
                                                background: u.role === 'admin'
                                                    ? 'linear-gradient(135deg, #E0A02A, #B8790F)'
                                                    : 'linear-gradient(135deg, #5B8DEF, #8B5CF6)',
                                            }}
                                        >
                                            {u.username.slice(0, 1).toUpperCase()}
                                        </span>
                                        <span className="text-[13px] text-[#1F2A44] whitespace-nowrap">{u.username}</span>
                                        {u.role === 'admin' && <TkTag tone="amber">管理员</TkTag>}
                                        {!u.is_active && <TkTag tone="quiet">停用</TkTag>}
                                        {dirty && (
                                            <>
                                                <TkTag tone="brand">
                                                    <Dot tone="brand" className="!h-[5px] !w-[5px]" />
                                                    未保存
                                                </TkTag>
                                                <Button size="small" type="link" className="!px-0 !text-[11px]"
                                                    onClick={() => resetRow(u.id)}>还原</Button>
                                            </>
                                        )}
                                    </Space>
                                </td>
                                {mods.map((m) => {
                                    if (u.role === 'admin') {
                                        return (
                                            <td key={m.code} className="mg-module-col text-center">
                                                <Tooltip title="管理员默认拥有全部模块">
                                                    <span className="text-[11px] text-[#A9B4C9]">全部</span>
                                                </Tooltip>
                                            </td>
                                        );
                                    }
                                    const state = matrix[u.id]?.[m.code] ?? 'default';
                                    const meta = STATE_META[state];
                                    return (
                                        <td key={m.code} className="mg-module-col text-center">
                                            <Tooltip title={`${meta.tip}（点击切换）`}>
                                                <button
                                                    type="button"
                                                    aria-label={`${u.username} / ${m.name}：${meta.tip}`}
                                                    onClick={() => cycleCell(u.id, m.code)}
                                                    className={`mg-cell ${meta.cls}`}
                                                >
                                                    {meta.icon}
                                                </button>
                                            </Tooltip>
                                        </td>
                                    );
                                })}
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );

    const legendBar = (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11.5px] text-[#66738F]">
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--default">默</span>跟随模块默认
            </span>
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--allow">许</span>允许
            </span>
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--deny">拒</span>拒绝
            </span>
            <span className="text-[#A3ADC2]">点击单元格循环切换：默认 → 允许 → 拒绝</span>
        </div>
    );

    // ── 角色卡片
    const roleCards = (
        <div>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <SectionHead title="角色预设" hint="角色 = 一组模块开通预设；应用时会全量覆盖所选用户的模块授权" />
                <Button type="primary" className="tk-primary-btn" icon={<PlusOutlined />} onClick={() => openRoleModal(null)}>新建角色</Button>
            </div>
            {roles.length === 0 ? (
                <div className="surface-card flex flex-col items-center px-4 py-14 text-center">
                    <span
                        className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] text-[#2F62D8]"
                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                    >
                        <TeamOutlined />
                    </span>
                    <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">暂无角色</div>
                    <div className="mt-1 text-[12.5px] text-[#66738F]">把常用的一组模块授权存成角色，之后一键套用</div>
                    <Button className="tk-soft-btn mt-4" icon={<PlusOutlined />} onClick={() => openRoleModal(null)}>
                        新建角色
                    </Button>
                </div>
            ) : (
                <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
                    {roles.map((r) => (
                        <div
                            key={r.id}
                            className="flex flex-col rounded-2xl border border-[#E9EEF8] p-4 transition-[border-color,box-shadow] duration-200 hover:border-[#CBD9F2] hover:shadow-[0_16px_32px_-20px_rgba(15,23,42,0.30)]"
                            style={{ backgroundImage: 'linear-gradient(160deg, #FFFFFF 0%, rgba(91,141,239,0.07) 100%)' }}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <Space size={10}>
                                    <span
                                        className="flex h-8 w-8 items-center justify-center rounded-lg text-[15px]"
                                        style={{ background: 'rgba(91,141,239,0.12)', color: '#2A57C6' }}
                                    >
                                        <TeamOutlined />
                                    </span>
                                    <span className="font-medium text-[#1F2A44]">{r.name}</span>
                                    {r.is_builtin && <TkTag tone="brand">内置</TkTag>}
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
                            <div className="mt-2 min-h-[32px] text-xs leading-relaxed text-[#66738F]">
                                {r.description || '暂无描述'}
                                <span className="ml-2 whitespace-nowrap">· <span className="tk-mono">{r.module_codes.length}</span> 个模块</span>
                            </div>
                            <div className="mt-2 flex min-h-[46px] flex-1 flex-wrap content-start gap-1">
                                {r.module_codes.map((c) => (
                                    <TkTag key={c} tone="quiet">{moduleMap.get(c)?.name ?? c}</TkTag>
                                ))}
                                {r.module_codes.length === 0 && (
                                    <span className="text-[11.5px] text-[#A3ADC2]">未开通任何模块</span>
                                )}
                            </div>
                            <div className="mt-3 border-t border-[#F0F4FB] pt-3">
                                <Button size="small" className="tk-soft-btn w-full" icon={<UserSwitchOutlined />}
                                    onClick={() => openApplyRole(r)}>
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
                    <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-[#66738F]">
                        {group.title === '工具模块' && <ToolOutlined />}
                        {group.title}
                        <span className="tk-mono text-[10px] text-[#A3ADC2]">{group.mods.length}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                        {group.mods.map((m) => {
                            const checked = roleForm.module_codes.includes(m.code);
                            return (
                                /* div 而不是 label：antd Checkbox 自身是 label，嵌套会导致点复选框切换两次 */
                                <div
                                    key={m.code}
                                    className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
                                        checked
                                            ? 'border-[rgba(91,141,239,0.42)] bg-[rgba(91,141,239,0.08)]'
                                            : 'border-[#E7EEF9] hover:border-[rgba(91,141,239,0.30)] hover:bg-[rgba(91,141,239,0.04)]'
                                    }`}
                                    onClick={() => toggleRoleModule(m.code, !checked)}
                                >
                                    <span className="inline-flex pt-0.5" onClick={(e) => e.stopPropagation()}>
                                        <Checkbox checked={checked}
                                            onChange={(e) => toggleRoleModule(m.code, e.target.checked)} />
                                    </span>
                                    <span className="min-w-0">
                                        <span className="block text-[12.5px] leading-4 text-[#1F2A44]">
                                            {group.title === '工具模块' ? (TOOL_LABELS[m.code] ?? m.name) : m.name}
                                        </span>
                                        <span className="tk-mono block text-[10px] leading-4 text-[#A3ADC2]">{m.code}</span>
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            ))}
        </div>
    );

    const filteredApplyUsers = applySearch.trim()
        ? normalUsers.filter((u) => u.username.toLowerCase().includes(applySearch.trim().toLowerCase()))
        : normalUsers;

    const dirtyCount = dirtyUsers.size;

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={[
                { title: '首页', path: '/' },
                { title: '系统配置' },
                { title: '模块授权' },
            ]} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title="模块授权"
                    titleSuffix="ACCESS MATRIX"
                    subtitle="按用户配置业务模块与工具功能的开通情况；角色 = 一组可复用的模块授权预设"
                    showConstellation={users.length > 0}
                    actions={
                        <>
                            <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>刷新</Button>
                            {dirtyCount > 0 && (
                                <TkTag tone="amber">
                                    <Dot tone="amber" className="!h-[5px] !w-[5px]" />
                                    <span className="tk-mono">{dirtyCount}</span> 个用户未保存
                                </TkTag>
                            )}
                        </>
                    }
                />

                <StatBand
                    className="mb-4"
                    items={[
                        { label: '可授权用户', value: normalUsers.length, tone: 'brand', icon: <TeamOutlined /> },
                        { label: '业务模块', value: businessModules.length, tone: 'violet', icon: <SafetyOutlined /> },
                        { label: '工具模块', value: toolModules.length, tone: 'cyan', icon: <ToolOutlined /> },
                        { label: '角色预设', value: roles.length, tone: 'amber', icon: <UserSwitchOutlined /> },
                    ]}
                />

                {/* 刷新时旧矩阵保留在 Spin 遮罩下（与用户管理的 Table loading 同一观感） */}
                <Spin spinning={loading && users.length > 0}>
                {loading && users.length === 0 ? (
                    <div className="surface-card p-4">
                        <div className="flex items-center gap-2">
                            <div className="tk-skel h-3 w-24" />
                            <div className="tk-skel h-3 w-16" />
                            <div className="tk-skel ml-auto h-3 w-20" />
                        </div>
                        <div className="mt-3 space-y-2">
                            {[0, 1, 2, 3, 4, 5].map((i) => (
                                <div key={i} className="tk-skel h-9 w-full !rounded-lg" />
                            ))}
                        </div>
                    </div>
                ) : (
                <Tabs
                    defaultActiveKey="users"
                    items={[
                        {
                            key: 'users',
                            label: (
                                <span className="inline-flex items-center gap-1.5">
                                    用户权限
                                    <span className="tk-mono text-[11px] text-[#8A94AB]">{normalUsers.length}</span>
                                </span>
                            ),
                            children: (
                                <div>
                                    {legendBar}
                                    {renderMatrix(businessModules)}
                                </div>
                            ),
                        },
                        {
                            key: 'tools',
                            label: (
                                <span className="inline-flex items-center gap-1.5">
                                    工具授权
                                    <span className="tk-mono text-[11px] text-[#8A94AB]">{toolModules.length}</span>
                                </span>
                            ),
                            children: (
                                <div>
                                    <Note tone="quiet" className="mb-3">
                                        工具类功能单独授权：本体问答 / 报告生成 / PPT生成 / MCP 工具，分别控制「工具」子菜单中各入口的可见与可用。
                                    </Note>
                                    {legendBar}
                                    {renderMatrix(toolModules)}
                                </div>
                            ),
                        },
                        {
                            key: 'roles',
                            label: (
                                <span className="inline-flex items-center gap-1.5">
                                    角色配置
                                    <span className="tk-mono text-[11px] text-[#8A94AB]">{roles.length}</span>
                                </span>
                            ),
                            children: roleCards,
                        },
                    ]}
                />
                )}
                </Spin>

                {/* 吸底保存条：矩阵很长时改动后的保存入口一直贴在视口底部 */}
                {dirtyCount > 0 && (
                    <div className="tk-save-bar mt-3">
                        <Dot tone="amber" />
                        <span className="text-[12.5px] text-[#3D4B66]">
                            <span className="tk-mono text-[14px] font-semibold text-[#B8790F]">{dirtyCount}</span> 个用户有未保存的修改
                        </span>
                        <Popconfirm
                            title="放弃所有未保存的修改？"
                            description="矩阵会恢复到服务器上的当前授权。"
                            okText="放弃修改" cancelText="继续编辑"
                            onConfirm={load}
                        >
                            <Button size="small" icon={<UndoOutlined />} disabled={saving}>放弃修改</Button>
                        </Popconfirm>
                        <Button type="primary" size="small" className="tk-primary-btn" icon={<SaveOutlined />} loading={saving} onClick={saveAll}>
                            保存全部
                        </Button>
                    </div>
                )}

                {/* 新建/编辑角色 */}
                <Modal
                    title={editingRole ? `编辑角色 · ${editingRole.name}` : '新建角色'}
                    open={roleModalOpen} onOk={saveRole} onCancel={() => setRoleModalOpen(false)}
                    okText="保存" destroyOnHidden width={620}
                >
                    <div className="space-y-3 py-1">
                        <div>
                            <span className="tk-field-label">角色名称</span>
                            <Input value={roleForm.name} maxLength={64} placeholder="如：建模员"
                                onChange={(e) => setRoleForm((f) => ({ ...f, name: e.target.value }))} />
                        </div>
                        <div>
                            <span className="tk-field-label">描述（可选）</span>
                            <Input value={roleForm.description} maxLength={255} placeholder="该角色的职责说明"
                                onChange={(e) => setRoleForm((f) => ({ ...f, description: e.target.value }))} />
                        </div>
                        <div>
                            <span className="tk-field-label">开通模块（勾选 = 允许；应用角色时未勾选模块将显式关闭）</span>
                            {roleModulePicker}
                        </div>
                    </div>
                </Modal>

                {/* 应用角色到用户 */}
                <Modal
                    title={`应用角色「${applyRole?.name ?? ''}」到用户`}
                    open={!!applyRole} onOk={doApplyRole} onCancel={() => setApplyRole(null)}
                    okText={`应用到 ${applyUserIds.length} 个用户`} confirmLoading={applying}
                    okButtonProps={{ disabled: applyUserIds.length === 0 }}
                    width={560}
                >
                    <div className="space-y-3 py-1">
                        <Note tone="amber">
                            应用后所选用户的模块授权将被该角色预设<b>全量覆盖</b>（允许列表内开通、其余关闭）。管理员默认全模块，已自动排除。
                        </Note>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-[11.5px] text-[#66738F]">
                                选择用户（<span className="tk-mono">{normalUsers.length}</span> 个可选，已选
                                <span className="tk-mono font-semibold text-[#2A57C6]"> {applyUserIds.length} </span>个）
                            </span>
                            <Button size="small" type="link"
                                onClick={() => setApplyUserIds(applyUserIds.length === normalUsers.length ? [] : normalUsers.map((u) => u.id))}>
                                {applyUserIds.length === normalUsers.length ? '取消全选' : '全选'}
                            </Button>
                        </div>
                        {normalUsers.length > 8 && (
                            <Input
                                allowClear
                                value={applySearch}
                                onChange={(e) => setApplySearch(e.target.value)}
                                placeholder="搜索用户名…"
                                prefix={<SearchOutlined className="text-[#5B8DEF]" />}
                            />
                        )}
                        <div className="max-h-64 divide-y divide-[#F0F4FB] overflow-auto rounded-lg border border-[#E7EEF9]">
                            {filteredApplyUsers.map((u) => {
                                const checked = applyUserIds.includes(u.id);
                                const toggleUser = (next: boolean) => setApplyUserIds((prev) => next
                                    ? [...prev, u.id]
                                    : prev.filter((id) => id !== u.id));
                                return (
                                    <div key={u.id}
                                        className={`flex cursor-pointer items-center gap-2 px-3 py-2 transition-colors ${checked ? 'bg-[rgba(91,141,239,0.08)]' : 'hover:bg-[rgba(91,141,239,0.05)]'}`}
                                        onClick={() => toggleUser(!checked)}
                                    >
                                        <span className="inline-flex" onClick={(e) => e.stopPropagation()}>
                                            <Checkbox
                                                checked={checked}
                                                onChange={(e) => toggleUser(e.target.checked)}
                                            />
                                        </span>
                                        <span className="text-[13px] text-[#1F2A44]">{u.username}</span>
                                        {!u.is_active && <TkTag tone="quiet">停用</TkTag>}
                                    </div>
                                );
                            })}
                            {filteredApplyUsers.length === 0 && (
                                <div className="px-3 py-6 text-center text-[11.5px] text-[#A3ADC2]">
                                    {normalUsers.length === 0 ? '暂无普通用户' : '没有匹配的用户名'}
                                </div>
                            )}
                        </div>
                    </div>
                </Modal>

                <style>{`
                    /* 矩阵：冷蓝表体 + 固定用户列 + 未保存行左侧品牌竖条（2026-11-23） */
                    .mg-matrix {
                        width: 100%;
                        border-collapse: separate;
                        border-spacing: 0;
                        table-layout: fixed;
                    }
                    .mg-matrix thead th {
                        background: #E7EFFC;
                        border-bottom: 1px solid #DCE6F6;
                        padding: 9px 6px;
                        font-size: 11.5px;
                        font-weight: 600;
                        color: #52607A;
                    }
                    .mg-matrix thead th.mg-user-col {
                        padding-left: 16px;
                        padding-right: 16px;
                    }
                    .mg-matrix tbody td {
                        background: #F5F9FF;
                        border-bottom: 1px solid #E9F0FA;
                        padding: 8px 6px;
                        vertical-align: middle;
                    }
                    .mg-matrix tbody tr:last-child td { border-bottom: 0; }
                    .mg-matrix tbody tr:hover td { background: #EDF4FF; }
                    .mg-matrix .mg-user-col {
                        position: sticky;
                        left: 0;
                        z-index: 2;
                        padding-left: 16px;
                        padding-right: 12px;
                    }
                    .mg-matrix thead .mg-user-col { z-index: 4; }
                    .mg-matrix tbody tr:hover .mg-user-col { background: #EDF4FF; }
                    .mg-matrix tbody tr.mg-row--dirty .mg-user-col {
                        box-shadow: inset 3px 0 0 #5B8DEF;
                    }
                    .mg-module-col { border-left: 1px solid #E9F0FA; }
                    .mg-cell {
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        width: 28px;
                        height: 28px;
                        border: 1px solid transparent;
                        border-radius: 8px;
                        font-size: 11px;
                        cursor: pointer;
                        transition: background-color .15s ease, border-color .15s ease, box-shadow .15s ease;
                    }
                    .mg-cell:focus-visible {
                        outline: none;
                        box-shadow: 0 0 0 3px rgba(91, 141, 239, 0.22);
                    }
                    .mg-cell--default {
                        background: rgba(15, 23, 42, 0.05);
                        color: #8A94AB;
                    }
                    .mg-cell--default:hover { background: rgba(15, 23, 42, 0.09); }
                    .mg-cell--allow {
                        background: #5B8DEF;
                        color: #FFFFFF;
                        box-shadow: 0 6px 12px -8px rgba(91, 141, 239, 0.9);
                    }
                    .mg-cell--allow:hover { background: #4A7DE0; }
                    .mg-cell--deny {
                        background: rgba(180, 35, 24, 0.10);
                        color: #B42318;
                        border-color: rgba(180, 35, 24, 0.16);
                    }
                    .mg-cell--deny:hover { background: rgba(180, 35, 24, 0.16); }
                    .mg-legend {
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        width: 18px;
                        height: 18px;
                        border-radius: 5px;
                        font-size: 10px;
                    }
                    .mg-legend--default { background: rgba(15, 23, 42, 0.05); color: #8A94AB; }
                    .mg-legend--allow { background: #5B8DEF; color: #FFFFFF; }
                    .mg-legend--deny { background: rgba(180, 35, 24, 0.10); color: #B42318; }
                `}</style>
            </div>
        </div>
    );
};

export default ModuleGrantsPage;
