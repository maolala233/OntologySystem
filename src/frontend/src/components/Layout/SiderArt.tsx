import React from 'react';

/**
 * 侧栏底部的科技感插画（2026-11-19）：
 * 三块半透明等距玻璃板 + 冷色辉光 + 浮尘点，参考「本体平台参考图例」左下角的图形——
 * 菜单本身保持简单，靠一块有体积感的图形把侧栏下半截「托」住。
 * 纯装饰：aria-hidden，不参与交互。
 * compact：侧栏折叠成 80px 时的简化版（两块板 + 亮核），免得下半截折叠后又空掉。
 */
interface SiderArtProps {
    compact?: boolean;
}

const CompactArt: React.FC = () => (
    <svg width="56" height="56" viewBox="0 0 48 48" fill="none" aria-hidden="true" className="block">
        <defs>
            <radialGradient id="siderArtHaloMini" cx="0.5" cy="0.5" r="0.5">
                <stop offset="0" stopColor="#5B8DEF" stopOpacity="0.34" />
                <stop offset="1" stopColor="#5B8DEF" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="siderArtPlateMini" x1="24" y1="12" x2="24" y2="38" gradientUnits="userSpaceOnUse">
                <stop offset="0" stopColor="#F8FBFF" stopOpacity="0.98" />
                <stop offset="0.6" stopColor="#A6C4FF" stopOpacity="0.9" />
                <stop offset="1" stopColor="#8B5CF6" stopOpacity="0.45" />
            </linearGradient>
            <filter id="siderArtMiniBlur" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur stdDeviation="2.2" />
            </filter>
        </defs>
        <ellipse cx="24" cy="24" rx="22" ry="20" fill="url(#siderArtHaloMini)" />
        <polygon points="24,25 37,31.5 24,38 11,31.5" fill="#6E9BE4" opacity="0.34" transform="translate(0 3)" />
        <polygon points="24,25 37,31.5 24,38 11,31.5" fill="url(#siderArtPlateMini)" stroke="#FFFFFF" strokeOpacity="0.8" strokeWidth="0.9" />
        <ellipse cx="24" cy="23.5" rx="8.5" ry="3.6" fill="#FFFFFF" opacity="0.85" filter="url(#siderArtMiniBlur)" />
        <polygon points="24,14 38,21 24,28 10,21" fill="#5F91DE" opacity="0.32" transform="translate(0 3)" />
        <polygon points="24,14 38,21 24,28 10,21" fill="url(#siderArtPlateMini)" stroke="#FFFFFF" strokeOpacity="0.9" strokeWidth="1" />
        <circle cx="11" cy="13" r="1.2" fill="#FFFFFF" opacity="0.8" />
        <circle cx="37" cy="27" r="1" fill="#5B8DEF" opacity="0.6" />
    </svg>
);

const SiderArt: React.FC<SiderArtProps> = ({ compact = false }) => {
    if (compact) return <CompactArt />;
    return (
    <svg
        width="200"
        height="134"
        viewBox="0 50 200 134"
        fill="none"
        aria-hidden="true"
        className="mx-auto block"
    >
        <defs>
            <radialGradient id="siderArtHalo" cx="0.5" cy="0.5" r="0.5">
                <stop offset="0" stopColor="#5B8DEF" stopOpacity="0.36" />
                <stop offset="0.55" stopColor="#5B8DEF" stopOpacity="0.11" />
                <stop offset="1" stopColor="#5B8DEF" stopOpacity="0" />
            </radialGradient>
            <linearGradient
                id="siderArtPlateBottom"
                x1="100" y1="86" x2="100" y2="178"
                gradientUnits="userSpaceOnUse"
            >
                <stop offset="0" stopColor="#F7FBFF" stopOpacity="0.95" />
                <stop offset="1" stopColor="#93BAF6" stopOpacity="0.68" />
            </linearGradient>
            <linearGradient
                id="siderArtPlateMid"
                x1="100" y1="75" x2="100" y2="145"
                gradientUnits="userSpaceOnUse"
            >
                <stop offset="0" stopColor="#EAF3FF" stopOpacity="0.95" />
                <stop offset="1" stopColor="#6F9EEB" stopOpacity="0.72" />
            </linearGradient>
            <linearGradient
                id="siderArtPlateTop"
                x1="100" y1="66" x2="100" y2="114"
                gradientUnits="userSpaceOnUse"
            >
                <stop offset="0" stopColor="#F8FBFF" stopOpacity="0.98" />
                <stop offset="0.55" stopColor="#9FC0FF" stopOpacity="0.92" />
                <stop offset="1" stopColor="#8B5CF6" stopOpacity="0.5" />
            </linearGradient>
            <filter id="siderArtBlur" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur stdDeviation="6" />
            </filter>
            <filter id="siderArtBlurSoft" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur stdDeviation="2" />
            </filter>
        </defs>

        {/* 背后辉光：让插画从侧栏底部的浅色里浮出来 */}
        <ellipse cx="100" cy="108" rx="88" ry="54" fill="url(#siderArtHalo)" />

        {/* 底座薄雾与光环 */}
        <ellipse cx="100" cy="152" rx="76" ry="13" fill="#FFFFFF" opacity="0.55" filter="url(#siderArtBlur)" />
        <ellipse cx="100" cy="152" rx="82" ry="16" stroke="#5B8DEF" strokeOpacity="0.16" strokeWidth="2" />

        {/* 底层玻璃板 */}
        <g>
            <path
                d="M 112.63 98.05 L 183.37 131.95 Q 196.00 138.00 183.37 144.05 L 112.63 177.95 Q 100.00 184.00 87.37 177.95 L 16.63 144.05 Q 4.00 138.00 16.63 131.95 L 87.37 98.05 Q 100.00 92.00 112.63 98.05"
                fill="#6E9BE4"
                opacity="0.38"
            />
            <path
                d="M 112.63 92.05 L 183.37 125.95 Q 196.00 132.00 183.37 138.05 L 112.63 171.95 Q 100.00 178.00 87.37 171.95 L 16.63 138.05 Q 4.00 132.00 16.63 125.95 L 87.37 92.05 Q 100.00 86.00 112.63 92.05"
                fill="url(#siderArtPlateBottom)"
                stroke="#FFFFFF"
                strokeOpacity="0.85"
                strokeWidth="1.2"
                strokeLinejoin="round"
            />
        </g>

        {/* 中层玻璃板 */}
        <g>
            <path
                d="M 109.94 85.70 L 164.06 111.30 Q 174.00 116.00 164.06 120.70 L 109.94 146.30 Q 100.00 151.00 90.06 146.30 L 35.94 120.70 Q 26.00 116.00 35.94 111.30 L 90.06 85.70 Q 100.00 81.00 109.94 85.70"
                fill="#5F91DE"
                opacity="0.36"
            />
            <path
                d="M 109.94 79.70 L 164.06 105.30 Q 174.00 110.00 164.06 114.70 L 109.94 140.30 Q 100.00 145.00 90.06 140.30 L 35.94 114.70 Q 26.00 110.00 35.94 105.30 L 90.06 79.70 Q 100.00 75.00 109.94 79.70"
                fill="url(#siderArtPlateMid)"
                stroke="#FFFFFF"
                strokeOpacity="0.8"
                strokeWidth="1.1"
                strokeLinejoin="round"
            />
        </g>

        {/* 顶层玻璃板 */}
        <g>
            <path
                d="M 107.26 74.35 L 144.74 91.65 Q 152.00 95.00 144.74 98.35 L 107.26 115.65 Q 100.00 119.00 92.74 115.65 L 55.26 98.35 Q 48.00 95.00 55.26 91.65 L 92.74 74.35 Q 100.00 71.00 107.26 74.35"
                fill="#7C63D8"
                opacity="0.30"
            />
            <path
                d="M 107.26 69.35 L 144.74 86.65 Q 152.00 90.00 144.74 93.35 L 107.26 110.65 Q 100.00 114.00 92.74 110.65 L 55.26 93.35 Q 48.00 90.00 55.26 86.65 L 92.74 69.35 Q 100.00 66.00 107.26 69.35"
                fill="url(#siderArtPlateTop)"
                stroke="#FFFFFF"
                strokeOpacity="0.9"
                strokeWidth="1"
                strokeLinejoin="round"
            />
        </g>

        {/* 玻璃核心的亮斑 */}
        <ellipse cx="100" cy="106" rx="30" ry="11" fill="#FFFFFF" opacity="0.9" filter="url(#siderArtBlur)" />

        {/* 浮尘点 */}
        <circle cx="38" cy="86" r="2.2" fill="#FFFFFF" opacity="0.85" filter="url(#siderArtBlurSoft)" />
        <circle cx="160" cy="98" r="1.8" fill="#5B8DEF" opacity="0.6" filter="url(#siderArtBlurSoft)" />
        <circle cx="54" cy="136" r="1.5" fill="#5B8DEF" opacity="0.5" filter="url(#siderArtBlurSoft)" />
        <circle cx="152" cy="140" r="2" fill="#8B5CF6" opacity="0.45" filter="url(#siderArtBlurSoft)" />
        <circle cx="118" cy="50" r="1.4" fill="#FFFFFF" opacity="0.8" />
    </svg>
    );
};

export default SiderArt;
