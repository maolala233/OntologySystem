import React from 'react';

/**
 * 品牌标志：三节点图谱（与 public/favicon.svg 同一套图形）。
 * 统一用于侧边栏、登录页、问答空状态等位置，避免各处出现占位字母“O”。
 */
const BrandMark: React.FC<{ size?: number; strokeWidth?: number }> = ({ size = 20, strokeWidth = 4 }) => (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
        <g stroke="#ffffff" strokeWidth={strokeWidth} strokeLinecap="round" opacity="0.9">
            <line x1="32" y1="19" x2="18" y2="43" />
            <line x1="32" y1="19" x2="46" y2="43" />
            <line x1="18" y1="43" x2="46" y2="43" />
        </g>
        <g fill="#ffffff">
            <circle cx="32" cy="19" r="7" />
            <circle cx="18" cy="43" r="7" />
            <circle cx="46" cy="43" r="7" />
        </g>
    </svg>
);

export default BrandMark;
