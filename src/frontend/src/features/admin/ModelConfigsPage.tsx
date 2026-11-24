/**
 * 管理后台 · 模型配置（M2，docs/design/05 §6.10 / 03 §5）
 * purpose 四 Tab + 卡片列表 + provider 动态表单 + 连通性测试（密钥掩码回显）。
 * 权限模型（与后端 _scope_guard 对齐）：global 配置仅 admin 可建/改；
 * project 配置项目 owner 可建/改——普通用户（项目 owner）进入本页时
 * 默认项目作用域、仅展示自己拥有的项目，全局行只读展示。
 * 视觉（2026-11-23）：TechHero + 当前用途指标带；卡片体改成「字段行」结构，
 * 最近测试用发光圆点表达；抽屉里按基础信息 / 连接参数 / 运行参数分段，
 * 测试结果直接回显在抽屉底部，不再只靠顶部 message。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, Drawer, Form, Input, InputNumber, Select, Space, Spin, Switch, Tabs, Tooltip } from 'antd';
import {
    ApiOutlined, CloudOutlined, DeploymentUnitOutlined, EyeOutlined, MessageOutlined,
    PlusOutlined, QuestionCircleOutlined, ReloadOutlined, RobotOutlined, StarFilled,
    StarOutlined, ThunderboltOutlined, WarningOutlined,
} from '@ant-design/icons';
import { modelConfigsApi, ModelConfigRow, ProviderMeta, TestResult } from '../../api/model-configs';
import { message, confirmModal, notifyError } from '../../shared/feedback';
import { useAuthStore } from '../../shared/auth/authStore';
import { projectsApi } from '../../api/projects';
import { ProjectData } from '../../types/ontology';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, PageLoading, SectionHead, StatBand, TkTag } from '../../shared/consoleUi';
import Tip from '../../shared/Tip';

const PURPOSES = [
    { key: 'extract', label: '抽取模型', hint: '文档解析后的本体抽取（消耗最大）', icon: <ThunderboltOutlined /> },
    { key: 'chat', label: '对话模型', hint: '问答与对话场景', icon: <MessageOutlined /> },
    { key: 'embedding', label: '嵌入模型', hint: '向量化（bge-m3=1024 维，须与 Milvus 集合一致）', icon: <DeploymentUnitOutlined /> },
    { key: 'vl', label: '视觉模型', hint: '扫描件兜底解析', icon: <EyeOutlined /> },
] as const;

const PURPOSE_META = Object.fromEntries(PURPOSES.map((p) => [p.key, p]));

/** 字段行：左标签固定宽、右值等宽字形，卡片里扫视时标签列是对齐的 */
const FieldRow: React.FC<{ label: string; children: React.ReactNode; mono?: boolean }> = ({ label, children, mono }) => (
    <div className="flex items-baseline gap-2 min-w-0">
        <span className="w-14 shrink-0 text-[11.5px] text-[#8A94AB]">{label}</span>
        <span className={`min-w-0 flex-1 ${mono ? 'tk-mono' : ''} text-[12.5px] text-[#3D4B66]`}>{children}</span>
    </div>
);

const ModelConfigsPage: React.FC = () => {
    const { user, loaded: authLoaded } = useAuthStore();
    const isAdmin = user?.role === 'admin';
    // 每个用途 tab 各自缓存一份 rows：切回已加载过的 tab 直接渲染缓存，
    // 不再先显示上一个 tab 的卡片再被替换（2026-11-24：切 tab 闪烁）
    const [rowsByPurpose, setRowsByPurpose] = useState<Record<string, ModelConfigRow[]>>({});
    const [providers, setProviders] = useState<ProviderMeta[]>([]);
    const [myProjects, setMyProjects] = useState<ProjectData[]>([]);
    const [activePurpose, setActivePurpose] = useState<string>('extract');
    // 刷新按钮只在手动点击时转圈；首次进入某用途由一条延迟淡入的加载带表达
    const [refreshing, setRefreshing] = useState(false);
    // 当前 tab 的数据/是否已加载过（有缓存即算已加载，空数组也算）
    const rows = rowsByPurpose[activePurpose] ?? [];
    const rowsLoaded = Object.prototype.hasOwnProperty.call(rowsByPurpose, activePurpose);
    // 其余用途后台预取只需一次
    const prefetchedRef = useRef(false);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [editing, setEditing] = useState<ModelConfigRow | null>(null);
    // 测试中的行/表单（用 Set：并发测试时各自保持 loading，不会互相清掉）
    const [testingKeys, setTestingKeys] = useState<Set<string>>(new Set());
    const [formTest, setFormTest] = useState<TestResult | null>(null);
    const [form] = Form.useForm();
    const [providerSel, setProviderSel] = useState<string>('openai_compatible');
    // 保存中：防连点重复创建 / 重复 PATCH
    const [saving, setSaving] = useState(false);
    // 各用途的加载失败标记：失败 ≠ 没有配置，不能渲染成空态
    const [failedByPurpose, setFailedByPurpose] = useState<Record<string, boolean>>({});

    // 普通用户可管理的项目（owner）；admin 不需要
    // projectsReady：load() 依赖这份列表，没到手之前先不 load——
    // 否则会先发一轮「空项目」的请求，等列表回来再整套重跑一次（双发，2026-11-18）
    const [projectsReady, setProjectsReady] = useState(false);
    useEffect(() => {
        if (!authLoaded) return;        // 登录态还没 hydrate 完：先别判断角色、也别拉项目
        if (isAdmin) { setProjectsReady(true); return; }
        let alive = true;
        projectsApi.getMyProjects()
            .then((ps) => { if (alive) setMyProjects(ps); })
            .catch(() => { if (alive) setMyProjects([]); })
            .finally(() => { if (alive) setProjectsReady(true); });
        return () => { alive = false; };
    }, [authLoaded, isAdmin]);

    const load = async (purpose: string = activePurpose) => {
        try {
            if (isAdmin) {
                const [rs, ps] = await Promise.all([
                    modelConfigsApi.list({ purpose }),
                    modelConfigsApi.listProviders(),
                ]);
                setRowsByPurpose((prev) => ({ ...prev, [purpose]: rs }));
                setProviders(ps);
            } else {
                // 普通用户：全局行（只读展示，默认项可见）+ 自己拥有项目的配置
                const [globalRs, ps] = await Promise.all([
                    modelConfigsApi.list({ purpose, scope: 'global' }),
                    modelConfigsApi.listProviders(),
                ]);
                const projectRs = await Promise.all(
                    myProjects.map((p) =>
                        modelConfigsApi.list({ purpose, scope: 'project', project_id: p.id })
                            .catch(() => [] as ModelConfigRow[])),
                );
                setRowsByPurpose((prev) => ({ ...prev, [purpose]: [...globalRs, ...projectRs.flat()] }));
                setProviders(ps);
            }
            setFailedByPurpose((prev) => ({ ...prev, [purpose]: false }));
        } catch (err: any) {
            notifyError(err, '加载失败');
            setFailedByPurpose((prev) => ({ ...prev, [purpose]: true }));
            // 失败也记为「已加载（空）」，避免加载提示一直挂着；可点「刷新」重试
            setRowsByPurpose((prev) => (prev[purpose] ? prev : { ...prev, [purpose]: [] }));
        }
    };

    useEffect(() => {
        if (!projectsReady) return;
        // 只有首次进入该用途才请求：已有缓存的 tab 直接展示，切 tab 不会闪旧卡片
        if (rowsByPurpose[activePurpose]) return;
        void load(activePurpose);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activePurpose, isAdmin, myProjects, projectsReady]);

    // 后台预取其余用途（管理员：本页只出现在「系统配置」管理员菜单里）：
    // 四个用途都先进缓存，之后再切 tab 就是纯本地渲染，加载带根本不会出现、也就没有闪烁。
    // 普通用户按项目逐项拉取，请求量会随项目数放大，不预取——切 tab 时才按需加载。
    useEffect(() => {
        if (!projectsReady || !isAdmin || prefetchedRef.current) return;
        prefetchedRef.current = true;
        const others = PURPOSES.map((p) => p.key).filter((k) => k !== activePurpose);
        others.forEach((k) => { if (!rowsByPurpose[k]) void load(k); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectsReady, isAdmin]);

    const refresh = async () => {
        setRefreshing(true);
        try {
            await load();
        } finally {
            setRefreshing(false);
        }
    };

    // 权限判定：该行当前用户是否可管理（编辑/设默认/删除）
    const canManage = (row: ModelConfigRow) =>
        isAdmin || (row.scope === 'project' && !!row.project_id && myProjects.some((p) => p.id === row.project_id));

    const providerMeta = useMemo(
        () => providers.find((p) => p.id === providerSel),
        [providers, providerSel],
    );

    const purposeMeta = PURPOSE_META[activePurpose as keyof typeof PURPOSE_META];
    const stats = useMemo(() => ({
        total: rows.length,
        globals: rows.filter((r) => r.scope === 'global').length,
        projects: rows.filter((r) => r.scope === 'project').length,
        enabled: rows.filter((r) => r.enabled).length,
    }), [rows]);

    const openCreate = () => {
        setEditing(null);
        setFormTest(null);
        form.resetFields();
        // 普通用户默认项目作用域（全局仅 admin 可建，避免 403）
        const defaultScope = isAdmin ? 'global' : 'project';
        form.setFieldsValue({
            scope: defaultScope,
            project_id: !isAdmin && myProjects.length === 1 ? myProjects[0].id : undefined,
            purpose: activePurpose, provider: 'openai_compatible',
            base_url: providers.find((p) => p.id === 'openai_compatible')?.default_base_url || '',
        });
        setProviderSel('openai_compatible');
        setDrawerOpen(true);
    };

    const openEdit = (row: ModelConfigRow) => {
        setEditing(row);
        setFormTest(null);
        form.resetFields();
        // params 回填（2026-11-08）：…row 只带嵌套的 params 对象，表单字段
        // （chunk_size 等）取不到值 → 落到硬编码 initialValue，用户打开编辑
        // 再保存就会把库里调好的参数悄悄覆盖成默认值
        const p = (row.params || {}) as Record<string, any>;
        form.setFieldsValue({
            ...row, api_key: '', scope: row.scope,
            chunk_size: p.chunk_size, chunk_overlap: p.chunk_overlap,
            request_interval: p.request_interval, parallelism: p.parallelism ?? 16,
            llm_timeout: p.llm_timeout,
            disable_think: !!p.disable_think, streaming_enabled: !!p.streaming_enabled,
        });
        setProviderSel(row.provider);
        setDrawerOpen(true);
    };

    const handleSave = async () => {
        if (saving) return;
        // 校验失败交给 antd 在字段下方提示；这里吞掉 reject，避免控制台出现未处理的 Promise
        let values: any;
        try {
            values = await form.validateFields();
        } catch {
            return;
        }
        if (values.scope === 'global' && !isAdmin) {
            message.error('全局模型配置需要管理员权限：普通用户请选择「项目」作用域');
            return;
        }
        const payload = {
            scope: values.scope,
            project_id: values.scope === 'project' ? values.project_id : null,
            purpose: values.purpose,
            name: values.name,
            provider: values.provider,
            base_url: values.base_url,
            model_name: values.model_name,
            api_key: values.api_key || undefined,
            dims: values.purpose === 'embedding' ? values.dims : null,
            params: {
                chunk_size: values.chunk_size,
                chunk_overlap: values.chunk_overlap,
                request_interval: values.request_interval,
                parallelism: values.parallelism,
                llm_timeout: values.llm_timeout,
                disable_think: !!values.disable_think,
                streaming_enabled: !!values.streaming_enabled,
            },
        };
        setSaving(true);
        try {
            if (editing) {
                await modelConfigsApi.patch(editing.id, payload);
                message.success('配置已更新');
            } else {
                await modelConfigsApi.create(payload);
                message.success('配置已创建');
            }
            setDrawerOpen(false);
            if (values.purpose !== activePurpose) {
                // 在别的 tab 里新建的配置：作废该用途缓存，下次进入重新拉取
                setRowsByPurpose((prev) => {
                    if (!(values.purpose in prev)) return prev;
                    const next = { ...prev };
                    delete next[values.purpose];
                    return next;
                });
            }
            load();
        } catch (err: any) {
            notifyError(err, '保存失败');
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async (row?: ModelConfigRow) => {
        const key = row ? `row:${row.id}` : 'form';
        if (testingKeys.has(key)) return;
        setTestingKeys((prev) => new Set(prev).add(key));
        try {
            if (row) {
                const result = await modelConfigsApi.test({
                    purpose: row.purpose, provider: row.provider, base_url: row.base_url,
                    model_name: row.model_name, dims: row.dims, config_id: row.id,
                });
                if (result.ok) {
                    message.success(`连通成功（${result.latency_ms}ms）：${result.message}`);
                } else {
                    message.error(`测试失败：${result.message}`);
                }
            } else {
                const values = await form.validateFields();
                const result = await modelConfigsApi.test({
                    purpose: values.purpose, provider: values.provider, base_url: values.base_url,
                    model_name: values.model_name, api_key: values.api_key || undefined,
                    dims: values.purpose === 'embedding' ? values.dims : null,
                    // 编辑模式带 config_id：key 留空（=保持不变）时后端用已存的密钥测试，
                    // 否则空 key 直接发出去 401（2026-11-08 用户实测）
                    config_id: editing?.id,
                });
                setFormTest(result);
                if (result.ok) {
                    message.success(`连通成功（${result.latency_ms}ms）：${result.message}`);
                } else {
                    message.error(`测试失败：${result.message}`);
                }
            }
            load();
        } catch (err: any) {
            if (err?.errorFields) {
                message.warning('请先补全必填的连接参数');
            } else {
                notifyError(err, '测试请求失败');
            }
        } finally {
            setTestingKeys((prev) => {
                const next = new Set(prev);
                next.delete(key);
                return next;
            });
        }
    };

    const handleSetDefault = async (row: ModelConfigRow) => {
        try {
            await modelConfigsApi.setDefault(row.id);
            message.success(`已设为 ${row.purpose} 默认`);
            load();
        } catch (err: any) {
            notifyError(err, '操作失败');
        }
    };

    const handleDelete = (row: ModelConfigRow) => {
        confirmModal({
            title: `删除配置「${row.name}」？`,
            content: '默认配置不可删除；删除后使用该配置的流程将回落到环境变量。',
            okText: '删除', okType: 'danger',
            onOk: async () => {
                try {
                    await modelConfigsApi.remove(row.id);
                    message.success('配置已删除');
                    load();
                } catch (err: any) {
                    notifyError(err, '删除失败');
                }
            },
        });
    };

    const purposeWatch = Form.useWatch('purpose', form);
    const scopeWatch = Form.useWatch('scope', form);
    const isEmbedding = purposeWatch === 'embedding';
    const isExtract = purposeWatch === 'extract';
    const isChat = purposeWatch === 'chat';
    const isVL = purposeWatch === 'vl';
    const projectName = (pid?: number | null) =>
        myProjects.find((p) => p.id === pid)?.name || (pid ? `项目#${pid}` : '');

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={[
                { title: '首页', path: '/' },
                { title: '系统配置' },
                { title: '模型配置' },
            ]} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title="模型配置"
                    titleSuffix="MODEL REGISTRY"
                    subtitle="配置抽取 / 对话 / 嵌入 / 视觉四类模型服务；全局配置由管理员维护，项目级配置由项目 owner 维护"
                    showConstellation={rows.length > 0}
                    actions={
                        <>
                            <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>刷新</Button>
                            <Button type="primary" className="tk-primary-btn" icon={<PlusOutlined />} onClick={openCreate}>新建配置</Button>
                        </>
                    }
                />

                {/* 指标带与页签属于静态结构，先出；数值等当前用途数据回来再填。
                    位置与「模块授权」一致：指标带在页签上方 */}
                <StatBand
                    className="mb-1.5"
                    items={[
                        {
                            label: `${purposeMeta?.label ?? '当前用途'}配置`, value: stats.total, tone: 'brand',
                            icon: purposeMeta?.icon ?? <RobotOutlined />,
                        },
                        { label: '全局', value: stats.globals, tone: 'violet', icon: <CloudOutlined /> },
                        { label: '项目级', value: stats.projects, tone: 'cyan', icon: <DeploymentUnitOutlined /> },
                        { label: '已启用', value: stats.enabled, tone: 'amber', icon: <ApiOutlined /> },
                    ]}
                />

                <Tabs
                    activeKey={activePurpose}
                    onChange={setActivePurpose}
                    items={PURPOSES.map((p) => ({
                        key: p.key,
                        label: (
                            <span className="inline-flex items-center gap-1.5">
                                {p.icon}
                                {p.label}
                                <Tooltip title={p.hint}>
                                    <QuestionCircleOutlined className="text-[#A9B4C9] text-[11px]" />
                                </Tooltip>
                            </span>
                        ),
                    }))}
                    className="mc-tabs"
                    tabBarStyle={{ marginBottom: 8 }}
                />

                {/* 手动刷新时旧卡片压在 Spin 遮罩下 */}
                {rowsLoaded ? (
                failedByPurpose[activePurpose] && rows.length === 0 ? (
                    /* 加载失败 ≠ 没有配置：权限/网络错误不能渲染成空态 */
                    <div className="surface-card">
                        <div className="flex flex-col items-center justify-center px-4 py-14 text-center">
                            <span
                                className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px]"
                                style={{ background: 'rgba(180,35,24,0.10)', color: '#B42318' }}
                            >
                                <WarningOutlined />
                            </span>
                            <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">模型配置加载失败</div>
                            <div className="mt-1 text-[12.5px] text-[#66738F]">请检查网络或后端服务后重试</div>
                            <Button className="tk-soft-btn mt-4" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>
                                重新加载
                            </Button>
                        </div>
                    </div>
                ) : (
                <Spin spinning={refreshing}>
                {rows.length === 0 ? (
                    /* 空态不做整块白卡：横向一条虚线占位带，高度只有原先三分之一，不抢卡片区的视觉重心 */
                    <div
                        className="flex flex-wrap items-center gap-4 rounded-2xl px-5 py-4"
                        style={{
                            border: '1px dashed rgba(91,141,239,0.30)',
                            backgroundImage: 'linear-gradient(120deg, rgba(251,253,255,0.96) 0%, rgba(91,141,239,0.06) 100%)',
                        }}
                    >
                        <span
                            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-[18px] text-[#2F62D8]"
                            style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                        >
                            {purposeMeta?.icon ?? <RobotOutlined />}
                        </span>
                        <div className="min-w-0 flex-1">
                            <div className="text-[13.5px] font-semibold text-[#0F172A]">
                                暂无「{purposeMeta?.label ?? '当前用途'}」配置
                            </div>
                            <div className="mt-0.5 text-[12px] leading-relaxed text-[#66738F]">
                                {purposeMeta?.hint}
                            </div>
                        </div>
                        <Button className="tk-soft-btn shrink-0" icon={<PlusOutlined />} onClick={openCreate}>新建配置</Button>
                    </div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>
                        {rows.map((row) => {
                            const pMeta = PURPOSE_META[row.purpose];
                            const manageable = canManage(row);
                            return (
                                // 与「模块授权 · 角色卡」完全同一支背景：
                                // 内联写死，避免被 antd 运行时注入的 .ant-card 背景覆盖
                                <Card
                                    key={row.id}
                                    size="small"
                                    className="mc-card tk-cold-card"
                                    style={{
                                        backgroundColor: 'transparent',
                                        backgroundImage: 'linear-gradient(160deg, #FFFFFF 0%, rgba(91,141,239,0.07) 100%)',
                                    }}
                                    title={
                                        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                                            <span
                                                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[13px]"
                                                style={{ background: 'rgba(91,141,239,0.10)', color: '#2A57C6' }}
                                            >
                                                {pMeta?.icon ?? <RobotOutlined />}
                                            </span>
                                            <Tip text={row.name}>
                                                <span className="min-w-0 truncate text-[13.5px] font-semibold text-[#1F2A44]">{row.name}</span>
                                            </Tip>
                                            {row.is_default && <TkTag tone="brand"><StarFilled className="text-[9px]" />默认</TkTag>}
                                            {!row.enabled && <TkTag tone="quiet">停用</TkTag>}
                                            <TkTag tone={row.scope === 'global' ? 'violet' : 'cyan'}>
                                                <span className="max-w-[160px] truncate">
                                                    {row.scope === 'global' ? '全局' : projectName(row.project_id)}
                                                </span>
                                            </TkTag>
                                            {!manageable && <TkTag tone="quiet">只读</TkTag>}
                                        </div>
                                    }
                                >
                                    <div className="space-y-1.5">
                                        <FieldRow label="模型" mono>
                                            <Tip text={row.model_name}>
                                                <span className="block truncate">{row.model_name}</span>
                                            </Tip>
                                        </FieldRow>
                                        <FieldRow label="地址" mono>
                                            <span className="break-all text-[#66738F]">{row.base_url}</span>
                                        </FieldRow>
                                        <FieldRow label="密钥" mono>
                                            <span className="truncate text-[#66738F]">
                                                {row.api_key_set ? row.api_key_masked : '（无需密钥）'}
                                            </span>
                                            {row.dims ? <span className="ml-2 inline-flex align-middle"><TkTag tone="quiet" mono>{row.dims} 维</TkTag></span> : null}
                                        </FieldRow>
                                        <FieldRow label="最近测试">
                                            <span className="flex flex-wrap items-center gap-1.5">
                                                <Dot tone={row.last_test_ok === true ? 'cyan' : row.last_test_ok === false ? 'danger' : 'quiet'} />
                                                <span className={row.last_test_ok === true ? 'text-[#1B87AD]' : row.last_test_ok === false ? 'text-[#B42318]' : 'text-[#8A94AB]'}>
                                                    {row.last_test_ok === true ? '通过' : row.last_test_ok === false ? '失败' : '未测试'}
                                                </span>
                                                {row.last_test_at && (
                                                    <span className="tk-mono text-[11px] text-[#A3ADC2]">{row.last_test_at.slice(0, 19)}</span>
                                                )}
                                            </span>
                                        </FieldRow>
                                    </div>

                                    {/* 卡片操作：自定义底栏，不再用 antd actions 的等分格子——
                                        只读行需要把「只读」说明放在同一行右侧 */}
                                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[#EDF1F8] pt-2.5">
                                        <div className="flex items-center gap-0.5">
                                            <Tooltip title={manageable
                                                ? ''
                                                : (row.scope === 'global' ? '全局配置由管理员测试' : '项目 owner 可测试')}>
                                                <Button size="small" type="link" icon={<ApiOutlined />}
                                                        loading={testingKeys.has(`row:${row.id}`)}
                                                        disabled={!manageable}
                                                        onClick={() => handleTest(row)}>测试</Button>
                                            </Tooltip>
                                            <Button size="small" type="link"
                                                    icon={row.is_default ? <StarFilled style={{ color: '#E0A02A' }} /> : <StarOutlined />}
                                                    disabled={row.is_default || !manageable}
                                                    onClick={() => handleSetDefault(row)}>
                                                {row.is_default ? '默认' : '设默认'}
                                            </Button>
                                        </div>
                                        <div className="flex items-center gap-0.5">
                                            {manageable ? (
                                                <>
                                                    <Button size="small" type="link" onClick={() => openEdit(row)}>编辑</Button>
                                                    <Tooltip title={row.is_default ? '默认配置不可删除' : '删除配置'}>
                                                        <Button size="small" type="link" danger disabled={row.is_default}
                                                                onClick={() => handleDelete(row)}>删除</Button>
                                                    </Tooltip>
                                                </>
                                            ) : (
                                                <span className="text-[11px] text-[#A3ADC2]">
                                                    {row.scope === 'global' ? '管理员管理' : '项目 owner 可管理'}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </Card>
                            );
                        })}
                    </div>
                )}
                </Spin>
                )
                ) : (
                    /* 首屏不做骨架屏：页头/页签/指标带先出，数据齐了再填正文 */
                    <PageLoading hint={`正在加载「${purposeMeta?.label ?? '当前用途'}」配置…`} />
                )}

                <Drawer
                    title={editing ? '编辑模型配置' : '新建模型配置'}
                    width={560}
                    open={drawerOpen}
                    onClose={() => { if (!saving) setDrawerOpen(false); }}
                    destroyOnHidden
                    styles={{ body: { padding: '12px 24px 8px' } }}
                    footer={
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex min-w-0 items-center gap-2">
                                <Button icon={<ApiOutlined />} loading={testingKeys.has('form')}
                                        disabled={saving} onClick={() => handleTest()}>
                                    测试连接
                                </Button>
                                {formTest && (
                                    <Tooltip title={formTest.message}>
                                        <span className="flex min-w-0 items-center gap-1.5 text-[11.5px]">
                                            <Dot tone={formTest.ok ? 'cyan' : 'danger'} />
                                            <span className={`max-w-[160px] truncate ${formTest.ok ? 'text-[#1B87AD]' : 'text-[#B42318]'}`}>
                                                {formTest.ok ? `连通 ${formTest.latency_ms}ms` : '连接失败'}
                                            </span>
                                        </span>
                                    </Tooltip>
                                )}
                            </div>
                            <Space>
                                <Button onClick={() => setDrawerOpen(false)} disabled={saving}>取消</Button>
                                <Button type="primary" onClick={handleSave} loading={saving}>保存</Button>
                            </Space>
                        </div>
                    }
                >
                    <Form form={form} layout="vertical" className="mc-form">
                        <SectionHead title="基础信息" hint="用途与作用域决定谁可以管理这份配置" className="mb-2" />
                        <Form.Item name="purpose" label="用途" rules={[{ required: true }]}>
                            <Select disabled={!!editing}
                                    options={PURPOSES.map((p) => ({ value: p.key, label: `${p.label} — ${p.hint}` }))} />
                        </Form.Item>
                        <Form.Item name="scope" label="作用域" rules={[{ required: true }]}
                                   extra={isAdmin ? undefined : '普通用户仅可创建项目级配置（需为项目 owner）'}>
                            <Select disabled={!!editing || !isAdmin}
                                    options={isAdmin
                                        ? [{ value: 'global', label: '全局' }, { value: 'project', label: '项目' }]
                                        : [{ value: 'project', label: '项目（我的项目）' }]} />
                        </Form.Item>
                        {scopeWatch === 'project' && !editing && (
                            <Form.Item name="project_id" label="所属项目" rules={[{ required: true, message: '请选择项目' }]}>
                                <Select
                                    placeholder="选择要应用该模型配置的项目"
                                    options={myProjects.map((p) => ({ value: p.id, label: p.name }))}
                                />
                            </Form.Item>
                        )}
                        <Form.Item name="name" label="配置名称" rules={[{ required: true }]}>
                            <Input placeholder="如：GLM-抽取-方舟" />
                        </Form.Item>

                        <SectionHead title="连接参数" hint="provider 决定表单默认值；密钥留空表示保持不变" className="mb-2 mt-3" />
                        <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
                            <Select
                                onChange={(v) => {
                                    setProviderSel(v);
                                    const meta = providers.find((p) => p.id === v);
                                    if (meta?.default_base_url) form.setFieldsValue({ base_url: meta.default_base_url });
                                }}
                                options={providers.map((p) => ({ value: p.id, label: `${p.label}（${p.hint}）` }))}
                            />
                        </Form.Item>
                        <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}>
                            <Input placeholder="https://.../v1" />
                        </Form.Item>
                        <Form.Item name="model_name" label="模型名称" rules={[{ required: true }]}>
                            <Input placeholder="如 glm-5-3-flash / bge-m3:latest" />
                        </Form.Item>
                        <Form.Item
                            name="api_key"
                            label={editing?.api_key_set ? `API Key（当前 ${editing.api_key_masked}，留空保持不变）` : 'API Key'}
                        >
                            <Input.Password placeholder={providerMeta?.needs_key === false ? '该 provider 无需密钥' : '必填项（Ollama 留空）'} />
                        </Form.Item>
                        {isEmbedding && (
                            <Form.Item name="dims" label="向量维度" rules={[{ required: true }]}
                                       extra="必须与 Milvus collection 维度一致（bge-m3=1024）">
                                <InputNumber min={1} max={65536} style={{ width: '100%' }} />
                            </Form.Item>
                        )}

                        {(isExtract || isChat || isVL) && (
                            <>
                                <SectionHead title="运行参数" hint="按用途展开：抽取参数只在抽取模型出现" className="mb-2 mt-3" />
                                {isExtract && (
                                    <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                                        <Form.Item name="chunk_size" label="chunk_size" initialValue={15000}>
                                            <InputNumber min={200} max={50000} style={{ width: '100%' }} />
                                        </Form.Item>
                                        <Form.Item name="chunk_overlap" label="overlap %" initialValue={10}>
                                            <InputNumber min={0} max={50} style={{ width: '100%' }} />
                                        </Form.Item>
                                        <Form.Item name="parallelism" label="并行度" initialValue={16}
                                                   tooltip="同时在途的切片抽取任务数（默认 16）：后端按滑动窗口调度，完成一个立即补一个。远端模型服务限速严格时可调小">
                                            <InputNumber min={1} max={64} style={{ width: '100%' }} />
                                        </Form.Item>
                                        <Form.Item name="request_interval" label="请求间隔(s)" initialValue={2}
                                                   tooltip="每次任务提交之间的间隔（秒），用于给远程模型服务限速；0 表示不限速">
                                            <InputNumber min={0} max={60} style={{ width: '100%' }} />
                                        </Form.Item>
                                    </div>
                                )}
                                <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                                    <Form.Item name="llm_timeout" label="超时(s)" initialValue={300}>
                                        <InputNumber min={30} max={1800} style={{ width: '100%' }} />
                                    </Form.Item>
                                    <Form.Item name="disable_think" label="关闭思考" valuePropName="checked"
                                               tooltip="开启后抽取/QA 调用该模型时禁用思考模式（方舟 GLM 等模型关思考可显著提速、避免空回复）">
                                        <Switch />
                                    </Form.Item>
                                    <Form.Item name="streaming_enabled" label="流式" valuePropName="checked"
                                               tooltip="开启后：抽取输出走流式聚合，对话问答的答案生成走流式（更快出首字，避免长阻塞超时）；关闭则内部调用整段非流式。问答页面的逐字显示由问答页开关控制">
                                        <Switch />
                                    </Form.Item>
                                </div>
                            </>
                        )}

                        {!isAdmin && (
                            <Note tone="quiet" className="mt-1">
                                普通用户可为自己的项目建模型配置；「全局」配置只读，默认项由管理员设置。
                            </Note>
                        )}
                    </Form>
                </Drawer>

                <style>{`
                    /* 页签：antd 默认 tab 上下各 12px 内边距，会把「指标带 → 页签」的空档撑大；
                       这里收到 6px，配合指标带 mb-1.5 让视觉间距更紧凑 */
                    .mc-tabs.ant-tabs > .ant-tabs-nav .ant-tabs-tab { padding: 6px 0; }
                    /* 模型卡片：头部与底栏留白收一档，和站内面板同一件材质 */
                    .mc-card.ant-card { border-radius: 16px; margin-bottom: 0; }
                    .mc-card .ant-card-head { min-height: 46px; padding: 0 14px; border-bottom-color: #F0F4FB; }
                    .mc-card .ant-card-head-title { padding: 10px 0; }
                    .mc-card .ant-card-body { padding: 12px 14px 10px; }
                    /* 新建/编辑抽屉：antd 默认 24px 字段行距在 560px 宽里偏空，收一档 */
                    .mc-form .ant-form-item { margin-bottom: 14px; }
                    .mc-form .ant-form-item-label { padding-bottom: 4px; }
                    .mc-form .ant-form-item-extra { margin-top: 3px; font-size: 11.5px; }
                    .mc-form .ant-form-item-explain { font-size: 11.5px; }
                    /* 最后一项（含底部 Note）不再留底距，底部只保留抽屉 body 的 8px */
                    .mc-form .ant-form-item:last-of-type { margin-bottom: 0; }
                `}</style>
            </div>
        </div>
    );
};

export default ModelConfigsPage;
