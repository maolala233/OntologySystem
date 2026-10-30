/**
 * 审核工作台 /reviews（05 §6.8，M3-7 新增）：跨项目队列 + 详情面板 + 键盘流。
 * 键盘：↑/↓ 或 J/K 切换、1 通过 / 2 编辑后通过（改 payload）/ 3 驳回、C 认领、E 展开证据。
 * 打开详情自动 claim（他人已认领 → 409 提示）；批量裁决走 batch-decide（≤50，同类型同动作）。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Button, Drawer, Empty, Input, Modal, Radio, Select, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { CheckOutlined, CloseOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { ReviewItem, reviewsApi } from '../../api/governance';
import { AXIOM_KIND_LABEL, AXIOM_SOURCE_TAG, FriendlyPayload, humanSummary, PRIORITY_TAG, pct, TYPE_LABEL } from './reviewShared';
import { useAuthStore } from '../../shared/auth/authStore';

const { Text, Paragraph } = Typography;

const ReviewsPage: React.FC = () => {
    const { hasModule } = useAuthStore();
    const [items, setItems] = useState<ReviewItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [statusFilter, setStatusFilter] = useState<string[]>(['pending']);
    const [typeFilter, setTypeFilter] = useState<string[]>([]);
    // 项目筛选（多选可搜索）；选项来自 /api/reviews 响应的 projects（可读范围内有审核项的项目）
    const [projectFilter, setProjectFilter] = useState<number[]>([]);
    const [projectOptions, setProjectOptions] = useState<Array<{ id: number; name: string | null }>>([]);
    const [selectedIds, setSelectedIds] = useState<React.Key[]>([]);
    const [pageSize, setPageSize] = useState(50);
    // 最后浏览过的项：抽屉关闭后 J/K 从这里继续前进/后退，而不是每次都回到第一条
    const lastViewedIdRef = useRef<number | null>(null);
    const [detail, setDetail] = useState<ReviewItem | null>(null);
    // conflict_type 人工选定的正确类别（随 approve 提交）
    const [chosenClass, setChosenClass] = useState<string | null>(null);
    const [showEvidence, setShowEvidence] = useState(false);
    const [note, setNote] = useState('');
    const [editOpen, setEditOpen] = useState(false);
    const [editText, setEditText] = useState('');
    const [currentIndex, setCurrentIndex] = useState(0);

    const canDecide = hasModule('review');

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await reviewsApi.listAllPaged({
                status: statusFilter.length ? statusFilter.join(',') : undefined,
                type: typeFilter.length ? typeFilter.join(',') : undefined,
                project_id: projectFilter.length ? projectFilter.join(',') : undefined,
            });
            setItems(r.items || []);
            if (r.projects?.length) setProjectOptions(r.projects);
            if (r.truncated) {
                message.warning(`审核项共 ${r.total} 条，超出单次加载上限 2000，已显示前 2000 条；请用状态/类型筛选缩小范围`);
            }
            // 过滤/刷新后剔除已不在结果中的选中项
            setSelectedIds((prev) => prev.filter((id) => (r.items || []).some((i) => i.id === id)));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载审核队列失败');
        } finally {
            setLoading(false);
        }
    }, [statusFilter, typeFilter, projectFilter]);

    useEffect(() => { load(); }, [load]);

    const openDetail = useCallback(async (item: ReviewItem) => {
        setDetail(item);
        setNote('');
        setChosenClass(null);
        setShowEvidence(false);
        setCurrentIndex(items.findIndex((i) => i.id === item.id));
        lastViewedIdRef.current = item.id;
        // 打开详情自动认领（pending → claimed；他人已认领 409 提示）
        if (item.status === 'pending') {
            try {
                await reviewsApi.claim(item.id);
                setDetail((d: ReviewItem | null) => (d && d.id === item.id ? { ...d, status: 'claimed' } : d));
                load();
            } catch (e: any) {
                const code = e.response?.data?.error?.code;
                if (code === 'ALREADY_CLAIMED') {
                    message.warning('该审核项已被他人认领');
                } else if (code !== 'MODULE_NOT_GRANTED') {
                    // 认领失败不阻断查看
                }
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [items]);

    const decide = useCallback(async (action: 'approve' | 'reject' | 'edit', editedPayload?: Record<string, any>) => {
        if (!detail) return;
        // 类型冲突必须先选定正确类别，通过时随 payload 提交（后端据此回写画布）
        if (action === 'approve' && detail.item_type === 'conflict_type' && !editedPayload?.chosen_class) {
            message.warning('请先在上方选择正确的类别');
            return;
        }
        try {
            const r = await reviewsApi.decide(detail.id, { action, edited_payload: editedPayload, note: note || undefined });
            message.success(`已${action === 'approve' ? '通过' : action === 'reject' ? '驳回' : '修订通过'}：${JSON.stringify(r.effect).slice(0, 80)}`);
            setDetail(null);
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '裁决失败');
        }
    }, [detail, note, load]);

    const approveCurrent = useCallback(() => {
        if (!detail) return;
        if (detail.item_type === 'conflict_type') {
            decide('approve', { chosen_class: chosenClass });
        } else if (detail.item_type === 'new_class'
                   && chosenClass && chosenClass !== '__new__') {
            decide('approve', { merge_into_class: chosenClass });
        } else if (detail.item_type === 'new_predicate'
                   && chosenClass && chosenClass !== '__new__') {
            decide('approve', { merge_into_predicate: chosenClass });
        } else {
            decide('approve');
        }
    }, [detail, chosenClass, decide]);

    // 键盘流（05 §6.8：↑↓/J K 切换，1/2/3 裁决，E 展开证据）。
    // J/K/↑/↓ 无需先打开详情：抽屉关闭时从"最后浏览过的项"继续前进/后退。
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (editOpen || e.ctrlKey || e.metaKey || e.altKey) return;
            const target = e.target as HTMLElement;
            if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;

            const viewIdx = lastViewedIdRef.current != null
                ? items.findIndex((i) => i.id === lastViewedIdRef.current)
                : -1;
            if (e.key === 'ArrowDown' || e.key === 'j' || e.key === 'J') {
                e.preventDefault();
                const from = detail ? currentIndex : (viewIdx < 0 ? -1 : viewIdx);
                const next = items[Math.min(from + 1, items.length - 1)];
                if (next && from + 1 < items.length) openDetail(next);
            } else if (e.key === 'ArrowUp' || e.key === 'k' || e.key === 'K') {
                e.preventDefault();
                const from = detail ? currentIndex : (viewIdx < 0 ? 1 : viewIdx);
                const prev = items[Math.max(from - 1, 0)];
                if (prev) openDetail(prev);
            } else if (!detail) {
                return; // 裁决类快捷键需要先打开详情
            } else if (e.key === '1') {
                decide('approve');
            } else if (e.key === '2') {
                setEditText(JSON.stringify(detail.payload, null, 2));
                setEditOpen(true);
            } else if (e.key === '3') {
                decide('reject');
            } else if (e.key === 'e' || e.key === 'E') {
                setShowEvidence((v) => !v);
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [detail, editOpen, items, currentIndex, openDetail, decide]);

    const batch = async (action: 'approve' | 'reject') => {
        if (selectedIds.length === 0) return;
        Modal.confirm({
            title: `批量${action === 'approve' ? '通过' : '驳回'} ${selectedIds.length} 项`,
            content: '将按项目×类型分组提交；entity_merge 批量仅通过且 similarity≥0.75。',
            onOk: async () => {
                try {
                    // batch-decide 约束：项目级端点、同类型、单次 ≤50 → 按(项目×类型)分组再切块
                    const groups: Record<string, number[]> = {};
                    items.filter((i) => selectedIds.includes(i.id)).forEach((i) => {
                        (groups[`${i.project_id}|${i.item_type}`] ||= []).push(i.id);
                    });
                    let total = 0;
                    const failed: string[] = [];
                    for (const [key, ids] of Object.entries(groups)) {
                        const [pid, type] = key.split('|');
                        for (let off = 0; off < ids.length; off += 50) {
                            try {
                                const r = await reviewsApi.batchDecide(Number(pid), ids.slice(off, off + 50), action);
                                total += r.batch;
                            } catch (e: any) {
                                const pname = items.find((i) => String(i.project_id) === pid)?.project_name;
                                failed.push(`${TYPE_LABEL[type] || type}（${pname || `项目${pid}`}）：${e.response?.data?.error?.message || e.message}`);
                            }
                        }
                    }
                    if (total > 0) message.success(`批量完成 ${total} 项`);
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
            sorter: (a, b) => ['critical', 'high', 'medium', 'low'].indexOf(a.priority) - ['critical', 'high', 'medium', 'low'].indexOf(b.priority),
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
            title: '项目', dataIndex: 'project_name', key: 'project_name', width: 130,
            ellipsis: true,
            render: (_, r) => (
                <Tooltip title={r.project_name ? `项目 #${r.project_id}` : '项目已删除'}>
                    <span>{r.project_name || `#${r.project_id}`}</span>
                </Tooltip>
            ),
        },
        {
            title: '状态', dataIndex: 'status', key: 'status', width: 90,
            render: (s: string) => (
                <Tag color={s === 'pending' ? 'gold' : s === 'claimed' ? 'processing' : s === 'approved' ? 'success' : s === 'rejected' ? 'error' : 'cyan'}>
                    {s}
                </Tag>
            ),
        },
    ];

    const payloadSummary = useMemo(() => {
        if (!detail) return '';
        return JSON.stringify(detail.payload, null, 2);
    }, [detail]);

    return (
        <div className="p-4">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <span className="text-lg font-semibold">审核工作台</span>
                    <Text type="secondary" className="text-xs">
                        键盘流：↑/↓ 或 J/K 切换 · 1 通过 · 2 编辑 · 3 驳回 · E 证据
                    </Text>
                </div>
                <Space>
                    <Select
                        mode="multiple"
                        style={{ minWidth: 160, maxWidth: 300 }}
                        allowClear
                        maxTagCount="responsive"
                        placeholder="全部状态"
                        value={statusFilter}
                        onChange={setStatusFilter}
                        options={[
                            { value: 'pending', label: '待处理' },
                            { value: 'claimed', label: '已认领' },
                            { value: 'approved', label: '已通过' },
                            { value: 'rejected', label: '已驳回' },
                            { value: 'edited', label: '已修订' },
                        ]}
                    />
                    <Select
                        mode="multiple"
                        style={{ minWidth: 180, maxWidth: 320 }}
                        allowClear
                        maxTagCount="responsive"
                        showSearch
                        optionFilterProp="label"
                        placeholder="全部项目"
                        value={projectFilter}
                        onChange={setProjectFilter}
                        options={projectOptions.map(p => ({ value: p.id, label: p.name || `#${p.id}（已删除）` }))}
                    />
                    <Select
                        mode="multiple"
                        style={{ minWidth: 160, maxWidth: 320 }}
                        allowClear
                        maxTagCount="responsive"
                        placeholder="全部类型"
                        value={typeFilter}
                        onChange={setTypeFilter}
                        options={Object.entries(TYPE_LABEL).map(([v, l]) => ({ value: v, label: l }))}
                    />
                    <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
                    <Button type="primary" disabled={selectedIds.length === 0}
                            onClick={() => batch('approve')}>批量通过</Button>
                    <Button danger disabled={selectedIds.length === 0}
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
                    // 仅待处理/已认领可批量裁决
                    getCheckboxProps: (r) => ({
                        disabled: !(r.status === 'pending' || r.status === 'claimed'),
                    }),
                    selections: [Table.SELECTION_ALL, Table.SELECTION_INVERT, Table.SELECTION_NONE],
                }}
                pagination={{
                    pageSize,
                    onChange: (_page, size) => setPageSize(size),
                    showSizeChanger: true,
                    pageSizeOptions: [50, 100, 200],
                    showTotal: (t) => `共 ${t} 条`,
                    showQuickJumper: true,
                }}
                onRow={(r) => ({
                    onClick: () => canDecide && openDetail(r),
                    style: { cursor: canDecide ? 'pointer' : 'default' },
                })}
                locale={{ emptyText: <Empty description="队列已清空 🎉" /> }}
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
                            }} icon={<EditOutlined />}>编辑后通过 (2)</Button>
                            <Button danger onClick={() => decide('reject')}
                                    icon={<CloseOutlined />}>驳回 (3)</Button>
                            <Button type="primary" onClick={approveCurrent}
                                    icon={<CheckOutlined />}>通过 (1)</Button>
                        </Space>
                    ) : (
                        <Text type="secondary">
                            {detail?.status && ['approved', 'rejected', 'edited'].includes(detail.status)
                                ? `该审核项已终态（${detail.status}）`
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
                            <Tag color="blue">{detail.status}</Tag>
                            {detail.decided_at && <Text type="secondary" className="text-xs">裁决于 {detail.decided_at.slice(0, 19)}</Text>}
                        </Space>
                        {detail.reason && <div className="text-gray-700">{detail.reason}</div>}
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
                        {/* 人话说明区：按类型结构化展示，替代裸 payload */}
                        <FriendlyPayload item={detail} chosenClass={chosenClass} onChooseClass={setChosenClass} />
                        <div>
                            <div className="flex items-center justify-between mb-1">
                                <Text type="secondary" className="text-xs">原始数据（JSON，供排查用）</Text>
                                <Button size="small" type="text" onClick={() => setShowEvidence((v) => !v)}>
                                    {showEvidence ? '收起 (E)' : '展开 (E)'}
                                </Button>
                            </div>
                            {showEvidence && (
                                <Paragraph code copyable={{ text: payloadSummary }} className="!mb-0"
                                           style={{ maxHeight: 360, overflow: 'auto', fontSize: 12 }}>
                                    {payloadSummary}
                                </Paragraph>
                            )}
                        </div>
                        <div>
                            <Text type="secondary" className="text-xs">备注（写入 result_ref.note）</Text>
                            <Input.TextArea
                                rows={2}
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                                placeholder="可选：裁决理由"
                            />
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

export default ReviewsPage;
