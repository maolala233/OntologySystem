// src/features/qa/quoteText.ts - 溯源引用文本清理：把解析器残留（markdown 表格、
// 标题符、分隔线、连续空白）转成用户可直接阅读的文本。问答卡与检索过程卡共用。
export function cleanQuote(raw: string | undefined | null): string {
    if (!raw) return '';
    let t = raw.trim()
        // PDF/HTML 换行残留
        .replace(/<br\s*\/?>/gi, ' ')
        .replace(/<[^>]+>/g, ' ')
        // markdown 标题符
        .replace(/^#{1,6}\s*/gm, '')
        // 表格分隔行（|---|---| 或 ---|--- 等）
        .replace(/^\s*\|?[\s\-—_=|:·]+\|[\s\-—_=|:·]*$/gm, '')
        // 行首/行尾的表格竖线
        .replace(/^\s*\|/gm, '').replace(/\|\s*$/gm, '')
        // 单元格竖线转间隔（保内容可读）
        .replace(/\s*\|\s*/g, '　')
        // 多余空白压缩
        .replace(/[ \t]{2,}/g, ' ')
        .replace(/　{2,}/g, '　')
        .replace(/\n{2,}/g, '\n')
        // 超长单元格/行截断（表格切片的说明列动辄数百字，撑爆引用卡）
        .split('\n')
        .map(l => (l.length > 100 ? l.slice(0, 100) + '…' : l))
        .join('\n')
        .trim();
    return t;
}

/** 从「X」类共 N 个实例…格式的图节点清单引用中，提取人话摘要行（类名/总数/来源）。 */
export function classSummaryLine(quote: string): { summary: string; rest: string } | null {
    const m = quote.match(/^「(.+?)」(?:类)?共\s*(\d+)\s*(?:个实例|个|条)(?:（来源：(.+?)）)?[:：]\s*([\s\S]*)$/);
    if (!m) return null;
    const [, cls, n, src, rest] = m;
    const srcPart = src ? `，来源：${src}` : '';
    return { summary: `${cls}：共 ${n} 个${srcPart}`, rest: rest.trim() };
}
