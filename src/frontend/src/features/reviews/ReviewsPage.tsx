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
import { useAuthStore } from '../../shared/auth/authStore';

const { Text, Paragraph } = Typography;

const PRIORITY_TAG: Record<string, { color: string; label: string }> = {
    critical: { color: 'red', label: 'critical' },
    high: { color: 'orange', label: 'high' },
    medium: { color: 'blue', label: 'medium' },
    low: { color: 'default', label: 'low' },
};

const TYPE_LABEL: Record<string, string> = {
    entity_merge: '实体合并',
    new_class: '新类型',
    low_confidence_entity: '低置信实体',
    low_confidence_relation: '低置信关系',
    conflict_value: '属性值冲突',
    conflict_type: '类型冲突',
    conflict_relationship: '关系冲突',
    missing_evidence: '缺证据',
};

/** 0.793 → "79%"；无效值返回 null */
const pct = (v: any): string | null => {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return null;
    return `${Math.round(n * 100)}%`;
};

/**
 * 通俗摘要（摘要列用）：面向不熟悉图谱术语的用户，一句话说清"发现了什么问题、需要你做什么"。
 */
function humanSummary(item: ReviewItem): string {
    const p = item.payload || {};
    switch (item.item_type) {
        case 'entity_merge': {
            const c = p.cluster || {};
            const labels: string[] = c.member_labels || [];
            const sim = pct(c.similarity);
            if (labels.length > 1) {
                return `以下称呼疑似指同一个东西，建议合并为一个：「${labels.join('”、“')}」${sim ? `（相似度 ${sim}）` : ''}`;
            }
            if (p.merged_ids || p.canonical_label) {
                return `已按规则自动合并到「${p.canonical_label || p.label || ''}」，此记录留痕，确认无误即可`;
            }
            return '发现疑似重复的实体，建议合并';
        }
        case 'low_confidence_entity':
            return `从文档中识别出实体「${p.label || ''}」，但把握不大${pct(p.confidence) ? `（可信度 ${pct(p.confidence)}）` : ''}，请判断该实体是否真实存在`;
        case 'low_confidence_relation':
            return `识别出关系「${p.subject_label || '?'} → ${p.predicate || '?'} → ${p.object_label || '?'}」，但把握不大${pct(p.confidence) ? `（可信度 ${pct(p.confidence)}）` : ''}，请判断该关系是否成立`;
        case 'missing_evidence':
            return `实体「${p.label || ''}」没能找到原文依据，只保留了所在段落的摘要，请人工核实`;
        case 'conflict_type': {
            const sources = Array.isArray(p.sources) ? p.sources : [];
            const classes = [...new Set(sources.map((s: any) => s.class).filter(Boolean))];
            const label = sources[0]?.label || '';
            return classes.length > 0
                ? `「${label}」在不同文档中被归入了不同类别（${classes.join('、')}），请判断哪个正确`
                : '同一实体在不同文档中被归入不同类别，请判断';
        }
        case 'conflict_value': {
            const vals = Array.isArray(p.values) ? p.values : [];
            return `「${p.label || p.entity_label || ''}」的属性「${p.property || '?'}」出现了多个不同取值（${vals.map((v: any) => String(v.value ?? v)).join('、') || '见详情'}），请判断以哪个为准`;
        }
        case 'conflict_relationship':
            return `「${p.subject_label || '?'}」与「${p.object_label || '?'}」之间的关系在不同文档中描述不一致，请判断`;
        case 'new_class':
            return `从文档中发现了新类别「${p.label || ''}」，它不在当前本体骨架中，请决定是否加入`;
        default:
            return item.reason || JSON.stringify(p).slice(0, 60);
    }
}

/**
 * 详情抽屉的"人话"展示区：按类型渲染结构化说明 + 关键事实；原始 JSON 折叠给高级用户。
 * conflict_type 会内嵌类别单选（chosenClass/onChooseClass 由外层持有，随裁决提交）。
 */
const FriendlyPayload: React.FC<{
    item: ReviewItem;
    chosenClass?: string | null;
    onChooseClass?: (cls: string) => void;
}> = ({ item, chosenClass, onChooseClass }) => {
    const p = item.payload || {};
    const [showRaw, setShowRaw] = useState(false);

    const Quote = ({ text, title }: { text?: string | null; title: string }) => (
        !text ? null : (
            <div>
                <Text type="secondary" className="text-xs">{title}</Text>
                <Paragraph className="!mb-0 mt-1 p-2 bg-amber-50 border-l-4 border-amber-400 rounded-r text-gray-800"
                           style={{ fontSize: 13 }}>{String(text).replace(/^\[chunk \d+ 摘要\]\s*/, '')}</Paragraph>
            </div>
        )
    );

    switch (item.item_type) {
        case 'entity_merge': {
            const c = p.cluster || {};
            const labels: string[] = c.member_labels || [];
            const sim = pct(c.similarity);
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded">
                        <Text strong>系统认为这些称呼指的是同一个东西：</Text>
                        <div className="mt-2 flex flex-wrap gap-2">
                            {labels.map((l, i) => (
                                <Tag key={i} color={l === c.canonical_label ? 'purple' : 'default'}>{l}</Tag>
                            ))}
                        </div>
                        {sim && <div className="mt-2 text-sm">相似度：<Text strong>{sim}</Text>（越高越可能是同一个）</div>}
                        {c.canonical_label && <div className="mt-1 text-sm">建议保留：<Text strong>「{c.canonical_label}」</Text>作为统一名称</div>}
                    </div>
                    <div className="text-sm text-gray-600">
                        点击下方「通过」将把它们合并为一个实体；「驳回」则保留各自独立。合并后可随时撤销。
                    </div>
                    <Quote text={item.reason} title="系统说明" />
                </div>
            );
        }
        case 'low_confidence_entity':
        case 'low_confidence_relation':
        case 'missing_evidence': {
            const ev = p.evidence || p.downgraded_evidence;
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        {item.item_type === 'low_confidence_entity' && (
                            <>识别出实体 <Text strong>「{p.label}」</Text>（类别：{p.class_label || '未知'}），
                                可信度仅 <Text strong type="danger">{pct(p.confidence) || '未知'}</Text>（一般 ≥70% 才会自动收录）。</>
                        )}
                        {item.item_type === 'low_confidence_relation' && (
                            <>识别出关系：<Text strong>「{p.subject_label}」 → 「{p.predicate}」 → 「{p.object_label}」</Text>，
                                可信度仅 <Text strong type="danger">{pct(p.confidence) || '未知'}</Text>。</>
                        )}
                        {item.item_type === 'missing_evidence' && (
                            <>实体 <Text strong>「{p.label}」</Text>（类别：{p.class_label || '未知'}）在原文中找不到对应原句，
                                下方仅是它所在段落的摘要，可能不准确。</>
                        )}
                    </div>
                    <Quote text={ev} title="原文依据（来自文档切片）" />
                </div>
            );
        }
        case 'conflict_type': {
            const sources = Array.isArray(p.sources) ? p.sources : [];
            const steps = p.guide?.steps as string[] | undefined;
            const classOptions = [...new Set(sources.map((s: any) => String(s.class || '')).filter(Boolean))];
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        同一实体被归入了不同类别。请点选<b>正确的那一个</b>，点「通过」后实体将归入该类别：
                    </div>
                    <Radio.Group
                        value={chosenClass ?? null}
                        onChange={(e) => onChooseClass?.(e.target.value)}
                        className="flex flex-col gap-2"
                    >
                        {classOptions.map((cls) => (
                            <Radio key={cls} value={cls}>
                                <Tag color={cls === chosenClass ? 'purple' : 'default'}>{cls}</Tag>
                                {chosenClass === cls && <Text type="secondary" className="text-xs">（已选：通过后实体将归入此类）</Text>}
                            </Radio>
                        ))}
                    </Radio.Group>
                    {steps && steps.length > 0 && (
                        <div>
                            <Text type="secondary" className="text-xs">排查建议</Text>
                            <ol className="list-decimal pl-5 text-sm text-gray-700 space-y-1">
                                {steps.map((s, i) => <li key={i}>{s}</li>)}
                            </ol>
                        </div>
                    )}
                    <div className="text-sm text-gray-600">
                        选择后点「通过」即生效；若两个类别都不对，可「驳回」并在骨架编辑中手工调整。
                    </div>
                </div>
            );
        }
        case 'conflict_value': {
            const vals = Array.isArray(p.values) ? p.values : [];
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        「{p.label || p.entity_label}」的属性「{p.property}」在不同文档中取值不一致：
                    </div>
                    <div className="space-y-2">
                        {vals.map((v: any, i: number) => (
                            <div key={i} className="text-sm">
                                <Tag color="orange">{String(v.value ?? v)}</Tag>
                                {v.source && <Text type="secondary" className="text-xs">来源：{String(v.source)}</Text>}
                            </div>
                        ))}
                        {vals.length === 0 && <div className="text-sm text-gray-500">详见下方原始数据</div>}
                    </div>
                </div>
            );
        }
        case 'new_class':
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        文档中出现了新类别 <Text strong>「{p.label}」</Text>，当前本体骨架中没有它。
                        通过后会加入骨架；驳回则忽略。
                    </div>
                    {p.definition && <Quote text={p.definition} title="定义" />}
                </div>
            );
        default:
            return null;
    }
};

const ReviewsPage: React.FC = () => {
    const { hasModule } = useAuthStore();
    const [items, setItems] = useState<ReviewItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [statusFilter, setStatusFilter] = useState<string[]>(['pending']);
    const [typeFilter, setTypeFilter] = useState<string[]>([]);
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
            const r = await reviewsApi.listAll({
                status: statusFilter.length ? statusFilter.join(',') : undefined,
                type: typeFilter.length ? typeFilter.join(',') : undefined,
            });
            setItems(r.items || []);
            // 过滤/刷新后剔除已不在结果中的选中项
            setSelectedIds((prev) => prev.filter((id) => (r.items || []).some((i) => i.id === id)));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载审核队列失败');
        } finally {
            setLoading(false);
        }
    }, [statusFilter, typeFilter]);

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
        decide('approve', detail.item_type === 'conflict_type' ? { chosen_class: chosenClass } : undefined);
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
                                failed.push(`${TYPE_LABEL[type] || type}(项目${pid})：${e.response?.data?.error?.message || e.message}`);
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
        { title: '项目', dataIndex: 'project_id', key: 'project_id', width: 70 },
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
