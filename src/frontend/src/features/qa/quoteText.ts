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

/** 跨文件拼接时 parser.py 写在正文里的分隔标记，会跟着 chunk 一起被检索出来 */
const FILE_MARK_RE = /^<<<<<<\s*文件(?:开始|结束)：.*?>>>>>>\s*$/gm;

/** 「a | b | c」形式的一行 → 单元格数组；不是表格行返回 null */
function splitPipeRow(line: string): string[] | null {
    const t = line.trim();
    if (!t.includes('|')) return null;
    const cells = t.split('|').map(c => c.trim());
    // 只去掉行首因「| a | b」造成的那个空单元；行尾的空单元是有意义的空单元格，
    // 去掉会让「E-005 | … | 停机 |」比别的行少一列，整块表格就此断成两段
    while (cells.length && cells[0] === '') cells.shift();
    return cells.length >= 2 ? cells : null;
}

const mdRow = (cells: string[]) => `| ${cells.map(c => c.replace(/\|/g, '\\|')).join(' | ')} |`;

/**
 * 溯源引用正文：把解析器拍平的文本恢复成能真正渲染的 markdown。
 * 三件事（2026-11-19 用户反馈「引用看起来就是纯文本」）：
 * 1) 去掉跨文件拼接的 `<<<<<< 文件开始/结束：xxx >>>>>>` 标记；
 * 2) docx / pptx 的表格在解析阶段就被拍成「a | b | c」的行（parser.py:350 / 431），
 *    chunk 里没有 |---| 分隔行，markdown 只能当普通段落渲染。这里按「连续 ≥2 行、
 *    列数一致」识别成表并补上分隔行——首行当表头：切片的表头可能被切在上一块，
 *    但也不给表格编造列名，宁可让真实首行顶在那里；
 * 3) 其余行的单个换行在 CommonMark 里会被吞成空格，逐行补两个空格转硬换行，
 *    条款清单 / 设备名单才不会挤成一整段。
 */
export function toCitationMarkdown(raw: string | undefined | null): string {
    if (!raw) return '';
    const lines = raw.replace(/\r\n?/g, '\n').replace(FILE_MARK_RE, '').split('\n');
    const out: string[] = [];
    let i = 0;
    while (i < lines.length) {
        const head = splitPipeRow(lines[i]);
        if (head) {
            const rows: string[][] = [head];
            let j = i + 1;
            // 切片里的表块中间常夹空行，跳过但不算断块
            while (j < lines.length) {
                if (!lines[j].trim()) { j++; continue; }
                const cells = splitPipeRow(lines[j]);
                if (!cells || cells.length !== head.length) break;
                rows.push(cells);
                j++;
            }
            if (rows.length >= 2) {
                out.push(mdRow(rows[0]));
                out.push(`| ${rows[0].map(() => '---').join(' | ')} |`);
                rows.slice(1).forEach(r => out.push(mdRow(r)));
                out.push('');
                i = j;
                continue;
            }
        }
        const line = lines[i].trimEnd();
        out.push(line ? `${line}  ` : '');
        i++;
    }
    return out.join('\n').trim();
}
