/**
 * shared/Tip - 全站统一的文字提示（2026-11-19）
 *
 * 背景：项目里原本混着两套悬停提示——antd <Tooltip>（深色毛玻璃、顶部弹出）
 * 和原生 title 属性（浏览器自绘：白底、跟随光标、约 1 秒延迟，CSS/主题完全管不到）。
 * 凡是"悬停看全称 / 图标按钮说明"这类提示，统一走这里，不再直接写 title 属性。
 *
 * - 默认顶部弹出，和既有 <Tooltip> 的位置一致；
 * - 延迟 0.25s：比 antd 默认的 0.1s 稍长，鼠标扫过整屏时提示不会一路乱闪；
 * - text 为空时不挂 Tooltip，直接把子元素渲染出去（截断文本没有全称可看的情况）。
 */
import React from 'react';
import { Tooltip } from 'antd';

interface TipProps {
    /** 提示内容；为空则不弹提示 */
    text?: React.ReactNode;
    /** 必须是单个可挂 ref 的元素（原生标签或 antd 组件） */
    children: React.ReactElement;
}

export default function Tip({ text, children }: TipProps) {
    if (text === undefined || text === null || text === '') return children;
    return (
        <Tooltip title={text} placement="top" mouseEnterDelay={0.25}>
            {children}
        </Tooltip>
    );
}
