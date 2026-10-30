/**
 * 构建器 · 时间轴 Tab（05 §6.7）：版本时间轴（GET /versions）+ 双版本 diff + 回滚 +
 * 实体时间线（GET /timeline 双时间轴）+ 发布门控/影响分析（O3-B13/B14/F9）。
 * vis-timeline 渲染归 M4，本 Tab 用 antd 结构化呈现。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Alert, Button, Card, Collapse, Descriptions, Empty, Input, Modal, Segmented, Select, Space, Spin, Table, Tag, Timeline as AntTimeline, Tooltip, message,
} from 'antd';
import { RollbackOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons';

import { GateEvaluationRow, ImpactSummary, gateApi, impactApi } from '../../../api/cqs';
import {
    ClassModified, DiffResult, InstanceBrief, InstanceModified, RelationRow, VersionRow, versionsApi,
} from '../../../api/governance';
import { listCopilotAudit } from '../../../api/copilot';

const KIND_META: Record<string, { color: string; label: string }> = {
    schema: { color: 'blue', label: '骨架' },
    full: { color: 'green', label: '实例' },
    publication: { color: 'purple', label: '发布' },
    rollback: { color: 'orange', label: '回滚' },
    manual: { color: 'geekblue', label: '手动编辑' },
};

// 门控结论呈现（候选视角：win=候选胜）
const GATE_VERDICT: Record<string, { color: string; text: string }> = {
    pass: { color: 'success', text: '通过' },
    reject: { color: 'error', text: '未通过' },
};
const RESULT_VERDICT: Record<string, { color: string; text: string }> = {
    win: { color: 'success', text: '候选胜' },
    loss: { color: 'error', text: '候选负' },
    tie: { color: 'default', text: '平局' },
};

// ── diff 友好呈现 ─────────────────────────────────────────────
const MAX_DIRECT = 20;   // 列表直接展示的行数，超出折叠
const MAX_TAGS = 30;     // 标签墙直接展示的数量

const fmtVal = (v: any) => {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    return s && s.length > 40 ? `${s.slice(0, 40)}…` : (s || '（空）');
};

const SectionHeader = ({ title, added, removed, modified }: { title: string; added: number; removed: number; modified: number }) => (
    <div className="font-medium mb-2 flex items-center gap-2 flex-wrap">
        <span>{title}</span>
        <span className="text-xs font-normal space-x-2">
            {added > 0 && <span className="text-green-600">+{added} 新增</span>}
            {removed > 0 && <span className="text-red-600">−{removed} 删除</span>}
            {modified > 0 && <span className="text-amber-600">~{modified} 变化</span>}
            {!added && !removed && !modified && <span className="text-gray-500">无差异</span>}
        </span>
    </div>
);

const TagWall = ({ words, color }: { words: string[]; color: string }) => (
    <Space wrap size={[6, 6]}>
        {words.slice(0, MAX_TAGS).map((w) => <Tag key={w} color={color}>{w}</Tag>)}
        {words.length > MAX_TAGS && <span className="text-xs text-gray-500">…还有 {words.length - MAX_TAGS} 个</span>}
    </Space>
);

/** 实例按类别分组：一行一个类别，类别 × 数量 + 前若干个实例名 */
const InstanceGroupLines = ({ items, color }: { items: InstanceBrief[]; color: 'success' | 'error' }) => {
    const groups = new Map<string, string[]>();
    items.forEach((i) => {
        const k = i.class_label || '未分类';
        groups.set(k, [...(groups.get(k) || []), i.label || i.id]);
    });
    return (
        <div className="space-y-1">
            {[...groups.entries()].sort((a, b) => b[1].length - a[1].length).map(([cls, labels]) => (
                <div key={cls} className="text-sm leading-6">
                    <Tag color={color}>{cls} × {labels.length}</Tag>
                    <span className="text-gray-700">
                        {labels.slice(0, 10).join('、')}{labels.length > 10 ? ` 等 ${labels.length} 个` : ''}
                    </span>
                </div>
            ))}
        </div>
    );
};

/** 实例字段级变化：人话描述每条（类别调整/新增/移除/值变化） */
const ModifiedLines = ({ mods }: { mods: InstanceModified[] }) => {
    const lines = mods.map((m) => {
        const parts: string[] = [];
        if (m.class_from !== m.class_to) parts.push(`类别「${m.class_from || '—'}」→「${m.class_to || '—'}」`);
        if (m.props_added.length) parts.push(`新增属性：${m.props_added.join('、')}`);
        if (m.props_removed.length) parts.push(`移除属性：${m.props_removed.join('、')}`);
        for (const c of m.props_changed) parts.push(`${c.key}：${fmtVal(c.from)} → ${fmtVal(c.to)}`);
        return { key: m.id, label: m.label || m.id, text: parts.join('；') || '（无字段变化）' };
    });
    const show = lines.slice(0, MAX_DIRECT);
    const rest = lines.slice(MAX_DIRECT);
    const renderLine = (l: { key: string; label: string; text: string }) => (
        <div key={l.key} className="text-sm leading-6">
            <Tag color="warning">变</Tag>
            <span className="font-medium">{l.label}</span>
            <span className="ml-1 text-gray-700">{l.text}</span>
        </div>
    );
    return (
        <div className="space-y-1">
            {show.map(renderLine)}
            {rest.length > 0 && (
                <Collapse
                    size="small"
                    items={[{ key: 'more', label: `还有 ${rest.length} 条变化`, children: <div className="space-y-1">{rest.map(renderLine)}</div> }]}
                />
            )}
        </div>
    );
};

/** 关系行：谓词 Tag + 主体 → 客体（超出折叠） */
const RelationLines = ({ rows, color }: { rows: RelationRow[]; color: 'success' | 'error' }) => {
    const show = rows.slice(0, MAX_DIRECT);
    const rest = rows.slice(MAX_DIRECT);
    const renderLine = (r: RelationRow, i: number) => (
        <div key={`${r.subject}-${r.predicate}-${r.object}-${i}`} className="text-sm leading-6">
            <Tag color={color}>{r.predicate}</Tag>
            <span className="text-gray-700">{r.subject_label} → {r.object_label}</span>
        </div>
    );
    return (
        <div className="space-y-1">
            {show.map(renderLine)}
            {rest.length > 0 && (
                <Collapse
                    size="small"
                    items={[{ key: 'more', label: `还有 ${rest.length} 条`, children: <div className="space-y-1">{rest.map(renderLine)}</div> }]}
                />
            )}
        </div>
    );
};

/** 类字段级变化：属性增删/类型变化/父类调整/定义调整（旧格式无字段级 detail 时退化为「定义调整」） */
const ClassModifiedLines = ({ mods }: { mods: ClassModified[] }) => {
    const lines = mods.map((m) => {
        const parts: string[] = [];
        for (const p of m.props_added || []) parts.push(`新增属性：${p.name}(${p.data_type || '未定类型'})`);
        for (const p of m.props_removed || []) parts.push(`移除属性：${p.name}(${p.data_type || '未定类型'})`);
        for (const p of m.props_changed || []) parts.push(`${p.name}：${p.from || '未定类型'} → ${p.to || '未定类型'}`);
        if (m.parent_from !== m.parent_to) parts.push(`父类：「${m.parent_from || '—'}」→「${m.parent_to || '—'}」`);
        if (m.definition_changed) parts.push('定义描述调整');
        return { key: m.label, label: m.label, text: parts.join('；') || '定义调整' };
    });
    const show = lines.slice(0, MAX_DIRECT);
    const rest = lines.slice(MAX_DIRECT);
    const renderLine = (l: { key: string; label: string; text: string }) => (
        <div key={l.key} className="text-sm leading-6">
            <Tag color="warning">变</Tag>
            <span className="font-medium">{l.label}</span>
            <span className="ml-1 text-gray-700">{l.text}</span>
        </div>
    );
    return (
        <div className="space-y-1">
            {show.map(renderLine)}
            {rest.length > 0 && (
                <Collapse
                    size="small"
                    items={[{ key: 'more', label: `还有 ${rest.length} 条变化`, children: <div className="space-y-1">{rest.map(renderLine)}</div> }]}
                />
            )}
        </div>
    );
};

interface Props {
    projectId: number;
    onChanged?: () => void;
}

// AI 协同操作历史（O5-B29/F15）：copilot.* 审计流水的人话呈现
interface CopilotAuditRow {
    id: number; action: string; actor_id: number | null; actor: string | null;
    detail: Record<string, any>; created_at: string | null;
}
const AUDIT_ACTION: Record<string, { color: string; text: string }> = {
    'copilot.chat': { color: 'default', text: '协同对话' },
    'copilot.proposal.apply': { color: 'green', text: '提案应用' },
    'copilot.proposal.reject': { color: 'default', text: '提案忽略' },
    'copilot.suggestion.convert': { color: 'cyan', text: '建议转审核' },
    'copilot.verify.archive': { color: 'purple', text: '验证报告存档' },
    'copilot.session.delete': { color: 'default', text: '会话删除' },
};
const AUDIT_SUMMARY: Record<string, (d: Record<string, any>) => string> = {
    'copilot.chat': d => `会话 #${d.session_id ?? '-'}（${d.status ?? '-'}，${(d.latency_ms ?? 0) / 1000 | 0}s）`,
    'copilot.proposal.apply': d => `提案 ${d.proposal_id ?? '-'}（${d.kind ?? '-'}），`
        + (d.version_no ? `生成 manual 版本 v${d.version_no}` : '画布已更新')
        + (d.diff_summary ? `，改动 ${d.diff_summary}` : ''),
    'copilot.proposal.reject': d => `提案 ${d.proposal_id ?? '-'}（无画布副作用）`,
    'copilot.suggestion.convert': d => `建议 ${d.proposal_id ?? '-'} → 审核项 #${d.review_item_id ?? '-'}`,
    'copilot.verify.archive': d => `报告 #${d.message_id ?? '-'} → 版本 v${d.version_no ?? '-'}（结论 ${d.overall ?? '-'}）`,
    'copilot.session.delete': d => `会话 #${d.session_id ?? '-'}`,
};

const TimelineTab: React.FC<Props> = ({ projectId, onChanged }) => {
    const [subTab, setSubTab] = useState<'versions' | 'entity' | 'gate' | 'audit'>('versions');
    const [versions, setVersions] = useState<VersionRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [selA, setSelA] = useState<number | null>(null);
    const [selB, setSelB] = useState<number | null>(null);
    const [diff, setDiff] = useState<DiffResult | null>(null);
    const [diffLoading, setDiffLoading] = useState(false);

    // 实体时间线
    const [entityUri, setEntityUri] = useState('');
    const [axis, setAxis] = useState<'valid' | 'transaction' | 'both'>('both');
    const [events, setEvents] = useState<Record<string, any>[]>([]);
    const [entityLoading, setEntityLoading] = useState(false);

    // 发布门控/影响分析（O3-B13/B14/F9）
    const [gates, setGates] = useState<GateEvaluationRow[]>([]);
    const [impact, setImpact] = useState<ImpactSummary | null>(null);
    const [gateLoading, setGateLoading] = useState(false);

    // AI 协同操作历史（O5-B29/F15）
    const [audit, setAudit] = useState<CopilotAuditRow[]>([]);
    const [auditLoading, setAuditLoading] = useState(false);
    const loadAudit = useCallback(async () => {
        setAuditLoading(true);
        try {
            setAudit(await listCopilotAudit(projectId));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载 AI 协同历史失败');
        } finally {
            setAuditLoading(false);
        }
    }, [projectId]);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await versionsApi.list(projectId);
            setVersions(r.items || []);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载版本失败');
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    useEffect(() => { load(); }, [load]);

    const loadGate = useCallback(async () => {
        setGateLoading(true);
        try {
            const [g, imp] = await Promise.all([
                gateApi.evaluations(projectId, 20),
                impactApi.preview(projectId).catch(() => null),
            ]);
            setGates(g.items || []);
            setImpact(imp);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载门控记录失败');
        } finally {
            setGateLoading(false);
        }
    }, [projectId]);

    const options = versions.map((v) => ({ value: v.version_no, label: `v${v.version_no} ${KIND_META[v.kind]?.label || v.kind}` }));

    const loadDiff = async (a: number, b: number) => {
        setDiffLoading(true);
        try {
            const d = await versionsApi.diff(projectId, a, b);
            setDiff(d);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || 'diff 失败（快照缺失？）');
            setDiff(null);
        } finally {
            setDiffLoading(false);
        }
    };

    const handleRestore = (v: VersionRow) => {
        Modal.confirm({
            title: `回滚到 v${v.version_no}`,
            content: (
                <div>
                    <p>将自动保存当前状态的 pre_rollback 快照，再把画布恢复到 v{v.version_no}。</p>
                    <p className="text-gray-500 text-sm">输入版本号 {v.version_no} 以确认：</p>
                    <Input id="restore-confirm-input" placeholder={String(v.version_no)} />
                </div>
            ),
            okText: '回滚',
            okButtonProps: { danger: true },
            onOk: async () => {
                const el = document.getElementById('restore-confirm-input') as HTMLInputElement | null;
                if (el?.value !== String(v.version_no)) {
                    message.error('确认版本号不匹配');
                    return Promise.reject();
                }
                try {
                    const r = await versionsApi.restore(projectId, v.version_no);
                    message.success(`已回滚：新版本 v${r.new_version_no}（kind=rollback）`);
                    load();
                    onChanged?.();
                } catch (e: any) {
                    message.error(e.response?.data?.error?.message || '回滚失败');
                }
            },
        });
    };

    const searchEntity = async () => {
        setEntityLoading(true);
        try {
            const r = await versionsApi.timeline(projectId, {
                entity_uri: entityUri.trim() || undefined,
                time_axis: axis,
            });
            setEvents(r.events || []);
            if ((r.events || []).length === 0) message.info('无匹配事件');
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '查询失败');
        } finally {
            setEntityLoading(false);
        }
    };

    const diffSections = useMemo(() => {
        if (!diff) return [];
        return [
            { key: 'classes', title: '类', data: diff.classes },
            { key: 'properties', title: '对象属性', data: diff.properties },
            { key: 'instances', title: '实例', data: diff.instances },
            { key: 'relations', title: '关系', data: diff.relations },
        ];
    }, [diff]);

    return (
        <Card
            title="时间轴"
            extra={
                <Space>
                    <Segmented
                        value={subTab}
                        onChange={(v) => {
                            setSubTab(v as any);
                            if (v === 'gate') loadGate();
                            if (v === 'audit') loadAudit();
                        }}
                        options={[
                            { value: 'versions', label: '版本时间轴' },
                            { value: 'entity', label: '实体时间线' },
                            { value: 'gate', label: '发布门控' },
                            { value: 'audit', label: 'AI 协同' },
                        ]}
                    />
                    <Button icon={<SearchOutlined />} onClick={load}>刷新</Button>
                </Space>
            }
        >
            {subTab === 'versions' && (
                <Spin spinning={loading}>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <div>
                            <div className="mb-3 flex items-center gap-2">
                                <span className="text-gray-600">对比：</span>
                                <Select style={{ width: 160 }} placeholder="版本 A" value={selA ?? undefined}
                                        options={options} onChange={(v) => setSelA(v)} />
                                <span>→</span>
                                <Select style={{ width: 160 }} placeholder="版本 B" value={selB ?? undefined}
                                        options={options} onChange={(v) => setSelB(v)} />
                                <Button type="primary" size="small" disabled={!selA || !selB || selA === selB}
                                        onClick={() => loadDiff(selA!, selB!)}>对比</Button>
                            </div>
                            <AntTimeline
                                items={versions.map((v) => ({
                                    color: KIND_META[v.kind]?.color || 'gray',
                                    children: (
                                        <div className="pb-2">
                                            <Space wrap>
                                                <span className="font-medium">v{v.version_no}</span>
                                                <Tag color={KIND_META[v.kind]?.color}>{KIND_META[v.kind]?.label || v.kind}</Tag>
                                                {v.is_current && <Tag color="gold">当前</Tag>}
                                                <span className="text-xs text-gray-500">
                                                    {v.created_at ? v.created_at.slice(0, 19).replace('T', ' ') : ''}
                                                </span>
                                            </Space>
                                            <div className="text-sm text-gray-700">{v.label || ''}</div>
                                            <div className="text-xs text-gray-500">
                                                {v.stats
                                                    ? `类 ${v.stats.classes ?? 0} · 属性 ${v.stats.properties ?? 0} · 实例 ${v.stats.instances ?? 0} · 关系 ${v.stats.relations ?? 0}`
                                                    : v.description || ''}
                                                {` · checksum ${String(v.checksum).slice(0, 8)}`}
                                            </div>
                                            <Button size="small" icon={<RollbackOutlined />} className="mt-1"
                                                    onClick={() => handleRestore(v)}>回滚到此版</Button>
                                        </div>
                                    ),
                                }))}
                            />
                            {versions.length === 0 && <Empty description="暂无版本；抽取/发布会自动落版" />}
                        </div>
                        <div>
                            {!diff && <Empty description="选择两个版本进行对比" />}
                            {diffLoading && <Spin />}
                            {diff && (
                                <div className="space-y-4">
                                    {diffSections.map(({ key, title, data }) => {
                                        const added = (data as any)?.added || [];
                                        const removed = (data as any)?.removed || [];
                                        const modified = (data as any)?.modified || [];
                                        if (!added.length && !removed.length && !modified.length) return null;
                                        const isInstances = key === 'instances';
                                        const isRelations = key === 'relations';
                                        const isClasses = key === 'classes';
                                        const isProps = key === 'properties';
                                        return (
                                            <div key={key} className="border rounded p-3">
                                                <SectionHeader title={title} added={added.length} removed={removed.length} modified={modified.length} />
                                                {isInstances ? (
                                                    <div className="space-y-2">
                                                        {added.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-green-700 mb-1">新增实例（按类别分组）</div>
                                                                <InstanceGroupLines items={added} color="success" />
                                                            </div>
                                                        )}
                                                        {removed.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-red-700 mb-1">删除实例（按类别分组）</div>
                                                                <InstanceGroupLines items={removed} color="error" />
                                                            </div>
                                                        )}
                                                        {modified.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-amber-700 mb-1">属性/类别变化</div>
                                                                <ModifiedLines mods={modified} />
                                                            </div>
                                                        )}
                                                    </div>
                                                ) : isRelations ? (
                                                    <div className="space-y-2">
                                                        {added.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-green-700 mb-1">新增关系</div>
                                                                <RelationLines rows={added} color="success" />
                                                            </div>
                                                        )}
                                                        {removed.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-red-700 mb-1">删除关系</div>
                                                                <RelationLines rows={removed} color="error" />
                                                            </div>
                                                        )}
                                                    </div>
                                                ) : isClasses || isProps ? (
                                                    <div className="space-y-2">
                                                        {added.length > 0 && <TagWall words={added} color="success" />}
                                                        {removed.length > 0 && <TagWall words={removed} color="error" />}
                                                        {modified.length > 0 && (
                                                            <div>
                                                                <div className="text-xs text-amber-700 mb-1">
                                                                    {isProps ? '定义域/值域变化' : '类定义变化'}
                                                                </div>
                                                                {isClasses ? (
                                                                    <ClassModifiedLines mods={modified} />
                                                                ) : (
                                                                    <TagWall words={modified.map((m: any) => `${m.label}：${m.from || '—'} → ${m.to || '—'}`)} color="warning" />
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <div className="space-y-2">
                                                        {added.length > 0 && <TagWall words={added} color="success" />}
                                                        {removed.length > 0 && <TagWall words={removed} color="error" />}
                                                        {modified.length > 0 && (
                                                            <TagWall words={modified.map((m: any) => `${m.label}（定义调整）`)} color="warning" />
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                </Spin>
            )}

            {subTab === 'gate' && (
                <Spin spinning={gateLoading}>
                    <Alert
                        type="info" showIcon className="mb-4"
                        message="发布前系统会拿「评测问题（CQ）」给新旧版本匿名对答：新版本出现硬伤或负场多于胜场时拒绝发布，旧版本继续生效。"
                        description="维护 CQ 请前往「骨架编辑 → 评测问题」；发布时的影响分析也会在这里留痕。"
                    />

                    {/* 影响分析数字（相对最新正式基线） */}
                    <div className="mb-4">
                        <div className="font-medium mb-2">发布影响分析预览</div>
                        {impact ? (
                            <Descriptions size="small" bordered column={4}>
                                <Descriptions.Item label="基线版本">{impact.has_baseline ? `#${impact.baseline_version_id}` : '无（首次发布落基线）'}</Descriptions.Item>
                                <Descriptions.Item label="类变化">
                                    +{impact.diff.classes.added} / −{impact.diff.classes.removed} / ~{impact.diff.classes.modified}
                                </Descriptions.Item>
                                <Descriptions.Item label="关系变化">
                                    +{impact.diff.relations.added} / −{impact.diff.relations.removed} / ~{impact.diff.relations.modified}
                                </Descriptions.Item>
                                <Descriptions.Item label="实例变化">
                                    +{impact.diff.instances.added} / −{impact.diff.instances.removed} / ~{impact.diff.instances.modified}
                                </Descriptions.Item>
                                <Descriptions.Item label="波及实例">{impact.affected.instances}</Descriptions.Item>
                                <Descriptions.Item label="波及关系">{impact.affected.relations}</Descriptions.Item>
                                <Descriptions.Item label="待审核项">
                                    <span className={impact.affected.pending_reviews > 0 ? 'text-red-600 font-medium' : ''}>
                                        {impact.affected.pending_reviews}
                                    </span>
                                </Descriptions.Item>
                                <Descriptions.Item label="骨架变更待复核">
                                    {impact.drift?.length
                                        ? <Tooltip title={impact.drift.map((d) => `${d.label}×${d.instance_count}`).join('、')}>{impact.drift.length} 类</Tooltip>
                                        : '无'}
                                </Descriptions.Item>
                            </Descriptions>
                        ) : (
                            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无影响分析（可能尚无基线版本）" />
                        )}
                    </div>

                    {/* 门控记录（可审计） */}
                    <div className="font-medium mb-2">门控评估记录（{gates.length}）</div>
                    {gates.length === 0 && !gateLoading && (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无门控记录；发布（配置了启用 CQ 时）会自动产生" />
                    )}
                    <div className="space-y-3">
                        {gates.map((g) => {
                            const vm = GATE_VERDICT[g.verdict] || { color: 'default', text: g.verdict };
                            return (
                                <div key={g.id} className="border rounded p-3">
                                    <Space wrap className="mb-2">
                                        <Tag color={vm.color}>{vm.text}</Tag>
                                        <Tag color="geekblue">{g.mode === 'gt' ? '绝对评分' : '匿名 A/B'}</Tag>
                                        <span className="text-sm">胜 <b className="text-green-600">{g.wins}</b> ·
                                            平 <b>{g.ties}</b> · 负 <b className="text-red-600">{g.losses}</b> ·
                                            硬伤 <b className={g.hard_failures > 0 ? 'text-red-600' : ''}>{g.hard_failures}</b>
                                        </span>
                                        {g.unacceptable_regressions && <Tag color="red">零容忍模式</Tag>}
                                        {g.candidate_version_id && <Tag>候选 #{g.candidate_version_id}</Tag>}
                                        <span className="text-xs text-gray-500">
                                            {g.created_at ? String(g.created_at).slice(0, 19).replace('T', ' ') : ''}
                                        </span>
                                    </Space>
                                    {g.results?.length > 0 && (
                                        <Collapse
                                            size="small"
                                            items={[{
                                                key: 'detail',
                                                label: `逐题明细（${g.results.length} 条）`,
                                                children: (
                                                    <Table
                                                        size="small"
                                                        rowKey={(r: any, i) => `${r.cq_id}-${i}`}
                                                        dataSource={g.results}
                                                        pagination={false}
                                                        columns={[
                                                            { title: '问题', dataIndex: 'question', ellipsis: true },
                                                            {
                                                                title: '结论', dataIndex: 'verdict', width: 96,
                                                                render: (v: string, r: any) => (
                                                                    <Space size={4}>
                                                                        <Tag color={RESULT_VERDICT[v]?.color || 'default'}>
                                                                            {RESULT_VERDICT[v]?.text || v || '—'}
                                                                        </Tag>
                                                                        {r.hard_failure && <Tag color="red">硬伤</Tag>}
                                                                    </Space>
                                                                ),
                                                            },
                                                            {
                                                                title: '依据', key: 'why', ellipsis: true,
                                                                render: (_: any, r: any) => (r.judge_reason
                                                                    || (typeof r.delta === 'number'
                                                                        ? `覆盖率 ${r.baseline_score} → ${r.candidate_score}（Δ${r.delta > 0 ? '+' : ''}${r.delta}）`
                                                                        : '—')),
                                                            },
                                                        ] as any}
                                                    />
                                                ),
                                            }]}
                                        />
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <div className="mt-3">
                        <Button size="small" icon={<ThunderboltOutlined />} onClick={loadGate}>刷新门控记录</Button>
                    </div>
                </Spin>
            )}

            {subTab === 'entity' && (
                <div>
                    <Space wrap className="mb-4">                        <Input
                            style={{ width: 380 }}
                            placeholder="实体 URI（留空=全部实体）；可从实例详情复制"
                            value={entityUri}
                            onChange={(e) => setEntityUri(e.target.value)}
                            onPressEnter={searchEntity}
                        />
                        <Segmented
                            value={axis}
                            onChange={(v) => setAxis(v as any)}
                            options={[
                                { value: 'both', label: '双轴' },
                                { value: 'transaction', label: '事务时间' },
                                { value: 'valid', label: '业务时间' },
                            ]}
                        />
                        <Button type="primary" loading={entityLoading} onClick={searchEntity}>查询</Button>
                    </Space>
                    <AntTimeline
                        items={events.map((e) => ({
                            color: e.axis === 'valid' ? 'purple' : 'blue',
                            children: (
                                <div>
                                    <span className="font-medium">{String(e.time).slice(0, 19).replace('T', ' ')}</span>
                                    <Space className="ml-2">
                                        <Tag>{e.axis === 'valid' ? '业务' : '事务'}</Tag>
                                        <Tag color="cyan">{e.type}</Tag>
                                    </Space>
                                    <span className="ml-2 text-gray-700">{e.label}</span>
                                    <span className="ml-2 text-xs text-gray-500">{e.uri}</span>
                                </div>
                            ),
                        }))}
                    />
                    {events.length === 0 && !entityLoading && <Empty description="查询实体时间线" />}
                </div>
            )}

            {subTab === 'audit' && (
                <Spin spinning={auditLoading}>
                    <div className="mb-3 flex items-center gap-2">
                        <span className="text-sm text-gray-600">
                            AI 协同操作历史：谁、何时、通过哪个会话、依据什么证据改了什么（含提案 diff 摘要与 manual 版本号）
                        </span>
                        <Button size="small" icon={<SearchOutlined />} onClick={loadAudit}>刷新</Button>
                    </div>
                    {audit.length === 0 && !auditLoading ? (
                        <Empty description="暂无 AI 协同操作记录" />
                    ) : (
                        <AntTimeline
                            items={audit.map((a) => {
                                const meta = AUDIT_ACTION[a.action] ?? { color: 'default', text: a.action };
                                const summarize = AUDIT_SUMMARY[a.action];
                                return {
                                    color: meta.color === 'green' ? 'green' : meta.color === 'purple' ? 'purple' : 'blue',
                                    children: (
                                        <div>
                                            <span className="font-medium">{String(a.created_at ?? '').slice(0, 19).replace('T', ' ')}</span>
                                            <Space className="ml-2">
                                                <Tag color={meta.color}>{meta.text}</Tag>
                                            </Space>
                                            <span className="ml-2 text-gray-700">{a.actor ?? `用户#${a.actor_id ?? '-'}`}</span>
                                            <span className="ml-2 text-xs text-gray-500">{summarize ? summarize(a.detail ?? {}) : ''}</span>
                                        </div>
                                    ),
                                };
                            })}
                        />
                    )}
                </Spin>
            )}
        </Card>
    );
};

export default TimelineTab;
