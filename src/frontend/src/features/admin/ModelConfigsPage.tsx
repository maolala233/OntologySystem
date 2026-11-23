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
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Card, Drawer, Form, Input, InputNumber, Select, Space, Switch, Tabs, Tooltip } from 'antd';
import {
    ApiOutlined, CloudOutlined, DeploymentUnitOutlined, EyeOutlined, MessageOutlined,
    PlusOutlined, QuestionCircleOutlined, ReloadOutlined, RobotOutlined, StarFilled,
    StarOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { modelConfigsApi, ModelConfigRow, ProviderMeta, TestResult } from '../../api/model-configs';
import { message, confirmModal, notifyError } from '../../shared/feedback';
import { useAuthStore } from '../../shared/auth/authStore';
import { projectsApi } from '../../api/projects';
import { ProjectData } from '../../types/ontology';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, SectionHead, StatBand, TkTag } from '../../shared/consoleUi';
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
    const [rows, setRows] = useState<ModelConfigRow[]>([]);
    const [providers, setProviders] = useState<ProviderMeta[]>([]);
    const [myProjects, setMyProjects] = useState<ProjectData[]>([]);
    const [activePurpose, setActivePurpose] = useState<string>('extract');
    const [loading, setLoading] = useState(true);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [editing, setEditing] = useState<ModelConfigRow | null>(null);
    const [testingId, setTestingId] = useState<number | 'form' | null>(null);
    const [formTest, setFormTest] = useState<TestResult | null>(null);
    const [form] = Form.useForm();
    const [providerSel, setProviderSel] = useState<string>('openai_compatible');

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

    const load = async () => {
        setLoading(true);
        try {
            if (isAdmin) {
                const [rs, ps] = await Promise.all([
                    modelConfigsApi.list({ purpose: activePurpose }),
                    modelConfigsApi.listProviders(),
                ]);
                setRows(rs);
                setProviders(ps);
            } else {
                // 普通用户：全局行（只读展示，默认项可见）+ 自己拥有项目的配置
                const [globalRs, ps] = await Promise.all([
                    modelConfigsApi.list({ purpose: activePurpose, scope: 'global' }),
                    modelConfigsApi.listProviders(),
                ]);
                const projectRs = await Promise.all(
                    myProjects.map((p) =>
                        modelConfigsApi.list({ purpose: activePurpose, scope: 'project', project_id: p.id })
                            .catch(() => [] as ModelConfigRow[])),
                );
                setRows([...globalRs, ...projectRs.flat()]);
                setProviders(ps);
            }
        } catch (err: any) {
            notifyError(err, '加载失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { if (projectsReady) void load(); }, [activePurpose, isAdmin, myProjects, projectsReady]);

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
        try {
            if (editing) {
                await modelConfigsApi.patch(editing.id, payload);
                message.success('配置已更新');
            } else {
                await modelConfigsApi.create(payload);
                message.success('配置已创建');
            }
            setDrawerOpen(false);
            load();
        } catch (err: any) {
            notifyError(err, '保存失败');
        }
    };

    const handleTest = async (row?: ModelConfigRow) => {
        const id = row ? row.id : 'form';
        setTestingId(id);
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
            setTestingId(null);
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
                            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
                            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建配置</Button>
                        </>
                    }
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
                    className="mb-2"
                />

                <StatBand
                    className="mb-4"
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

                {loading && rows.length === 0 ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>
                        {[0, 1, 2, 3].map((i) => (
                            <div key={i} className="surface-card p-4">
                                <div className="flex items-center gap-2">
                                    <div className="tk-skel h-7 w-7 !rounded-lg" />
                                    <div className="tk-skel h-3.5 w-32" />
                                    <div className="tk-skel ml-auto h-4 w-12 !rounded-full" />
                                </div>
                                <div className="mt-4 space-y-2.5">
                                    {[0, 1, 2, 3].map((j) => (
                                        <div key={j} className="flex items-center gap-2">
                                            <div className="tk-skel h-2.5 w-12" />
                                            <div className="tk-skel h-2.5 flex-1" />
                                        </div>
                                    ))}
                                </div>
                                <div className="mt-4 tk-skel h-7 w-full !rounded-lg" />
                            </div>
                        ))}
                    </div>
                ) : rows.length === 0 && !loading ? (
                    <div className="surface-card flex flex-col items-center px-4 py-16 text-center">
                        <span
                            className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] text-[#2F62D8]"
                            style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                        >
                            {purposeMeta?.icon ?? <RobotOutlined />}
                        </span>
                        <div className="mt-4 text-[14px] font-semibold text-[#0F172A]">
                            暂无「{purposeMeta?.label ?? '当前用途'}」配置
                        </div>
                        <div className="mt-1.5 max-w-[360px] text-[12.5px] leading-[1.7] text-[#66738F]">
                            {purposeMeta?.hint}
                        </div>
                        <Button className="tk-soft-btn mt-4" icon={<PlusOutlined />} onClick={openCreate}>新建配置</Button>
                    </div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>
                        {rows.map((row) => {
                            const pMeta = PURPOSE_META[row.purpose];
                            const manageable = canManage(row);
                            return (
                                <Card
                                    key={row.id}
                                    size="small"
                                    className="mc-card"
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
                                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[#F0F4FB] pt-2.5">
                                        <div className="flex items-center gap-0.5">
                                            <Tooltip title="测试连接">
                                                <Button size="small" type="link" icon={<ApiOutlined />}
                                                        loading={testingId === row.id}
                                                        onClick={() => handleTest(row)}>测试</Button>
                                            </Tooltip>
                                            <Tooltip title={manageable ? '设为该用途的默认配置' : '全局默认由管理员设置，项目配置需为项目 owner'}>
                                                <Button size="small" type="link"
                                                        icon={row.is_default ? <StarFilled style={{ color: '#E0A02A' }} /> : <StarOutlined />}
                                                        disabled={row.is_default || !manageable}
                                                        onClick={() => handleSetDefault(row)}>
                                                    {row.is_default ? '默认' : '设默认'}
                                                </Button>
                                            </Tooltip>
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

                <Drawer
                    title={editing ? '编辑模型配置' : '新建模型配置'}
                    width={560}
                    open={drawerOpen}
                    onClose={() => setDrawerOpen(false)}
                    destroyOnHidden
                    footer={
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex min-w-0 items-center gap-2">
                                <Button icon={<ApiOutlined />} loading={testingId === 'form'} onClick={() => handleTest()}>
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
                                <Button onClick={() => setDrawerOpen(false)}>取消</Button>
                                <Button type="primary" onClick={handleSave}>保存</Button>
                            </Space>
                        </div>
                    }
                >
                    <Form form={form} layout="vertical">
                        <SectionHead title="基础信息" hint="用途与作用域决定谁可以管理这份配置" className="mb-3" />
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

                        <SectionHead title="连接参数" hint="provider 决定表单默认值；密钥留空表示保持不变" className="mb-3 mt-1" />
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
                                <SectionHead title="运行参数" hint="按用途展开：抽取参数只在抽取模型出现" className="mb-3 mt-1" />
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
                    /* 模型卡片：头部与底栏留白收一档，和站内面板同一件材质 */
                    .mc-card.ant-card { border-radius: 14px; margin-bottom: 0; }
                    .mc-card .ant-card-head { min-height: 46px; padding: 0 14px; border-bottom-color: #F0F4FB; }
                    .mc-card .ant-card-head-title { padding: 10px 0; }
                    .mc-card .ant-card-body { padding: 12px 14px 10px; }
                `}</style>
            </div>
        </div>
    );
};

export default ModelConfigsPage;
