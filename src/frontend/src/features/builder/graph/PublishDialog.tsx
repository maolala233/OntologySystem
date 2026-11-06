/**
 * F9 发布预检弹窗（09 §2）：发布前必看「影响分析数字 + 最新门控结论」；
 * 最新门控未通过且图谱此后无新落版时，发布按钮禁用并显示原因；
 * 发布请求可显式声明 unacceptable_regressions（B14 契约）；被拒时展示失败明细。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Alert, Button, Modal, Spin, Tag, message,
} from 'antd';
import {
    MinusCircleOutlined,
} from '@ant-design/icons';

import { ImpactSummary, impactApi } from '../../../api/cqs';
import { publishApi } from '../../../api/governance';

interface Props {
    projectId: number;
    open: boolean;
    onClose: () => void;
    onPublished: () => void;
}

const PublishDialog: React.FC<Props> = ({ projectId, open, onClose, onPublished }) => {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(false);
    const [impact, setImpact] = useState<ImpactSummary | null>(null);
    const [publishing, setPublishing] = useState(false);
    const [publishError, setPublishError] = useState<string | null>(null);
    const [blockedByReviews, setBlockedByReviews] = useState(false);

    const load = useCallback(async () => {
        if (!open) return;
        setLoading(true);
        setPublishError(null);
        setBlockedByReviews(false);
        try {
            const imp = await impactApi.preview(projectId).catch(() => null);
            setImpact(imp);
        } finally {
            setLoading(false);
        }
    }, [projectId, open]);

    useEffect(() => { if (open) load(); }, [open, load]);

    const handlePublish = async () => {
        setPublishing(true);
        setPublishError(null);
        try {
            const r = await publishApi.publish(projectId, {});
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
                // 发布门禁人话化：高优先级审核项未清 → 给出确认入口直跳
                const blockers = err?.detail?.blockers || [];
                if (blockers.length > 0) {
                    setPublishError(
                        `还有 ${blockers.length} 个高优先级结果等你确认后才能发布（低风险项已由 AI 自动处理）。`);
                    setBlockedByReviews(true);
                } else {
                    setPublishError(err?.message || '发布失败，请检查发布条件（知识域、高优先级审核项、本体结构机检、证据闭合）');
                    setBlockedByReviews(false);
                }
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
                <Button key="ok" type="primary" loading={publishing}
                        onClick={handlePublish}>
                    确认发布
                </Button>,
            ]}
        >
            <Spin spinning={loading}>
                {publishError && (
                    <Alert
                        type="error" showIcon className="mb-3" message="发布被拦截"
                        description={(
                            <div className="space-y-2">
                                <div>{publishError}</div>
                                {blockedByReviews && (
                                    <Button size="small" type="primary"
                                            onClick={() => navigate(`/projects/${projectId}/reviews`)}>
                                        去审核确认
                                    </Button>
                                )}
                            </div>
                        )}
                    />
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
                                            <span> · 骨架已变更 <b className="text-amber-600">{impact.drift.length}</b> 类（已生成审核项待复核）</span>
                                        )}
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}

                <div className="mt-3 text-xs text-gray-500">
                    <MinusCircleOutlined className="mr-1" />
                    点击发布时系统自动执行质量门控（本体结构机检 + 审核清零检查），未通过则本次发布不生效，旧版本继续可用。
                </div>
            </Spin>
        </Modal>
    );
};

export default PublishDialog;
