import React from 'react';
import { CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';

type Tone = 'info' | 'warning' | 'error' | 'success';

const TONE: Record<Tone, { bg: string; border: string; color: string; icon: React.ReactNode }> = {
    info: {
        bg: 'rgba(91, 141, 239, 0.07)',
        border: 'rgba(91, 141, 239, 0.28)',
        color: '#2A57C6',
        icon: <InfoCircleOutlined />,
    },
    warning: {
        bg: 'rgba(242, 185, 80, 0.12)',
        border: 'rgba(242, 185, 80, 0.42)',
        color: '#8A5A12',
        icon: <ExclamationCircleOutlined />,
    },
    error: {
        bg: 'rgba(180, 35, 24, 0.06)',
        border: 'rgba(180, 35, 24, 0.24)',
        color: '#B42318',
        icon: <CloseCircleOutlined />,
    },
    success: {
        bg: 'rgba(91, 141, 239, 0.07)',
        border: 'rgba(91, 141, 239, 0.28)',
        color: '#2A57C6',
        icon: <CheckCircleOutlined />,
    },
};

interface InlineNoticeProps {
    tone?: Tone;
    /** 覆盖默认图标 */
    icon?: React.ReactNode;
    /** 一句话说明：单行显示，过长截断，悬停看全文 */
    children: React.ReactNode;
    /** 行尾操作（链接按钮等） */
    action?: React.ReactNode;
    className?: string;
}

/**
 * 一行式提示条（2026-11-13）：
 * antd 的 Alert 一旦带 description，就会渲染成「图标 + 标题 + 一段正文 + 按钮」的方块，
 * 在项目详情这种内容已经很密的页面里非常抢眼。状态类提示（构建完成/停止/有待审项）
 * 其实一句话就够，统一收成这条窄带：一行、可截断、行尾放操作。
 * 需要展示列表、错误明细这类确实要多行的内容，仍直接用 Alert。
 */
const InlineNotice: React.FC<InlineNoticeProps> = ({
    tone = 'info', icon, children, action, className = '',
}) => {
    const t = TONE[tone];
    return (
        <div
            className={`flex items-center gap-2 rounded-lg border px-3 py-[7px] text-[12.5px] leading-5 ${className}`}
            style={{ background: t.bg, borderColor: t.border, color: t.color }}
        >
            <span className="shrink-0 text-[13px]">{icon ?? t.icon}</span>
            <span
                className="min-w-0 flex-1 truncate"
                title={typeof children === 'string' ? children : undefined}
            >
                {children}
            </span>
            {action && <span className="shrink-0">{action}</span>}
        </div>
    );
};

export default InlineNotice;
