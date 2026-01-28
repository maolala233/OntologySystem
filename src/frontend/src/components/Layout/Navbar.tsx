import React from 'react';
import { Breadcrumb, Input, Button } from 'antd';
import { SearchOutlined, PlusOutlined } from '@ant-design/icons';

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
        <div className="h-16 bg-white border-b border-gray-200 px-6 flex items-center justify-between shadow-sm">
            {/* 左侧：面包屑 */}
            <div className="flex items-center space-x-4">
                <Breadcrumb
                    items={breadcrumbs.map((item) => ({
                        title: item.title,
                        href: item.path,
                    }))}
                />
            </div>

            {/* 右侧：搜索 + 新建按钮 */}
            <div className="flex items-center space-x-4">
                {onSearch && (
                    <Input
                        placeholder="搜索项目或本体..."
                        prefix={<SearchOutlined className="text-gray-400" />}
                        onChange={(e) => onSearch(e.target.value)}
                        className="w-64"
                        allowClear
                    />
                )}
                {showCreateButton && onCreateProject && (
                    <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={onCreateProject}
                        className="bg-blue-600 hover:bg-blue-700"
                    >
                        新建项目
                    </Button>
                )}
            </div>
        </div>
    );
};

export default Navbar;
