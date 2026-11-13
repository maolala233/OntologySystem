import React from 'react';

interface PageHeaderProps {
    /** 页面标题 */
    title: React.ReactNode;
    /** 标题下方的一句话说明 */
    subtitle?: React.ReactNode;
    /** 右侧操作区（按钮 / 筛选等） */
    extra?: React.ReactNode;
    /** 标题前的图标，与左侧菜单图标呼应 */
    icon?: React.ReactNode;
    className?: string;
}

/**
 * 后台管理类页面的统一页头：图标 + 标题 + 说明 + 右侧操作区。
 * 与全站 Navbar 一起构成「顶栏（面包屑/账号）→ 页头（页面身份/主操作）→ 内容」的层次；
 * 系统配置五个页面共用，避免各页标题字号、操作位、间距各不相同。
 */
const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, extra, icon, className = '' }) => (
    <div className={`flex flex-wrap items-start justify-between gap-3 mb-4 ${className}`}>
        <div className="flex items-start gap-3 min-w-0">
            {icon && (
                <span className="mt-0.5 w-9 h-9 shrink-0 inline-flex items-center justify-center rounded-xl bg-[rgba(91,141,239,0.10)] text-[#5B8DEF] text-lg">
                    {icon}
                </span>
            )}
            <div className="min-w-0">
                <h1 className="m-0 text-xl font-bold leading-7 text-[#0F172A]">{title}</h1>
                {subtitle && <p className="m-0 mt-1 text-[13px] leading-5 text-[#52607A]">{subtitle}</p>}
            </div>
        </div>
        {extra && <div className="flex flex-wrap items-center gap-2 shrink-0">{extra}</div>}
    </div>
);

export default PageHeader;
