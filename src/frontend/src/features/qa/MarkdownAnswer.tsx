// src/features/qa/MarkdownAnswer.tsx - 问答答案 Markdown 渲染（05 §6）
// react-markdown + remark-gfm：标题/加粗/列表/表格正常排版；
// 正文中的 [n] 引用标号渲染为可点击角标，点击滚动定位到对应溯源引用卡。
import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const CITE_RE = /\[(\d{1,2})\]/g;

interface MarkdownAnswerProps {
    content: string;
    /** 溯源引用总数（判断 [n] 是否可点击） */
    sourceCount: number;
    /** 点击引用标号（n 为 1 起的引用序号） */
    onCite: (n: number) => void;
}

/** 引用角标：n 在溯源范围内可点击，否则灰显纯文本 */
function CitationMark({ n, active, onCite }: { n: number; active: boolean; onCite: (n: number) => void }) {
    if (!active) {
        return <sup className="text-[10px] text-gray-400 mx-0.5">[{n}]</sup>;
    }
    return (
        <sup>
            <button
                type="button"
                title={`查看引用 [${n}]`}
                onClick={e => {
                    e.stopPropagation();
                    onCite(n);
                }}
                className="inline-flex items-center justify-center mx-0.5 px-1.5 min-w-[18px] h-[16px] align-super
                    text-[10px] leading-none font-medium text-blue-600 bg-blue-50 border border-blue-200 rounded-full
                    cursor-pointer hover:bg-blue-100 hover:text-blue-700 transition-colors"
            >
                {n}
            </button>
        </sup>
    );
}

/** 把文本中的 [n] 切成 文本 + 角标 序列 */
function renderTextWithCitations(text: string, sourceCount: number, onCite: (n: number) => void): React.ReactNode {
    const re = new RegExp(CITE_RE.source, 'g');
    const parts: React.ReactNode[] = [];
    let last = 0;
    let m: RegExpExecArray | null;
    let key = 0;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) parts.push(text.slice(last, m.index));
        parts.push(
            <CitationMark key={`cite-${key++}`} n={Number(m[1])} active={Number(m[1]) <= sourceCount} onCite={onCite} />,
        );
        last = m.index + m[0].length;
    }
    if (last === 0) return text;
    if (last < text.length) parts.push(text.slice(last));
    return parts;
}

/** 递归处理元素子节点中的裸文本（p/li/td 等的直接文本子串） */
function withCitations(children: React.ReactNode, sourceCount: number, onCite: (n: number) => void): React.ReactNode {
    return React.Children.map(children, child =>
        typeof child === 'string' ? renderTextWithCitations(child, sourceCount, onCite) : child,
    );
}

export default function MarkdownAnswer({ content, sourceCount, onCite }: MarkdownAnswerProps) {
    const cite = (children: React.ReactNode) => withCitations(children, sourceCount, onCite);
    return (
        <div className="text-sm text-gray-800 leading-relaxed break-words">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: ({ children }) => <h1 className="text-[15px] font-bold text-gray-900 mt-3 mb-1.5">{cite(children)}</h1>,
                    h2: ({ children }) => <h2 className="text-sm font-bold text-gray-900 mt-3 mb-1.5">{cite(children)}</h2>,
                    h3: ({ children }) => <h3 className="text-sm font-semibold text-gray-900 mt-2.5 mb-1">{cite(children)}</h3>,
                    h4: ({ children }) => <h4 className="text-sm font-semibold text-gray-800 mt-2 mb-1">{cite(children)}</h4>,
                    p: ({ children }) => <p className="my-1.5">{cite(children)}</p>,
                    strong: ({ children }) => <strong className="font-semibold text-gray-900">{cite(children)}</strong>,
                    em: ({ children }) => <em className="italic">{cite(children)}</em>,
                    ul: ({ children }) => <ul className="list-disc pl-5 my-1.5 space-y-1">{children}</ul>,
                    ol: ({ children }) => <ol className="list-decimal pl-5 my-1.5 space-y-1">{children}</ol>,
                    li: ({ children }) => <li className="leading-relaxed">{cite(children)}</li>,
                    blockquote: ({ children }) => (
                        <blockquote className="border-l-4 border-gray-200 pl-2.5 my-1.5 text-gray-500">{children}</blockquote>
                    ),
                    a: ({ children, href }) => (
                        <a href={href} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">
                            {children}
                        </a>
                    ),
                    table: ({ children }) => (
                        <div className="overflow-x-auto my-2 rounded-md border border-gray-200">
                            <table className="min-w-full border-collapse text-xs">{children}</table>
                        </div>
                    ),
                    thead: ({ children }) => <thead className="bg-gray-100">{children}</thead>,
                    th: ({ children }) => (
                        <th className="border-b border-gray-200 px-2.5 py-1.5 text-left font-semibold text-gray-700 whitespace-nowrap">
                            {cite(children)}
                        </th>
                    ),
                    td: ({ children }) => <td className="border-b border-gray-100 px-2.5 py-1.5 align-top">{cite(children)}</td>,
                    tr: ({ children }) => <tr className="hover:bg-gray-50">{children}</tr>,
                    code: ({ children }) => <code className="px-1 py-0.5 bg-gray-100 rounded text-[12px] text-pink-600">{children}</code>,
                    pre: ({ children }) => <pre className="bg-gray-50 rounded-md p-2.5 my-1.5 overflow-x-auto text-[12px]">{children}</pre>,
                    hr: () => <hr className="my-3 border-gray-200" />,
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}
