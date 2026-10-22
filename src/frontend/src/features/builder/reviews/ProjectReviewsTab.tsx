/**
 * 构建器 · 审核 Tab：本项目审核队列（审核工作台的项目内视图，05 §6.8）。
 * S12：详情能力与审核工作台对齐——人话说明区（FriendlyPayload 共享件）、
 * conflict_type 选类、备注、编辑后通过；完整键盘流仍在跨项目工作台。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
    Button, Drawer, Empty, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { CheckOutlined, CloseOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { ReviewItem, reviewsApi } from '../../../api/governance';
import { useAuthStore } from '../../../shared/auth/authStore';
import { FriendlyPayload, humanSummary, PRIORITY_TAG, TYPE_LABEL } from '../../reviews/reviewShared';

const { Text, Paragraph } = Typography;

const STATUS_TAG: Record<string, { color: string; text: string }> = {
    pending: { color: 'gold', text: '待确认' },
    claimed: { color: 'processing', text: '已认领' },
    approved: { color: 'success', text: '已通过' },
    rejected: { color: 'error', text: '已驳回' },
    edited: { color: 'cyan', text: '已修订' },
};

interface Props {
    projectId: number;
}

const PAGE_SIZE = 50;

const ProjectReviewsTab: React.FC<Props> = ({ projectId }) => {
    const { hasModule } = useAuthStore();
    const canDecide = hasModule('review');
    const [items, setItems] = useState<ReviewItem[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [statusFilter, setStatusFilter] = useState<string[]>(['pending']);
    const [typeFilter, setTypeFilter] = useState<string[]>([]);
    const [selectedIds, setSelectedIds] = useState<React.Key[]>([]);
    // 详情抽屉：与审核工作台同款能力（选类/备注/编辑后通过）
    const [detail, setDetail] = useState<ReviewItem | null>(null);
    const [chosenClass, setChosenClass] = useState<string | null>(null);
    const [note, setNote] = useState('');
    const [editOpen, setEditOpen] = useState(false);
    const [editText, setEditText] = useState('');
    const [showRaw, setShowRaw] = useState(false);

    const load = useCallback(async (p = page) => {
        setLoading(true);
        try {
            const r = await reviewsApi.listByProject(projectId, {
                status: statusFilter.length ? statusFilter.join(',') : undefined,
                type: typeFilter.length ? typeFilter.join(',') : undefined,
                limit: PAGE_SIZE,
                offset: (p - 1) * PAGE_SIZE,
            });
            setItems(r.items || []);
            setTotal(r.total ?? (r.items || []).length);
            setSelectedIds((prev) => prev.filter((id) => (r.items || []).some((i) => i.id === id)));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载审核队列失败');
        } finally {
            setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, statusFilter, typeFilter, page]);

    useEffect(() => { load(page); }, [load]);

    const openDetail = async (item: ReviewItem) => {
        setDetail(item);
        setNote('');
        setChosenClass(null);
        setShowRaw(false);
        setEditText(JSON.stringify(item.payload, null, 2));
        // 打开详情自动认领（pending → claimed；与审核工作台行为一致）
        if (item.status === 'pending' && canDecide) {
            try {
                await reviewsApi.claim(item.id);
                setDetail((d) => (d && d.id === item.id ? { ...d, status: 'claimed' } : d));
                load();
            } catch { /* 认领失败不阻断查看 */ }
        }
    };

    const decide = async (action: 'approve' | 'reject' | 'edit', editedPayload?: Record<string, any>) => {
        if (!detail) return;
        // 类型冲突必须先选定正确类别，通过时随 payload 提交（后端据此回写画布）
        if (action === 'approve' && detail.item_type === 'conflict_type' && !editedPayload?.chosen_class) {
            message.warning('请先在上方选择正确的类别');
            return;
        }
        try {
            await reviewsApi.decide(detail.id, {
                action,
                edited_payload: editedPayload,
                note: note || undefined,
            });
            message.success(action === 'approve' ? '已通过' : action === 'reject' ? '已驳回' : '已修订通过');
            setDetail(null);
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '裁决失败');
        }
    };

    const approveCurrent = () => {
        if (!detail) return;
        decide('approve', detail.item_type === 'conflict_type' ? { chosen_class: chosenClass } : undefined);
    };

    const batch = (action: 'approve' | 'reject') => {
        if (selectedIds.length === 0) return;
        Modal.confirm({
            title: `批量${action === 'approve' ? '通过' : '驳回'} ${selectedIds.length} 项`,
            content: '将按类型分组提交；entity_merge 批量仅通过且 similarity≥0.75。',
            onOk: async () => {
                try {
                    const groups: Record<string, number[]> = {};
                    items.filter((i) => selectedIds.includes(i.id)).forEach((i) => {
                        (groups[i.item_type] ||= []).push(i.id);
                    });
                    let ok = 0;
                    const failed: string[] = [];
                    for (const [type, ids] of Object.entries(groups)) {
                        for (let off = 0; off < ids.length; off += 50) {
                            try {
                                const r = await reviewsApi.batchDecide(projectId, ids.slice(off, off + 50), action);
                                ok += r.batch;
                            } catch (e: any) {
                                failed.push(`${TYPE_LABEL[type] || type}：${e.response?.data?.error?.message || e.message}`);
                            }
                        }
                    }
                    if (ok > 0) message.success(`批量完成 ${ok} 项`);
                    if (failed.length > 0) message.warning(`部分分组失败：${failed.join('；')}`);
                    setSelectedIds([]);
                    load();
                } catch (e: any) {
                    message.error(e.response?.data?.error?.message || '批量裁决失败');
                }
            },
        });
    };

    const columns: ColumnsType<ReviewItem> = [
        {
            title: '优先级', dataIndex: 'priority', key: 'priority', width: 90,
            render: (p: string) => <Tag color={PRIORITY_TAG[p]?.color}>{PRIORITY_TAG[p]?.label || p}</Tag>,
        },
        {
            title: '类型', dataIndex: 'item_type', key: 'item_type', width: 110,
            render: (t: string) => TYPE_LABEL[t] || t,
        },
        {
            title: '摘要', key: 'summary', ellipsis: true,
            render: (_, r) => {
                const s = humanSummary(r);
                const friendly = r.item_type !== 'entity_merge' || (r.payload || {}).cluster;
                return (
                    <Tooltip title={s}>
                        <span style={{ color: friendly ? undefined : '#8B94AB' }}>{s}</span>
                    </Tooltip>
                );
            },
        },
        {
            title: '状态', dataIndex: 'status', key: 'status', width: 90,
            render: (s: string) => <Tag color={STATUS_TAG[s]?.color}>{STATUS_TAG[s]?.text || s}</Tag>,
        },
        {
            title: '时间', dataIndex: 'created_at', key: 'created_at', width: 100,
            render: (v: string | null) => (v ? v.slice(5, 16).replace('T', ' ') : '-'),
        },
    ];

    return (
        <div>
            <div className="flex items-center justify-between mb-3">
                <Text type="secondary" className="text-xs">
                    本项目抽取产生的待确认项；跨项目全量队列见左侧导航「审核工作台」
                </Text>
                <Space>
                    <Select
                        mode="multiple" style={{ minWidth: 140 }} allowClear maxTagCount="responsive"
                        placeholder="全部状态" value={statusFilter} onChange={(v) => { setPage(1); setStatusFilter(v); }}
                        options={Object.entries(STATUS_TAG).map(([v, m]) => ({ value: v, label: m.text }))}
                    />
                    <Select
                        mode="multiple" style={{ minWidth: 150 }} allowClear maxTagCount="responsive"
                        placeholder="全部类型" value={typeFilter} onChange={(v) => { setPage(1); setTypeFilter(v); }}
                        options={Object.entries(TYPE_LABEL).map(([v, l]) => ({ value: v, label: l }))}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => load()}>刷新</Button>
                    <Button type="primary" disabled={selectedIds.length === 0 || !canDecide}
                            onClick={() => batch('approve')}>批量通过</Button>
                    <Button danger disabled={selectedIds.length === 0 || !canDecide}
                            onClick={() => batch('reject')}>批量驳回</Button>
                </Space>
            </div>

            <Table
                rowKey="id"
                size="small"
                loading={loading}
                columns={columns}
                dataSource={items}
                rowSelection={{
                    selectedRowKeys: selectedIds,
                    onChange: setSelectedIds,
                    getCheckboxProps: (r) => ({
                        disabled: !canDecide || !(r.status === 'pending' || r.status === 'claimed'),
                    }),
                    selections: [Table.SELECTION_ALL, Table.SELECTION_INVERT, Table.SELECTION_NONE],
                }}
                onRow={(r) => ({
                    onClick: () => openDetail(r),
                    style: { cursor: 'pointer' },
                })}
                pagination={{
                    current: page, pageSize: PAGE_SIZE, total,
                    onChange: (p) => setPage(p),
                    showSizeChanger: false,
                    showTotal: (t) => `共 ${t} 条`,
                }}
                locale={{ emptyText: <Empty description="本项目暂无待确认项 🎉" /> }}
            />

            <Drawer
                title={detail ? `${TYPE_LABEL[detail.item_type] || detail.item_type} · #${detail.id}` : ''}
                open={!!detail}
                onClose={() => setDetail(null)}
                width={560}
                footer={
                    detail && canDecide && (detail.status === 'pending' || detail.status === 'claimed') ? (
                        <Space className="w-full justify-end">
                            <Button onClick={() => {
                                setEditText(JSON.stringify(detail.payload, null, 2));
                                setEditOpen(true);
                            }} icon={<EditOutlined />}>编辑后通过</Button>
                            <Button danger onClick={() => decide('reject')}
                                    icon={<CloseOutlined />}>驳回</Button>
                            <Button type="primary" onClick={approveCurrent}
                                    icon={<CheckOutlined />}>通过</Button>
                        </Space>
                    ) : (
                        <Text type="secondary">
                            {detail?.status && ['approved', 'rejected', 'edited'].includes(detail.status)
                                ? `该审核项已终态（${STATUS_TAG[detail.status]?.text || detail.status}）`
                                : '只读（未开通 review 模块或无权裁决）'}
                        </Text>
                    )
                }
            >
                {detail && (
                    <div className="space-y-3">
                        <Space wrap>
                            <Tag color={PRIORITY_TAG[detail.priority]?.color}>{detail.priority}</Tag>
                            <Tag>{TYPE_LABEL[detail.item_type] || detail.item_type}</Tag>
                            <Tag color={STATUS_TAG[detail.status]?.color}>{STATUS_TAG[detail.status]?.text || detail.status}</Tag>
                            {detail.decided_at && <Text type="secondary" className="text-xs">裁决于 {detail.decided_at.slice(0, 19)}</Text>}
                        </Space>
                        {detail.reason && <div className="text-gray-700 text-sm">{detail.reason}</div>}
                        {detail.suggested_action && (
                            <div className="p-2 bg-blue-50 border border-blue-100 rounded">
                                <Text type="secondary" className="text-xs">系统建议</Text>
                                <div className="text-sm">
                                    {detail.suggested_action.action === 'split'
                                        ? '如合并有误，可按此记录拆分恢复'
                                        : detail.suggested_action.action === 'merge'
                                            ? '建议合并为同一实体（参与项见下方说明）'
                                            : detail.suggested_action.action === 'manual_review'
                                                ? '系统无法自动判断，需要人工裁定'
                                                : JSON.stringify(detail.suggested_action)}
                                </div>
                            </div>
                        )}
                        {/* 人话说明区：与审核工作台同一共享件（conflict_type 内嵌选类） */}
                        <FriendlyPayload item={detail} chosenClass={chosenClass} onChooseClass={setChosenClass} />
                        <div>
                            <Text type="secondary" className="text-xs">备注（写入裁决备注）</Text>
                            <Input.TextArea
                                rows={2}
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                                placeholder="可选：裁决理由"
                            />
                        </div>
                        <div>
                            <div className="flex items-center justify-between mb-1">
                                <Text type="secondary" className="text-xs">原始数据（JSON，供排查用）</Text>
                                <Button size="small" type="text" onClick={() => setShowRaw((v) => !v)}>
                                    {showRaw ? '收起' : '展开'}
                                </Button>
                            </div>
                            {showRaw && (
                                <Paragraph code copyable={{ text: editText }} className="!mb-0"
                                           style={{ maxHeight: 360, overflow: 'auto', fontSize: 12 }}>
                                    {editText}
                                </Paragraph>
                            )}
                        </div>
                    </div>
                )}
            </Drawer>

            <Modal
                title="编辑后通过"
                open={editOpen}
                okText="提交修订"
                cancelText="取消"
                onOk={async () => {
                    try {
                        const parsed = JSON.parse(editText);
                        await decide('edit', parsed);
                        setEditOpen(false);
                    } catch (e: any) {
                        if (e instanceof SyntaxError) {
                            message.error('payload 不是合法 JSON');
                        } else {
                            message.error(e.response?.data?.error?.message || '提交失败');
                        }
                    }
                }}
                onCancel={() => setEditOpen(false)}
                width={640}
            >
                <Input.TextArea
                    rows={14}
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                />
            </Modal>
        </div>
    );
};

export default ProjectReviewsTab;
