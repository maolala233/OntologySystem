/**
 * 面板装饰线稿（2026-11-17）：一组「节点 + 连线」的浅色曲线，给浅色面板补一点科技质感。
 *
 * 只做装饰：容器上务必给它 pointer-events-none + aria-hidden，透明度由调用方控制；
 * 渐变 id 必须各面板唯一（同一页面出现两份时 id 冲突会让后一份拿到前一份的渐变）。
 */
import React from 'react';

const DecoConstellation: React.FC<{ id: string; className?: string; style?: React.CSSProperties }> = ({
    id, className, style,
}) => (
    <svg
        className={className}
        style={style}
        viewBox="0 0 420 150"
        fill="none"
        preserveAspectRatio="xMaxYMin meet"
        aria-hidden
        focusable="false"
    >
        <defs>
            <linearGradient id={`${id}-line`} x1="0" y1="1" x2="1" y2="0">
                <stop offset="0%" stopColor="#5B8DEF" stopOpacity="0.05" />
                <stop offset="55%" stopColor="#5B8DEF" stopOpacity="0.28" />
                <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0.36" />
            </linearGradient>
            <radialGradient id={`${id}-dot`} cx="0.5" cy="0.5" r="0.5">
                <stop offset="0%" stopColor="#8B5CF6" stopOpacity="0.42" />
                <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0" />
            </radialGradient>
        </defs>

        <g stroke={`url(#${id}-line)`} strokeWidth="1.1" strokeLinecap="round">
            <path d="M6 128 C 64 128, 78 62, 134 62 S 196 110, 250 110 S 306 34, 378 34" />
            <path d="M6 108 C 70 108, 96 84, 150 84 S 214 128, 268 128 S 328 66, 414 66"
                  strokeDasharray="2 7" opacity="0.85" />
        </g>

        {/* 轨道环 + 节点：右上角一组，越靠右越清楚（视觉重量落在面板边缘，不压内容） */}
        <circle cx="378" cy="34" r="17" stroke={`url(#${id}-line)`} strokeOpacity="0.8" />
        <circle cx="378" cy="34" r="10" fill={`url(#${id}-dot)`} />
        <circle cx="250" cy="110" r="8" fill={`url(#${id}-dot)`} />
        <circle cx="134" cy="62" r="9" fill={`url(#${id}-dot)`} />

        <g>
            <circle cx="378" cy="34" r="3" fill="#8B5CF6" fillOpacity="0.5" />
            <circle cx="250" cy="110" r="2.2" fill="#5B8DEF" fillOpacity="0.42" />
            <circle cx="134" cy="62" r="2.6" fill="#5B8DEF" fillOpacity="0.45" />
            <circle cx="150" cy="84" r="2.2" fill="#8B5CF6" fillOpacity="0.34" />
            <circle cx="268" cy="128" r="2.2" fill="#8B5CF6" fillOpacity="0.34" />
        </g>
    </svg>
);

export default DecoConstellation;
