/**
 * 管理后台 · 环境配置（中间件连接参数）
 * Milvus / Neo4j / Redis / MinIO 地址与凭据可改：DB 覆盖（platform_config）→ .env 默认；
 * 保存后 API 进程热生效（Neo4j/MinIO 重连，Milvus 下次实例化生效），Celery worker 需重启。
 * MySQL 为平台自身元数据库，不开放修改。
 * 视觉（2026-11-23）：TechHero + 指标带；服务卡片统一冷蓝字段材质，
 * 已修改字段用琥珀淡底 + 发光圆点标出；改动后吸底保存条常驻。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { message, notifyError } from '../../shared/feedback';
import { Button, Card, Input, Switch, Tooltip } from 'antd';
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
import { envConfigApi, EnvConfigResponse, EnvTestResult } from '../../api/envConfig';
import Navbar from '../../components/Layout/Navbar';
import TechHero from '../../components/TechHero';
import { Dot, Note, StatBand, TkTag } from '../../shared/consoleUi';

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

const EnvConfigPage: React.FC = () => {
    const [data, setData] = useState<EnvConfigResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState<Record<string, string | boolean>>({});
    const [testing, setTesting] = useState<string | null>(null);
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

    const dirtyCount = useMemo(() => Object.keys(dirty).length, [dirty]);

    const stats = useMemo(() => {
        const services = data ? Object.keys(data.services).length : 0;
        const fields = data ? Object.values(data.services).flat() : [];
        const customized = fields.filter((f) => f.source === 'db').length;
        const tested = Object.values(testResults);
        const ok = tested.filter((r) => r.status === 'ok').length;
        return { services, customized, ok, tested: tested.length };
    }, [data, testResults]);

    const setValue = (key: string, value: string | boolean) => {
        setDirty((prev) => ({ ...prev, [key]: value }));
    };

    const handleSave = async () => {
        if (dirtyCount === 0) { message.info('没有修改需要保存'); return; }
        setSaving(true);
        try {
            const result = await envConfigApi.update(dirty);
            message.success(`已保存 ${result.changed} 项修改` +
                (result.skipped ? `，${result.skipped} 项（掩码/空值）未变更` : ''));
            await load();
        } catch (err: any) {
            notifyError(err, '保存失败');
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async (service: string) => {
        setTesting(service);
        try {
            const result = await envConfigApi.test(service);
            setTestResults((prev) => ({ ...prev, [service]: result }));
        } catch (err: any) {
            setTestResults((prev) => ({
                ...prev,
                [service]: { service, status: 'down', error: err.response?.data?.error?.message || err.message },
            }));
        } finally {
            setTesting(null);
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
                <>
                    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
                    <Button
                        type="primary"
                        icon={<SaveOutlined />}
                        onClick={handleSave}
                        loading={saving}
                        disabled={dirtyCount === 0}
                    >
                        保存全部{dirtyCount > 0 ? `（${dirtyCount} 项修改）` : ''}
                    </Button>
                </>
            }
        />
    );

    if (loading && !data) {
        return shell(
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {hero}
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {[0, 1, 2, 3].map((i) => (
                        <div key={i} className="surface-card p-4">
                            <div className="flex items-center gap-2.5">
                                <span className="tk-skel h-8 w-8 !rounded-lg" />
                                <div className="flex-1">
                                    <div className="tk-skel h-3.5 w-28" />
                                    <div className="tk-skel mt-1.5 h-2.5 w-16" />
                                </div>
                            </div>
                            <div className="mt-4 space-y-3">
                                {[0, 1, 2].map((j) => (
                                    <div key={j}>
                                        <div className="flex justify-between">
                                            <div className="tk-skel h-2.5 w-20" />
                                            <div className="tk-skel h-2.5 w-24" />
                                        </div>
                                        <div className="tk-skel mt-1.5 h-8 w-full !rounded-lg" />
                                    </div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
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

            <StatBand
                className="mb-4"
                items={[
                    { label: '中间件服务', value: stats.services, tone: 'brand', icon: <CloudServerOutlined /> },
                    { label: '已自定义字段', value: stats.customized, tone: 'violet', icon: <SettingOutlined />, hint: '覆盖 .env 默认值' },
                    { label: '待保存修改', value: dirtyCount, tone: dirtyCount > 0 ? 'amber' : 'quiet', icon: <SaveOutlined /> },
                    { label: '连通通过', value: `${stats.ok}/${stats.tested}`, tone: 'cyan', icon: <ApiOutlined />, hint: '本次已测试的服务' },
                ]}
            />

            <Note className="mb-4">
                <span className="tk-note-title">存储机制：</span>
                修改保存在数据库 <span className="tk-mono text-[12px]">platform_config</span> 中（覆盖 .env 默认值）；密码类字段 AES 加密存储。
                <ul className="m-0 mt-1 list-disc pl-4 text-[11.5px] leading-[1.7] text-[#66738F]">
                    {data.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
            </Note>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {Object.entries(data.services).map(([service, fields]) => {
                    const meta = SERVICE_META[service] || {
                        title: service, icon: <ApiOutlined />, ink: '#2A57C6', tint: 'rgba(91,141,239,0.12)',
                    };
                    const tr = testResults[service];
                    const serviceDirty = fields.filter((f) => f.key in dirty).length;
                    return (
                        <Card
                            key={service}
                            className="env-card tk-controls"
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
                                    <Button
                                        size="small"
                                        className="tk-soft-btn"
                                        icon={<ApiOutlined />}
                                        loading={testing === service}
                                        onClick={() => handleTest(service)}
                                    >
                                        测试连接
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
                                                changed ? 'bg-[rgba(242,185,80,0.08)]' : ''
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
                                                    checked={changed ? Boolean(dirty[f.key]) : Boolean(f.value)}
                                                    onChange={(v) => setValue(f.key, v)}
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

            {/* 吸底保存条：页面较长时，改动后的保存入口一直可见 */}
            {dirtyCount > 0 && (
                <div className="tk-save-bar mt-4">
                    <Dot tone="amber" />
                    <span className="text-[12.5px] text-[#3D4B66]">
                        <span className="tk-mono text-[14px] font-semibold text-[#B8790F]">{dirtyCount}</span> 项修改待保存
                    </span>
                    <Button size="small" icon={<ReloadOutlined />} onClick={load} disabled={saving}>放弃修改</Button>
                    <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
                        保存全部
                    </Button>
                </div>
            )}

            <style>{`
                /* 环境卡片：头部留白与站内面板对齐；状态区（圆点 + 测试按钮）右侧成组 */
                .env-card.ant-card { border-radius: 14px; margin-bottom: 0; }
                .env-card .ant-card-head { min-height: 50px; padding: 0 14px; border-bottom-color: #F0F4FB; }
                .env-card .ant-card-head-title { padding: 12px 0; }
                .env-card .ant-card-extra { padding: 12px 0; }
                .env-card .ant-card-body { padding: 12px 14px 14px; }
            `}</style>
        </div>,
    );
};

export default EnvConfigPage;
