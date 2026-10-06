// src/features/qa/SourceCard.tsx - 问答溯源引用卡（QaDrawer / QaPage 共用）
// evidence 原句 + char 定位 + 文档预签名链接；highlighted 用于正文 [n] 角标点击定位。
import { Button, Tag } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { QaSource } from '../../api/qa';

export const REF_TYPE_LABEL: Record<string, string> = {
    vector_chunk: '向量切片',
    graph_edge: '图关系',
    graph_node: '图节点',
    keyword_chunk: '关键词切片',
};

export default function SourceCard({ index, source, highlighted }: {
    index: number;
    source: QaSource;
    highlighted?: boolean;
}) {
    const openDetail = () => {
        if (source.doc_url) window.open(source.doc_url, '_blank');
    };

    // 清理 PDF 解析残留：表格换行符、分隔线（如 [---|---]）、连续空白
    const quote = source.quote?.trim()
        ?.replace(/<br\s*\/?>/gi, ' ')
        ?.replace(/\[\s*[-—_=|:.\s]+\]/g, ' ')
        ?.replace(/\s{2,}/g, ' ');

    return (
        <div className={`p-2 bg-white border rounded-lg text-xs transition-all ${
            highlighted
                ? 'border-blue-400 ring-2 ring-blue-200 shadow-sm'
                : 'border-gray-200 hover:border-blue-300'
        }`}>
            <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                <Tag color="purple" className="mr-0">[{index + 1}]</Tag>
                <Tag color="geekblue" className="mr-0">{REF_TYPE_LABEL[source.ref_type] ?? source.ref_type}</Tag>
                <span className="text-gray-600 font-medium flex items-center gap-1">
                    <FileTextOutlined />{source.doc_file || '未知文件'}
                </span>
                {source.score > 0 && <span className="text-gray-400 ml-auto">{(source.score * 100).toFixed(0)}%</span>}
            </div>
            {quote && (
                <div className="text-gray-500 leading-relaxed line-clamp-3 pl-1">{quote}</div>
            )}
            {source.char_start != null && (
                <div className="text-gray-400 mt-1">原文定位：第 {source.char_start}–{source.char_end ?? source.char_start} 字符</div>
            )}
            {source.doc_url && (
                <Button size="small" type="link" className="!px-0 !text-[11px]" onClick={openDetail}>
                    打开原文
                </Button>
            )}
        </div>
    );
}
