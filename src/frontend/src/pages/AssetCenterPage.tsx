import React, { useEffect, useState } from 'react';
import { Card, Empty, Spin, message, Tag, Input, Select } from 'antd';
import { EyeOutlined, UserOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';

const AssetCenterPage: React.FC = () => {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [filteredProjects, setFilteredProjects] = useState<ProjectData[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchText, setSearchText] = useState('');
    const [sortBy, setSortBy] = useState<'latest' | 'nodes'>('latest');

    useEffect(() => {
        loadPublicProjects();
    }, []);

    useEffect(() => {
        filterAndSortProjects();
    }, [projects, searchText, sortBy]);

    const loadPublicProjects = async () => {
        setLoading(true);
        try {
            const data = await projectsApi.getPublicProjects();
            setProjects(data);
        } catch (error: any) {
            message.error('加载公共本体失败');
        } finally {
            setLoading(false);
        }
    };

    const filterAndSortProjects = () => {
        let filtered = projects;

        // 搜索过滤
        if (searchText) {
            filtered = filtered.filter(
                (p) =>
                    p.name.toLowerCase().includes(searchText.toLowerCase()) ||
                    p.description?.toLowerCase().includes(searchText.toLowerCase())
            );
        }

        // 排序
        if (sortBy === 'latest') {
            filtered = [...filtered].sort((a, b) => b.id - a.id);
        } else if (sortBy === 'nodes') {
            filtered = [...filtered].sort(
                (a, b) =>
                    (b.graph_data?.nodes?.length || 0) - (a.graph_data?.nodes?.length || 0)
            );
        }

        setFilteredProjects(filtered);
    };

    const handleSearch = (value: string) => {
        setSearchText(value);
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '资产中心' },
    ];

    return (
        <div className="min-h-screen bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} onSearch={handleSearch} />

            <div className="p-4 sm:p-6">
                {/* 过滤和排序栏 - 响应式设计 */}
                <div className="mb-4 sm:mb-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 sm:gap-0 bg-white p-3 sm:p-4 rounded-lg shadow-sm">
                    <div className="flex items-center space-x-2 sm:space-x-4">
                        <span className="text-gray-600 font-medium text-sm sm:text-base">
                            共 {filteredProjects.length} 个公开本体
                        </span>
                    </div>
                    <div className="flex items-center space-x-2 sm:space-x-4 w-full sm:w-auto">
                        <span className="text-gray-600 text-sm sm:text-base">排序:</span>
                        <Select
                            value={sortBy}
                            onChange={setSortBy}
                            style={{ width: '100%', minWidth: 120 }}
                            className="flex-1 sm:flex-none"
                            options={[
                                { label: '最新发布', value: 'latest' },
                                { label: '节点数量', value: 'nodes' },
                            ]}
                        />
                    </div>
                </div>

                {loading ? (
                    <div className="flex justify-center items-center h-96">
                        <Spin size="large" />
                    </div>
                ) : filteredProjects.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description={
                                searchText ? '没有找到匹配的本体' : '还没有公开的本体'
                            }
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                        {filteredProjects.map((project) => (
                            <Card
                                key={project.id}
                                hoverable
                                className="rounded-xl shadow-md hover:shadow-xl transition-all duration-300 transform hover:-translate-y-1"
                                cover={
                                    <div className="h-40 sm:h-48 bg-gradient-to-br from-indigo-400 via-purple-400 to-pink-400 relative overflow-hidden">
                                        {/* 装饰性图案 */}
                                        <div className="absolute inset-0 opacity-20">
                                            <div className="absolute top-4 left-4 w-16 h-16 border-4 border-white rounded-full"></div>
                                            <div className="absolute bottom-4 right-4 w-24 h-24 border-4 border-white rounded-full"></div>
                                            <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-32 h-32 border-4 border-white rounded-full"></div>
                                        </div>

                                        {/* 项目首字母 */}
                                        <div className="absolute inset-0 flex items-center justify-center">
                                            <div className="text-white text-5xl sm:text-7xl font-bold opacity-30">
                                                {project.name.charAt(0).toUpperCase()}
                                            </div>
                                        </div>

                                        {/* 发布标签 */}
                                        <div className="absolute top-4 right-4">
                                            <Tag color="green" className="font-medium">
                                                已发布
                                            </Tag>
                                        </div>
                                    </div>
                                }
                                onClick={() => navigate(`/asset-center/${project.id}`)}
                            >
                                <Card.Meta
                                    title={
                                        <div className="text-base sm:text-lg font-semibold text-gray-800 truncate">
                                            {project.name}
                                        </div>
                                    }
                                    description={
                                        <div className="space-y-2 sm:space-y-3">
                                            <div className="text-gray-500 text-sm line-clamp-2 min-h-[40px]">
                                                {project.description || '暂无描述'}
                                            </div>

                                            {/* 统计信息 */}
                                            <div className="flex items-center justify-between pt-2 border-t border-gray-100 text-xs sm:text-sm">
                                                <div className="flex items-center space-x-1 text-blue-600">
                                                    <NodeIndexOutlined />
                                                    <span className="font-medium">
                                                        {project.graph_data?.nodes?.length || 0} 节点
                                                    </span>
                                                </div>
                                                <div className="flex items-center space-x-1 text-purple-600">
                                                    <span className="font-medium">
                                                        {project.graph_data?.edges?.length || 0} 关系
                                                    </span>
                                                </div>
                                            </div>

                                            {/* 作者信息 */}
                                            <div className="flex items-center space-x-2 text-gray-400 text-xs pt-1">
                                                <UserOutlined />
                                                <span>创建者：{project.owner?.username || '未知'}</span>
                                            </div>
                                        </div>
                                    }
                                />
                            </Card>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default AssetCenterPage;
