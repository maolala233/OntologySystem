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

/**
 * 大尺寸（工作台）专用的浅色底。
 * 同一个 135° 渐变铺在 1370×190 的横幅上会被拉薄：终点色落在最右侧的一条窄边里，
 * 内容区几乎还是纯白，和下面那张冷蓝面板放在一起就成了「上轻下重」。
 * 这里把终点色提前到 78%，并让末端收在蓝上（0.16 而不是紫）——
 * 它下面紧挨着的是冷蓝面板，右缘从紫过渡到蓝紫，两张大面才接得顺；
 * 紧凑版（我的项目 / 资产中心）几何比例本来就合适，仍用 LIGHT_COLORS.bg。
 */
const LIGHT_BG_WIDE =
    'linear-gradient(135deg, rgba(255,255,255,0.84) 0%, rgba(252,253,255,0.55) 40%, rgba(139,92,246,0.10) 78%, rgba(91,141,239,0.16) 100%), ' +
    'linear-gradient(140deg, rgba(255,255,255,0.93) 0%, rgba(249,251,255,0.82) 55%, rgba(237,244,255,0.88) 100%)';

/** 大尺寸的柔光：紧凑版那两团按比例放大并加深，否则在大横幅里缩成两个小点 */
const LIGHT_WASH_WIDE =
    'radial-gradient(760px 400px at 96% -16%, rgba(91,141,239,0.18), transparent 70%), ' +
    'radial-gradient(660px 340px at 0% 122%, rgba(139,92,246,0.13), transparent 72%)';

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
            className={`relative overflow-hidden rounded-2xl ${compact ? 'mb-4' : 'mb-6'} ${className}`}
            style={{
                background: isDark || compact ? C.bg : LIGHT_BG_WIDE,
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
                <div
                    className={`absolute inset-0 ${compact ? 'tk-wash' : ''}`}
                    style={compact ? undefined : { background: LIGHT_WASH_WIDE }}
                />
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
                紧凑版单独用更矮的尺寸和裁切后的 viewBox，避免在矮卡片里被上下截断。
                紧凑版有操作时不再画：那块空间已经由控件占据，线稿垫在按钮背后
                只会让按钮边缘发脏 */}
            {showConstellation && !(compact && actions) && (
                <svg
                    className={`absolute top-1/2 -translate-y-1/2 hidden lg:block ${compact ? 'right-3 opacity-60' : '-right-4 lg:-right-6'}`}
                    width={compact ? 160 : 330}
                    height={compact ? 100 : 230}
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

                    {/* 核心柔光 */}
                    <circle cx="196" cy="130" r="112" fill={`url(#hero-core-${uid})`} />

                    {/* 虚线弧：16% 在浅底上等于没画，提到 24% 才算一层 */}
                    <circle
                        cx="200" cy="126" r="108"
                        stroke={isDark ? 'rgba(201,212,255,0.26)' : 'rgba(139,92,246,0.24)'}
                        strokeWidth={compact ? 1.6 : 1}
                        strokeDasharray="3 9"
                        fill="none"
                    />

                    {/* 连线分两档：主节点发出的三条更实，外围的退一档——
                        眼睛先抓主节点，再顺着线往外走，图才有读法 */}
                    <g
                        stroke={isDark ? 'rgba(201,212,255,0.34)' : 'rgba(91,141,239,0.34)'}
                        strokeWidth={compact ? 2.4 : 1.5}
                        fill="none"
                    >
                        <path d="M272 108 L 168 62 M272 108 L 326 150 M272 108 L 250 196" />
                    </g>
                    <g
                        stroke={isDark ? 'rgba(201,212,255,0.18)' : 'rgba(91,141,239,0.18)'}
                        strokeWidth={compact ? 1.6 : 1}
                        fill="none"
                    >
                        {/* compact 只留一条外围线：160×100 里再多就糊成一团 */}
                        {compact
                            ? <path d="M168 62 L 128 118" />
                            : <path d="M272 108 L 300 52 M272 108 L 206 118 M168 62 L 128 118 M128 118 L 180 168 M180 168 L 250 196 M206 118 L 180 168" />}
                    </g>

                    {/* 主节点：光晕 + 最大半径，整张图的视觉重心 */}
                    <circle cx="272" cy="108" r={compact ? 30 : 20} fill={isDark ? 'rgba(181,133,242,0.16)' : 'rgba(139,92,246,0.13)'} />
                    <circle cx="272" cy="108" r={compact ? 10.5 : 7} fill={C.blue} opacity="0.92" />

                    {/* 卫星节点：半径从 4.5 到 2.5 拉开差距，别做成一样大 */}
                    <circle cx="168" cy="62" r={compact ? 6.8 : 4.5} fill={C.cyan} opacity="0.88" />
                    <circle cx="326" cy="150" r={compact ? 5.3 : 3.5} fill={C.amber} opacity="0.85" />
                    <circle cx="250" cy="196" r={compact ? 6 : 4} fill={C.blue} opacity="0.80" />
                    <circle cx="128" cy="118" r={compact ? 4.5 : 3} fill={C.purple} opacity="0.80" />
                    {!compact && (
                        <>
                            <circle cx="300" cy="52" r="3" fill={C.cyan} opacity="0.80" />
                            <circle cx="180" cy="168" r="3" fill={C.purple} opacity="0.70" />
                            <circle cx="206" cy="118" r="2.5" fill={isDark ? 'rgba(201,212,255,0.55)' : '#A9B8D0'} />
                        </>
                    )}
                </svg>
            )}

            {/* 紧凑版（二级页）的纵向节奏（2026-11-25 收口）：
                原来是上下各 24px 内边距 + 24px 标题 + 10px 行距 + 14px 按钮间距，
                一张纯信息卡在 1080 高屏上要吃掉近 1/5；现在压到 18px 内边距、
                20px 标题、13px 副标题，文字块缩一圈，卡片不再比下方内容还抢眼。
                宽屏上操作从副标题下方挪到右侧居中：右侧本来只挂着装饰线稿，
                按钮过去之后卡片高度少约 40px，主操作也回到视线落点上。
                首页大卡（工作台）同步收一档：内边距 28→20/24、标题 32→24、副标题 16→15；
                它仍是全站最大的标题，只是不再比下方内容板还占屏。 */}
            <div className={`relative z-10 ${compact ? 'px-4 py-3.5 sm:px-5 sm:py-4 lg:py-[18px]' : 'p-4 sm:p-5 lg:px-6 lg:py-5'}`}>
                <div className={compact ? 'flex flex-col gap-y-3 xl:flex-row xl:items-center xl:gap-x-8' : ''}>
                    <div className="min-w-0 flex-1">
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

                        <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${compact ? 'mb-1.5' : 'mb-2'}`}>
                            <h1
                                className={`${titleClass} font-bold leading-tight ${compact
                                    ? 'tracking-[-0.01em] text-[17px] sm:text-[18px] lg:text-[20px]'
                                    : 'tracking-[-0.02em] text-[20px] sm:text-[22px] lg:text-[24px]'}`}
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
                                // 只有「上下排布、下面还有按钮」时才留下边距；紧凑版按钮在右侧，
                                // 文字块与操作之间的间距交给外层 flex 的 gap-y-3
                                className={`${!compact && actions ? 'mb-3.5' : ''} ${compact ? 'max-w-2xl text-[12.5px] leading-[1.65] sm:text-[13px]' : 'max-w-2xl text-sm leading-[1.7] sm:text-[15px]'} [text-wrap:balance]`}
                                style={{ color: C.textSub }}
                            >
                                {subtitle}
                            </p>
                        )}
                    </div>
                    {actions && (
                        <div className={`flex flex-wrap gap-2.5 sm:gap-3 ${compact ? 'shrink-0 xl:justify-end' : ''}`}>
                            {actions}
                        </div>
                    )}
                </div>
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
