/**
 * 管理后台 · 环境配置（中间件连接参数）
 * Milvus / Neo4j / Redis / MinIO 地址与凭据可改：DB 覆盖（platform_config）→ .env 默认；
 * 保存后 API 进程热生效（Neo4j/MinIO 重连，Milvus 下次实例化生效），Celery worker 需重启。
 * MySQL 为平台自身元数据库，不开放修改。
 * 视觉（2026-11-23）：TechHero + 指标带；服务卡片统一冷蓝字段材质，
 * 已修改字段用琥珀淡底 + 发光圆点标出。
 * 交互（2026-11-24）：开关类字段即时保存；文本/密码按卡片走草稿，
 * 每张卡片表头有只作用于本卡的「保存」按钮（有改动才可点），不做全局浮层。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { confirmModal, message, notifyError } from '../../shared/feedback';
import { Button, Card, Input, Spin, Switch, Tooltip } from 'antd';
import {
    ApiOutlined,
    ApartmentOutlined,
    CloudServerOutlined,
    DatabaseOutlined,
    ExclamationCircleOutlined,
    ReloadOutlined,
    SaveOutlined,
    SettingOutlined,
    ThunderboltOutlined,
} from '@ant-design/icons';
import { envConfigApi, EnvConfigResponse, EnvFieldRow, EnvTestResult, EnvUpdateResult } from '../../api/envConfig';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, PageLoading, StatBand, StatItem, TkTag } from '../../shared/consoleUi';

const SERVICE_META: Record<string, { title: string; icon: React.ReactNode; ink: string; tint: string }> = {
    milvus: { title: 'Milvus 向量库', icon: <ThunderboltOutlined />, ink: '#6D3FD8', tint: 'rgba(139,92,246,0.12)' },
    neo4j: { title: 'Neo4j 知识图谱', icon: <ApartmentOutlined />, ink: '#2A57C6', tint: 'rgba(91,141,239,0.12)' },
    redis: { title: 'Redis 缓存/队列', icon: <CloudServerOutlined />, ink: '#1B87AD', tint: 'rgba(27,135,173,0.10)' },
    minio: { title: 'MinIO 对象存储', icon: <DatabaseOutlined />, ink: '#B8790F', tint: 'rgba(242,185,80,0.18)' },
};

const BREADCRUMBS = [
    { title: '首页', path: '/' },
    { title: '系统配置' },
    { title: '环境配置' },
];

/** 就地替换某个字段（开关即时保存用，不整体 reload，避免冲掉正在编辑的文本草稿） */
const patchField = (
    prev: EnvConfigResponse | null,
    key: string,
    patch: Partial<EnvFieldRow>,
): EnvConfigResponse | null => {
    if (!prev) return prev;
    const services = Object.fromEntries(
        Object.entries(prev.services).map(([service, fields]) => [
            service,
            fields.map((f) => (f.key === key ? { ...f, ...patch } : f)),
        ]),
    );
    return { ...prev, services };
};

const EnvConfigPage: React.FC = () => {
    const [data, setData] = useState<EnvConfigResponse | null>(null);
    const [loading, setLoading] = useState(true);
    // 刷新按钮只在手动点击时转圈；首屏由骨架屏表达
    const [refreshing, setRefreshing] = useState(false);
    // 正在保存的服务卡片（表头上的保存按钮转圈）
    const [savingService, setSavingService] = useState<string | null>(null);
    const [dirty, setDirty] = useState<Record<string, string | boolean>>({});
    // 开关即时保存的进行中集合（开关按钮上转圈）
    const [savingKeys, setSavingKeys] = useState<Set<string>>(new Set());
    // 测试中的服务集合：并发测试时各自保持 loading，不会互相清掉
    const [testingServices, setTestingServices] = useState<Set<string>>(new Set());
    const [testResults, setTestResults] = useState<Record<string, EnvTestResult>>({});

    const load = async () => {
        setLoading(true);
        try {
            const d = await envConfigApi.get();
            setData(d);
            setDirty({});
            setTestResults({});
        } catch (err: any) {
            notifyError(err, '加载环境配置失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const doRefresh = async () => {
        setRefreshing(true);
        try {
            await load();
        } finally {
            setRefreshing(false);
        }
    };

    const refresh = async () => {
        // 刷新会清空草稿：有未保存改动时先确认，避免静默丢数据
        if (dirtyCount > 0) {
            confirmModal({
                title: '放弃未保存的修改？',
                content: `当前有 ${dirtyCount} 项改动尚未保存，刷新会丢弃这些草稿。`,
                okText: '放弃并刷新',
                okType: 'danger',
                cancelText: '取消',
                onOk: doRefresh,
            });
            return;
        }
        await doRefresh();
    };

    const dirtyCount = useMemo(() => Object.keys(dirty).length, [dirty]);

    // 有未保存草稿时，关闭/刷新浏览器给原生确认（SPA 内部路由跳转不在覆盖范围）
    useEffect(() => {
        if (dirtyCount === 0) return;
        const handler = (e: BeforeUnloadEvent) => {
            e.preventDefault();
            e.returnValue = '';
        };
        window.addEventListener('beforeunload', handler);
        return () => window.removeEventListener('beforeunload', handler);
    }, [dirtyCount]);

    const stats = useMemo(() => {
        const services = data ? Object.keys(data.services).length : 0;
        const fields = data ? Object.values(data.services).flat() : [];
        const customized = fields.filter((f) => f.source === 'db').length;
        const tested = Object.values(testResults);
        const ok = tested.filter((r) => r.status === 'ok').length;
        return { services, customized, ok, tested: tested.length };
    }, [data, testResults]);

    // 指标带属于静态结构：加载时就先展示标签，数值等数据齐了再填
    const statItems: StatItem[] = [
        { label: '中间件服务', value: stats.services, tone: 'brand', icon: <CloudServerOutlined /> },
        { label: '已自定义字段', value: stats.customized, tone: 'violet', icon: <SettingOutlined />, hint: '覆盖 .env 默认值' },
        { label: '待保存修改', value: dirtyCount, tone: dirtyCount > 0 ? 'amber' : 'quiet', icon: <SaveOutlined /> },
        { label: '连通通过', value: `${stats.ok}/${stats.tested}`, tone: 'cyan', icon: <ApiOutlined />, hint: '本次已测试的服务' },
    ];

    const setValue = (key: string, value: string | boolean) => {
        setDirty((prev) => ({ ...prev, [key]: value }));
    };

    /** 后端 applied 表示 API 进程内热重连是否成功：失败必须说出来，否则“已保存”会误导。
     *  onlyService 用来过滤无关服务——保存 Redis 时 Neo4j 重连失败不该打扰用户。 */
    const appliedFailures = (applied: EnvUpdateResult['applied'] | undefined, onlyService?: string): string[] => {
        const failed: string[] = [];
        if (applied && applied.neo4j === false && (!onlyService || onlyService === 'neo4j')) {
            failed.push('Neo4j');
        }
        if (applied && applied.minio === false && (!onlyService || onlyService === 'minio')) {
            failed.push('MinIO');
        }
        return failed;
    };

    /** 字段 key 的所属服务（milvus_host → milvus），用于过滤 applied 提示 */
    const serviceOfKey = (key: string): string =>
        ['milvus', 'neo4j', 'redis', 'minio'].find((s) => key.startsWith(s)) ?? '';

    /** 开关类字段即时保存：不进草稿、不弹吸底条；失败回滚到拨动前的值。
     *  不走 load() 刷新——那会把正在编辑的文本草稿一起清掉。 */
    const toggleSwitch = async (key: string, next: boolean) => {
        if (!data || savingKeys.has(key)) return;
        const before = Object.values(data.services).flat().find((f) => f.key === key);
        setSavingKeys((prev) => new Set(prev).add(key));
        setData((prev) => patchField(prev, key, { value: next, source: 'db' }));
        try {
            const result = await envConfigApi.update({ [key]: next });
            const failed = appliedFailures(result.applied, serviceOfKey(key));
            if (failed.length > 0) {
                message.warning(`${failed.join(' / ')} 热重连失败：开关已保存，但可能需要重启后端或检查连接参数`);
            }
        } catch (err: any) {
            setData((prev) => patchField(prev, key, {
                value: before?.value ?? !next,
                source: before?.source ?? 'env',
            }));
            notifyError(err, '保存开关失败');
        } finally {
            setSavingKeys((prev) => {
                const nextSet = new Set(prev);
                nextSet.delete(key);
                return nextSet;
            });
        }
    };

    /** 保存单张卡片的文本/密码改动：只提交这张卡的 dirty keys；
     *  成功后只回填这张卡（用回读结果，掩码/空值以服务端为准），别的卡片的草稿不受影响 */
    const saveService = async (service: string) => {
        if (!data || savingService) return;
        const patch: Record<string, string | boolean> = {};
        for (const f of data.services[service] ?? []) {
            if (f.key in dirty) patch[f.key] = dirty[f.key];
        }
        if (Object.keys(patch).length === 0) return;
        setSavingService(service);
        try {
            const result = await envConfigApi.update(patch);
            const fresh = await envConfigApi.get();
            setData((prev) => prev ? {
                ...prev,
                services: { ...prev.services, [service]: fresh.services[service] ?? prev.services[service] },
            } : fresh);
            setDirty((prev) => {
                const next = { ...prev };
                Object.keys(patch).forEach((k) => delete next[k]);
                return next;
            });
            // 配置值已变：清掉该服务的旧测试结果，避免继续显示保存前的“连通 Xms”
            setTestResults((prev) => {
                const next = { ...prev };
                delete next[service];
                return next;
            });
            const base = `已保存 ${result.changed} 项` +
                (result.skipped ? `，${result.skipped} 项（掩码/空值）未变更` : '');
            const failed = appliedFailures(result.applied, service);
            if (failed.length > 0) {
                message.warning(`${base}；但 ${failed.join(' / ')} 热重连失败，可能需要重启后端或检查连接参数`);
            } else {
                message.success(base);
            }
        } catch (err: any) {
            notifyError(err, '保存失败');
        } finally {
            setSavingService(null);
        }
    };

    const handleTest = async (service: string) => {
        if (testingServices.has(service)) return;
        setTestingServices((prev) => new Set(prev).add(service));
        try {
            const result = await envConfigApi.test(service);
            setTestResults((prev) => ({ ...prev, [service]: result }));
        } catch (err: any) {
            setTestResults((prev) => ({
                ...prev,
                [service]: { service, status: 'down', error: err.response?.data?.error?.message || err.message },
            }));
        } finally {
            setTestingServices((prev) => {
                const next = new Set(prev);
                next.delete(service);
                return next;
            });
        }
    };

    // 页面外壳：加载/失败态也保留顶栏，进入页面不会先闪一屏无顶栏的布局再跳变
    const shell = (body: React.ReactNode) => (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={BREADCRUMBS} />
            {body}
        </div>
    );

    const hero = (
        <TechHero
            compact
            tone="light"
            className="!mb-4"
            title="环境配置"
            titleSuffix="RUNTIME ENV"
            subtitle="中间件连接参数 · 修改保存后 API 进程即时生效（无需重启后端）"
            showConstellation={!!data}
            actions={
                <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>刷新</Button>
            }
        />
    );

    if (loading && !data) {
        return shell(
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {hero}
                {/* 首屏不做骨架屏：页头与指标带先出（数值显示 —），说明条/服务卡片等数据齐了一次性呈现 */}
                <StatBand className="mb-3" items={statItems} />
                <PageLoading hint="正在加载中间件连接参数…" />
            </div>,
        );
    }
    if (!data) {
        return shell(
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {hero}
                <div className="surface-card flex flex-col items-center px-4 py-16 text-center">
                    <span
                        className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] text-[#B8790F]"
                        style={{ background: 'linear-gradient(135deg, rgba(242,185,80,0.20), rgba(91,141,239,0.08))' }}
                    >
                        <ExclamationCircleOutlined />
                    </span>
                    <div className="mt-4 text-[14px] font-semibold text-[#0F172A]">环境配置加载失败</div>
                    <div className="mt-1.5 text-[12.5px] text-[#66738F]">请检查后端服务是否可用，然后重试</div>
                    <Button className="tk-soft-btn mt-4" icon={<ReloadOutlined />} onClick={load}>重试</Button>
                </div>
            </div>,
        );
    }

    return shell(
        <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
            {hero}

            <StatBand className="mb-3" items={statItems} />

            <Note className="mb-4">
                <span className="tk-note-title">存储机制：</span>
                修改保存在数据库 <span className="tk-mono text-[12px]">platform_config</span> 中（覆盖 .env 默认值）；密码类字段 AES 加密存储。
                <ul className="m-0 mt-1 list-disc pl-4 text-[11.5px] leading-[1.7] text-[#66738F]">
                    {data.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
            </Note>

            {/* 刷新时旧卡片保留在 Spin 遮罩下（与用户管理的 Table loading 同一观感） */}
            <Spin spinning={loading}>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {Object.entries(data.services).map(([service, fields]) => {
                    const meta = SERVICE_META[service] || {
                        title: service, icon: <ApiOutlined />, ink: '#2A57C6', tint: 'rgba(91,141,239,0.12)',
                    };
                    const tr = testResults[service];
                    const serviceDirty = fields.filter((f) => f.key in dirty).length;
                    return (
                        // 与「模块授权 · 角色卡」完全同一支背景：
                        // 内联写死，避免被 antd 运行时注入的 .ant-card 背景覆盖
                        <Card
                            key={service}
                            className="env-card tk-controls tk-cold-card"
                            style={{
                                backgroundColor: 'transparent',
                                backgroundImage: 'linear-gradient(160deg, #FFFFFF 0%, rgba(91,141,239,0.07) 100%)',
                            }}
                            title={
                                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                                    <span
                                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[15px]"
                                        style={{ background: meta.tint, color: meta.ink }}
                                    >
                                        {meta.icon}
                                    </span>
                                    <span className="truncate text-[13.5px] font-semibold text-[#1F2A44]">{meta.title}</span>
                                    <span className="tk-mono shrink-0 text-[10px] uppercase tracking-[0.14em] text-[#A3ADC2]">
                                        {service}
                                    </span>
                                    {serviceDirty > 0 && (
                                        <TkTag tone="amber" mono>
                                            <Dot tone="amber" className="!h-[5px] !w-[5px]" />
                                            {serviceDirty} 待保存
                                        </TkTag>
                                    )}
                                </span>
                            }
                            extra={
                                <div className="flex items-center gap-2">
                                    {tr && (
                                        <Tooltip title={tr.status === 'ok' ? `连通正常，延迟 ${tr.latency_ms}ms` : (tr.error || '连接失败')}>
                                            <span className="inline-flex items-center gap-1.5 text-[11.5px]">
                                                <Dot tone={tr.status === 'ok' ? 'cyan' : 'danger'} />
                                                <span className={tr.status === 'ok' ? 'text-[#1B87AD]' : 'text-[#B42318]'}>
                                                    {tr.status === 'ok' ? `连通 ${tr.latency_ms}ms` : '失败'}
                                                </span>
                                            </span>
                                        </Tooltip>
                                    )}
                                    <Tooltip title={serviceDirty > 0 ? '有未保存的修改：请先保存再测试' : ''}>
                                        <Button
                                            size="small"
                                            className="tk-soft-btn"
                                            icon={<ApiOutlined />}
                                            loading={testingServices.has(service)}
                                            disabled={serviceDirty > 0}
                                            onClick={() => handleTest(service)}
                                        >
                                            测试连接
                                        </Button>
                                    </Tooltip>
                                    <Button
                                        size="small"
                                        type="primary"
                                        className="tk-primary-btn"
                                        icon={<SaveOutlined />}
                                        disabled={serviceDirty === 0 || (savingService !== null && savingService !== service)}
                                        loading={savingService === service}
                                        onClick={() => void saveService(service)}
                                    >
                                        保存
                                    </Button>
                                </div>
                            }
                        >
                            <div className="space-y-3">
                                {fields.map((f) => {
                                    const changed = f.key in dirty;
                                    return (
                                        <div
                                            key={f.key}
                                            className={`rounded-lg px-1.5 py-1 transition-colors ${
                                                changed
                                                    ? 'bg-[rgba(242,185,80,0.10)]'
                                                    : 'hover:bg-[rgba(91,141,239,0.045)]'
                                            }`}
                                        >
                                            <div className="mb-1 flex items-center justify-between gap-2">
                                                <span className="flex min-w-0 items-center gap-1.5 text-[12.5px] font-medium text-[#3D4B66]">
                                                    <span className="truncate">{f.label}</span>
                                                    {f.source === 'db' && <TkTag tone="violet">已自定义</TkTag>}
                                                    {changed && (
                                                        <TkTag tone="amber">
                                                            <Dot tone="amber" className="!h-[5px] !w-[5px]" />
                                                            待保存
                                                        </TkTag>
                                                    )}
                                                </span>
                                                <span className="tk-mono shrink-0 text-[10px] text-[#A3ADC2]">{f.key}</span>
                                            </div>

                                            {f.kind === 'switch' ? (
                                                <Switch
                                                    checked={Boolean(f.value)}
                                                    loading={savingKeys.has(f.key)}
                                                    onChange={(v) => void toggleSwitch(f.key, v)}
                                                />
                                            ) : f.kind === 'password' ? (
                                                <Input.Password
                                                    value={changed ? String(dirty[f.key]) : ''}
                                                    placeholder={f.has_value ? '已设置（输入新值覆盖，留空不变更）' : '未设置'}
                                                    onChange={(e) => setValue(f.key, e.target.value)}
                                                />
                                            ) : (
                                                <Input
                                                    value={changed ? String(dirty[f.key]) : String(f.value ?? '')}
                                                    onChange={(e) => setValue(f.key, e.target.value)}
                                                />
                                            )}
                                            {f.help && <div className="mt-1 text-[11px] leading-[1.6] text-[#8A94AB]">{f.help}</div>}
                                        </div>
                                    );
                                })}
                            </div>
                        </Card>
                    );
                })}
            </div>
            </Spin>

            <style>{`
                /* 环境卡片：头部留白与站内面板对齐；状态区（圆点 + 测试按钮）右侧成组 */
                .env-card.ant-card { border-radius: 16px; margin-bottom: 0; }
                .env-card .ant-card-head { min-height: 50px; padding: 0 14px; border-bottom-color: #F0F4FB; }
                .env-card .ant-card-head-title { padding: 12px 0; }
                .env-card .ant-card-extra { padding: 12px 0; }
                .env-card .ant-card-body { padding: 12px 14px 14px; }
            `}</style>
        </div>,
    );
};

export default EnvConfigPage;
