/**
 * 管理后台 · 环境配置（中间件连接参数）
 * Milvus / Neo4j / Redis / MinIO 地址与凭据可改：DB 覆盖（platform_config）→ .env 默认；
 * 保存后 API 进程热生效（Neo4j/MinIO 重连，Milvus 下次实例化生效），Celery worker 需重启。
 * MySQL 为平台自身元数据库，不开放修改。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { message } from '../../shared/feedback';
import { Alert, Button, Card, Empty, Input, Spin, Switch, Tag, Tooltip } from 'antd';
import {
    ApiOutlined,
    CloudServerOutlined,
    DatabaseOutlined,
    ReloadOutlined,
    SaveOutlined,
    ApartmentOutlined,
    ThunderboltOutlined,
} from '@ant-design/icons';
import { envConfigApi, EnvConfigResponse, EnvTestResult } from '../../api/envConfig';
import Navbar from '../../components/Layout/Navbar';
import PageHeader from '../../components/Layout/PageHeader';

const SERVICE_META: Record<string, { title: string; icon: React.ReactNode; color: string }> = {
    milvus: { title: 'Milvus 向量库', icon: <ThunderboltOutlined />, color: '#B585F2' },
    neo4j: { title: 'Neo4j 知识图谱', icon: <ApartmentOutlined />, color: '#5B8DEF' },
    redis: { title: 'Redis 缓存/队列', icon: <CloudServerOutlined />, color: '#5AC8FA' },
    minio: { title: 'MinIO 对象存储', icon: <DatabaseOutlined />, color: '#F2B950' },
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
            message.error(err.response?.data?.error?.message || '加载环境配置失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const dirtyCount = useMemo(() => Object.keys(dirty).length, [dirty]);

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
            message.error(err.response?.data?.error?.message || '保存失败');
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

    if (loading) {
        return shell(
            <div className="flex-1 flex items-center justify-center">
                <Spin size="large" />
            </div>,
        );
    }
    if (!data) {
        return shell(
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
                <Empty description="环境配置加载失败，请稍后重试" />
                <Button icon={<ReloadOutlined />} onClick={load}>重试</Button>
            </div>,
        );
    }

    return shell(
        <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
            <PageHeader
                icon={<CloudServerOutlined />}
                title="环境配置"
                subtitle="中间件连接参数 · 修改保存后 API 进程即时生效（无需重启后端）"
                extra={
                    <>
                        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
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

            <Alert
                type="info"
                showIcon
                className="mb-4"
                message="存储机制：修改保存在数据库 platform_config 中（覆盖 .env 默认值）；密码类字段 AES 加密存储"
                description={
                    <ul className="list-disc pl-5 m-0 text-xs">
                        {data.notes.map((n, i) => <li key={i}>{n}</li>)}
                    </ul>
                }
            />

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {Object.entries(data.services).map(([service, fields]) => {
                    const meta = SERVICE_META[service] || { title: service, icon: <ApiOutlined />, color: '#5B8DEF' };
                    const tr = testResults[service];
                    return (
                        <Card
                            key={service}
                            title={
                                <span className="flex items-center gap-2">
                                    <span style={{ color: meta.color }}>{meta.icon}</span>
                                    <span className="font-semibold">{meta.title}</span>
                                    <span className="tk-mono text-[10px] tracking-widest text-[#66738F] uppercase">
                                        {service}
                                    </span>
                                </span>
                            }
                            extra={
                                <div className="flex items-center gap-2">
                                    {tr && (
                                        <Tooltip title={tr.status === 'ok' ? `连通正常，延迟 ${tr.latency_ms}ms` : (tr.error || '连接失败')}>
                                            <Tag className="!m-0 max-w-[180px] truncate" style={tr.status === 'ok' ? { background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' } : { background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' }}>
                                                {tr.status === 'ok' ? `连通 ${tr.latency_ms}ms` : `失败：${(tr.error || '').slice(0, 24)}`}
                                            </Tag>
                                        </Tooltip>
                                    )}
                                    <Button
                                        size="small"
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
                                        <div key={f.key}>
                                            <div className="flex items-center justify-between mb-1">
                                                <span className="text-sm text-[#3D4B66] font-medium">
                                                    {f.label}
                                                    {f.source === 'db' && (
                                                        <Tag className="ml-2" style={{ fontSize: 10, lineHeight: '16px' }} color="blue">
                                                            已自定义
                                                        </Tag>
                                                    )}
                                                    {changed && (
                                                        <Tag className="ml-1" style={{ fontSize: 10, lineHeight: '16px' }} color="orange">
                                                            待保存
                                                        </Tag>
                                                    )}
                                                </span>
                                                <span className="tk-mono text-[10px] text-[#66738F]">{f.key}</span>
                                            </div>

                                            {f.kind === 'switch' ? (
                                                <Switch
                                                    checked={
                                                        changed
                                                            ? Boolean(dirty[f.key])
                                                            : Boolean(f.value)
                                                    }
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
                                            {f.help && <div className="text-xs text-[#66738F] mt-1">{f.help}</div>}
                                        </div>
                                    );
                                })}
                            </div>
                        </Card>
                    );
                })}
            </div>
        </div>,
    );
};

export default EnvConfigPage;
