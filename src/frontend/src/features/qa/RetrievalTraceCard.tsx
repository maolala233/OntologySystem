// src/features/qa/RetrievalTraceCard.tsx - 检索过程卡片：本次问答"怎么得到这个结果"的
// 编号步骤展示（①查图谱→②查规则/公理→③查关系→④查切片→⑤综合得到答案），
// 每次查询动态不同；可折叠（默认展开，头部点击收起只留摘要行）。
import { useState } from 'react';
import { Tag } from 'antd';
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
    UpOutlined,
} from '@ant-design/icons';
import type { QaTraceStep } from '../../api/qa';
import { cleanQuote } from './quoteText';

const STAGE_ICON: Record<string, JSX.Element> = {
    '查图谱': <ApartmentOutlined />,
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
        <div className="mt-1 rounded-xl border border-[#E7EEF9] bg-[#F7F9FD] p-2.5">
            <button type="button" className="w-full mb-1.5 flex items-center gap-1.5 text-[11.5px] text-[#66738F] text-left"
                    onClick={() => setCollapsed(v => !v)}>
                <DatabaseOutlined className="text-[#5B8DEF]" />
                <span className="font-medium text-[#3B4A63]">检索过程</span>
                <span className="tk-mono text-[10.5px] text-[#8A94AB]">{trace.length - 1} 步检索 · {totalRefs} 条证据</span>
                <span className="ml-auto text-[#5B8DEF] flex items-center gap-0.5 text-[11px]">
                    {collapsed ? <DownOutlined /> : <UpOutlined />}
                    <span>{collapsed ? '展开过程' : '收起'}</span>
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
                                    <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold shrink-0 ${isLast ? 'bg-[#2F62D8] text-white' : 'bg-white border border-[#9DBCF3] text-[#2F62D8]'}`}>
                                        {isLast && streaming
                                            ? <LoadingOutlined className="text-[12px] animate-spin" />
                                            : isLast ? <CheckCircleFilled className="text-[12px]" /> : k + 1}
                                    </span>
                                    {!isLast && <span className="flex-1 w-px bg-[#D7E3F3] my-0.5" />}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        <span className={`text-[12.5px] font-medium ${isLast ? 'text-[#2F62D8]' : 'text-[#3B4A63]'}`}>
                                            {st.stage}
                                        </span>
                                        <span className="text-[11px] text-[#8A94AB]">{st.what}</span>
                                        <Tag className="mr-0 !text-[10px] !leading-4 !px-1.5" color={isLast ? 'blue' : 'default'}>
                                            {isLast ? (streaming ? '综合中…' : `综合 ${st.count} 条引用`) : `命中 ${st.count} 条`}
                                        </Tag>
                                    </div>
                                    {st.sample && (
                                        <div className="mt-0.5 text-[11px] text-[#66738F] leading-relaxed line-clamp-2">
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
