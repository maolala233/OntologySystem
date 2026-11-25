import { forwardRef } from 'react';
import type { HTMLAttributes } from 'react';

/**
 * 页面内容滚动容器（2026-11-25）
 *
 * 全站主题页的内容区统一用它，不要再手写 div + className：
 * - 内边距与滚动条槽：左右 16 + 两侧槽 8 = 视觉 24（与页头 Navbar 对齐），
 *   上下移动端 16 / 桌面 24——具体数值见 index.css 的 .page-scroll；
 * - scrollbar-gutter: stable both-edges：滚动条出现/消失内容不横移，跨页宽度一致；
 * - 不支持 scrollbar-gutter 的老浏览器（Safari ≤ 18.1）由 index.css 的
 *   @supports 兜底，把桌面左右留白补回 24。
 *
 * className 只用于页面级作用域类（如 rp-page / mcp-page）或少量布局差异，
 * 不要往里写 padding —— .page-scroll 的规则会覆盖它。
 */
export interface PageScrollProps extends HTMLAttributes<HTMLDivElement> {
    className?: string;
}

const PageScroll = forwardRef<HTMLDivElement, PageScrollProps>(
    ({ className, children, ...rest }, ref) => (
        <div
            ref={ref}
            className={`page-scroll scrollbar-stable flex-1 min-h-0 overflow-y-auto${className ? ` ${className}` : ''}`}
            {...rest}
        >
            {children}
        </div>
    ),
);

PageScroll.displayName = 'PageScroll';

export default PageScroll;
