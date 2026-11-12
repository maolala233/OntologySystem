/**
 * 侧边栏折叠状态上下文（AppLayout 提供，顶栏的 SiderToggle 消费）。
 * 2026-11-11：折叠开关从侧栏底部上移到顶栏左侧——底部箭头悬浮在菜单下方很突兀，
 * 常规后台布局都把「收起/展开菜单」放在顶栏最左。
 */
import { createContext, useContext } from 'react';

export interface SiderCollapseCtx {
    collapsed: boolean;
    toggle: () => void;
}

export const SiderCollapseContext = createContext<SiderCollapseCtx | null>(null);

/** 不在 AppLayout 内（理论上不会发生）时返回 null，由调用方决定是否隐藏按钮 */
export const useSiderCollapse = () => useContext(SiderCollapseContext);
