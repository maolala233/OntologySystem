/**
 * 深色图谱主题 + 语义色板（06 §6.3，对比度 ≥ 4.5:1）+ 着色分配。
 * 色板唯一来源：本文件（05 §9 验收——色板单一来源）。
 */
export const GRAPH_THEME = {
    bg: '#0F1420',
    grid: '#1A2233',
    nodeDefault: '#5B8DEF',
    classNode: '#B585F2',
    edge: '#56679B',
    edgeHighlight: '#F2B950',
    text: '#E6EAF2',
    textDim: '#8B94AB',
    selected: '#F2B950',
    hover: '#5AC8FA',
};

export const SEMANTIC_PALETTE = [
    '#5B8DEF', '#34C3A4', '#F2B950', '#EF6F6C',
    '#B585F2', '#5AC8FA', '#A3B18A', '#F28CB1',
];

/** 稳定字符串→色板索引（同 group 恒同色；确定性） */
export function groupColor(group: string): string {
    let h = 0;
    for (let i = 0; i < group.length; i++) {
        h = (h * 31 + group.charCodeAt(i)) >>> 0;
    }
    return SEMANTIC_PALETTE[h % SEMANTIC_PALETTE.length];
}

/** 置信度分带（confidenceBand 着色字段候选之一） */
export function confidenceBand(c: number | null | undefined): string {
    if (c == null) return '未知';
    if (c >= 0.9) return '高 (≥0.9)';
    if (c >= 0.7) return '中 (0.7–0.9)';
    return '低 (<0.7)';
}
