import React from 'react';
import { Button, Tooltip } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { useSiderCollapse } from './siderCollapse';

/** 顶栏左侧的菜单折叠开关；无 AppLayout 上下文时不渲染，避免出现点了没反应的按钮 */
const SiderToggle: React.FC<{ className?: string }> = ({ className }) => {
    const ctx = useSiderCollapse();
    if (!ctx) return null;
    const { collapsed, toggle } = ctx;
    const label = collapsed ? '展开菜单' : '收起菜单';
    return (
        <Tooltip title={label}>
            <Button
                type="text"
                size="small"
                className={`!text-gray-500 hover:!text-gray-800 hover:!bg-gray-100 ${className || ''}`}
                aria-label={label}
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={toggle}
            />
        </Tooltip>
    );
};

export default SiderToggle;
