// src/components/Copilot/ToolSteps.tsx - 工具调用步骤（O5-S5 F16，用户反馈④）
// 像对话式 IDE 一样透明：每步工具名 + 耗时，点击展开调用参数与观察输出。
// 实时流：start 显示"执行中…"，end 落耗时；回放：从 tool_calls 还原终态。
import { useState } from 'react';
import { LoadingOutlined, RightOutlined, ToolOutlined } from '@ant-design/icons';
import type { CopilotToolCall, CopilotToolEvent } from '../../api/copilot';

export interface ToolStep {
    key: string;
    tool: string;
    args?: Record<string, unknown>;
    observation?: Record<string, unknown> | null;
    duration_ms?: number | null;
    status: 'running' | 'done';
}

const TOOL_NAMES: Record<string, string> = {
    get_schema_stats: '查本体结构统计',
    find_entity: '检索实体',
    trace_relation: '查关系链',
    get_scorecard: '查记分卡',
    get_conflicts: '查冲突现况',
    get_cq_results: '查 CQ 评测',
    get_evidence: '拉证据原文',
    run_verification: '验证三件套',
    query_graph: '查图谱数据',
};

const toolLabel = (t: string) => TOOL_NAMES[t] ?? t;

/** 从 SSE tool 事件序列构建步骤列表（实时流） */
export function stepsFromEvents(events: CopilotToolEvent[]): ToolStep[] {
    const steps: ToolStep[] = [];
    for (const ev of events) {
        if (ev.phase === 'start') {
            steps.push({ key: `${ev.tool}-${ev.round ?? steps.length}`, tool: ev.tool,
                         args: ev.args, status: 'running' });
        } else if (ev.phase === 'end') {
            // 就近回填最近的同名 running 步骤（正常一一对应）
            const idx = [...steps].reverse().findIndex(s => s.tool === ev.tool && s.status === 'running');
            const at = idx >= 0 ? steps.length - 1 - idx : steps.length - 1;
            if (steps[at]) {
                steps[at] = { ...steps[at], status: 'done', duration_ms: ev.duration_ms,
                              observation: ev.observation ?? null };
            }
        }
    }
    return steps;
}

/** 从落库 tool_calls 还原步骤（会话回放）；排除渲染为专属卡的伪工具 */
const PSEUDO_TOOLS = new Set(['generate_proposal', 'generate_clarification']);
export function stepsFromCalls(calls: CopilotToolCall[]): ToolStep[] {
    return (calls || [])
        .filter(c => !PSEUDO_TOOLS.has(c.tool))
        .map((c, i) => ({
            key: `${c.tool}-${c.round ?? i}-${i}`,
            tool: c.tool, args: c.args,
            observation: (c.observation as Record<string, unknown> | undefined) ?? null,
            duration_ms: c.duration_ms ?? null,
            status: 'done' as const,
        }));
}

const fmtArgs = (args?: Record<string, unknown>) => {
    const keys = Object.keys(args || {});
    if (!keys.length) return '';
    return keys.slice(0, 2).map(k => `${String((args as any)[k]).slice(0, 24)}`).join(' · ');
};

export default function ToolSteps({ steps }: { steps: ToolStep[] }) {
    const [openIdx, setOpenIdx] = useState<number | null>(null);
    if (!steps.length) return null;
    return (
        <div className="mt-1.5 rounded-lg border border-gray-100 bg-gray-50/70 overflow-hidden">
            <div className="px-2 py-1 text-[10px] text-gray-400 flex items-center gap-1">
                <ToolOutlined /> 工具调用 {steps.length} 步
            </div>
            <div className="pb-1">
                {steps.map((s, i) => (
                    <div key={s.key} className="border-t border-gray-100/80 first:border-t-0">
                        <button className="w-full flex items-center gap-1.5 px-2 py-1 text-[11px] text-gray-600
                                           hover:bg-gray-100 transition-colors"
                                onClick={() => setOpenIdx(openIdx === i ? null : i)}>
                            {s.status === 'running'
                                ? <LoadingOutlined className="text-blue-500" />
                                : <RightOutlined className={`text-[9px] text-gray-400 transition-transform ${openIdx === i ? 'rotate-90' : ''}`} />}
                            <span className="font-medium">{toolLabel(s.tool)}</span>
                            <span className="text-gray-400 truncate flex-1 text-left">{fmtArgs(s.args)}</span>
                            {s.status === 'done' && s.duration_ms != null && (
                                // 不足 1 秒显示毫秒，避免 12ms 显示成 0.0s
                                <span className="text-gray-400 shrink-0">
                                    {s.duration_ms < 1000 ? `${s.duration_ms}ms` : `${(s.duration_ms / 1000).toFixed(1)}s`}
                                </span>
                            )}
                        </button>
                        {openIdx === i && s.status === 'done' && (
                            <div className="mx-2 mb-1.5 rounded bg-white border border-gray-100 p-1.5 space-y-1">
                                {s.args && Object.keys(s.args).length > 0 && (
                                    <pre className="text-[10px] leading-4 text-gray-500 whitespace-pre-wrap break-all m-0">
                                        入参：{JSON.stringify(s.args)}
                                    </pre>
                                )}
                                {s.observation && (
                                    <pre className="text-[10px] leading-4 text-gray-600 whitespace-pre-wrap break-all m-0 max-h-40 overflow-auto">
                                        输出：{JSON.stringify(s.observation, null, 1)}
                                    </pre>
                                )}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
