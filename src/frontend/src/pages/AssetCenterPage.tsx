import React, { useEffect, useState } from 'react';
import { Card, Empty, Spin, message, Tag, Input, Select, Button } from 'antd';
import { EyeOutlined, UserOutlined, NodeIndexOutlined, AppstoreOutlined, DatabaseOutlined, ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import TechHero, { TECH_COLORS, GraphMotif } from '../components/TechHero';
import { projectsApi } from '../api/projects';
import { getDomains, KnowledgeDomain } from '../api/domains';
import { ProjectData } from '../types/ontology';

const TC = TECH_COLORS;

/**
 * 资产中心页面 - 两层视图结构：
 * 第一层：知识域选择视图 - 展示所有知识域及其包含的项目数量
 * 第二层：本体项目列表视图 - 展示选中知识域下的所有本体项目
 */
const AssetCenterPage: React.FC = () => {
    const navigate = useNavigate();
    
    // 视图状态：'domain' = 知识域选择层，'project' = 项目列表层
    const [currentView, setCurrentView] = useState<'domain' | 'project'>('domain');
    
    // 知识域相关状态
    interface DomainWithCount extends KnowledgeDomain {
        projectCount: number;
    }
    
    const [domains, setDomains] = useState<DomainWithCount[]>([]);
    const [selectedDomain, setSelectedDomain] = useState<KnowledgeDomain | null>(null);
    
    // 项目相关状态
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [filteredProjects, setFilteredProjects] = useState<ProjectData[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchText, setSearchText] = useState('');
    const [sortBy, setSortBy] = useState<'latest' | 'nodes'>('latest');

    useEffect(() => {
        if (currentView === 'domain') {
            loadDomainsWithProjects();
        } else {
            loadProjectsByDomain();
        }
    }, [currentView, selectedDomain]);

    useEffect(() => {
        filterAndSortProjects();
    }, [projects, searchText, sortBy]);

    // 加载所有知识域及其项目数量
    const loadDomainsWithProjects = async () => {
        setLoading(true);
        try {
            // 获取所有知识域
            const allDomains = await getDomains();
            
            // 获取所有公共项目
            const allProjects = await projectsApi.getPublicProjects();
            
            // 统计每个知识域的项目数量
            const domainStats: DomainWithCount[] = allDomains.map(domain => ({
                ...domain,
                projectCount: allProjects.filter(p => p.domain_id === domain.id).length,
            }));
            
            setDomains(domainStats);
        } catch (error: any) {
            message.error('加载知识域失败');
        } finally {
            setLoading(false);
        }
    };

    // 加载指定知识域下的项目
    const loadProjectsByDomain = async () => {
        setLoading(true);
        try {
            const allProjects = await projectsApi.getPublicProjects();
            
            // 如果选中了特定知识域，过滤项目
            let filtered = allProjects;
            if (selectedDomain && selectedDomain.id !== 0) {
                filtered = allProjects.filter(p => p.domain_id === selectedDomain.id);
            }
            
            setProjects(filtered);
        } catch (error: any) {
            message.error('加载项目失败');
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

    // 处理知识域选择
    const handleDomainSelect = (domain: KnowledgeDomain) => {
        setSelectedDomain(domain);
        setCurrentView('project');
    };

    // 返回知识域选择视图
    const handleBackToDomains = () => {
        setCurrentView('domain');
        setSelectedDomain(null);
        setSearchText('');
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { 
            title: currentView === 'domain' ? '资产中心' : '资产中心',
            path: currentView === 'domain' ? undefined : '/asset-center',
            onClick: currentView === 'project' ? handleBackToDomains : undefined,
        },
        ...(selectedDomain && currentView === 'project' ? [{ title: selectedDomain.name }] : []),
    ];

    // 渲染知识域选择视图（第一层）
    const renderDomainSelectionView = () => (
        <div>
            {loading ? (
                <div className="flex justify-center items-center h-96">
                    <Spin size="large" />
                </div>
            ) : domains.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-96 px-4">
                    <Empty
                        description="暂无知识域分类"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                    {domains.map((domain) => (
                        <Card
                            key={domain.id}
                            hoverable
                            className="rounded-xl shadow-sm hover:shadow-xl transition-all duration-300 transform hover:-translate-y-1 cursor-pointer border border-gray-100"
                            onClick={() => handleDomainSelect(domain)}
                            cover={
                                <div
                                    className="ac-cover h-32 sm:h-40"
                                    style={{ background: TC.bgDark }}
                                >
                                    {/* 网格底纹 + 辉光 */}
                                    <div className="absolute inset-0 ac-cover-grid" />
                                    <div
                                        className="absolute -top-8 -right-8 w-36 h-36 rounded-full"
                                        style={{ background: TC.blue, filter: 'blur(60px)', opacity: 0.3 }}
                                    />
                                    <div
                                        className="absolute -bottom-10 -left-6 w-36 h-36 rounded-full"
                                        style={{ background: TC.purple, filter: 'blur(60px)', opacity: 0.3 }}
                                    />

                                    {/* 知识域图标 */}
                                    <div className="absolute inset-0 flex items-center justify-center">
                                        <DatabaseOutlined
                                            className="text-5xl sm:text-6xl"
                                            style={{ color: TC.cyan, opacity: 0.75, filter: `drop-shadow(0 0 14px ${TC.cyan})` }}
                                        />
                                    </div>

                                    {/* 项目数量标签 */}
                                    <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between">
                                        <span
                                            className="tk-mono text-[11px] tracking-wider px-2.5 py-0.5 rounded-full"
                                            style={{ color: TC.cyan, border: `1px solid ${TC.line}`, background: 'rgba(90,200,250,0.08)' }}
                                        >
                                            {domain.projectCount} 个项目
                                        </span>
                                        <span
                                            className="tk-mono text-[10px] tracking-widest"
                                            style={{ color: TC.textSub }}
                                        >
                                            DOMAIN
                                        </span>
                                    </div>
                                </div>
                            }
                        >
                            <Card.Meta
                                title={
                                    <div className="text-base sm:text-lg font-semibold text-gray-800 truncate">
                                        {domain.name}
                                    </div>
                                }
                                description={
                                    <div className="text-gray-500 text-sm line-clamp-2 min-h-[40px]">
                                        {domain.description || '暂无描述'}
                                    </div>
                                }
                            />
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );

    // 渲染项目列表视图（第二层）
    const renderProjectListView = () => (
        <div>
            {/* 顶部导航栏 - 返回按钮 + 知识域信息 + 排序 */}
            <div className="mb-6 bg-white rounded-xl shadow-sm border border-gray-100 p-4 sm:p-5">
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <Button 
                            icon={<ArrowLeftOutlined />} 
                            onClick={handleBackToDomains}
                            className="shadow-sm hover:shadow-md transition-shadow"
                            size="large"
                        >
                            返回知识域
                        </Button>
                        
                        {selectedDomain && selectedDomain.id !== 0 && (
                            <div className="flex items-center gap-3 pl-3 border-l border-gray-200">
                                <div className="flex items-center justify-center w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-500">
                                    <DatabaseOutlined className="text-white text-lg" />
                                </div>
                                <div>
                                    <div className="font-semibold text-gray-800 text-base">{selectedDomain.name}</div>
                                    {selectedDomain.description && (
                                        <div className="text-gray-500 text-xs mt-0.5">{selectedDomain.description}</div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                    
                    {/* 统计和排序栏 */}
                    <div className="flex items-center gap-4 w-full sm:w-auto">
                        <div className="flex items-center gap-2 px-3 py-1.5 bg-gradient-to-r from-indigo-50 to-purple-50 rounded-full">
                            <span className="text-indigo-600 font-semibold">{filteredProjects.length}</span>
                            <span className="text-gray-600 text-sm">个本体</span>
                        </div>
                        <Select
                            value={sortBy}
                            onChange={setSortBy}
                            size="large"
                            className="min-w-[140px]"
                            options={[
                                { label: '📅 最新发布', value: 'latest' },
                                { label: ' 节点数量', value: 'nodes' },
                            ]}
                        />
                    </div>
                </div>
            </div>

            {loading ? (
                <div className="flex justify-center items-center h-96">
                    <Spin size="large" tip="加载中..." />
                </div>
            ) : filteredProjects.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-96 px-4 bg-white rounded-2xl shadow-sm border border-gray-100">
                    <div className="w-20 h-20 mb-4 rounded-full bg-gradient-to-br from-gray-100 to-gray-200 flex items-center justify-center">
                        <DatabaseOutlined className="text-4xl text-gray-400" />
                    </div>
                    <Empty
                        description={
                            <div className="text-gray-500">
                                {searchText ? '没有找到匹配的本体' : '该知识域下暂无公开本体'}
                            </div>
                        }
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                    {selectedDomain && selectedDomain.id !== 0 && (
                        <Button 
                            type="primary" 
                            onClick={handleBackToDomains}
                            className="mt-4"
                            size="large"
                        >
                            选择其他知识域
                        </Button>
                    )}
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-5 sm:gap-6 lg:gap-7">
                    {filteredProjects.map((project) => (
                        <Card
                            key={project.id}
                            hoverable
                            className="group rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 overflow-hidden border border-gray-100 cursor-pointer"
                            onClick={() => navigate(`/asset-center/${project.id}`)}
                            cover={
                                <div
                                    className="ac-cover h-44"
                                    style={{ background: TC.bgDark }}
                                >
                                    {/* 网格底纹 + 双色辉光 */}
                                    <div className="absolute inset-0 ac-cover-grid" />
                                    <div
                                        className="absolute -top-10 -right-10 w-40 h-40 rounded-full"
                                        style={{ background: TC.purple, filter: 'blur(60px)', opacity: 0.35 }}
                                    />
                                    <div
                                        className="absolute -bottom-12 -left-8 w-40 h-40 rounded-full"
                                        style={{ background: TC.blue, filter: 'blur(60px)', opacity: 0.35 }}
                                    />
                                    <GraphMotif seed={project.id} className="absolute inset-0 w-full h-full opacity-80" />

                                    {/* 标签区域 */}
                                    <div className="absolute top-3 left-3 right-3 flex items-start justify-between gap-2">
                                        {/* 知识域标签 */}
                                        {project.domain && (
                                            <Tag
                                                className="font-medium text-xs border-0 tk-mono"
                                                style={{ color: TC.cyan, background: 'rgba(90,200,250,0.1)', border: `1px solid ${TC.line}` }}
                                            >
                                                {project.domain.name}
                                            </Tag>
                                        )}
                                        {/* 发布标签 */}
                                        <Tag color="green" className="font-medium shadow-sm border-0 bg-green-500 text-white">
                                            <CheckCircleOutlined className="mr-0.5" />
                                            已发布
                                        </Tag>
                                    </div>

                                    {/* 底部标识 */}
                                    <div
                                        className="absolute bottom-3 right-3 tk-mono text-[10px] tracking-widest"
                                        style={{ color: TC.textSub }}
                                    >
                                        ONTOLOGY
                                    </div>
                                </div>
                            }
                        >
                            <Card.Meta
                                title={
                                    <div className="text-base font-semibold text-gray-800 truncate group-hover:text-indigo-600 transition-colors" title={project.name}>
                                        {project.name}
                                    </div>
                                }
                                description={
                                    <div className="space-y-3 mt-2">
                                        <div className="text-gray-500 text-sm line-clamp-2 min-h-[40px]" title={project.description || '暂无描述'}>
                                            {project.description || '暂无描述'}
                                        </div>

                                        {/* 统计信息 */}
                                        <div className="flex items-center justify-between pt-3 border-t border-gray-100">
                                            <div className="flex items-center gap-1.5 text-blue-600">
                                                <div className="w-7 h-7 rounded-full bg-blue-50 flex items-center justify-center">
                                                    <NodeIndexOutlined className="text-sm" />
                                                </div>
                                                <span className="font-medium text-sm">
                                                    {project.graph_data?.nodes?.length || 0}
                                                </span>
                                            </div>
                                            <div className="flex items-center gap-1.5 text-purple-600">
                                                <div className="w-7 h-7 rounded-full bg-purple-50 flex items-center justify-center">
                                                    <span className="text-sm font-medium">⚡</span>
                                                </div>
                                                <span className="font-medium text-sm">
                                                    {project.graph_data?.edges?.length || 0}
                                                </span>
                                            </div>
                                            <div className="flex items-center gap-1.5 text-gray-500">
                                                <div className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center">
                                                    <UserOutlined className="text-sm" />
                                                </div>
                                                <span className="text-xs truncate max-w-[60px]">
                                                    {project.owner?.username || '未知'}
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                }
                            />
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );

    return (
        <div className="min-h-screen bg-gray-50">
            <style>{`
                .ac-cover { position: relative; overflow: hidden; }
                .ac-cover-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.12) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.12) 1px, transparent 1px);
                    background-size: 24px 24px;
                }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} onSearch={handleSearch} />

            <div className="p-4 sm:p-6 lg:p-8">
                <TechHero
                    compact
                    chips={
                        currentView === 'domain'
                            ? ['ASSET CENTER', 'PUBLIC ONTOLOGIES']
                            : ['ASSET CENTER', selectedDomain && selectedDomain.id !== 0 ? selectedDomain.name : '全部知识域']
                    }
                    title="资产中心"
                    subtitle="浏览已发布的公共本体资产：按知识域归类 · 只读快照 · 支持在线检索与图谱预览"
                    showConstellation={currentView === 'domain'}
                />
                {currentView === 'domain' ? renderDomainSelectionView() : renderProjectListView()}
            </div>
        </div>
    );
};

export default AssetCenterPage;
