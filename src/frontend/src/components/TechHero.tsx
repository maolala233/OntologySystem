import React, { useId } from 'react';

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
    // 深蓝底上的副标题原来偏暗（#8A94B8），提到 #A9B7D9 提升可读性
    textSub: '#A9B7D9',
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
 * - light：白→浅蓝紫玻璃渐变 + 细描边 + 柔阴影 + 柔光带（二级页）
 * - dark ：深海军蓝渐变 + 蓝紫辉光 + 柔光带 + 扫描线 + 星点装饰（工作台）
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
    // 同一页面可能渲染多个 Hero：模糊/渐变 id 加唯一后缀，避免互相覆盖
    const uid = useId().replace(/:/g, '');
    const bandGradientId = `hero-band-${uid}`;
    const bandBlurId = `hero-blur-${uid}`;

    // 标题：深色档保留白→蓝紫渐变；浅色档统一用墨蓝纯色，
    // 和我的项目/资产中心的标题保持一致，避免首页出现另一套彩色标题语言
    const titleClass = isDark ? 'tk-gradient-text' : '';
    const titleStyle = isDark ? undefined : { color: '#0F172A' };

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
            `}</style>

            {/* 底纹 + 辉光 */}
            {isDark ? (
                <>
                    <div className="absolute -top-24 -right-16 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: TECH_COLORS.purple }} />
                    <div className="absolute -bottom-32 -left-24 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: TECH_COLORS.blue }} />
                    <div className="absolute top-10 left-0 h-px w-40 tk-scan" />
                </>
            ) : (
                <div className="absolute inset-0 tk-wash" />
            )}

            {/* 光带：与全屏背景同一种"带子"语言——宽、浅、模糊，不做网格线 */}
            <svg
                className="absolute inset-0 w-full h-full pointer-events-none"
                viewBox="0 0 900 300"
                preserveAspectRatio="xMidYMid slice"
                fill="none"
                aria-hidden
            >
                <defs>
                    <filter id={bandBlurId} x="-30%" y="-40%" width="160%" height="180%">
                        <feGaussianBlur stdDeviation="18" />
                    </filter>
                    <linearGradient id={bandGradientId} x1="0" y1="0" x2="900" y2="0" gradientUnits="userSpaceOnUse">
                        {isDark ? (
                            <>
                                <stop offset="0" stopColor="#FFFFFF" stopOpacity="0" />
                                <stop offset="0.35" stopColor="#B585F2" stopOpacity="0.14" />
                                <stop offset="0.65" stopColor="#FFFFFF" stopOpacity="0.04" />
                                <stop offset="1" stopColor="#5B8DEF" stopOpacity="0.12" />
                            </>
                        ) : (
                            <>
                                <stop offset="0" stopColor="#FFFFFF" stopOpacity="0" />
                                <stop offset="0.35" stopColor="#5B8DEF" stopOpacity="0.10" />
                                <stop offset="0.65" stopColor="#FFFFFF" stopOpacity="0.05" />
                                <stop offset="1" stopColor="#8B5CF6" stopOpacity="0.05" />
                            </>
                        )}
                    </linearGradient>
                </defs>
                <g filter={`url(#${bandBlurId})`} strokeLinecap="round">
                    <path
                        d="M-80 80 C 180 20, 360 150, 620 70 S 860 30, 980 90"
                        stroke={`url(#${bandGradientId})`}
                        strokeWidth="44"
                    />
                    <path
                        d="M-80 220 C 160 150, 340 270, 560 200 S 820 160, 980 220"
                        stroke={`url(#${bandGradientId})`}
                        strokeWidth="56"
                    />
                </g>
            </svg>

            {/* 右侧几何装饰：六边形网架 + 虚线轨道 + 小节点。
                紧凑版单独用更矮的尺寸和裁切后的 viewBox，避免在矮卡片里被上下截断 */}
            {showConstellation && (
                <svg
                    className={`absolute right-3 lg:right-12 top-1/2 -translate-y-1/2 hidden md:block ${compact ? 'opacity-60' : ''}`}
                    width={compact ? 160 : 300}
                    height={compact ? 100 : 216}
                    viewBox={compact ? '20 30 320 200' : '0 0 360 260'}
                    fill="none"
                    aria-hidden
                >
                    <defs>
                        <radialGradient id={`hero-core-${uid}`}>
                            <stop offset="0" stopColor={isDark ? '#8B5CF6' : '#5B8DEF'} stopOpacity={isDark ? 0.32 : 0.16} />
                            <stop offset="1" stopColor={isDark ? '#8B5CF6' : '#5B8DEF'} stopOpacity="0" />
                        </radialGradient>
                    </defs>

                    {/* 核心柔光 + 虚线轨道 */}
                    <circle cx="190" cy="130" r="112" fill={`url(#hero-core-${uid})`} />
                    <circle
                        cx="190" cy="130" r="118"
                        stroke={isDark ? 'rgba(201,212,255,0.22)' : '#D5E1F2'}
                        strokeDasharray="2 8"
                    />

                    {/* 双层六边形 + 辐条 */}
                    <g stroke={isDark ? 'rgba(201,212,255,0.22)' : '#D3DFF0'} strokeWidth="1">
                        <polygon points="280,130 235,208 145,208 100,130 145,52 235,52" />
                        <polygon points="236,130 213,170 167,170 144,130 167,90 213,90" />
                        <path d="M280 130 L236 130 M235 208 L213 170 M145 208 L167 170 M100 130 L144 130 M145 52 L167 90 M235 52 L213 90" />
                    </g>

                    {/* 外层节点 */}
                    <g>
                        <circle cx="280" cy="130" r="3" fill={C.blue} opacity="0.9" />
                        <circle cx="235" cy="208" r="2.5" fill={C.cyan} opacity="0.85" />
                        <circle cx="145" cy="208" r="2.5" fill={C.purple} opacity="0.85" />
                        <circle cx="100" cy="130" r="3" fill={C.amber} opacity="0.8" />
                        <circle cx="145" cy="52" r="2.5" fill={C.blue} opacity="0.85" />
                        <circle cx="235" cy="52" r="2.5" fill={C.cyan} opacity="0.85" />
                    </g>

                    {/* 内层节点 + 核心 */}
                    <g fill={isDark ? 'rgba(201,212,255,0.55)' : '#9FB0CB'}>
                        <circle cx="236" cy="130" r="1.5" />
                        <circle cx="213" cy="170" r="1.5" />
                        <circle cx="167" cy="170" r="1.5" />
                        <circle cx="144" cy="130" r="1.5" />
                        <circle cx="167" cy="90" r="1.5" />
                        <circle cx="213" cy="90" r="1.5" />
                    </g>
                    <circle cx="190" cy="130" r="4.5" fill={isDark ? '#C9D4FF' : '#5B8DEF'} />
                </svg>
            )}

            <div className={`relative z-10 ${compact ? 'p-4 sm:p-5 lg:p-6' : 'p-5 sm:p-6 lg:p-7'}`}>
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

                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2 sm:mb-2.5">
                    <h1
                        className={`${titleClass} font-bold leading-tight ${compact ? 'tracking-[-0.01em]' : 'tracking-[-0.02em]'} ${compact ? 'text-[18px] sm:text-[20px] lg:text-[24px]' : 'text-[22px] sm:text-[26px] lg:text-[32px]'}`}
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
                        className={`${actions ? 'mb-3.5 sm:mb-4' : ''} ${compact ? 'max-w-2xl text-sm leading-[1.6]' : 'max-w-xl text-sm sm:text-base leading-[1.7]'} [text-wrap:balance]`}
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
