// src/features/qa/SourceCard.tsx - 问答溯源引用卡（QaDrawer / QaPage 共用）
// evidence 原句 + char 定位 + 文档预签名链接；highlighted 用于正文 [n] 角标点击定位。
// 引用内容经 cleanQuote 清理解析残留；图节点类清单引用重组为「摘要行 + 实例列表」，
// 长内容默认截断可展开——溯源要完整且可读。
import { useState } from 'react';
import { Button, Popover, Tag } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { QaSource } from '../../api/qa';
import { cleanQuote, classSummaryLine } from './quoteText';
import MarkdownAnswer from './MarkdownAnswer';

export const REF_TYPE_LABEL: Record<string, string> = {
    vector_chunk: '向量切片',
    graph_edge: '图关系',
    graph_node: '图节点',
    keyword_chunk: '关键词切片',
    inference: '语义推理',
    axiom: '本体公理',
};

const REF_TYPE_COLOR: Record<string, string> = {
    vector_chunk: 'geekblue',
    keyword_chunk: 'cyan',
    graph_edge: 'purple',
    graph_node: 'geekblue',
    inference: 'magenta',
    axiom: 'gold',
};

/** 长实例列表转可读形态：前 12 项一行展示 + 尾注 */
function readableList(rest: string): { head: string; tailNote: string } {
    const sep = rest.includes('；') ? '；' : '、';
    const items = rest.split(sep).map(s => s.trim()).filter(Boolean);
    if (items.length <= 12) return { head: rest, tailNote: '' };
    return { head: items.slice(0, 12).join(sep), tailNote: `…等 ${items.length} 项` };
}

export default function SourceCard({ index, source, highlighted }: {
    index: number;
    source: QaSource;
    highlighted?: boolean;
}) {
    const [expanded, setExpanded] = useState(false);
    const openDetail = () => {
        if (source.doc_url) window.open(source.doc_url, '_blank');
    };

    const isInferred = source.ref_type === 'inference';
    const isAxiom = source.ref_type === 'axiom';
    const isNodeList = source.ref_type === 'graph_node';
    // 切片类（向量/关键词）：内容缩略一行，悬浮展示原文全文+来源文档（表格/word/txt 均适用）
    const isChunk = source.ref_type === 'vector_chunk' || source.ref_type === 'keyword_chunk';

    const quote = cleanQuote(source.quote);
    // 图节点清单引用 → 摘要行 + 实例列表分块
    const cls = isNodeList ? classSummaryLine(quote) : null;
    const listBody = cls && cls.rest.length > 40 ? readableList(cls.rest) : null;
    const longText = (cls ? cls.rest : quote).length > 140;

    const borderCls = highlighted
        ? 'border-blue-400 ring-2 ring-blue-200 shadow-sm'
        : isInferred
            ? 'border-purple-200 hover:border-purple-300'
            : isAxiom
                ? 'border-amber-200 hover:border-amber-300'
                : 'border-gray-200 hover:border-blue-300';

    return (
        <div className={`p-2.5 bg-white border rounded-lg text-xs transition-all ${borderCls}`}>
            <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                <Tag color={REF_TYPE_COLOR[source.ref_type] ?? 'default'} className="mr-0">[{index + 1}]</Tag>
                <Tag color={REF_TYPE_COLOR[source.ref_type] ?? 'default'} className="mr-0">
                    {REF_TYPE_LABEL[source.ref_type] ?? source.ref_type}
                </Tag>
                <span className="text-gray-600 font-medium flex items-center gap-1">
                    <FileTextOutlined />{source.doc_file || '未知文件'}
                </span>
                {source.score > 0 && <span className="text-gray-500 ml-auto">{(source.score * 100).toFixed(0)}%</span>}
            </div>
            {cls ? (
                /* 图节点清单：摘要行粗体 + 实例列表 */
                <div className="pl-1">
                    <div className="text-gray-700 font-medium leading-relaxed">{cls.summary}</div>
                    {listBody ? (
                        <>
                            <div className="text-gray-500 leading-relaxed mt-0.5">
                                {listBody.head}{listBody.tailNote && <span className="text-gray-400"> {listBody.tailNote}</span>}
                            </div>
                            {expanded && (
                                <div className="text-gray-500 leading-relaxed mt-1 whitespace-pre-wrap border-t border-dashed border-gray-200 pt-1">
                                    {cls.rest}
                                </div>
                            )}
                            <Button size="small" type="link" className="!px-1 !text-[11px]"
                                    onClick={() => setExpanded(v => !v)}>
                                {expanded ? '收起完整清单' : '查看完整清单'}
                            </Button>
                        </>
                    ) : (
                        <div className="text-gray-500 leading-relaxed mt-0.5">{cls.rest}</div>
                    )}
                </div>
            ) : isChunk && source.quote ? (
                /* 切片引用：缩略一行 + 悬浮看原文（markdown 渲染——表格还原为真表格，
                   word/txt 段落正常排版；来源文档在悬浮卡顶部标明） */
                <Popover
                    placement="rightTop"
                    mouseEnterDelay={0.25}
                    styles={{ body: { padding: '12px 14px', width: 660 } }}
                    content={
                        <div>
                            <div className="flex items-center gap-1.5 text-[11px] text-gray-500 mb-2 pb-1.5 border-b border-gray-100">
                                <FileTextOutlined />来源文档：{source.doc_file || '未知文件'}
                            </div>
                            <div className="max-h-96 overflow-y-auto pr-1">
                                <MarkdownAnswer content={source.quote} sourceCount={0} onCite={() => {}} />
                            </div>
                        </div>
                    }
                >
                    <div className="pl-1 cursor-help">
                        <div className="text-gray-500 leading-relaxed line-clamp-1">
                            {quote.slice(0, 60)}{quote.length > 60 ? '…' : ''}
                        </div>
                    </div>
                </Popover>
            ) : (
                /* 其它引用：清理后的原文（长文可展开） */
                quote && (
                    <div className="pl-1">
                        <div className={`text-gray-500 leading-relaxed whitespace-pre-wrap ${expanded ? '' : 'line-clamp-4'}`}>
                            {quote}
                        </div>
                        {longText && (
                            <Button size="small" type="link" className="!px-1 !text-[11px]"
                                    onClick={() => setExpanded(v => !v)}>
                                {expanded ? '收起' : '展开全文'}
                            </Button>
                        )}
                    </div>
                )
            )}
            {source.char_start != null && (
                <div className="text-gray-400 mt-1 pl-1">原文定位：第 {source.char_start}–{source.char_end ?? source.char_start} 字符</div>
            )}
            {source.doc_url && (
                <Button size="small" type="link" className="!px-1 !text-[11px]" onClick={openDetail}>
                    打开原文
                </Button>
            )}
        </div>
    );
}
