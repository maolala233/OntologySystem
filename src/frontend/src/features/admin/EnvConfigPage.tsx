/**
 * 管理后台 · 环境配置（中间件连接参数）
 * Milvus / Neo4j / Redis / MinIO 地址与凭据可改：DB 覆盖（platform_config）→ .env 默认；
 * 保存后 API 进程热生效（Neo4j/MinIO 重连，Milvus 下次实例化生效），Celery worker 需重启。
 * MySQL 为平台自身元数据库，不开放修改。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Input, message, Spin, Switch, Tag, Typography } from 'antd';
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

const { Text } = Typography;

const SERVICE_META: Record<string, { title: string; icon: React.ReactNode; color: string }> = {
    milvus: { title: 'Milvus 向量库', icon: <ThunderboltOutlined />, color: '#B585F2' },
    neo4j: { title: 'Neo4j 知识图谱', icon: <ApartmentOutlined />, color: '#5B8DEF' },
    redis: { title: 'Redis 缓存/队列', icon: <CloudServerOutlined />, color: '#5AC8FA' },
    minio: { title: 'MinIO 对象存储', icon: <DatabaseOutlined />, color: '#F2B950' },
};

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

    if (loading) {
        return <div className="flex justify-center items-center h-96"><Spin size="large" /></div>;
    }
    if (!data) return null;

    return (
        <div className="p-4 sm:p-6" style={{ maxWidth: 1100, margin: '0 auto' }}>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div>
                    <h1 className="text-xl font-bold text-gray-800 m-0">环境配置</h1>
                    <Text type="secondary" className="text-sm">
                        中间件连接参数 · 修改保存后 API 进程即时生效（无需重启后端）
                    </Text>
                </div>
                <div className="flex items-center gap-2">
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
                </div>
            </div>

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
                            className="rounded-xl shadow-sm"
                            title={
                                <span className="flex items-center gap-2">
                                    <span style={{ color: meta.color }}>{meta.icon}</span>
                                    <span className="font-semibold">{meta.title}</span>
                                    <span className="tk-mono text-[10px] tracking-widest text-gray-400 uppercase">
                                        {service}
                                    </span>
                                </span>
                            }
                            extra={
                                <div className="flex items-center gap-2">
                                    {tr && (
                                        <Tag color={tr.status === 'ok' ? 'green' : 'red'}>
                                            {tr.status === 'ok' ? `连通 ${tr.latency_ms}ms` : `失败：${(tr.error || '').slice(0, 40)}`}
                                        </Tag>
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
                            <div className="space-y-4">
                                {fields.map((f) => {
                                    const changed = f.key in dirty;
                                    return (
                                        <div key={f.key}>
                                            <div className="flex items-center justify-between mb-1">
                                                <span className="text-sm text-gray-700 font-medium">
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
                                                <span className="tk-mono text-[10px] text-gray-400">{f.key}</span>
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
                                            {f.help && <div className="text-xs text-gray-400 mt-1">{f.help}</div>}
                                        </div>
                                    );
                                })}
                            </div>
                        </Card>
                    );
                })}
            </div>
        </div>
    );
};

export default EnvConfigPage;
