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
// 2026-11-23：整块引用只保留「全部展开 / 全部折叠」两态，卡片不再单独折叠。
// 展开即全文，长内容在卡内 360px 滚动区里滚；不再有「展开全文 / 查看完整清单」。
import { Button } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { QaSource } from '../../api/qa';
import { cleanQuote, classSummaryLine, toCitationMarkdown } from './quoteText';
import MarkdownAnswer from './MarkdownAnswer';
import Tip from '../../shared/Tip';

export const REF_TYPE_LABEL: Record<string, string> = {
    vector_chunk: '向量切片',
    graph_edge: '图关系',
    graph_node: '图节点',
    tool_query: '工具查询',
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
    tool_query: 'brand',
    inference: 'violet',
    axiom: 'violet',
};

export default function SourceCard({ index, source, highlighted }: {
    index: number;
    source: QaSource;
    highlighted?: boolean;
}) {
    const openDetail = () => {
        if (source.doc_url) window.open(source.doc_url, '_blank');
    };

    const isNodeList = source.ref_type === 'graph_node';
    const quote = cleanQuote(source.quote);
    // 图节点清单引用 → 摘要行 + 实例列表分块；其余类型统一走 markdown
    const cls = isNodeList ? classSummaryLine(quote) : null;
    const md = cls ? '' : toCitationMarkdown(source.quote);

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
                {/* 文件名直接可见，不再挂同文悬停提示（2026-11-25）：
                    气泡内容和这行文字完全一样，只是干扰 */}
                <span className="flex min-w-0 items-center gap-1 text-[#3B4A63]">
                    <FileTextOutlined className="shrink-0 text-[#8A94AB]" />
                    <span className="truncate">{source.doc_file || '未知文件'}</span>
                </span>
                {source.score > 0 && (
                    <Tip text="检索相似度">
                        <span className="tk-mono ml-auto shrink-0 text-[11px] text-[#A3ADC2]">
                            {(source.score * 100).toFixed(0)}%
                        </span>
                    </Tip>
                )}
            </div>
            {cls ? (
                /* 图节点清单：摘要行粗体 + 实例列表；列表过长时卡内滚动 */
                <div className="pl-1">
                    <div className="font-medium leading-[1.7] text-[#3B4A63]">{cls.summary}</div>
                    {cls.rest && (
                        <div className="mt-0.5 max-h-[360px] overflow-auto whitespace-pre-wrap leading-[1.7] text-[#52607A]">
                            {cls.rest}
                        </div>
                    )}
                </div>
            ) : (
                md && (
                    /* 引用原文：展开即全文，长内容卡内滚动（关掉 [n] 角标解析——
                       原文里的方括号是正文） */
                    <div className="max-h-[360px] overflow-auto pl-1">
                        <MarkdownAnswer content={md} sourceCount={0} onCite={() => {}} dense citations={false} />
                    </div>
                )
            )}
            {(source.doc_url || source.char_start != null) && (
                <div className="mt-1 flex flex-wrap items-center gap-x-3 pl-1">
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
