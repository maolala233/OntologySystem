/**
 * 全局视觉主题（2026-11-11 样式优化）：
 * 与 TechHero 深色科技面板同源的「冷灰画布 + 蓝紫品牌色」设计语言。
 * 只收敛颜色 / 圆角 / 阴影等视觉 token，不改动任何尺寸与布局。
 */
import type { ThemeConfig } from 'antd';

/** 品牌基准色（与 features/graph-explorer/colors.ts、TechHero 保持一致） */
export const BRAND = {
    primary: '#5B8DEF',
    purple: '#8B5CF6',
    ink: '#0F1420',
    sider: '#0D1322',
    canvas: '#F3F6FB',
} as const;

export const appTheme: ThemeConfig = {
    token: {
        colorPrimary: BRAND.primary,
        colorInfo: BRAND.primary,
        colorLink: '#4C7DE7',

        // 文字层次：标题近墨蓝、正文深灰蓝、辅助信息冷灰
        colorText: '#1F2A44',
        colorTextHeading: '#0F172A',
        colorTextSecondary: '#52607A',
        colorTextTertiary: '#8A94AB',

        // 分隔线与描边整体调淡，避免灰线把界面切碎
        colorBorder: '#DCE3F0',
        colorBorderSecondary: '#E9EDF6',
        colorSplit: 'rgba(15, 23, 42, 0.06)',

        colorBgLayout: BRAND.canvas,

        borderRadius: 8,
        borderRadiusLG: 14,
        borderRadiusSM: 6,

        // 输入类控件聚焦时的品牌色光晕
        controlOutline: 'rgba(91, 141, 239, 0.16)',
        controlOutlineWidth: 3,

        // 系统 UI 字体优先：Windows 上落到 Segoe UI（与改版前一致）。
        // 不能把 Inter/微软雅黑放前面——Windows 没有 Inter 时会落到雅黑，
        // 密码框的圆点（•）在雅黑里是等宽全角字形，会被拉得很开（实测 52px → 128px）。
        fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', " +
            "'PingFang SC', 'HarmonyOS Sans SC', 'Microsoft YaHei', Arial, sans-serif",

        // 统一柔和阴影：卡片 / 浮层 / 弹窗共用同一套层次
        boxShadow: '0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 28px -16px rgba(15, 23, 42, 0.16)',
        boxShadowSecondary: '0 18px 44px -20px rgba(15, 23, 42, 0.24), 0 2px 8px rgba(15, 23, 42, 0.04)',
        boxShadowTertiary: '0 1px 2px rgba(15, 23, 42, 0.04), 0 12px 30px -20px rgba(15, 23, 42, 0.20)',
    },
    components: {
        // 侧边栏与 Hero 同色系，整体框架统一为墨蓝而非旧版海军蓝
        Layout: { siderBg: BRAND.sider, bodyBg: BRAND.canvas },
        Menu: {
            darkItemBg: BRAND.sider,
            darkPopupBg: BRAND.sider,
            darkSubMenuItemBg: '#091019',
            darkItemColor: 'rgba(203, 213, 225, 0.78)',
            darkItemHoverColor: '#FFFFFF',
            darkItemHoverBg: 'rgba(91, 141, 239, 0.10)',
            darkItemSelectedColor: '#FFFFFF',
            darkItemSelectedBg: 'rgba(91, 141, 239, 0.20)',
            itemBorderRadius: 10,
            itemMarginInline: 8,
            activeBarWidth: 0,
        },
        Card: {
            headerBg: 'transparent',
            headerFontSize: 15,
        },
        Table: {
            headerBg: '#F6F8FC',
            headerColor: '#5A6680',
            headerSplitColor: 'transparent',
            borderColor: '#ECF0F8',
            rowHoverBg: '#F5F8FF',
            headerBorderRadius: 12,
        },
        Segmented: {
            trackBg: 'rgba(15, 23, 42, 0.05)',
            itemSelectedBg: '#FFFFFF',
            itemSelectedColor: '#0F172A',
            itemColor: '#5A6680',
            itemHoverColor: '#0F172A',
        },
        Tabs: {
            itemSelectedColor: '#0F172A',
            itemColor: '#5A6680',
            itemHoverColor: '#4C7DE7',
            inkBarColor: BRAND.primary,
            titleFontSize: 14,
        },
        Button: {
            fontWeight: 500,
            primaryShadow: '0 8px 18px -10px rgba(91, 141, 239, 0.65)',
            defaultShadow: '0 1px 2px rgba(15, 23, 42, 0.04)',
        },
        Tag: {
            defaultBg: '#F1F4FA',
            defaultColor: '#52607A',
        },
        Pagination: {
            itemActiveBg: '#EAF1FF',
            itemBg: 'transparent',
        },
        Modal: { contentBg: '#FFFFFF' },
        Dropdown: { paddingBlock: 6 },
    },
};
