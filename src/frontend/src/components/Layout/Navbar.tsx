import React from 'react';
import { Breadcrumb, Input, Button, Switch, Tooltip } from 'antd';
import { SearchOutlined, PlusOutlined, PlayCircleOutlined } from '@ant-design/icons';

interface NavbarProps {
    breadcrumbs?: { title: string; path?: string }[];
    onSearch?: (value: string) => void;
    onCreateProject?: () => void;
    showCreateButton?: boolean;
}

const Navbar: React.FC<NavbarProps> = ({
    breadcrumbs = [],
    onSearch,
    onCreateProject,
    showCreateButton = false,
}) => {
    return (
        <div className="h-16 bg-white border-b border-gray-200 px-4 sm:px-6 flex items-center justify-between shadow-sm">
            {/* 左侧：面包屑 */}
            <div className="flex items-center flex-1 min-w-0">
                <Breadcrumb
                    items={breadcrumbs.map((item) => ({
                        title: item.title,
                        href: item.path,
                    }))}
                />
            </div>

            {/* 右侧：演示模式 + 搜索 + 新建按钮 */}
            <div className="flex items-center space-x-2 sm:space-x-4 flex-shrink-0">
                <Tooltip title="演示模式：右下角显示六步演示脚本（传文档→审核→发布门禁→Agent 查语义→问答→进化）">
                    <span className="flex items-center gap-1 text-sm">
                        <PlayCircleOutlined className={localStorage.getItem('demo_mode') === '1' ? 'text-blue-500' : 'text-gray-400'} />
                        <Switch size="small"
                                defaultChecked={localStorage.getItem('demo_mode') === '1'}
                                onChange={(v) => {
                                    localStorage.setItem('demo_mode', v ? '1' : '0');
                                    window.dispatchEvent(new Event('demo-mode-changed'));
                                }} />
                        <span className="hidden lg:inline">演示</span>
                    </span>
                </Tooltip>
                {onSearch && (
                    <Input
                        placeholder="搜索项目或本体..."
                        prefix={<SearchOutlined className="text-gray-500" />}
                        onChange={(e) => onSearch(e.target.value)}
                        className="w-32 sm:w-48 lg:w-64"
                        allowClear
                    />
                )}
                {showCreateButton && onCreateProject && (
                    <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={onCreateProject}
                        className="flex-shrink-0"
                        size="middle"
                    >
                        <span className="hidden sm:inline">新建项目</span>
                        <span className="sm:hidden">新建</span>
                    </Button>
                )}
            </div>
        </div>
    );
};

export default Navbar;
