/**
 * 管理后台 · 模型配置（M2，docs/design/05 §6.10 / 03 §5）
 * purpose 四 Tab + 卡片列表 + provider 动态表单 + 连通性测试（密钥掩码回显）。
 * 权限模型（与后端 _scope_guard 对齐）：global 配置仅 admin 可建/改；
 * project 配置项目 owner 可建/改——普通用户（项目 owner）进入本页时
 * 默认项目作用域、仅展示自己拥有的项目，全局行只读展示。
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
    Button, Card, Drawer, Form, Input, InputNumber, message, Modal,
    Select, Space, Switch, Tag, Typography, Empty, Tooltip,
} from 'antd';
import { ApiOutlined, PlusOutlined, ReloadOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import { modelConfigsApi, ModelConfigRow, ProviderMeta } from '../../api/model-configs';
import { useAuthStore } from '../../shared/auth/authStore';
import { projectsApi } from '../../api/projects';
import { ProjectData } from '../../types/ontology';

const { Text } = Typography;

const PURPOSES = [
    { key: 'extract', label: '抽取模型', hint: '文档解析后的本体抽取（消耗最大）' },
    { key: 'chat', label: '对话模型', hint: '问答与对话场景' },
    { key: 'embedding', label: '嵌入模型', hint: '向量化（bge-m3=1024 维，须与 Milvus 集合一致）' },
    { key: 'vl', label: '视觉模型', hint: '扫描件兜底解析' },
] as const;

const ModelConfigsPage: React.FC = () => {
    const { user } = useAuthStore();
    const isAdmin = user?.role === 'admin';
    const [rows, setRows] = useState<ModelConfigRow[]>([]);
    const [providers, setProviders] = useState<ProviderMeta[]>([]);
    const [myProjects, setMyProjects] = useState<ProjectData[]>([]);
    const [activePurpose, setActivePurpose] = useState<string>('extract');
    const [loading, setLoading] = useState(false);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [editing, setEditing] = useState<ModelConfigRow | null>(null);
    const [testingId, setTestingId] = useState<number | 'form' | null>(null);
    const [form] = Form.useForm();
    const [providerSel, setProviderSel] = useState<string>('openai_compatible');

    // 普通用户可管理的项目（owner）；admin 不需要
    useEffect(() => {
        if (isAdmin) return;
        projectsApi.getMyProjects()
            .then((ps) => setMyProjects(ps))
            .catch(() => setMyProjects([]));
    }, [isAdmin]);

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
            message.error(err.response?.data?.error?.message || '加载失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [activePurpose, isAdmin, myProjects.length]);

    // 权限判定：该行当前用户是否可管理（编辑/设默认/删除）
    const canManage = (row: ModelConfigRow) =>
        isAdmin || (row.scope === 'project' && !!row.project_id && myProjects.some((p) => p.id === row.project_id));

    const providerMeta = useMemo(
        () => providers.find((p) => p.id === providerSel),
        [providers, providerSel],
    );

    const openCreate = () => {
        setEditing(null);
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
        form.resetFields();
        form.setFieldsValue({
            ...row, api_key: '', scope: row.scope,
        });
        setProviderSel(row.provider);
        setDrawerOpen(true);
    };

    const handleSave = async () => {
        const values = await form.validateFields();
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
            message.error(err.response?.data?.error?.message || '保存失败');
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
                });
                if (result.ok) {
                    message.success(`连通成功（${result.latency_ms}ms）：${result.message}`);
                } else {
                    message.error(`测试失败：${result.message}`);
                }
            }
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '测试请求失败');
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
            message.error(err.response?.data?.error?.message || '操作失败');
        }
    };

    const handleDelete = (row: ModelConfigRow) => {
        Modal.confirm({
            title: `删除配置「${row.name}」？`,
            content: '默认配置不可删除；删除后使用该配置的流程将回落到环境变量。',
            okText: '删除', okType: 'danger',
            onOk: async () => {
                try {
                    await modelConfigsApi.remove(row.id);
                    message.success('已删除');
                    load();
                } catch (err: any) {
                    message.error(err.response?.data?.error?.message || '删除失败');
                }
            },
        });
    };

    const purposeWatch = Form.useWatch('purpose', form);
    const scopeWatch = Form.useWatch('scope', form);
    const isEmbedding = purposeWatch === 'embedding';
    const isExtract = purposeWatch === 'extract';
    const projectName = (pid?: number | null) =>
        myProjects.find((p) => p.id === pid)?.name || (pid ? `项目#${pid}` : '');

    return (
        <div style={{ padding: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <Typography.Title level={4} style={{ margin: 0 }}>模型配置</Typography.Title>
                <Space>
                    <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
                    <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建配置</Button>
                </Space>
            </div>

            <Select
                style={{ width: 200, marginBottom: 16 }}
                value={activePurpose}
                onChange={setActivePurpose}
                options={PURPOSES.map((p) => ({ value: p.key, label: p.label }))}
            />

            {rows.length === 0 && !loading ? (
                <Empty description={`暂无 ${activePurpose} 配置，点击「新建配置」添加`} />
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
                    {rows.map((row) => (
                        <Card
                            key={row.id}
                            size="small"
                            title={
                                <Space>
                                    <span>{row.name}</span>
                                    {row.is_default && <Tag color="green">默认</Tag>}
                                    {!row.enabled && <Tag color="red">停用</Tag>}
                                    <Tag>{row.scope === 'global' ? '全局' : projectName(row.project_id)}</Tag>
                                    {!canManage(row) && <Tag>只读</Tag>}
                                </Space>
                            }
                            actions={[
                                <Button key="test" size="small" type="link" icon={<ApiOutlined />}
                                        loading={testingId === row.id}
                                        onClick={() => handleTest(row)}>测试连接</Button>,
                                <Tooltip key="default" title={canManage(row) ? '' : '全局默认由管理员设置，项目配置需为项目 owner'}>
                                    <Button key="default" size="small" type="link"
                                            icon={row.is_default ? <StarFilled style={{ color: '#F59E0B' }} /> : <StarOutlined />}
                                            disabled={row.is_default || !canManage(row)}
                                            onClick={() => handleSetDefault(row)}>设为默认</Button>
                                </Tooltip>,
                                canManage(row) ? (
                                    <Button key="edit" size="small" type="link" onClick={() => openEdit(row)}>编辑</Button>
                                ) : (
                                    <span key="ro" style={{ fontSize: 12, color: '#999' }}>
                                        {row.scope === 'global' ? '管理员管理' : '项目 owner 可管理'}
                                    </span>
                                ),
                                canManage(row) ? (
                                    <Button key="del" size="small" type="link" danger
                                            disabled={row.is_default} onClick={() => handleDelete(row)}>删除</Button>
                                ) : (
                                    <span key="ro2" />
                                ),
                            ]}
                        >
                            <p style={{ margin: '4px 0' }}>
                                <Text type="secondary">模型：</Text><Text code>{row.model_name}</Text>
                            </p>
                            <p style={{ margin: '4px 0', wordBreak: 'break-all' }}>
                                <Text type="secondary">地址：</Text><Text code style={{ fontSize: 12 }}>{row.base_url}</Text>
                            </p>
                            <p style={{ margin: '4px 0' }}>
                                <Text type="secondary">密钥：</Text>
                                <Text code>{row.api_key_set ? row.api_key_masked : '（无需密钥）'}</Text>
                                {row.dims ? <Tag style={{ marginLeft: 8 }}>{row.dims} 维</Tag> : null}
                            </p>
                            <p style={{ margin: '4px 0' }}>
                                <Text type="secondary">最近测试：</Text>
                                {row.last_test_ok === true && <Tag color="green">通过 {row.last_test_at?.slice(0, 19)}</Tag>}
                                {row.last_test_ok === false && <Tag color="red">失败 {row.last_test_at?.slice(0, 19)}</Tag>}
                                {row.last_test_ok == null && <Tag>未测试</Tag>}
                            </p>
                        </Card>
                    ))}
                </div>
            )}

            <Drawer
                title={editing ? '编辑模型配置' : '新建模型配置'}
                width={520}
                open={drawerOpen}
                onClose={() => setDrawerOpen(false)}
                destroyOnClose
                footer={
                    <Space style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <Button icon={<ApiOutlined />} loading={testingId === 'form'} onClick={() => handleTest()}>
                            测试连接
                        </Button>
                        <Space>
                            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
                            <Button type="primary" onClick={handleSave}>保存</Button>
                        </Space>
                    </Space>
                }
            >
                <Form form={form} layout="vertical">
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
                    {isExtract && (
                        <>
                            <Typography.Title level={5} style={{ marginTop: 8 }}>抽取运行参数</Typography.Title>
                            <Space size={16} wrap>
                                <Form.Item name="chunk_size" label="chunk_size" initialValue={15000}>
                                    <InputNumber min={200} max={50000} />
                                </Form.Item>
                                <Form.Item name="chunk_overlap" label="overlap %" initialValue={10}>
                                    <InputNumber min={0} max={50} />
                                </Form.Item>
                                <Form.Item name="request_interval" label="请求间隔(s)" initialValue={2}>
                                    <InputNumber min={0} max={60} />
                                </Form.Item>
                                <Form.Item name="llm_timeout" label="超时(s)" initialValue={300}>
                                    <InputNumber min={30} max={1800} />
                                </Form.Item>
                                <Form.Item name="disable_think" label="关闭思考" valuePropName="checked">
                                    <Switch />
                                </Form.Item>
                                <Form.Item name="streaming_enabled" label="流式" valuePropName="checked">
                                    <Switch />
                                </Form.Item>
                            </Space>
                        </>
                    )}
                </Form>
            </Drawer>
        </div>
    );
};

export default ModelConfigsPage;
