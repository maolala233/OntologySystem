// src/features/qa/RetrievalTraceCard.tsx - 检索过程卡片：本次问答"怎么得到这个结果"的
// 编号步骤展示（①查图谱→②查规则/公理→③查关系→④查切片→⑤综合得到答案），
// 每次查询动态不同；可折叠（默认展开，头部点击收起只留摘要行）。
// 头部与「溯源引用」用同一套小标题语言（彩条 + 标题 + 等宽摘要），
// 命中数从 antd 彩色 Tag 换成冷调小胶囊（2026-11-19）。
import { useState } from 'react';
import {
    ApartmentOutlined,
    ApiOutlined,
    BulbOutlined,
    CheckCircleFilled,
    DatabaseOutlined,
    DownOutlined,
    FileSearchOutlined,
    LoadingOutlined,
    SearchOutlined,
    SafetyCertificateOutlined,
    ToolOutlined,
    UpOutlined,
} from '@ant-design/icons';
import type { QaTraceStep } from '../../api/qa';
import { cleanQuote } from './quoteText';

const STAGE_ICON: Record<string, JSX.Element> = {
    '查图谱': <ApartmentOutlined />,
    '查工具': <ToolOutlined />,
    '查规则/公理': <SafetyCertificateOutlined />,
    '语义推理': <BulbOutlined />,
    '查向量库': <SearchOutlined />,
    '查关键词索引': <FileSearchOutlined />,
    '大模型综合': <ApiOutlined />,
};

export default function RetrievalTraceCard({ trace, streaming }: {
    trace: QaTraceStep[];
    /** 流式生成中：最后一步显示"综合中"转圈而非完成勾 */
    streaming?: boolean;
}) {
    const [collapsed, setCollapsed] = useState(false);
    if (!trace?.length) return null;
    const last = trace[trace.length - 1];
    const totalRefs = trace.reduce((acc, st) => (st.stage !== '大模型综合' ? acc + (st.refs?.length || 0) : acc), 0);
    return (
        <div className="mt-1 rounded-xl border border-[rgba(91,141,239,0.16)] bg-[#EDF3FE] p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
            {/* 整行仍是点击区，但右侧的展开/收起做成与「溯源引用」同款的小胶囊（2026-11-25）：
                文案统一成「展开过程 / 收起过程」（原来展开 4 字、收起 2 字），
                hover 时整行点亮胶囊（group-hover），和下方引用区的切换控件一致 */}
            <button
                type="button"
                className={`group flex w-full items-center gap-2 rounded-[7px] text-left text-[11.5px] text-[#66738F] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)] ${collapsed ? '' : 'mb-1.5'}`}
                onClick={() => setCollapsed(v => !v)}
            >
                <span className="tk-mark" aria-hidden />
                <span className="font-medium text-[#3B4A63]">检索过程</span>
                <span className="tk-mono text-[10.5px] text-[#8A94AB]">{trace.length - 1} 步检索 · {totalRefs} 条证据</span>
                <span className="ml-auto -mr-1 flex items-center gap-1 rounded-[7px] px-1.5 py-0.5 text-[11px] text-[#2A57C6] transition-colors group-hover:bg-[rgba(91,141,239,0.12)] group-hover:text-[#1F47A8]">
                    {collapsed ? <DownOutlined /> : <UpOutlined />}
                    <span>{collapsed ? '展开过程' : '收起过程'}</span>
                </span>
            </button>
            {!collapsed && (
                <div>
                    {trace.map((st, k) => {
                        const isLast = k === trace.length - 1;
                        const stageKey = Object.keys(STAGE_ICON).find(key => st.stage.startsWith(key));
                        const icon = STAGE_ICON[stageKey ?? ''] ?? <DatabaseOutlined />;
                        return (
                            <div key={k} className="relative flex gap-2 pb-3 last:pb-0">
                                <div className="flex flex-col items-center">
                                    <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold ${isLast ? 'bg-[#2F62D8] text-white' : 'border border-[rgba(91,141,239,0.45)] bg-[#FBFDFF] text-[#2F62D8]'}`}>
                                        {isLast && streaming
                                            ? <LoadingOutlined className="animate-spin text-[12px]" />
                                            : isLast ? <CheckCircleFilled className="text-[12px]" /> : k + 1}
                                    </span>
                                    {!isLast && <span className="my-0.5 w-px flex-1 bg-[rgba(91,141,239,0.22)]" />}
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                        <span className={`text-[12.5px] font-medium ${isLast ? 'text-[#2F62D8]' : 'text-[#3B4A63]'}`}>
                                            {st.stage}
                                        </span>
                                        <span className="text-[11px] text-[#8A94AB]">{st.what}</span>
                                        <span className={isLast ? 'tk-chip' : 'tk-chip tk-chip--quiet'}>
                                            {isLast ? (streaming ? '综合中…' : `综合 ${st.count} 条引用`) : `命中 ${st.count} 条`}
                                        </span>
                                    </div>
                                    {st.sample && (
                                        <div className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-[#66738F]">
                                            {isLast && streaming ? '正在综合生成答案…'
                                                : isLast ? '综合以上检索结果，得到上方答案'
                                                : `「${cleanQuote(st.sample)}」`}
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
