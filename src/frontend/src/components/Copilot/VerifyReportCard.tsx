// src/components/Copilot/VerifyReportCard.tsx - 验证报告卡（O5-B28/F15，docs/design/14 §5）
// 三段折叠（CQ 评测 / 一致性 / 抽样溯源）+ 样例展开 + 与上一版对比 + 存档到版本。
import { useState } from 'react';
import { Button, Collapse, Tag, Tooltip, message } from 'antd';
import {
    SaveOutlined,
    CheckCircleOutlined,
    DownOutlined,
    ExclamationCircleOutlined,
    RightOutlined,
    SafetyOutlined,
} from '@ant-design/icons';
import { archiveCopilotVerify } from '../../api/copilot';
import type { CopilotVerifyReport } from '../../api/copilot';

const VERDICT_TAG: Record<string, { color: string; text: string }> = {
    supported: { color: 'green', text: '证据支撑' },
    partial: { color: 'orange', text: '部分支撑' },
    unsupported: { color: 'red', text: '无支撑' },
};

const METRIC_NAME: Record<string, string> = {
    coverage_rate: 'CQ 证据覆盖率',
    answerable: 'CQ 可回答数',
    owlrl_errors: 'owlrl 错误',
    shacl_violations: 'SHACL 违例',
    unsupported_samples: '抽样无支撑数',
};

const OVERALL_TAG: Record<string, { color: string; text: string }> = {
    pass: { color: 'green', text: '通过' },
    warn: { color: 'orange', text: '有警告' },
    fail: { color: 'red', text: '未通过' },
};

interface Props {
    projectId: number;
    report: CopilotVerifyReport;
    /** 回放时由消息 id 定位存档接口；实时流由调用方落库后补传 */
    messageId?: number;
}

export default function VerifyReportCard({ projectId, report, messageId }: Props) {
    const [archiving, setArchiving] = useState(false);
    const [archivedNo, setArchivedNo] = useState<number | null>(
        report.archived_version_no ?? null);
    const [openSamples, setOpenSamples] = useState(false);

    const overall = OVERALL_TAG[report.overall] ?? { color: 'default', text: report.overall };
    const cq = report.cq ?? {};
    const cons = report.consistency ?? {};
    const sample = report.sample ?? {};
    const cmp = report.comparison;
    const metricEntries = Object.entries(cmp?.metrics ?? {});

    const doArchive = async () => {
        if (archiving || archivedNo) return;
        if (messageId == null) { message.warning('该报告尚未落库，稍后可从历史会话存档'); return; }
        setArchiving(true);
        try {
            const r = await archiveCopilotVerify(projectId, messageId);
            setArchivedNo(r.version_no);
            message.success(`已存档为版本 v${r.version_no}，可在版本时间轴查看`);
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '存档失败');
        } finally {
            setArchiving(false);
        }
    };

    const cqBody = cq.total ? (
        <div className="text-xs text-gray-600 space-y-1">
            <div>
                可回答 <span className="font-medium text-gray-800">{cq.answerable}/{cq.total}</span>，
                证据覆盖率 <span className="font-medium text-gray-800">{cq.coverage_rate ?? '-'}</span>
            </div>
            {(cq.failed ?? []).length > 0 ? (
                <div>
                    <div className="text-gray-500">未通过（检索不到足够证据）：</div>
                    <ul className="pl-4 list-disc">
                        {cq.failed!.map((f, i) => (
                            <li key={i}>{f.question}<span className="text-gray-400">（{f.source_count} 条引用）</span></li>
                        ))}
                    </ul>
                </div>
            ) : <div className="text-green-600">全部 CQ 通过</div>}
        </div>
    ) : <div className="text-xs text-gray-500">{cq.note ?? cq.error ?? '未执行'}</div>;

    const consBody = (
        <div className="text-xs text-gray-600 space-y-1">
            <div>
                owlrl 错误 <span className="font-medium text-gray-800">{cons.owlrl_errors ?? '-'}</span> 项
                （蕴含 {(cons as any).inferred_count ?? 0} 条）；
                SHACL 违例 <span className="font-medium text-gray-800">{cons.shacl_violations ?? '-'}</span> 条
                {cons.shacl_error && <span className="text-red-500">（{cons.shacl_error}）</span>}
            </div>
            {(cons.error_samples ?? []).length > 0 && (
                <ul className="pl-4 list-disc">
                    {cons.error_samples!.slice(0, 5).map((e, i) => (
                        <li key={i}>
                            {e.subject ?? '-'}{e.object ? ` → ${e.object}` : ''}
                            {e.message ? <span className="text-red-500">{e.message}</span> : null}
                        </li>
                    ))}
                </ul>
            )}
            {cons.shacl_violations === 0 && <div className="text-green-600">SHACL 全部通过</div>}
        </div>
    );

    const samples = sample.samples ?? [];
    const sampleBody = (
        <div className="text-xs text-gray-600 space-y-1">
            {sample.sampled ? (
                <>
                    <div>
                        核验 <span className="font-medium text-gray-800">{sample.sampled}</span> 条：
                        支撑 {sample.supported} / 部分 {sample.partial} / 无支撑 {sample.unsupported}
                        {sample.support_rate != null && <>，支撑率 <span className="font-medium">{sample.support_rate}</span></>}
                    </div>
                    <button className="text-[11px] text-blue-600 hover:underline flex items-center gap-0.5"
                            onClick={() => setOpenSamples(v => !v)}>
                        {openSamples ? <DownOutlined /> : <RightOutlined />}
                        逐条样例（{samples.length}）
                    </button>
                    {openSamples && (
                        <ul className="pl-1 space-y-1.5">
                            {samples.map((s, i) => {
                                const v = VERDICT_TAG[s.verdict] ?? { color: 'default', text: s.verdict };
                                return (
                                    <li key={i} className="border border-gray-100 rounded p-1.5 bg-gray-50/60">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <Tag color={v.color} className="!m-0 !text-[10px]">{v.text}</Tag>
                                            <span className="text-[11px] font-medium break-all">{s.target}</span>
                                        </div>
                                        {s.quote && <div className="text-[11px] text-gray-500 mt-0.5">「{s.quote}」</div>}
                                        <div className="text-[11px] text-gray-500 mt-0.5">
                                            {s.reason}（证据 {s.evidence_count} 条）
                                        </div>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </>
            ) : <div>{sample.note ?? '未执行'}</div>}
        </div>
    );

    return (
        <div className="border border-blue-100 rounded-lg p-2.5 bg-blue-50/40">
            <div className="flex items-center gap-1.5 flex-wrap">
                <SafetyOutlined className="text-blue-500" />
                <span className="text-sm font-medium">AI 验证报告</span>
                <Tag color={overall.color} className="!m-0"
                     icon={report.overall === 'pass' ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}>
                    {overall.text}
                </Tag>
                <span className="flex-1" />
                {archivedNo ? (
                    <Tooltip title="报告摘要已写入该版本描述，可在版本时间轴查看/回滚">
                        <Tag color="green" className="!m-0" icon={<SaveOutlined />}>已存档 v{archivedNo}</Tag>
                    </Tooltip>
                ) : (
                    <Button size="small" icon={<SaveOutlined />} loading={archiving} onClick={doArchive}>
                        存档到版本
                    </Button>
                )}
            </div>
            <Collapse ghost size="small" className="mt-1 -ml-2"
                      items={[
                          { key: 'cq', label: <span className="text-xs">① CQ 评测</span>, children: cqBody },
                          { key: 'cons', label: <span className="text-xs">② 一致性</span>, children: consBody },
                          { key: 'sample', label: <span className="text-xs">③ 抽样溯源核验</span>, children: sampleBody },
                      ]}
                      defaultActiveKey={['cq', 'cons', 'sample']} />
            {cmp && (metricEntries.length > 0 || (cmp.newly_failed_cqs ?? []).length > 0
                     || (cmp.resolved_cqs ?? []).length > 0) && (
                <div className="mt-1 border-t border-blue-100 pt-1.5">
                    <div className="text-[11px] font-medium text-gray-500">与上一版对比</div>
                    {metricEntries.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                            {metricEntries.map(([k, v]) => (
                                <Tag key={k} color={v.delta > 0 ? (k.includes('errors') || k.includes('violations') || k.includes('unsupported') ? 'red' : 'green') : (k.includes('errors') || k.includes('violations') || k.includes('unsupported') ? 'green' : 'orange')}
                                     className="!m-0 !text-[10px]">
                                    {METRIC_NAME[k] ?? k}：{v.previous} → {v.current}
                                    （{v.delta > 0 ? `+${v.delta}` : v.delta}）
                                </Tag>
                            ))}
                        </div>
                    )}
                    {(cmp.newly_failed_cqs ?? []).length > 0 && (
                        <div className="text-[11px] text-red-500 mt-1">本版新失败：{cmp.newly_failed_cqs!.join('；')}</div>
                    )}
                    {(cmp.resolved_cqs ?? []).length > 0 && (
                        <div className="text-[11px] text-green-600 mt-0.5">本版解决：{cmp.resolved_cqs!.join('；')}</div>
                    )}
                </div>
            )}
        </div>
    );
}
