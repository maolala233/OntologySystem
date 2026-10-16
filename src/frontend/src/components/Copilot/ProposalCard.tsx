// src/components/Copilot/ProposalCard.tsx - diff 提案卡（O5-F13，docs/design/14 §5 S2）
// 渲染服务端确定性 diff（红删/绿增/蓝改）、影响面数字、高风险黄标；
// 按钮：应用（高风险二次确认）/ 忽略 / 反问调整。应用成功广播画布刷新（manual 版本可回滚）。
import { useState } from 'react';
import { Button, Popconfirm, Tag, message } from 'antd';
import {
    CheckOutlined,
    CloseOutlined,
    EditOutlined,
    ExclamationCircleOutlined,
    QuestionCircleOutlined,
} from '@ant-design/icons';
import {
    applyCopilotProposal,
    rejectCopilotProposal,
    type CopilotDiffEntry,
    type CopilotProposalItem,
} from '../../api/copilot';
import { notifyCanvasRefresh } from '../../shared/copilotBus';

const KIND_LABEL: Record<string, string> = {
    merge_classes: '合并类',
    delete_class: '删除类',
    add_class: '新建类',
    add_relation: '新增关系',
    edit_relation: '修改关系',
    delete_instance: '删除实例',
    edit_instance: '修改实例',
    edit_class: '编辑类属性',
    add_instance: '新增实例',
    declare_axiom: '声明公理',
};

const OP_STYLE: Record<string, { bg: string; badge: string; text: string }> = {
    remove: { bg: 'bg-red-50 border-red-100', badge: 'bg-red-500 text-white', text: 'text-red-700' },
    add: { bg: 'bg-green-50 border-green-100', badge: 'bg-green-500 text-white', text: 'text-green-700' },
    update: { bg: 'bg-blue-50 border-blue-100', badge: 'bg-blue-500 text-white', text: 'text-blue-700' },
    merge_into: { bg: 'bg-purple-50 border-purple-100', badge: 'bg-purple-500 text-white', text: 'text-purple-700' },
};
const OP_TEXT: Record<string, string> = {
    remove: '删', add: '增', update: '改', merge_into: '并',
};

function DiffRow({ d }: { d: CopilotDiffEntry }) {
    const style = OP_STYLE[d.op] || { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-400 text-white', text: 'text-gray-700' };
    const cascade = d.cascade
        ? [
            Number.isFinite(d.cascade.instances) ? `级联实例 ${d.cascade.instances}` : '',
            Number.isFinite(d.cascade.relations) ? `关系 ${d.cascade.relations}` : '',
        ].filter(Boolean).join(' · ')
        : '';
    const cascadeText = cascade ? `（${cascade}）` : '';
    return (
        <div className={`flex items-start gap-1.5 px-2 py-1 rounded border text-xs ${style.bg}`}>
            <span className={`shrink-0 w-4 text-center rounded-sm text-[10px] leading-4 ${style.badge}`}>
                {OP_TEXT[d.op] || d.op}
            </span>
            <span className="flex-1 leading-4">
                <span className={`font-medium ${style.text}`}>{d.label || d.type || '—'}</span>
                {d.detail && <span className="text-gray-500 ml-1">{d.detail}</span>}
                {cascadeText && <span className="text-orange-500 ml-1">{cascadeText}</span>}
            </span>
        </div>
    );
}

interface Props {
    projectId: number;
    proposal: CopilotProposalItem;
    /** 反问调整：把草稿填入输入框 */
    onAdjust?: (draft: string) => void;
    /** 状态变更后回传小窗（更新本地消息态） */
    onStatusChange?: (status: string, result?: Record<string, unknown>) => void;
}

export default function ProposalCard({ projectId, proposal, onAdjust, onStatusChange }: Props) {
    const [status, setStatus] = useState(proposal.status);
    const [result, setResult] = useState<Record<string, unknown> | null>(
        (proposal.result as Record<string, unknown>) || null);
    const [applying, setApplying] = useState(false);
    const high = proposal.risk_tier === 'high';
    const kindLabel = KIND_LABEL[proposal.kind] || proposal.kind;
    const impact = proposal.impact;
    const hasCascade = impact && (impact.instances > 0 || impact.relations > 0);
    // 警告已由下方琥珀块渲染时，理由里持久化的「【注意】…」尾巴不再重复展示
    const liveWarnings = (proposal.warnings || []).length > 0;
    const reason = liveWarnings && proposal.llm_reason
        ? proposal.llm_reason.split('【注意】')[0].trim()
        : proposal.llm_reason;

    const doApply = async () => {
        setApplying(true);
        try {
            const res = await applyCopilotProposal(projectId, proposal.proposal_id, true);
            if (res.applied) {
                setStatus('applied');
                setResult(res);
                message.success(res.message || '提案已应用');
                notifyCanvasRefresh();
            } else if (res.need_confirm) {
                message.warning(res.message || '高风险操作，请二次确认');
            } else if (res.conflict) {
                setStatus('expired');
                message.warning(res.message || '画布已更新，请刷新后重试');
            } else if (res.healed) {
                setStatus('rejected');
                message.warning(res.message || '提案被机检拦截已回滚');
            } else {
                message.error(res.error || '提案应用失败');
            }
            onStatusChange?.(status, res);
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || e?.message || '提案应用失败');
        } finally {
            setApplying(false);
        }
    };

    const doReject = async () => {
        try {
            await rejectCopilotProposal(projectId, proposal.proposal_id);
            setStatus('rejected');
            setResult({ reason: '用户忽略' });
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '操作失败');
        }
    };

    return (
        <div className="mt-2 rounded-lg border border-gray-200 bg-white overflow-hidden">
            {/* 头：kind + 风险 + 状态 */}
            <div className="px-2.5 py-1.5 bg-gray-50 border-b border-gray-100 flex items-center gap-1.5">
                <EditOutlined className="text-blue-500" />
                <span className="text-xs font-semibold text-gray-700">{kindLabel}提案</span>
                {high && status === 'pending' && (
                    <Tag color="warning" icon={<ExclamationCircleOutlined />}
                         className="!text-[10px] !leading-4 !px-1.5 !m-0">高风险，请核对影响面</Tag>
                )}
                <span className="flex-1" />
                {status === 'applied' && <Tag color="success" className="!text-[10px] !m-0">已应用{result?.version_no ? ` · v${result.version_no}` : ''}</Tag>}
                {status === 'rejected' && <Tag className="!text-[10px] !m-0">已忽略</Tag>}
                {status === 'expired' && <Tag color="default" className="!text-[10px] !m-0">已过期</Tag>}
            </div>

            <div className="px-2.5 py-2">
                {/* LLM 理由 */}
                {reason && (
                    <p className="text-xs text-gray-600 leading-5 mb-1.5">{reason}</p>
                )}

                {/* S8：校验警告（不阻断应用，供用户自行斟酌） */}
                {(proposal.warnings || []).length > 0 && (
                    <div className="mb-1.5 rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
                        {(proposal.warnings || []).map((w, i) => (
                            <p key={i} className="text-[11px] text-amber-700 leading-4 flex items-start gap-1">
                                <ExclamationCircleOutlined className="mt-0.5 shrink-0 text-amber-500" />
                                <span>{w}</span>
                            </p>
                        ))}
                    </div>
                )}

                {/* diff 红绿条目 */}
                <div className="space-y-1 mb-1.5">
                    {(proposal.diff_preview || []).map((d, i) => <DiffRow key={i} d={d} />)}
                </div>

                {/* 影响面 */}
                {hasCascade && (
                    <p className="text-[10px] text-gray-500 mb-1.5">
                        影响面：实例 {impact!.instances} · 关系 {impact!.relations}
                        {impact!.review_items > 0 && ` · 待审核项 ${impact!.review_items}`}
                    </p>
                )}

                {/* 按钮/状态说明 */}
                {status === 'pending' ? (
                    <div className="flex items-center gap-1.5">
                        {high ? (
                            <Popconfirm
                                title="高风险操作"
                                description="删除/合并会级联影响实例与关系，确认执行？"
                                okText="确认执行"
                                cancelText="再想想"
                                onConfirm={doApply}>
                                <Button size="small" danger type="primary" icon={<CheckOutlined />}
                                        loading={applying}>应用</Button>
                            </Popconfirm>
                        ) : (
                            <Button size="small" type="primary" icon={<CheckOutlined />}
                                    loading={applying} onClick={doApply}>应用</Button>
                        )}
                        <Button size="small" icon={<CloseOutlined />} onClick={doReject}>忽略</Button>
                        <Button size="small" type="text" icon={<QuestionCircleOutlined />}
                                onClick={() => onAdjust?.(`关于${kindLabel}提案，我想调整：`)}>反问调整</Button>
                    </div>
                ) : status === 'applied' ? (
                    <p className="text-[10px] text-green-600">
                        已生成 manual 版本{result?.version_no ? ` v${result.version_no}` : ''}，可在版本时间轴回滚
                    </p>
                ) : status === 'rejected' && result?.healed ? (
                    <p className="text-[10px] text-orange-500">已自动回滚：{String(result.reason || '')}</p>
                ) : status === 'rejected' ? (
                    <p className="text-[10px] text-gray-400">已忽略，画布无任何变更</p>
                ) : status === 'expired' ? (
                    <p className="text-[10px] text-gray-400">
                        提案已过期（画布已更新），请刷新画布后重新生成
                    </p>
                ) : null}
            </div>
        </div>
    );
}
