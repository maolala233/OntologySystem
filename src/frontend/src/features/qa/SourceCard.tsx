// src/features/qa/SourceCard.tsx - 问答溯源引用卡（问答页 /qa 使用）
// evidence 原句 + char 定位 + 文档预签名链接；highlighted 用于正文 [n] 角标点击定位。
// 引用正文统一走 markdown 渲染（2026-11-19）：解析器把 docx/pptx 的表格拍成了
// 「a | b | c」的行，chunk 里没有分隔行，直接渲染只会得到一堆纯文本段落。
// toCitationMarkdown 先把这些行块还原成 GFM 表格、把单个换行转成硬换行，
// 再交给 MarkdownAnswer——表格就是表格，条款就是一行一条。
// 图节点清单引用另走一套：摘要行 + 实例列表，比原样堆实例名好读。
// 视觉层同样统一到站内冷调：类型标记只留品牌蓝（检索证据）/ 品牌紫（推演结论）两支，
// 序号与相似度用等宽冷灰。
// 底色（2026-11-19 二次收敛）：原先每条都是一张白卡，5~6 条叠在冷蓝面板上，
// 白块把面板本身压没了，页面上还平白多出五六个"卡片边界"。现在卡片透明，
// 靠面板自己的冷蓝底 + 行间发丝线分区，hover 才浮出一层品牌淡底，
// 定位高亮用左侧品牌竖条 —— 同一张面板里的条目，不再各自是一块纸。
import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { QaSource } from '../../api/qa';
import { cleanQuote, classSummaryLine, toCitationMarkdown } from './quoteText';
import MarkdownAnswer from './MarkdownAnswer';

export const REF_TYPE_LABEL: Record<string, string> = {
    vector_chunk: '向量切片',
    graph_edge: '图关系',
    graph_node: '图节点',
    keyword_chunk: '关键词切片',
    inference: '语义推理',
    axiom: '本体公理',
};

/** 推演类引用（不是检索命中，而是规则/公理推出来的）用品牌紫，其余用品牌蓝 */
const REF_TYPE_TONE: Record<string, 'brand' | 'violet'> = {
    vector_chunk: 'brand',
    keyword_chunk: 'brand',
    graph_edge: 'brand',
    graph_node: 'brand',
    inference: 'violet',
    axiom: 'violet',
};

/** 长实例列表转可读形态：前 12 项一行展示 + 尾注 */
function readableList(rest: string): { head: string; tailNote: string } {
    const sep = rest.includes('；') ? '；' : '、';
    const items = rest.split(sep).map(s => s.trim()).filter(Boolean);
    if (items.length <= 12) return { head: rest, tailNote: '' };
    return { head: items.slice(0, 12).join(sep), tailNote: `…等 ${items.length} 项` };
}

export default function SourceCard({ index, source, highlighted, expandAll }: {
    index: number;
    source: QaSource;
    highlighted?: boolean;
    /** 父级「全部展开 / 全部收起」：变化时覆写本条自己的展开态，之后仍可单独切换 */
    expandAll?: boolean;
}) {
    const [expanded, setExpanded] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    // 正文角标点过来时顺带展开这条证据：原来只给一圈高亮，用户还得自己再点一次
    // 「展开全文」才看得到内容。只展开不自动收起，免得手动展开的卡被下一次定位折回去
    useEffect(() => {
        if (highlighted) setExpanded(true);
    }, [highlighted]);
    // 折叠后内容被裁到固定高度：内层滚动位置必须归零。否则展开时滚到中间、
    // 再收起，露出来的就是从中间截断的一屏，看着像内容丢了（2026-11-19 反馈）
    useEffect(() => {
        if (!expanded) scrollRef.current?.scrollTo({ top: 0 });
    }, [expanded]);
    useEffect(() => {
        setExpanded(!!expandAll);
    }, [expandAll]);

    const openDetail = () => {
        if (source.doc_url) window.open(source.doc_url, '_blank');
    };

    const isNodeList = source.ref_type === 'graph_node';

    const quote = cleanQuote(source.quote);
    // 图节点清单引用 → 摘要行 + 实例列表分块；其余类型统一走 markdown
    const cls = isNodeList ? classSummaryLine(quote) : null;
    const listBody = cls && cls.rest.length > 40 ? readableList(cls.rest) : null;
    const md = cls ? '' : toCitationMarkdown(source.quote);
    // 折叠时只留四行左右的高度；判断是否还有更多内容可展开（约 4 行 / 240 字）
    const moreLines = md.split('\n').filter(l => l.trim()).length > 4 || md.length > 240;
    const canExpand = cls ? !!listBody : moreLines;

    // 高亮：正文角标点过来时左侧亮一条品牌竖条 + 一层淡底（inset 阴影，不占布局宽度）
    const cardCls = highlighted
        ? 'bg-[rgba(91,141,239,0.10)] shadow-[inset_2px_0_0_0_#5B8DEF]'
        : 'hover:bg-[rgba(255,255,255,0.55)]';
    const toneCls = REF_TYPE_TONE[source.ref_type] === 'violet' ? 'tk-chip tk-chip--violet' : 'tk-chip';

    return (
        <div className={`rounded-[8px] px-2.5 py-2 text-[12px] transition-colors ${cardCls}`}>
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className="tk-chip tk-chip--quiet tk-mono !px-1.5">[{index + 1}]</span>
                <span className={toneCls}>{REF_TYPE_LABEL[source.ref_type] ?? source.ref_type}</span>
                <span className="flex min-w-0 items-center gap-1 text-[#3B4A63]" title={source.doc_file || '未知文件'}>
                    <FileTextOutlined className="shrink-0 text-[#8A94AB]" />
                    <span className="truncate">{source.doc_file || '未知文件'}</span>
                </span>
                {source.score > 0 && (
                    <span className="tk-mono ml-auto shrink-0 text-[11px] text-[#A3ADC2]" title="检索相似度">
                        {(source.score * 100).toFixed(0)}%
                    </span>
                )}
            </div>
            {cls ? (
                /* 图节点清单：摘要行粗体 + 实例列表 */
                <div className="pl-1">
                    <div className="font-medium leading-[1.7] text-[#3B4A63]">{cls.summary}</div>
                    {listBody ? (
                        <>
                            <div className="mt-0.5 leading-[1.7] text-[#52607A]">
                                {listBody.head}
                                {listBody.tailNote && <span className="text-[#A3ADC2]"> {listBody.tailNote}</span>}
                            </div>
                            {expanded && (
                                <div className="mt-1 whitespace-pre-wrap border-t border-dashed border-[rgba(91,141,239,0.18)] pt-1 leading-[1.7] text-[#52607A]">
                                    {cls.rest}
                                </div>
                            )}
                        </>
                    ) : (
                        <div className="mt-0.5 leading-[1.7] text-[#52607A]">{cls.rest}</div>
                    )}
                </div>
            ) : (
                md && (
                    /* 引用原文：折叠时只露两行左右（原来 86px 接近四行，一屏引用卡太长），
                       展开后卡内滚动。这里关掉 [n] 角标解析——原文里的方括号是正文 */
                    <div ref={scrollRef}
                         className={`pl-1 ${expanded ? 'max-h-[360px] overflow-auto' : 'max-h-[58px] overflow-hidden'} ${
                             !expanded && canExpand
                                 ? '[mask-image:linear-gradient(to_bottom,#000_72%,transparent)]'
                                 : ''
                         }`}>
                        <MarkdownAnswer content={md} sourceCount={0} onCite={() => {}} dense citations={false} />
                    </div>
                )
            )}
            {(canExpand || source.doc_url || source.char_start != null) && (
                <div className="mt-1 flex flex-wrap items-center gap-x-3 pl-1">
                    {canExpand && (
                        <Button size="small" type="link" className="!h-auto !px-1 !text-[11px]"
                                onClick={() => setExpanded(v => !v)}>
                            {expanded
                                ? (cls ? '收起完整清单' : '收起')
                                : (cls ? '查看完整清单' : '展开全文')}
                        </Button>
                    )}
                    {source.doc_url && (
                        <Button size="small" type="link" className="!h-auto !px-0 !text-[11px]" onClick={openDetail}>
                            打开原文
                        </Button>
                    )}
                    {source.char_start != null && (
                        <span className="text-[11px] text-[#A3ADC2]">
                            原文定位：第 <span className="tk-mono">{source.char_start}–{source.char_end ?? source.char_start}</span> 字符
                        </span>
                    )}
                </div>
            )}
        </div>
    );
}
