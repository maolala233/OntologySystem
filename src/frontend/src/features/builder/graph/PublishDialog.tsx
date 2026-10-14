/**
 * F9 发布预检弹窗（09 §2）：发布前必看「影响分析数字 + 最新门控结论」；
 * 最新门控未通过且图谱此后无新落版时，发布按钮禁用并显示原因；
 * 发布请求可显式声明 unacceptable_regressions（B14 契约）；被拒时展示失败明细。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
    Alert, Button, Checkbox, Divider, Modal, Space, Spin, Table, Tag, Tooltip, message,
} from 'antd';
import {
    CheckCircleOutlined, CloseCircleOutlined, MinusCircleOutlined, SafetyOutlined,
} from '@ant-design/icons';

import { GateEvaluationRow, ImpactSummary, gateApi, impactApi } from '../../../api/cqs';
import { publishApi, versionsApi } from '../../../api/governance';

const VERDICT_META = {
    pass: { color: 'success', icon: <CheckCircleOutlined />, text: '通过' },
    reject: { color: 'error', icon: <CloseCircleOutlined />, text: '未通过' },
} as const;

const RESULT_VERDICT_TAG = {
    win: { color: 'success', text: '候选胜' },
    loss: { color: 'error', text: '候选负' },
    tie: { color: 'default', text: '平局' },
} as const;

interface Props {
    projectId: number;
    open: boolean;
    onClose: () => void;
    onPublished: () => void;
}

const PublishDialog: React.FC<Props> = ({ projectId, open, onClose, onPublished }) => {
    const [loading, setLoading] = useState(false);
    const [impact, setImpact] = useState<ImpactSummary | null>(null);
    const [evaluations, setEvaluations] = useState<GateEvaluationRow[]>([]);
    const [latestVersionAt, setLatestVersionAt] = useState<string | null>(null);
    const [unacceptableRegressions, setUnacceptableRegressions] = useState(false);
    const [publishing, setPublishing] = useState(false);
    const [publishError, setPublishError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!open) return;
        setLoading(true);
        setPublishError(null);
        try {
            const [imp, gates, versionsResp] = await Promise.all([
                impactApi.preview(projectId).catch(() => null),
                gateApi.evaluations(projectId, 5).catch(() => ({ items: [], total: 0 })),
                // 版本列表用于判断「门控被拒后图谱是否有新落版」——有则允许重试发布（会重新评估）
                versionsApi.list(projectId).catch(() => null),
            ]);
            setImpact(imp);
            setEvaluations(gates.items || []);
            const versions = versionsResp?.items || [];
            setLatestVersionAt(versions[0]?.created_at || null);
        } finally {
            setLoading(false);
        }
    }, [projectId, open]);

    useEffect(() => { if (open) load(); }, [open, load]);

    const latest = evaluations[0] || null;
    // 门控拦截：最新结论是 reject，且门控之后没有任何新落版（改了图谱也没用，必须落版）
    const gateBlocked = !!latest && latest.verdict === 'reject'
        && (!latestVersionAt || !String(latest.created_at || '')
            || String(latestVersionAt) <= String(latest.created_at));

    const handlePublish = async () => {
        setPublishing(true);
        setPublishError(null);
        try {
            const r = await publishApi.publish(projectId, { unacceptable_regressions: unacceptableRegressions } as any);
            const warn = (r as any).warnings?.length ? `（${(r as any).warnings.length} 条警告）` : '';
            message.success(`发布成功：v${r.version_no}${warn}`);
            onPublished();
            onClose();
        } catch (e: any) {
            const err = e.response?.data?.error || e.response?.data;
            if (err?.code === 'GATE_REJECTED') {
                const d = err.detail || {};
                setPublishError(
                    `发布门控未通过：胜 ${d.wins ?? '—'} / 平 ${d.ties ?? '—'} / 负 ${d.losses ?? '—'}，`
                    + `硬伤 ${d.hard_failures ?? '—'}。旧版本保持生效；修复图谱并重新落版后可重试。`);
            } else {
                setPublishError(err?.message || '发布失败，请检查发布条件（知识域、高优先级审核项、TBox 机检、证据闭合）');
            }
            load();
        } finally {
            setPublishing(false);
        }
    };

    const diffRows = impact ? [
        { key: 'classes', label: '类', ...(impact.diff.classes) },
        { key: 'properties', label: '属性', ...(impact.diff.properties) },
        { key: 'instances', label: '实例', ...(impact.diff.instances) },
        { key: 'relations', label: '关系', ...(impact.diff.relations) },
    ] : [];

    return (
        <Modal
            title="发布到资产中心 · 预检"
            open={open}
            onCancel={onClose}
            width={720}
            footer={[
                <Button key="cancel" onClick={onClose}>取消</Button>,
                <Tooltip key="publish" title={gateBlocked
                    ? `最新门控未通过（${latest ? `胜 ${latest.wins}/平 ${latest.ties}/负 ${latest.losses}，硬伤 ${latest.hard_failures}` : ''}）；修复后需先重新落版（抽取或手动打版），再回来发布`
                    : ''}>
                    <Button key="ok" type="primary" loading={publishing}
                            disabled={gateBlocked} onClick={handlePublish}>
                        确认发布
                    </Button>
                </Tooltip>,
            ]}
        >
            <Spin spinning={loading}>
                {publishError && (
                    <Alert type="error" showIcon className="mb-3" message="发布被拦截" description={publishError} />
                )}

                {/* 1. 影响分析数字（O3-B13） */}
                <div className="mb-1 font-medium">发布影响分析</div>
                {!impact && <div className="text-gray-500 text-sm mb-3">影响分析不可用（可能无基线版本或服务异常）</div>}
                {impact && (
                    <div className="mb-3">
                        {!impact.has_baseline && (
                            <Tag color="gold" className="mb-2">无正式基线版本：本次为首次落版基线</Tag>
                        )}
                        <table className="w-full text-sm border rounded">
                            <thead>
                                <tr className="bg-gray-50 text-gray-600">
                                    <th className="text-left px-3 py-1.5 font-normal">对象</th>
                                    <th className="text-right px-3 py-1.5 font-normal">新增</th>
                                    <th className="text-right px-3 py-1.5 font-normal">删除</th>
                                    <th className="text-right px-3 py-1.5 font-normal">变化</th>
                                </tr>
                            </thead>
                            <tbody>
                                {diffRows.map((r) => (
                                    <tr key={r.key} className="border-t">
                                        <td className="px-3 py-1.5">{r.label}</td>
                                        <td className="px-3 py-1.5 text-right text-green-600">+{r.added}</td>
                                        <td className="px-3 py-1.5 text-right text-red-600">−{r.removed}</td>
                                        <td className="px-3 py-1.5 text-right text-amber-600">~{r.modified}</td>
                                    </tr>
                                ))}
                                <tr className="border-t bg-gray-50">
                                    <td className="px-3 py-1.5" colSpan={4}>
                                        波及：实例 <b>{impact.affected.instances}</b> 条 ·
                                        关系 <b>{impact.affected.relations}</b> 条 ·
                                        待审核项 <b className={impact.affected.pending_reviews > 0 ? 'text-red-600' : ''}>{impact.affected.pending_reviews}</b> 个
                                        {impact.drift?.length > 0 && (
                                            <span> · TBox 漂移 <b className="text-amber-600">{impact.drift.length}</b> 类（已物化为审核项）</span>
                                        )}
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}

                <Divider className="my-3" />

                {/* 2. 最新门控结论（O3-B14） */}
                <div className="mb-2 flex items-center gap-2">
                    <SafetyOutlined />
                    <span className="font-medium">CQ 配对门控结论</span>
                    {latest ? (
                        <Tag color={VERDICT_META[latest.verdict].color} icon={VERDICT_META[latest.verdict].icon}>
                            {VERDICT_META[latest.verdict].text}
                        </Tag>
                    ) : (
                        <Tag>暂无记录</Tag>
                    )}
                </div>
                {latest ? (
                    <div className="text-sm mb-2">
                        <Space wrap>
                            <span>胜 <b className="text-green-600">{latest.wins}</b></span>
                            <span>平 <b>{latest.ties}</b></span>
                            <span>负 <b className="text-red-600">{latest.losses}</b></span>
                            <span>硬伤 <b className={latest.hard_failures > 0 ? 'text-red-600' : ''}>{latest.hard_failures}</b></span>
                            <Tag color="geekblue">{latest.mode === 'gt' ? '绝对评分（有判定标准）' : '匿名 A/B（LLM Judge）'}</Tag>
                            <span className="text-xs text-gray-500">
                                {latest.created_at ? String(latest.created_at).slice(0, 19).replace('T', ' ') : ''}
                            </span>
                        </Space>
                        {latest.verdict === 'reject' && (
                            <div className="mt-2">
                                <Table
                                    size="small"
                                    rowKey={(r: any, i) => `${r.cq_id}-${i}`}
                                    dataSource={latest.results || []}
                                    pagination={false}
                                    columns={[
                                        { title: '问题', dataIndex: 'question', ellipsis: true },
                                        {
                                            title: '结论', dataIndex: 'verdict', width: 90,
                                            render: (v: string, r: any) => (
                                                <Space size={4}>
                                                    <Tag color={RESULT_VERDICT_TAG[v as keyof typeof RESULT_VERDICT_TAG]?.color || 'default'}>
                                                        {RESULT_VERDICT_TAG[v as keyof typeof RESULT_VERDICT_TAG]?.text || v}
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
                            </div>
                        )}
                        {gateBlocked && (
                            <Alert
                                type="error" showIcon className="mt-2"
                                message="发布按钮已禁用"
                                description="最新门控未通过，且门控之后没有新的版本落版。请修复图谱问题后重新抽取（或手动打版），系统会在发布时重新评估。"
                            />
                        )}
                    </div>
                ) : (
                    <div className="text-sm text-gray-600 mb-2">
                        项目配置了启用中的 CQ 时，发布将自动运行配对门控：新版本答得不如旧版本（出现硬伤或负场更多），发布会被拒绝。
                        在「骨架编辑 → 评测问题」中维护 CQ。
                    </div>
                )}

                <Divider className="my-3" />

                <Checkbox
                    checked={unacceptableRegressions}
                    onChange={(e) => setUnacceptableRegressions(e.target.checked)}
                >
                    零容忍模式：出现任何 1 场负局即拒绝发布
                    <span className="text-xs text-gray-500">（默认仅要求「零硬伤 + 胜场严格多于负场」）</span>
                </Checkbox>

                <div className="mt-3 text-xs text-gray-500">
                    <MinusCircleOutlined className="mr-1" />
                    门控评估在点击发布时执行并生成候选版本 vN-cK；候选不会覆盖正式版本，被拒时旧版本继续生效。
                </div>
            </Spin>
        </Modal>
    );
};

export default PublishDialog;
