/**
 * 管理后台 · 模块授权（R8 重构：界面平铺 + 角色配置；R11 拆分工具授权清单）
 * Tab1 用户权限：业务模块平铺矩阵（行=全部用户、列=模块）；
 * Tab2 工具授权：工具类四模块（本体问答/报告生成/PPT生成/MCP 工具）单独清单；
 * 单元格三态点击循环：默认（按 is_default_on）→ 允许 → 拒绝 → 默认；改动即时落库
 * （按用户防抖 400ms + 串行发送，整表 PUT），失败整行回滚到上次成功快照。
 * Tab3 角色配置：角色 = 模块授权预设（roles 表），应用角色即全量覆盖该用户 grants。
 * 视觉（2026-11-23）：TechHero + 指标带；矩阵换成冷蓝表体。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Popconfirm, Space, Spin, Tabs, Tooltip } from 'antd';
import { message, notifyError } from '../../shared/feedback';
import {
    CheckOutlined, CloseOutlined, DeleteOutlined, EditOutlined, LoadingOutlined, MinusOutlined,
    PlusOutlined, ReloadOutlined, SafetyOutlined, SearchOutlined, TeamOutlined,
    ToolOutlined, UserSwitchOutlined, WarningOutlined,
} from '@ant-design/icons';
import { adminApi, AdminModule, AdminRole, AdminUser } from '../../api/admin';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Note, PageLoading, SectionHead, StatBand, TkTag } from '../../shared/consoleUi';

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

/** 行内「保存中」小转圈：固定 16px 占位，出现/消失不挤动用户名；
 *  150ms 内完成的快速保存不闪转圈，只给真正慢下来的保存一个轻提示。 */
const SavingSpinner: React.FC<{ saving: boolean }> = ({ saving }) => {
    const [visible, setVisible] = useState(false);
    useEffect(() => {
        if (!saving) {
            setVisible(false);
            return;
        }
        const timer = window.setTimeout(() => setVisible(true), 150);
        return () => window.clearTimeout(timer);
    }, [saving]);
    return (
        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center" aria-live="polite">
            {visible && (
                <Tooltip title="正在保存授权…">
                    <LoadingOutlined spin className="text-[12px] text-[#5B8DEF]" aria-label="正在保存授权" />
                </Tooltip>
            )}
        </span>
    );
};

const ModuleGrantsPage: React.FC = () => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [modules, setModules] = useState<AdminModule[]>([]);
    const [roles, setRoles] = useState<AdminRole[]>([]);
    const [matrix, setMatrix] = useState<Matrix>({});
    // 即时保存：matrixRef 供防抖定时器/串行队列读最新行，savedMatrixRef 是失败回滚快照
    const matrixRef = useRef<Matrix>({});
    const savedMatrixRef = useRef<Matrix>({});
    // 行内「保存中」提示（请求在飞时显示）
    const [savingUsers, setSavingUsers] = useState<Set<number>>(new Set());
    const saveTimers = useRef<Map<number, number>>(new Map());
    const savingNow = useRef<Set<number>>(new Set());
    const saveQueued = useRef<Set<number>>(new Set());
    const [loading, setLoading] = useState(true);
    // 首屏加载失败 ≠ 没有数据：单独成态，避免把权限/网络错误渲染成空矩阵
    const [loadFailed, setLoadFailed] = useState(false);
    // 刷新按钮只在手动点击时转圈；首屏由骨架屏表达
    const [refreshing, setRefreshing] = useState(false);
    // 角色提交中：防连点重复创建 / 重复删除
    const [savingRole, setSavingRole] = useState(false);
    const [deletingRoleId, setDeletingRoleId] = useState<number | null>(null);

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
            matrixRef.current = next;
            savedMatrixRef.current = next;
            setLoadFailed(false);
            // 刷新拿回的是服务器真值：取消还没发出的防抖保存，避免旧草稿盖掉新数据
            saveTimers.current.forEach((t) => window.clearTimeout(t));
            saveTimers.current.clear();
            saveQueued.current.clear();
        } catch (e) {
            setLoadFailed(true);
            notifyError(e, '加载授权数据失败');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    /** 立即把某用户当前整行授权写入服务器；同一用户串行，在飞期间的新改动排队补发一次 */
    const flushSave = useCallback(async (userId: number) => {
        if (savingNow.current.has(userId)) {
            saveQueued.current.add(userId);
            return;
        }
        savingNow.current.add(userId);
        setSavingUsers((prev) => new Set(prev).add(userId));
        const row = { ...(matrixRef.current[userId] ?? {}) };
        try {
            const grants = Object.entries(row)
                .filter(([, state]) => state !== 'default')
                .map(([module_code, state]) => ({ module_code, allowed: state === 'allow' }));
            await adminApi.putGrants(userId, grants);
            savedMatrixRef.current = { ...savedMatrixRef.current, [userId]: row };
        } catch (err: any) {
            // 请求在飞期间用户又改了：保留最新本地行、排队再试一次，别把新改动一起回滚掉
            const latest = { ...(matrixRef.current[userId] ?? {}) };
            const hasNewer = Object.keys({ ...row, ...latest })
                .some((code) => latest[code] !== row[code]);
            if (hasNewer) {
                saveQueued.current.add(userId);
                notifyError(err, '授权保存失败，正在重试');
            } else {
                // 没有更新的改动：整行回滚到上次成功保存的快照，界面不停留在“看起来已改”的状态
                const snapshot = { ...(savedMatrixRef.current[userId] ?? {}) };
                matrixRef.current = { ...matrixRef.current, [userId]: snapshot };
                setMatrix((prev) => ({ ...prev, [userId]: snapshot }));
                notifyError(err, '授权保存失败');
            }
        } finally {
            savingNow.current.delete(userId);
            setSavingUsers((prev) => {
                const next = new Set(prev);
                next.delete(userId);
                return next;
            });
            if (saveQueued.current.has(userId)) {
                saveQueued.current.delete(userId);
                flushSave(userId);
            }
        }
    }, []);

    /** 400ms 防抖：同一行连点只发最后一次，不同用户互不影响 */
    const scheduleSave = useCallback((userId: number) => {
        const prev = saveTimers.current.get(userId);
        if (prev) window.clearTimeout(prev);
        saveTimers.current.set(userId, window.setTimeout(() => {
            saveTimers.current.delete(userId);
            flushSave(userId);
        }, 400));
    }, [flushSave]);

    /** 把所有还在防抖里的行立刻推入保存队列（卸载 / 刷新 / 角色变更前用） */
    const flushPendingSaves = useCallback(() => {
        const pending = Array.from(saveTimers.current.keys());
        saveTimers.current.forEach((t) => window.clearTimeout(t));
        saveTimers.current.clear();
        pending.forEach((uid) => { void flushSave(uid); });
    }, [flushSave]);

    /** 等保存队列归零：最多等 10s，避免后端异常时界面卡死 */
    const waitForSaves = useCallback(async () => {
        const deadline = Date.now() + 10_000;
        while ((savingNow.current.size > 0 || saveQueued.current.size > 0) && Date.now() < deadline) {
            await new Promise((resolve) => window.setTimeout(resolve, 50));
        }
    }, []);

    /** 先把本地授权改动落库再回源：否则 load 可能拿到旧值，在飞的 PUT 又晚一步覆盖 */
    const syncThenLoad = useCallback(async () => {
        flushPendingSaves();
        await waitForSaves();
        await load();
    }, [flushPendingSaves, waitForSaves, load]);

    const refresh = useCallback(async () => {
        setRefreshing(true);
        try {
            await syncThenLoad();
        } finally {
            setRefreshing(false);
        }
    }, [syncThenLoad]);

    /** 单元格三态循环：改本地矩阵后立即安排落库（无草稿态、无二次确认） */
    const cycleCell = (userId: number, code: string) => {
        const target = users.find((u) => u.id === userId);
        if (target?.role === 'admin') return;
        const prevRow = matrixRef.current[userId] ?? {};
        const next: Matrix = {
            ...matrixRef.current,
            [userId]: { ...prevRow, [code]: NEXT_STATE[prevRow[code] ?? 'default'] },
        };
        matrixRef.current = next;
        setMatrix(next);
        scheduleSave(userId);
    };

    // 离开页面不等于放弃改动：把还在防抖窗口里的行直接补发（请求不依赖组件存活）
    useEffect(() => () => { flushPendingSaves(); }, [flushPendingSaves]);

    // ── 角色编辑
    const openRoleModal = (role: AdminRole | null) => {
        setEditingRole(role);
        setRoleForm(role
            ? { name: role.name, description: role.description ?? '', module_codes: [...role.module_codes] }
            : { name: '', description: '', module_codes: [] });
        setRoleModalOpen(true);
    };

    const saveRole = async () => {
        if (savingRole) return;
        if (!roleForm.name.trim()) { message.warning('请填写角色名称'); return; }
        setSavingRole(true);
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
            // 角色变更后回源前，先把矩阵里未落库的手动改动推上去，避免被 load 的旧值覆盖
            await syncThenLoad();
        } catch (err: any) {
            notifyError(err, '保存失败');
        } finally {
            setSavingRole(false);
        }
    };

    const removeRole = async (role: AdminRole) => {
        if (deletingRoleId != null) return;
        setDeletingRoleId(role.id);
        try {
            await adminApi.deleteRole(role.id);
            message.success(`角色「${role.name}」已删除`);
            await syncThenLoad();
        } catch (err: any) {
            notifyError(err, '删除失败');
        } finally {
            setDeletingRoleId(null);
        }
    };

    const openApplyRole = (role: AdminRole) => {
        setApplyRole(role);
        setApplyUserIds([]);
        setApplySearch('');
    };

    const doApplyRole = async () => {
        if (!applyRole || applyUserIds.length === 0) { message.warning('请选择要应用的用户'); return; }
        if (applying) return;
        setApplying(true);
        try {
            // 应用角色会全量覆盖所选用户的 grants：先把本地手动改动落库并等队列清空，
            // 否则旧行可能在角色应用之后补发，把刚应用的授权又覆盖回去
            flushPendingSaves();
            await waitForSaves();
            const resp = await adminApi.applyRole(applyRole.id, applyUserIds);
            const skippedMsg = resp.skipped?.length
                ? `，跳过 ${resp.skipped.length} 个（${resp.skipped.map((s) => s.reason).join('、')}）` : '';
            message.success(`角色「${resp.role}」已应用到 ${resp.applied.length} 个用户${skippedMsg}`);
            setApplyRole(null);
            await load();
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
                        const saving = savingUsers.has(u.id);
                        return (
                            <tr key={u.id} className="mg-row">
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
                                        <SavingSpinner saving={saving} />
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
        <div className="mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11.5px] text-[#66738F]">
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--default">默</span>跟随模块默认
            </span>
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--allow">许</span>允许
            </span>
            <span className="inline-flex items-center gap-1.5">
                <span className="mg-legend mg-legend--deny">拒</span>拒绝
            </span>
            <span className="text-[#A3ADC2]">点击单元格循环切换，改动即时保存：默认 → 允许 → 拒绝</span>
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
                                    <Button size="small" type="text" icon={<EditOutlined />}
                                        aria-label="编辑角色"
                                        disabled={savingRole || deletingRoleId != null}
                                        onClick={() => openRoleModal(r)} />
                                    {!r.is_builtin && (
                                        <Popconfirm title={`确定删除角色「${r.name}」？`} onConfirm={() => removeRole(r)}>
                                            <Tooltip title="删除">
                                                <Button
                                                    size="small"
                                                    type="text"
                                                    danger
                                                    icon={<DeleteOutlined />}
                                                    loading={deletingRoleId === r.id}
                                                    disabled={deletingRoleId != null && deletingRoleId !== r.id}
                                                />
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

    // 首屏：指标带/页签等静态结构先出（计数显示占位），矩阵/角色卡等数据齐了再出
    const matrixLoading = loading && users.length === 0;

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
                        <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>刷新</Button>
                    }
                />

                {/* 静态结构先出：指标带与页签立刻可见（计数等数据齐了再填），矩阵/角色卡等数据齐了再出 */}
                <StatBand
                    className="mb-1.5"
                    items={[
                        { label: '可授权用户', value: matrixLoading ? '—' : normalUsers.length, tone: 'brand', icon: <TeamOutlined /> },
                        { label: '业务模块', value: matrixLoading ? '—' : businessModules.length, tone: 'violet', icon: <SafetyOutlined /> },
                        { label: '工具模块', value: matrixLoading ? '—' : toolModules.length, tone: 'cyan', icon: <ToolOutlined /> },
                        { label: '角色预设', value: matrixLoading ? '—' : roles.length, tone: 'amber', icon: <UserSwitchOutlined /> },
                    ]}
                />

                {/* 刷新时旧矩阵保留在 Spin 遮罩下（与用户管理的 Table loading 同一观感） */}
                {loadFailed && users.length === 0 && !loading ? (
                    /* 加载失败与「真的没有数据」必须区分：否则权限/网络错误看起来像空矩阵 */
                    <div className="surface-card">
                        <div className="flex flex-col items-center justify-center px-4 py-14 text-center">
                            <span
                                className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px]"
                                style={{ background: 'rgba(180,35,24,0.10)', color: '#B42318' }}
                            >
                                <WarningOutlined />
                            </span>
                            <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">授权数据加载失败</div>
                            <div className="mt-1 text-[12.5px] text-[#66738F]">请检查网络或后端服务后重试</div>
                            <Button className="tk-soft-btn mt-4" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>
                                重新加载
                            </Button>
                        </div>
                    </div>
                ) : (
                <Spin spinning={loading && users.length > 0}>
                <Tabs
                    defaultActiveKey="users"
                    className="mg-tabs"
                    tabBarStyle={{ marginBottom: 8 }}
                    items={[
                        {
                            key: 'users',
                            label: (
                                <span className="inline-flex items-center gap-1.5">
                                    用户权限
                                    {!matrixLoading && (
                                        <span className="tk-mono text-[11px] text-[#8A94AB]">{normalUsers.length}</span>
                                    )}
                                </span>
                            ),
                            children: matrixLoading ? (
                                <PageLoading hint="正在加载用户与授权矩阵…" />
                            ) : (
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
                                    {!matrixLoading && (
                                        <span className="tk-mono text-[11px] text-[#8A94AB]">{toolModules.length}</span>
                                    )}
                                </span>
                            ),
                            children: matrixLoading ? (
                                <PageLoading hint="正在加载工具授权…" />
                            ) : (
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
                                    {!matrixLoading && (
                                        <span className="tk-mono text-[11px] text-[#8A94AB]">{roles.length}</span>
                                    )}
                                </span>
                            ),
                            children: matrixLoading ? <PageLoading hint="正在加载角色预设…" /> : roleCards,
                        },
                    ]}
                />
                </Spin>
                )}

                {/* 新建/编辑角色 */}
                <Modal
                    title={editingRole ? `编辑角色 · ${editingRole.name}` : '新建角色'}
                    open={roleModalOpen}
                    onOk={saveRole}
                    onCancel={() => { if (!savingRole) setRoleModalOpen(false); }}
                    okText="保存"
                    confirmLoading={savingRole}
                    cancelButtonProps={{ disabled: savingRole }}
                    destroyOnHidden
                    width={620}
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
                    open={!!applyRole}
                    onOk={doApplyRole}
                    onCancel={() => { if (!applying) setApplyRole(null); }}
                    okText={`应用到 ${applyUserIds.length} 个用户`}
                    confirmLoading={applying}
                    cancelButtonProps={{ disabled: applying }}
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
                    /* 页签：antd 默认 tab 上下各 12px 内边距，会把「指标带 → 页签」的空档撑大；
                       这里收到 6px，配合指标带 mb-1.5 让视觉间距更紧凑 */
                    .mg-tabs.ant-tabs > .ant-tabs-nav .ant-tabs-tab { padding: 6px 0; }
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
                    .mg-module-col { border-left: 1px solid #E9F0FA; }
                    .mg-cell {
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        /* 2026-11-24：整格收一档（28→26、图标 11→10），矩阵更紧凑，图标比例不变 */
                        width: 26px;
                        height: 26px;
                        border: 1px solid transparent;
                        border-radius: 7px;
                        font-size: 10px;
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
