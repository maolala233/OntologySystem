import React from 'react';

// 图谱页同源色板（features/graph-explorer/colors.ts 的 GRAPH_THEME）
export const TECH_COLORS = {
    bgDark: '#0F1420',
    panel: '#131A2A',
    line: '#2A3550',
    purple: '#B585F2',
    blue: '#5B8DEF',
    cyan: '#5AC8FA',
    amber: '#F2B950',
    textSub: '#8A94B8',
    textBright: '#C9D4FF',
};

/**
 * 浅色档色板：与浅蓝画布 / 白色面板同一套语言。
 * 深色只保留给「工作台」这一处品牌门面，其余页面统一走浅色。
 */
const LIGHT_COLORS = {
    // 半透明的两层玻璃渐变：让下方的氛围背景（流动光带/辉光）隐约透上来
    bg:
        'linear-gradient(135deg, rgba(255,255,255,0.82) 0%, rgba(255,255,255,0.46) 44%, rgba(139,92,246,0.08) 100%), ' +
        'linear-gradient(140deg, rgba(255,255,255,0.95) 0%, rgba(252,253,255,0.86) 55%, rgba(244,248,255,0.90) 100%)',
    border: 'rgba(226, 235, 248, 0.9)',
    line: '#D9E2F2',
    purple: '#8B5CF6',
    blue: '#5B8DEF',
    cyan: '#2FA8C9',
    amber: '#E0A02A',
    textSub: '#52607A',
    textBright: '#2F62D8',
};

export type HeroTone = 'light' | 'dark';

/** 深色档色板：底色用偏蓝的深海军蓝渐变（比原 #0F1420 略抬一档），
 *  配合 1px 内描边 + 大扩散柔阴影，让它像一张"浮起的深色横幅"而不是黑洞。
 *  中深靛蓝（#2E4C86 一带）试过：大块中等明度的色面反而最别扭，已回退。 */
const DARK_PALETTE = {
    bg: 'linear-gradient(120deg, #121B31 0%, #0F1729 55%, #141A33 100%)',
    border: 'rgba(255, 255, 255, 0.08)',
    line: TECH_COLORS.line,
    purple: TECH_COLORS.purple,
    blue: TECH_COLORS.blue,
    cyan: TECH_COLORS.cyan,
    amber: TECH_COLORS.amber,
    textSub: TECH_COLORS.textSub,
    textBright: TECH_COLORS.textBright,
};

interface TechHeroProps {
    /** 顶部等宽字体徽标行，如 ['SEMANTIC KNOWLEDGE PLATFORM', 'ENGINE · v2.0'] */
    chips?: string[];
    title: string;
    /** 标题后的小号英文标签（可选）：补足短标题的视觉重量，不占额外行高 */
    titleSuffix?: string;
    subtitle?: string;
    actions?: React.ReactNode;
    /** 紧凑模式（二级页面用，内边距与装饰收敛） */
    compact?: boolean;
    /** 右侧知识图谱星点装饰 */
    showConstellation?: boolean;
    /**
     * 深/浅两档。默认浅色（与浅蓝画布一致）；
     * 工作台显式传 dark，作为全站唯一的深色品牌门面。
     */
    tone?: HeroTone;
    /** 追加到根节点的自定义类（如覆盖底部外边距） */
    className?: string;
}

/**
 * 科技风 Hero 面板（工作台/我的项目/资产中心共用）：
 * - light：白→浅蓝紫渐变底 + 细描边 + 柔阴影 + 淡网格/辉光（二级页）
 * - dark ：#0F1420 底 + 网格底纹 + 蓝紫辉光 + 扫描线 + 星点装饰（工作台）
 */
const TechHero: React.FC<TechHeroProps> = ({
    chips = [],
    title,
    titleSuffix,
    subtitle,
    actions,
    compact = false,
    showConstellation = true,
    tone = 'light',
    className = '',
}) => {
    const isDark = tone === 'dark';
    const C = isDark ? DARK_PALETTE : LIGHT_COLORS;

    // 标题：深色档用白→蓝紫渐变；浅色档保持墨蓝（紧凑版直接纯色，避免小面板上出现彩色标题）
    const titleClass = isDark
        ? 'tk-gradient-text'
        : compact
            ? ''
            : 'tk-gradient-text-light';
    const titleStyle = !isDark && compact ? { color: '#0F172A' } : undefined;

    return (
        <div
            className={`relative overflow-hidden rounded-2xl mb-6 sm:mb-8 ${className}`}
            style={{
                background: C.bg,
                border: `1px solid ${C.border}`,
                // 深色档也补一层大扩散柔阴影：和下方白卡共用同一套"浮起"语言，
                // 只是材质相反，避免它看起来像贴在画布上的一个黑块
                boxShadow: isDark
                    ? '0 30px 60px -40px rgba(15, 23, 42, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.07)'
                    : 'inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 -1px 0 rgba(219, 228, 242, 0.55), 0 18px 44px -32px rgba(15, 23, 42, 0.38), 0 2px 6px rgba(15, 23, 42, 0.04)',
            }}
        >
            <style>{`
                .tk-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.056) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.056) 1px, transparent 1px);
                    background-size: 36px 36px;
                }
                .tk-grid-light {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.075) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.075) 1px, transparent 1px);
                    background-size: 36px 36px;
                }
                /* 浅色档的辉光改用径向渐变一次画好（不用 blur 大圆，合成开销更低） */
                .tk-wash {
                    background:
                        radial-gradient(560px 300px at 94% -18%, rgba(91,141,239,0.16), transparent 70%),
                        radial-gradient(500px 280px at 2% 118%, rgba(139,92,246,0.10), transparent 70%);
                }
                .tk-glow { filter: blur(90px); opacity: 0.28; }
                .tk-scan {
                    background: linear-gradient(90deg, transparent, rgba(181,133,242,0.5), transparent);
                    animation: tk-scan 4.5s ease-in-out infinite;
                }
                @keyframes tk-scan { 0%,100% { transform: translateX(-10%); opacity:.2 } 50% { transform: translateX(110%); opacity:.9 } }
                .tk-node { animation: tk-pulse 3s ease-in-out infinite; }
                .tk-node:nth-of-type(2n) { animation-delay: .8s; }
                .tk-node:nth-of-type(3n) { animation-delay: 1.6s; }
                @keyframes tk-pulse { 0%,100% { opacity:.55 } 50% { opacity:1 } }
                .tk-mono { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace; }
                .tk-gradient-text {
                    background: linear-gradient(100deg, #FFFFFF 15%, ${TECH_COLORS.textBright} 45%, ${TECH_COLORS.purple} 90%);
                    -webkit-background-clip: text; background-clip: text; color: transparent;
                }
                .tk-gradient-text-light {
                    background: linear-gradient(96deg, #16233D 0%, #2F5BD0 62%, #7C3AED 100%);
                    -webkit-background-clip: text; background-clip: text; color: transparent;
                }
            `}</style>

            {/* 底纹 + 辉光 */}
            <div className={`absolute inset-0 ${isDark ? 'tk-grid' : 'tk-grid-light'}`} />
            {isDark ? (
                <>
                    <div className="absolute -top-24 -right-16 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: TECH_COLORS.purple }} />
                    <div className="absolute -bottom-32 -left-24 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: TECH_COLORS.blue }} />
                    <div className="absolute top-10 left-0 h-px w-40 tk-scan" />
                </>
            ) : (
                <div className="absolute inset-0 tk-wash" />
            )}

            {/* 右侧知识图谱星点装饰 */}
            {showConstellation && (
                <svg
                    className={`absolute right-4 lg:right-16 top-1/2 -translate-y-1/2 hidden md:block ${compact ? 'opacity-60' : ''}`}
                    width={compact ? 240 : 320}
                    height={compact ? 180 : 230}
                    viewBox="0 0 360 260"
                    fill="none"
                    preserveAspectRatio="xMidYMid meet"
                >
                    <g stroke={C.line} strokeWidth="1">
                        <line x1="60" y1="50" x2="170" y2="110" />
                        <line x1="170" y1="110" x2="300" y2="60" />
                        <line x1="170" y1="110" x2="120" y2="200" />
                        <line x1="170" y1="110" x2="290" y2="190" />
                        <line x1="300" y1="60" x2="290" y2="190" />
                        <line x1="60" y1="50" x2="120" y2="200" />
                    </g>
                    <circle className="tk-node" cx="60" cy="50" r="7" fill={C.blue} />
                    <circle className="tk-node" cx="170" cy="110" r="11" fill={C.purple} />
                    <circle className="tk-node" cx="300" cy="60" r="6" fill={C.cyan} />
                    <circle className="tk-node" cx="120" cy="200" r="6" fill={C.amber} />
                    <circle className="tk-node" cx="290" cy="190" r="8" fill={C.blue} />
                </svg>
            )}

            <div className={`relative z-10 ${compact ? 'p-4 sm:p-5 lg:p-6' : 'p-6 sm:p-7 lg:p-9'}`}>
                {chips.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 sm:gap-3 mb-2.5 sm:mb-3">
                        {chips.map((chip, i) => (
                            <span
                                key={i}
                                className="tk-mono text-[11px] sm:text-xs tracking-[0.2em] px-3 py-1 rounded-full"
                                style={isDark ? {
                                    color: i === 0 ? TECH_COLORS.cyan : TECH_COLORS.textSub,
                                    border: `1px solid ${TECH_COLORS.line}`,
                                    background: i === 0 ? 'rgba(90,200,250,0.06)' : 'transparent',
                                } : {
                                    color: i === 0 ? '#2F62D8' : LIGHT_COLORS.textSub,
                                    border: `1px solid ${i === 0 ? 'rgba(91,141,239,0.22)' : '#E4EAF5'}`,
                                    background: i === 0 ? 'rgba(91,141,239,0.10)' : 'rgba(255,255,255,0.72)',
                                }}
                            >
                                {chip}
                            </span>
                        ))}
                    </div>
                )}

                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2.5 sm:mb-3">
                    <h1
                        className={`${titleClass} font-bold leading-tight ${compact ? 'text-[18px] sm:text-[20px] lg:text-[24px]' : 'text-[22px] sm:text-[26px] lg:text-[32px]'}`}
                        style={titleStyle}
                    >
                        {title}
                    </h1>
                    {titleSuffix && (
                        <>
                            <span
                                aria-hidden
                                className="hidden sm:block self-center w-px h-3.5"
                                style={{ background: isDark ? 'rgba(255,255,255,0.18)' : '#DCE4F2' }}
                            />
                            <span
                                className="tk-mono text-[10px] sm:text-[11px] tracking-[0.18em] uppercase"
                                style={{ color: isDark ? 'rgba(201,212,255,0.6)' : '#8A94AB' }}
                            >
                                {titleSuffix}
                            </span>
                        </>
                    )}
                </div>
                {subtitle && (
                    <p
                        // 只有下面还有按钮时才留下边距；否则这段 margin 会和卡片底部内边距叠加，
                        // 让卡片"上紧下松"（我的项目/资产中心都没有 actions，表现最明显）
                        className={`${actions ? 'mb-4 sm:mb-5' : ''} max-w-2xl ${compact ? 'text-[13px] sm:text-sm' : 'text-sm sm:text-base'}`}
                        style={{ color: C.textSub }}
                    >
                        {subtitle}
                    </p>
                )}
                {actions && <div className="flex flex-wrap gap-3 sm:gap-4">{actions}</div>}
            </div>
        </div>
    );
};

/**
 * 卡片封面装饰：小型知识图谱星点（seed 决定强调色与微移，避免每张卡完全相同）
 * tone 默认浅色，与改版后的浅色封面一致；深色档保留给需要深底封面的场景。
 */
export const GraphMotif: React.FC<{ seed?: number; className?: string; tone?: HeroTone }> = ({
    seed = 0,
    className,
    tone = 'light',
}) => {
    const C = tone === 'dark' ? TECH_COLORS : LIGHT_COLORS;
    const s = Math.abs(seed);
    const palette = [C.purple, C.blue, C.cyan, C.amber];
    const accent = palette[s % palette.length];
    const dx = (s % 5) * 8 - 16;
    const dy = (s % 3) * 6 - 6;
    const nodes = [
        { x: 40, y: 34, r: 5, c: C.blue },
        { x: 120, y: 66, r: 8, c: accent },
        { x: 210, y: 30, r: 4, c: C.cyan },
        { x: 268, y: 92, r: 6, c: C.amber },
        { x: 86, y: 110, r: 4, c: accent },
        { x: 196, y: 112, r: 5, c: C.blue },
    ];
    const edges: Array<[number, number]> = [[0, 1], [1, 2], [1, 4], [1, 5], [2, 3], [3, 5], [0, 4]];
    return (
        <svg
            className={className}
            viewBox="0 0 320 140"
            fill="none"
            preserveAspectRatio="xMidYMid slice"
            style={{ transform: `translate(${dx}px, ${dy}px)` }}
        >
            <g stroke={C.line} strokeWidth="1">
                {edges.map(([a, b], i) => (
                    <line key={i} x1={nodes[a].x} y1={nodes[a].y} x2={nodes[b].x} y2={nodes[b].y} />
                ))}
            </g>
            {nodes.map((n, i) => (
                <circle key={i} className="tk-node" cx={n.x} cy={n.y} r={n.r} fill={n.c} />
            ))}
        </svg>
    );
};

export default TechHero;
