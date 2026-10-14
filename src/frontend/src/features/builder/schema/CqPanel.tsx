/**
 * F8 CQ 管理面板（09 §2）：CQ 列表（增删改/排序/启停、来源标记 人工/进化生成）
 * + 评测结果视图（每条 CQ 的可回答状态与证据、历史评测切换）。
 * 挂载点：骨架编辑 Tab 悬浮工具条「评测问题」→ 抽屉（CQ 同时约束抽取与发布门控）。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Alert, Button, Empty, Form, Input, Modal, Popconfirm, Select, Space, Spin,
    Switch, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
    ArrowDownOutlined, ArrowUpOutlined, CheckCircleOutlined, CloseCircleOutlined,
    PlusOutlined, QuestionCircleOutlined, ReloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons';

import { CqEvaluationRow, CqRow, cqApi } from '../../../api/cqs';

const SOURCE_META: Record<string, { color: string; text: string }> = {
    manual: { color: 'blue', text: '人工' },
    evolution: { color: 'purple', text: '进化生成' },
};

const TRIGGER_META: Record<string, string> = {
    manual: '手动触发',
    auto_post_extract: '抽取后自动',
    publish_gate: '发布门控',
};

interface Props {
    projectId: number;
    /** 抽屉打开时为 true，用于懒加载 */
    open: boolean;
}

const CqPanel: React.FC<Props> = ({ projectId, open }) => {
    const [items, setItems] = useState<CqRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [editModalOpen, setEditModalOpen] = useState(false);
    const [editing, setEditing] = useState<CqRow | null>(null);
    const [batchText, setBatchText] = useState('');
    const [form] = Form.useForm();

    // 评测
    const [evaluations, setEvaluations] = useState<CqEvaluationRow[]>([]);
    const [activeEvalId, setActiveEvalId] = useState<number | null>(null);
    const [evaluating, setEvaluating] = useState(false);

    const canEdit = true; // 编辑权限由后端 require_project_role 把关（403 时报错提示）

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [list, evals] = await Promise.all([
                cqApi.list(projectId),
                cqApi.evaluations(projectId, 10),
            ]);
            setItems(list.items || []);
            setEvaluations(evals.items || []);
            setActiveEvalId((prev) => prev ?? (evals.items || [])[0]?.id ?? null);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载 CQ 列表失败');
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    useEffect(() => { if (open) load(); }, [open, load]);

    const activeEval = useMemo(
        () => evaluations.find((e) => e.id === activeEvalId) || null,
        [evaluations, activeEvalId]);

    // ── CRUD ──

    const openCreate = () => {
        setEditing(null);
        setBatchText('');
        form.resetFields();
        setEditModalOpen(true);
    };

    const openEdit = (row: CqRow) => {
        setEditing(row);
        setBatchText('');
        form.setFieldsValue({ question: row.question, answer_criteria: row.answer_criteria || undefined });
        setEditModalOpen(true);
    };

    const submitEdit = async () => {
        const values = await form.validateFields();
        try {
            if (editing) {
                await cqApi.update(projectId, editing.id, values);
                message.success('已保存');
            } else {
                // 支持批量：多行文本每行一条
                const lines = String(batchText || '').split('\n').map((s) => s.trim()).filter(Boolean);
                const itemsToCreate = lines.length > 0
                    ? lines.map((q) => ({ question: q, answer_criteria: values.answer_criteria }))
                    : [{ question: values.question, answer_criteria: values.answer_criteria }];
                const r = await cqApi.create(projectId, itemsToCreate);
                message.success(`已创建 ${r.created} 条 CQ`);
            }
            setEditModalOpen(false);
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '保存失败');
        }
    };

    const handleDelete = async (row: CqRow) => {
        try {
            await cqApi.remove(projectId, row.id);
            message.success('已删除');
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '删除失败');
        }
    };

    const handleToggle = async (row: CqRow, enabled: boolean) => {
        try {
            await cqApi.update(projectId, row.id, { enabled });
            setItems((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled } : r)));
            if (!enabled) message.info('已停用：该 CQ 不再注入抽取 guidance，也不参与评测与门控');
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '操作失败');
        }
    };

    const move = async (row: CqRow, dir: -1 | 1) => {
        const idx = items.findIndex((r) => r.id === row.id);
        const swapWith = idx + dir;
        if (swapWith < 0 || swapWith >= items.length) return;
        const next = [...items];
        [next[idx], next[swapWith]] = [next[swapWith], next[idx]];
        setItems(next);
        try {
            await cqApi.reorder(projectId, next.map((r) => r.id));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '排序失败');
            load();
        }
    };

    // ── 评测 ──

    const runEvaluate = async () => {
        setEvaluating(true);
        try {
            const ev = await cqApi.evaluate(projectId);
            message.success(`评测完成：${ev.answerable_count}/${ev.total_cq} 可回答，覆盖率 ${(ev.coverage_rate * 100).toFixed(0)}%`);
            const evals = await cqApi.evaluations(projectId, 10);
            setEvaluations(evals.items || []);
            setActiveEvalId(ev.id);
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '评测失败');
        } finally {
            setEvaluating(false);
        }
    };

    const columns = [
        {
            title: '排序',
            key: 'sort',
            width: 72,
            render: (_: any, row: CqRow, idx: number) => (
                <Space size={0}>
                    <Button type="text" size="small" icon={<ArrowUpOutlined />}
                            disabled={idx === 0} onClick={() => move(row, -1)} />
                    <Button type="text" size="small" icon={<ArrowDownOutlined />}
                            disabled={idx === items.length - 1} onClick={() => move(row, 1)} />
                </Space>
            ),
        },
        {
            title: '问题',
            dataIndex: 'question',
            ellipsis: true,
            render: (q: string, row: CqRow) => (
                <Tooltip title={row.answer_criteria ? `判定标准：${row.answer_criteria}` : q}>
                    <span>{q}</span>
                </Tooltip>
            ),
        },
        {
            title: '来源',
            dataIndex: 'source',
            width: 96,
            render: (s: string) => (
                <Tag color={SOURCE_META[s]?.color || 'default'}>{SOURCE_META[s]?.text || s}</Tag>
            ),
        },
        {
            title: '启用',
            dataIndex: 'enabled',
            width: 64,
            render: (v: boolean, row: CqRow) => (
                <Switch size="small" checked={v} onChange={(nv) => handleToggle(row, nv)} />
            ),
        },
        {
            title: '操作',
            key: 'actions',
            width: 110,
            render: (_: any, row: CqRow) => (
                <Space size={4}>
                    <Button type="text" size="small" onClick={() => openEdit(row)}>编辑</Button>
                    <Popconfirm
                        title="删除这条 CQ？"
                        description="影响范围：该问题不再注入抽取 guidance，也不再参与评测与发布门控；历史评测记录保留。"
                        okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
                        onConfirm={() => handleDelete(row)}
                    >
                        <Button type="text" size="small" danger>删除</Button>
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    const evalColumns = [
        {
            title: '问题',
            dataIndex: 'question',
            ellipsis: true,
        },
        {
            title: '可回答',
            dataIndex: 'answerable',
            width: 88,
            render: (v: boolean, row: any) => (
                <Tooltip title={v ? `检索到 ${row.source_count} 条引用` : '未检索到任何证据来源'}>
                    <Tag icon={v ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                         color={v ? 'success' : 'error'}>{v ? '可回答' : '失败'}</Tag>
                </Tooltip>
            ),
        },
        {
            title: '证据',
            dataIndex: 'source_count',
            width: 76,
            render: (n: number) => (n ? <Tag color="blue">{n} 条</Tag> : <Tag>无</Tag>),
        },
    ];

    return (
        <div className="space-y-4">
            {/* 概念引导（Jordan 链）：CQ 是什么、有什么用 */}
            <Alert
                type="info"
                showIcon
                icon={<QuestionCircleOutlined />}
                message="评测问题（Competency Questions）是「用户会拿这套知识图谱问什么」"
                description={(
                    <div className="text-sm space-y-1">
                        <p>写下你最关心的 3~10 个业务问题（例如「某公司投资了哪些机构？」）。系统会：</p>
                        <p>① 抽取前把它们注入 AI 抽取范围，减少无关噪声；② 每次抽取后自动试答并生成评测报告；③ 发布新版本时作为「发布门控」的评分依据——新版本答得不如旧版，发布会被拒绝。</p>
                    </div>
                )}
            />

            {/* 评测结果视图 */}
            <div className="border rounded p-3">
                <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
                    <span className="font-medium">评测报告</span>
                    <Space wrap>
                        {evaluations.length > 0 && (
                            <Select
                                size="small"
                                style={{ width: 220 }}
                                value={activeEvalId ?? undefined}
                                onChange={(v) => setActiveEvalId(v)}
                                options={evaluations.map((e) => ({
                                    value: e.id,
                                    label: `#${e.id} ${TRIGGER_META[e.trigger] || e.trigger} · ${e.created_at ? String(e.created_at).slice(5, 16).replace('T', ' ') : ''}`,
                                }))}
                            />
                        )}
                        <Tooltip title={canEdit ? '检索式试答每条启用的 CQ，生成跑通率/证据覆盖率' : ''}>
                            <Button size="small" type="primary" icon={<ThunderboltOutlined />}
                                    loading={evaluating}
                                    disabled={items.filter((r) => r.enabled).length === 0}
                                    onClick={runEvaluate}>
                                运行评测
                            </Button>
                        </Tooltip>
                        <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
                    </Space>
                </div>
                {activeEval ? (
                    <div className="space-y-2">
                        <Space wrap>
                            <Tag color="geekblue">{TRIGGER_META[activeEval.trigger] || activeEval.trigger}</Tag>
                            <span className="text-sm">
                                可回答 <b>{activeEval.answerable_count}</b> / {activeEval.total_cq}
                            </span>
                            <span className="text-sm">证据覆盖率 <b>{(activeEval.coverage_rate * 100).toFixed(0)}%</b></span>
                            <span className="text-xs text-gray-500">
                                {activeEval.created_at ? String(activeEval.created_at).slice(0, 19).replace('T', ' ') : ''}
                            </span>
                        </Space>
                        <Table
                            size="small"
                            rowKey={(r: any) => r.cq_id}
                            columns={evalColumns}
                            dataSource={activeEval.results || []}
                            pagination={false}
                            expandable={{
                                expandedRowRender: (r: any) => (
                                    <div className="space-y-1 text-xs text-gray-600">
                                        <div>检索到 <b>{r.source_count ?? 0}</b> 条引用，证据覆盖率 <b>{((r.coverage ?? 0) * 100).toFixed(0)}%</b>
                                            {r.answerable
                                                ? '（达到可回答阈值）'
                                                : '（引用不足，可能图谱里还没有能回答该问题的内容，或问题措辞与本体词汇差异较大）'}</div>
                                    </div>
                                ),
                            }}
                        />
                    </div>
                ) : (
                    <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={items.length === 0
                            ? '还没有评测问题：先在下方添加 CQ，再运行评测'
                            : '还没有评测报告：点击「运行评测」生成'}
                    />
                )}
            </div>

            {/* CQ 列表 */}
            <div>
                <div className="flex items-center justify-between mb-2">
                    <span className="font-medium">评测问题列表（{items.length}）</span>
                    <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={openCreate}>
                        添加 CQ
                    </Button>
                </div>
                <Table
                    size="small"
                    rowKey="id"
                    loading={loading}
                    columns={columns as any}
                    dataSource={items}
                    pagination={false}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 CQ；建议从最高频的业务问题写起" /> }}
                />
            </div>

            {/* 新建/编辑弹窗 */}
            <Modal
                title={editing ? '编辑评测问题' : '添加评测问题'}
                open={editModalOpen}
                onCancel={() => setEditModalOpen(false)}
                onOk={submitEdit}
                okText={editing ? '保存' : '创建'}
                cancelText="取消"
                destroyOnClose
            >
                {!editing && (
                    <div className="mb-3">
                        <Typography.Text type="secondary" className="text-sm">
                            一条好问题 = 疑问词 + 本体里的词。例如「某公司对外投资了哪些机构？」
                            比笼统的「讲讲投资情况」更容易得到可验证的答案。
                        </Typography.Text>
                    </div>
                )}
                {!editing && (
                    <Form.Item label="批量添加（每行一条，可留空）">
                        <Input.TextArea
                            rows={4}
                            value={batchText}
                            onChange={(e) => setBatchText(e.target.value)}
                            placeholder={'某公司对外投资了哪些机构？\n哪些公司受到监管机构的处罚？'}
                        />
                    </Form.Item>
                )}
                <Form form={form} layout="vertical">
                    {!editing && batchText.trim() === '' && (
                        <Form.Item
                            name="question"
                            label="问题"
                            rules={[{ required: true, message: '请输入问题' }, { max: 2000 }]}
                        >
                            <Input.TextArea rows={2} placeholder="例如：某公司对外投资了哪些机构？" />
                        </Form.Item>
                    )}
                    {editing && (
                        <Form.Item
                            name="question"
                            label="问题"
                            rules={[{ required: true, message: '请输入问题' }, { max: 2000 }]}
                        >
                            <Input.TextArea rows={2} />
                        </Form.Item>
                    )}
                    <Form.Item
                        name="answer_criteria"
                        label={
                            <span>
                                判定标准（可选）
                                <Tooltip title="填写后该 CQ 用于绝对评分门控（答案须覆盖这些关键词/要点）；留空则发布时走匿名 A/B 对比评估。">
                                    <QuestionCircleOutlined className="text-gray-400 ml-1" />
                                </Tooltip>
                            </span>
                        }
                    >
                        <Input placeholder="例如：需提到被投公司名称与投资关系" />
                    </Form.Item>
                </Form>
            </Modal>
        </div>
    );
};

export default CqPanel;
