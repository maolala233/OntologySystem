// src/components/Copilot/SuggestionCard.tsx - 首轮诊断建议卡（O5-B27/F14，docs/design/14 §5）
// 诊断来源徽标 + LLM 理由 + 证据展开 + 转审核项/忽略；applied/rejected 后展示终态。
import { useState } from 'react';
import { Button, Tag, Tooltip, message } from 'antd';
import {
    AuditOutlined,
    BulbOutlined,
    CloseOutlined,
    DownOutlined,
    RightOutlined,
} from '@ant-design/icons';
import {
    convertCopilotSuggestion,
    rejectCopilotProposal,
} from '../../api/copilot';
import type { CopilotSuggestionItem } from '../../api/copilot';

const PRIORITY_STYLE: Record<string, { color: string; text: string }> = {
    high: { color: 'red', text: '高优先' },
    medium: { color: 'orange', text: '中优先' },
    low: { color: 'default', text: '低优先' },
};

const TRIGGER_TEXT: Record<string, string> = {
    schema_extract: '骨架抽取后诊断',
    instance_extract: '实例抽取后诊断',
    manual: '手动诊断',
};

interface Props {
    projectId: number;
    item: CopilotSuggestionItem;
    onChanged?: (status: string, result?: Record<string, unknown>) => void;
}

export default function SuggestionCard({ projectId, item, onChanged }: Props) {
    const [busy, setBusy] = useState(false);
    const [showEvidence, setShowEvidence] = useState(false);

    const title = String(item.targets?.title ?? '诊断建议');
    const priority = String(item.targets?.priority ?? 'medium');
    const trigger = String(item.targets?.trigger ?? '');
    const reviewKind = String(item.targets?.review_kind ?? 'none');
    const pStyle = PRIORITY_STYLE[priority] ?? PRIORITY_STYLE.medium;

    const doConvert = async () => {
        if (busy) return;
        setBusy(true);
        try {
            const res = await convertCopilotSuggestion(projectId, item.proposal_id);
            message.success(`已转审核项 #${res.review_item_id}，可在「审核工作台」裁决`);
            onChanged?.('applied', { converted_review_item_id: res.review_item_id });
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '转审核项失败');
        } finally {
            setBusy(false);
        }
    };

    const doReject = async () => {
        if (busy) return;
        setBusy(true);
        try {
            await rejectCopilotProposal(projectId, item.proposal_id);
            message.success('已忽略该建议');
            onChanged?.('rejected');
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '忽略失败');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="border border-amber-100 rounded-lg p-2.5 bg-amber-50/40">
            <div className="flex items-center gap-1.5 flex-wrap">
                <BulbOutlined className="text-amber-500" />
                <span className="text-sm font-medium flex-1">{title}</span>
                <Tag color={pStyle.color} className="!m-0">{pStyle.text}</Tag>
            </div>
            <div className="flex items-center gap-1 mt-1">
                <Tooltip title={trigger ? `诊断来源：${TRIGGER_TEXT[trigger] ?? trigger}` : '诊断来源：AI 首轮诊断'}>
                    <Tag icon={<AuditOutlined />} color="purple" className="!m-0 !text-[10px]">
                        {TRIGGER_TEXT[trigger] ?? 'AI 首轮诊断'}
                    </Tag>
                </Tooltip>
                {reviewKind !== 'none' && reviewKind !== '' && (
                    <Tag className="!m-0 !text-[10px]">建议动作：{reviewKind}</Tag>
                )}
            </div>
            <p className="text-xs text-gray-600 mt-1.5 whitespace-pre-wrap">{item.llm_reason}</p>
            {(item.evidence?.length ?? 0) > 0 && (
                <div className="mt-1">
                    <button className="text-[11px] text-blue-600 hover:underline flex items-center gap-0.5"
                            onClick={() => setShowEvidence(v => !v)}>
                        {showEvidence ? <DownOutlined /> : <RightOutlined />}
                        证据（{item.evidence.length}）
                    </button>
                    {showEvidence && (
                        <ul className="mt-1 pl-4 list-disc text-[11px] text-gray-500 space-y-0.5">
                            {item.evidence.map((ev, i) => <li key={i}>{ev}</li>)}
                        </ul>
                    )}
                </div>
            )}
            <div className="mt-2 flex items-center gap-2">
                {item.status === 'pending' ? (
                    <>
                        <Button size="small" type="primary" icon={<AuditOutlined />}
                                loading={busy} onClick={doConvert}>转审核项</Button>
                        <Button size="small" icon={<CloseOutlined />} disabled={busy}
                                onClick={doReject}>忽略</Button>
                    </>
                ) : item.status === 'applied' ? (
                    <Tag color="green" className="!m-0">已转审核项{item.result?.converted_review_item_id != null ? ` #${item.result.converted_review_item_id}` : ''}</Tag>
                ) : item.status === 'rejected' ? (
                    <Tag className="!m-0">已忽略</Tag>
                ) : (
                    <Tag className="!m-0">已处理</Tag>
                )}
            </div>
        </div>
    );
}
