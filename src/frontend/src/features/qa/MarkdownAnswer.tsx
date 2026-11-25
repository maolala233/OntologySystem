// src/features/qa/MarkdownAnswer.tsx - 问答答案 Markdown 渲染（05 §6）
// react-markdown + remark-gfm：标题/加粗/列表/表格正常排版；
// 正文中的 [n] 引用标号渲染为可点击角标，点击滚动定位到对应溯源引用卡。
// 配色统一到站内冷调（2026-11-19）：原先正文是另一套 Tailwind 灰阶
// （gray-800/100/50 + 粉红代码），和问答区其余部分（#1F2A44 / #66738F / #E7EEF9）
// 不是一个色温，读起来像在冷蓝页面上嵌了一个 Markdown 预览器。
// 这里全部换成冷墨 + 品牌蓝；表格的行 hover 沿用详情页表格的口径
// ——表头是更深的色带、hover 是更浅的一层，两者要能看出层级。
import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Tip from '../../shared/Tip';

/**
 * Safari < 16.4 的正则引擎不支持 lookbehind。remark-gfm 的邮箱自动链接扩展
 * 在解析时会构造 `new RegExp('(?<=…)')` 并抛 SyntaxError；本组件用于问答 /
 * Copilot / 报告，且全局没有 ErrorBoundary，渲染期抛错会让整页白屏。
 * 这里只检测一次：不支持时就不挂 remark-gfm（表格等 GFM 语法降级为纯文本，
 * 但页面不再崩）。esbuild 只会把生产包里的字面量正则转成 new RegExp，
 * 不会补 lookbehind，所以这个检测必须放在业务侧。
 */
const SUPPORTS_LOOKBEHIND = (() => {
    try {
        new RegExp('(?<=a)b');
        return true;
    } catch {
        return false;
    }
})();

const REMARK_PLUGINS = SUPPORTS_LOOKBEHIND ? [remarkGfm] : [];

const CITE_RE = /\[(\d{1,2})\]/g;

interface MarkdownAnswerProps {
    content: string;
    /** 溯源引用总数（判断 [n] 是否可点击） */
    sourceCount: number;
    /** 点击引用标号（n 为 1 起的引用序号） */
    onCite: (n: number) => void;
    /** 紧凑版（嵌在溯源卡里看切片原文）：正文与标题各收一档 */
    dense?: boolean;
    /** 是否把 [n] 当引用角标解析；溯源原文里的方括号是正文，传 false */
    citations?: boolean;
}

/** 引用角标：n 在溯源范围内可点击，否则灰显纯文本 */
function CitationMark({ n, active, onCite }: { n: number; active: boolean; onCite: (n: number) => void }) {
    if (!active) {
        return <sup className="mx-0.5 text-[10px] text-[#A3ADC2]">[{n}]</sup>;
    }
    return (
        <sup>
            <Tip text={`查看引用 [${n}]`}>
                <button
                    type="button"
                    onClick={e => {
                        e.stopPropagation();
                        onCite(n);
                    }}
                    className="mx-0.5 inline-flex h-[16px] min-w-[18px] cursor-pointer items-center justify-center
                        rounded-full border border-[rgba(91,141,239,0.30)] bg-[rgba(91,141,239,0.08)] px-1.5
                        align-super text-[10px] font-medium leading-none text-[#2A57C6] transition-colors
                        hover:border-[#5B8DEF] hover:bg-[rgba(91,141,239,0.16)]
                        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)]"
                >
                    {n}
                </button>
            </Tip>
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

export default function MarkdownAnswer({ content, sourceCount, onCite, dense, citations = true }: MarkdownAnswerProps) {
    const cite = (children: React.ReactNode) => (citations ? withCitations(children, sourceCount, onCite) : children);
    // 紧凑版只服务溯源卡里的切片原文：整体收一档，标题同比缩小，
    // 否则 16px 的 h1 会从 12px 的引用卡里顶出来
    const h1 = dense ? 'text-[14px]' : 'text-[16px]';
    const h2 = dense ? 'text-[13.5px]' : 'text-[15px]';
    const h3 = dense ? 'text-[13px]' : 'text-[14px]';
    const h4 = dense ? 'text-[12.5px]' : 'text-[13.5px]';
    return (
        /* 首/末块去掉外边距（2026-11-19 用户反馈）：正文块都带 my-*，落在容器里会和
           容器自己的 padding 叠成一段说不清来源的留白——回答以段落开头是 6+10，以标题开头
           是 14+10，以表格开头是 8+10，于是"有的上面空得多、有的正常"。这里统一把第一个块的
           上边距、最后一个块的下边距归零，留白只由容器决定 */
        <div className={`break-words leading-[1.75] text-[#2B3852] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 ${dense ? 'text-[12.5px]' : 'text-[14px]'}`}>
            <ReactMarkdown
                remarkPlugins={REMARK_PLUGINS}
                components={{
                    h1: ({ children }) => <h1 className={`mb-1.5 mt-3.5 font-semibold text-[#0F172A] ${h1}`}>{cite(children)}</h1>,
                    h2: ({ children }) => <h2 className={`mb-1.5 mt-3 font-semibold text-[#0F172A] ${h2}`}>{cite(children)}</h2>,
                    h3: ({ children }) => <h3 className={`mb-1 mt-2.5 font-semibold text-[#1F2A44] ${h3}`}>{cite(children)}</h3>,
                    h4: ({ children }) => <h4 className={`mb-1 mt-2 font-semibold text-[#1F2A44] ${h4}`}>{cite(children)}</h4>,
                    p: ({ children }) => <p className="my-1.5">{cite(children)}</p>,
                    strong: ({ children }) => <strong className="font-semibold text-[#0F172A]">{cite(children)}</strong>,
                    em: ({ children }) => <em className="italic">{cite(children)}</em>,
                    ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5 marker:text-[#5B8DEF]">{children}</ul>,
                    ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5 marker:text-[#5B8DEF]">{children}</ol>,
                    li: ({ children }) => <li className="leading-[1.75]">{cite(children)}</li>,
                    blockquote: ({ children }) => (
                        <blockquote className="my-2 rounded-r-[8px] border-l-[3px] border-[rgba(91,141,239,0.35)] bg-[rgba(91,141,239,0.045)] px-3 py-1.5 text-[#52607A]">
                            {children}
                        </blockquote>
                    ),
                    a: ({ children, href }) => (
                        <a
                            href={href}
                            target="_blank"
                            rel="noreferrer"
                            className="break-all text-[#2A57C6] underline decoration-[rgba(91,141,239,0.45)] underline-offset-2 transition-colors hover:decoration-[#2A57C6]"
                        >
                            {children}
                        </a>
                    ),
                    table: ({ children }) => (
                        <div className="my-2 max-w-full overflow-x-auto overscroll-x-contain rounded-[10px] border border-[rgba(91,141,239,0.14)]">
                            <table className="w-max min-w-full border-collapse text-[12.5px] [&_tr:last-child>td]:border-b-0">
                                {children}
                            </table>
                        </div>
                    ),
                    thead: ({ children }) => <thead className="bg-[rgba(91,141,239,0.08)]">{children}</thead>,
                    th: ({ children }) => (
                        <th className="whitespace-nowrap border-b border-[rgba(91,141,239,0.14)] px-3 py-1.5 text-left font-semibold text-[#3B4A63]">
                            {cite(children)}
                        </th>
                    ),
                    td: ({ children }) => (
                        /* 单元格不折行（2026-11-19 用户反馈）：数据列一多，换行会把一行数据
                           摊成三行，「哪一格对哪一列」就看不出来了。宁可表格变宽、
                           外层横向滚动，也要保持一格一行 */
                        <td className="whitespace-nowrap border-b border-[#EDF2FA] px-3 py-1.5 align-top text-[#2B3852]">{cite(children)}</td>
                    ),
                    tr: ({ children }) => <tr className="transition-colors hover:bg-[rgba(91,141,239,0.045)]">{children}</tr>,
                    /* 代码块里的 <code> 只负责等宽字形，外框交给 <pre>；
                       行内代码才需要那枚浅蓝药丸，否则代码块内部会再套一层底色 */
                    code: ({ className, children }) =>
                        className
                            ? <code className="tk-mono text-[12px] leading-[1.7] text-[#2B3852]">{children}</code>
                            : <code className="tk-mono rounded-[5px] border border-[rgba(91,141,239,0.14)] bg-[rgba(91,141,239,0.07)] px-1.5 py-[1px] text-[12.5px] text-[#2A57C6]">{children}</code>,
                    pre: ({ children }) => (
                        <pre className="my-2 overflow-x-auto rounded-[10px] border border-[rgba(91,141,239,0.14)] bg-[#F4F8FE] p-3">
                            {children}
                        </pre>
                    ),
                    hr: () => <hr className="my-3 border-[#EDF2FA]" />,
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}
