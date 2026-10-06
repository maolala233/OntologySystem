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

interface TechHeroProps {
    /** 顶部等宽字体徽标行，如 ['SEMANTIC KNOWLEDGE PLATFORM', 'ENGINE · v2.0'] */
    chips?: string[];
    title: string;
    subtitle?: string;
    actions?: React.ReactNode;
    /** 紧凑模式（二级页面用，内边距与装饰收敛） */
    compact?: boolean;
    /** 右侧知识图谱星点装饰 */
    showConstellation?: boolean;
}

/**
 * 深色科技风 Hero 面板（工作台/我的项目/资产中心共用）：
 * #0F1420 底 + 网格底纹 + 蓝紫辉光 + 扫描线 + 星点装饰
 */
const TechHero: React.FC<TechHeroProps> = ({
    chips = [],
    title,
    subtitle,
    actions,
    compact = false,
    showConstellation = true,
}) => {
    const C = TECH_COLORS;

    return (
        <div
            className={`relative overflow-hidden rounded-2xl mb-6 sm:mb-8 ${compact ? '' : ''}`}
            style={{ background: C.bgDark }}
        >
            <style>{`
                .tk-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.07) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.07) 1px, transparent 1px);
                    background-size: 36px 36px;
                }
                .tk-glow { filter: blur(90px); opacity: 0.35; }
                .tk-scan {
                    background: linear-gradient(90deg, transparent, rgba(181,133,242,0.6), transparent);
                    animation: tk-scan 4.5s ease-in-out infinite;
                }
                @keyframes tk-scan { 0%,100% { transform: translateX(-10%); opacity:.2 } 50% { transform: translateX(110%); opacity:.9 } }
                .tk-node { animation: tk-pulse 3s ease-in-out infinite; }
                .tk-node:nth-of-type(2n) { animation-delay: .8s; }
                .tk-node:nth-of-type(3n) { animation-delay: 1.6s; }
                @keyframes tk-pulse { 0%,100% { opacity:.55 } 50% { opacity:1 } }
                .tk-mono { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace; }
                .tk-gradient-text {
                    background: linear-gradient(100deg, #FFFFFF 15%, ${C.textBright} 45%, ${C.purple} 90%);
                    -webkit-background-clip: text; background-clip: text; color: transparent;
                }
            `}</style>

            {/* 网格底纹 + 双色辉光 + 扫描线 */}
            <div className="absolute inset-0 tk-grid" />
            <div className="absolute -top-24 -right-16 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: C.purple }} />
            <div className="absolute -bottom-32 -left-24 w-[420px] h-[420px] rounded-full tk-glow" style={{ background: C.blue }} />
            <div className="absolute top-10 left-0 h-px w-40 tk-scan" />

            {/* 右侧知识图谱星点装饰 */}
            {showConstellation && (
                <svg
                    className={`absolute right-4 lg:right-16 top-1/2 -translate-y-1/2 hidden md:block ${compact ? 'opacity-60' : ''}`}
                    width={compact ? 280 : 360}
                    height={compact ? 200 : 260}
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

            <div className={`relative z-10 ${compact ? 'p-6 sm:p-7 lg:p-8' : 'p-6 sm:p-8 lg:p-12'}`}>
                {chips.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 sm:gap-3 mb-3 sm:mb-4">
                        {chips.map((chip, i) => (
                            <span
                                key={i}
                                className="tk-mono text-[11px] sm:text-xs tracking-[0.2em] px-3 py-1 rounded-full"
                                style={{
                                    color: i === 0 ? C.cyan : C.textSub,
                                    border: `1px solid ${C.line}`,
                                    background: i === 0 ? 'rgba(90,200,250,0.06)' : 'transparent',
                                }}
                            >
                                {chip}
                            </span>
                        ))}
                    </div>
                )}

                <h1
                    className={`tk-gradient-text font-bold mb-3 sm:mb-4 leading-tight ${compact ? 'text-xl sm:text-2xl lg:text-3xl' : 'text-2xl sm:text-3xl lg:text-[40px]'}`}
                >
                    {title}
                </h1>
                {subtitle && (
                    <p
                        className={`mb-5 sm:mb-6 max-w-2xl ${compact ? 'text-sm sm:text-base' : 'text-sm sm:text-base lg:text-lg'}`}
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
 */
export const GraphMotif: React.FC<{ seed?: number; className?: string }> = ({ seed = 0, className }) => {
    const C = TECH_COLORS;
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
