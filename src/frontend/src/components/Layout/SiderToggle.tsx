import React from 'react';
import { Button } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { useSiderCollapse } from './siderCollapse';

/**
 * 顶栏左侧的菜单折叠开关；无 AppLayout 上下文时不渲染，避免出现点了没反应的按钮。
 * 2026-11-19：去掉悬停提示（折叠/展开图标本身语义明确），aria-label 保留给读屏；
 * 悬停底色从原生的冷灰改成站内统一的品牌淡蓝，按下再深一档（样式见 index.css .sider-toggle）
 */
const SiderToggle: React.FC<{ className?: string }> = ({ className }) => {
    const ctx = useSiderCollapse();
    if (!ctx) return null;
    const { collapsed, toggle } = ctx;
    const label = collapsed ? '展开菜单' : '收起菜单';
    return (
        <Button
            type="text"
            className={`sider-toggle ${className || ''}`}
            aria-label={label}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={toggle}
        />
    );
};

export default SiderToggle;
