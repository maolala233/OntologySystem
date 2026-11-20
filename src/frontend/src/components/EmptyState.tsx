import React from 'react';
import { Button } from 'antd';
import { SearchOutlined } from '@ant-design/icons';

interface EmptyStateProps {
    /** light：浅色页面（与「我的项目」搜索空态同款）；dark：图谱深色左右列表 */
    tone?: 'light' | 'dark';
    /** 窄栏（如 MCP 工具列表）用紧凑版，图标和留白各收一档 */
    compact?: boolean;
    title: React.ReactNode;
    hint?: React.ReactNode;
    actionText?: string;
    onAction?: () => void;
    icon?: React.ReactNode;
    className?: string;
}

/**
 * 搜索空结果提示（2026-11-20）：
 * 全站搜索无结果统一成一套——渐变图标块 + 主标题 + 一句原因/下一步 + 次要按钮。
 * 造型取自「我的项目」的搜索空态；图谱深色列表用 dark，窄栏用 compact。
 */
const EmptyState: React.FC<EmptyStateProps> = ({
    tone = 'light', compact = false, title, hint, actionText, onAction, icon, className = '',
}) => {
    const dark = tone === 'dark';
    const iconBox = compact
        ? (dark ? 'h-10 w-10 text-[18px]' : 'h-11 w-11 text-[20px]')
        : (dark ? 'h-12 w-12 text-[22px]' : 'h-14 w-14 text-[24px]');
    const titleCls = compact
        ? 'mt-3 text-[13px]'
        : (dark ? 'mt-3 text-[13.5px]' : 'mt-4 text-[15px]');
    const hintCls = compact
        ? 'mt-1 text-[11.5px]'
        : (dark ? 'mt-1 text-[11.5px]' : 'mt-1.5 text-[12.5px]');
    return (
        <div
            className={`flex flex-col items-center justify-center px-4 text-center ${
                compact ? 'py-8' : (dark ? 'py-8' : 'py-14')
            } ${className}`}
        >
            <div
                className={`flex flex-col items-center ${
                    compact ? 'max-w-[240px]' : (dark ? 'max-w-[260px]' : 'max-w-[420px]')
                }`}
            >
                <span
                    className={`flex items-center justify-center rounded-2xl ${iconBox}`}
                    style={{
                        color: dark ? '#9BC0F7' : '#2F62D8',
                        background: dark
                            ? 'linear-gradient(135deg, rgba(91,141,239,0.22), rgba(139,92,246,0.14))'
                            : 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))',
                    }}
                >
                    {icon ?? <SearchOutlined />}
                </span>
                <h3
                    className={`font-semibold ${titleCls}`}
                    style={{ color: dark ? '#E6EAF2' : '#0F172A' }}
                >
                    {title}
                </h3>
                {hint && (
                    <p
                        className={`leading-[1.7] ${hintCls}`}
                        style={{ color: dark ? '#8B94AB' : '#66738F' }}
                    >
                        {hint}
                    </p>
                )}
                {actionText && onAction && (
                    dark ? (
                        <Button type="text" size="small" className="mt-3" style={{ color: '#9BC0F7' }} onClick={onAction}>
                            {actionText}
                        </Button>
                    ) : (
                        <Button className="tk-soft-btn mt-4" onClick={onAction}>{actionText}</Button>
                    )
                )}
            </div>
        </div>
    );
};

export default EmptyState;
