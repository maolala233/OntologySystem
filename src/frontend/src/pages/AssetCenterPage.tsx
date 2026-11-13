import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Empty, Spin, message, Input, Select, Button } from 'antd';
import { UserOutlined, NodeIndexOutlined, DatabaseOutlined, ApartmentOutlined, ArrowLeftOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import TechHero from '../components/TechHero';
import { projectsApi } from '../api/projects';
import { getDomains, KnowledgeDomain } from '../api/domains';
import { ProjectData } from '../types/ontology';

// 模块级缓存：进入详情页再返回时，组件会重新挂载——先用上次的数据立即渲染，
// 再后台刷新，避免"整页转圈 → 内容重现"的闪烁（2026-11-11）
interface DomainWithCount extends KnowledgeDomain {
    projectCount: number;
}
let cachedDomains: DomainWithCount[] | null = null;
let cachedPublicProjects: ProjectData[] | null = null;

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
    const [domains, setDomains] = useState<DomainWithCount[]>(cachedDomains ?? []);
    const [selectedDomain, setSelectedDomain] = useState<KnowledgeDomain | null>(null);
    
    // 项目相关状态
    const [projects, setProjects] = useState<ProjectData[]>(cachedPublicProjects ?? []);
    // 有缓存说明是"返回"场景：不显示整页 loading，直接渲染缓存再后台刷新
    const [loading, setLoading] = useState(!cachedDomains);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [searchText, setSearchText] = useState('');
    const [sortBy, setSortBy] = useState<'latest' | 'nodes'>('latest');

    // 切换层级时回到顶部：否则新视图会继承上一屏的滚动位置，看起来像"跳了一下"
    const scrollRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        scrollRef.current?.scrollTo({ top: 0 });
    }, [currentView, selectedDomain]);

    // 一次性拉取知识域 + 公共项目（首屏与失败重试共用）；
    // 切换视图/选域一律本地过滤，不再整页 loading——原来每次切换都是
    // 「内容消失 → 转圈 → 内容重现」的强闪烁（2026-11-11）
    const loadAll = async () => {
        if (!cachedDomains) setLoading(true);
        try {
            const [allDomains, allProjects] = await Promise.all([
                getDomains(),
                projectsApi.getPublicProjects(),
            ]);
            const domainsWithCount = allDomains.map((domain) => ({
                ...domain,
                projectCount: allProjects.filter((p) => p.domain_id === domain.id).length,
            }));
            cachedDomains = domainsWithCount;
            cachedPublicProjects = allProjects;
            setDomains(domainsWithCount);
            setProjects(allProjects);
            setLoadError(null);
        } catch (error: any) {
            const d = error?.response?.data?.message || error?.response?.data?.detail || error?.message;
            setLoadError(String(d || '未知错误'));
        } finally {
            setLoading(false);
        }
    };
    // 两个视图的错误重试按钮继续可用
    const loadDomainsWithProjects = loadAll;
    const loadProjectsByDomain = loadAll;

    useEffect(() => {
        loadAll();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // 过滤/排序在渲染时计算：原来走 state + effect，切换视图的那一帧会先渲染上一版
    // 列表（旧域的项目或空列表），随后 effect 再刷新 → 肉眼可见的闪烁（2026-11-11）
    const filteredProjects = useMemo(() => {
        let filtered = projects;
        // 视图层过滤：进入某知识域时只看该域（id=0 为「全部」）
        if (currentView === 'project' && selectedDomain && selectedDomain.id !== 0) {
            filtered = filtered.filter((p) => p.domain_id === selectedDomain.id);
        }

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

        return filtered;
    }, [projects, currentView, selectedDomain, searchText, sortBy]);

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
            ) : loadError ? (
                <div className="flex flex-col items-center justify-center h-96 px-4">
                    <Empty
                        description={`知识域加载失败：${loadError}`}
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    >
                        <Button type="primary" icon={<ReloadOutlined />} onClick={loadDomainsWithProjects}>
                            重试
                        </Button>
                    </Empty>
                </div>
            ) : domains.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-96 px-4">
                    <Empty
                        description="暂无知识域分类"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                </div>
            ) : (
                /* 知识域卡片（2026-11-13 重设计）：去掉大封面，改成紧凑信息卡——
                   一行能放 5–6 个；项目数量提到右上角做成大号数字，成为卡片的第一视觉焦点 */
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3 sm:gap-4">
                    {domains.map((domain) => (
                        <div
                            key={domain.id}
                            role="button"
                            tabIndex={0}
                            aria-label={`进入知识域 ${domain.name}`}
                            onClick={() => handleDomainSelect(domain)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    handleDomainSelect(domain);
                                }
                            }}
                            className="group relative rounded-2xl border border-[#E9EEF8] bg-white p-4 cursor-pointer transition-[border-color,box-shadow] duration-200 hover:border-[#CBD9F2] hover:shadow-[0_16px_32px_-20px_rgba(15,23,42,0.30)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                        >
                            <div className="flex items-start justify-between gap-2">
                                <span
                                    className="w-10 h-10 shrink-0 rounded-xl flex items-center justify-center text-[18px]"
                                    style={{
                                        background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))',
                                        color: '#5B8DEF',
                                    }}
                                >
                                    <DatabaseOutlined />
                                </span>
                                {/* 项目数量：大号等宽数字 + 小标签，压过描述成为卡片主信息 */}
                                <div className="text-right shrink-0 leading-none">
                                    <div
                                        className="tk-mono text-[24px] font-semibold leading-none"
                                        style={{ color: domain.projectCount > 0 ? '#2F62D8' : '#A9B4C9' }}
                                    >
                                        {domain.projectCount}
                                    </div>
                                    <div className="text-[10px] text-[#8A94AB] mt-1 tracking-wider">个项目</div>
                                </div>
                            </div>
                            <div className="mt-3 min-w-0">
                                <div className="text-[14.5px] font-semibold text-[#0F172A] truncate group-hover:text-[#2F62D8] transition-colors">
                                    {domain.name}
                                </div>
                                <div className="text-[12px] text-[#8A94AB] truncate mt-0.5" title={domain.description || undefined}>
                                    {domain.description || '暂无描述'}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );

    // 渲染项目列表视图（第二层）
    const renderProjectListView = () => (
        <div>
            {/* 工具条：返回 + 当前知识域 + 搜索/计数/排序。
                Hero chips 里已经展示过知识域名，这里只保留一行轻量文字，不再用大图标块重复；
                控件统一默认尺寸，不再套一层白卡 */}
            <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
                <Button icon={<ArrowLeftOutlined />} onClick={handleBackToDomains}>
                    返回知识域
                </Button>
                {selectedDomain && selectedDomain.id !== 0 && (
                    <>
                        <span className="h-4 w-px bg-gray-300" />
                        <span className="inline-flex items-center gap-1.5 min-w-0 text-sm font-medium text-gray-700">
                            <DatabaseOutlined className="text-[#5B8DEF]" />
                            <span className="truncate max-w-[200px]" title={selectedDomain.description || undefined}>
                                {selectedDomain.name}
                            </span>
                        </span>
                    </>
                )}
                <div className="w-full sm:w-auto sm:ml-auto flex flex-wrap items-center gap-2">
                    <Input
                        allowClear
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        placeholder="搜索本体名称或描述…"
                        prefix={<SearchOutlined className="text-gray-400" />}
                        className="w-full sm:w-[220px]"
                    />
                    <span className="text-sm text-gray-500 whitespace-nowrap">
                        <span className="text-[#5B8DEF] font-semibold">{filteredProjects.length}</span> 个本体
                    </span>
                    <Select
                        value={sortBy}
                        onChange={setSortBy}
                        className="min-w-[132px]"
                        options={[
                            { label: '最新发布', value: 'latest' },
                            { label: '节点数量', value: 'nodes' },
                        ]}
                    />
                </div>
            </div>

            {loading ? (
                <div className="flex justify-center items-center h-96">
                    <Spin size="large" tip="加载中..." />
                </div>
            ) : loadError ? (
                <div className="flex flex-col items-center justify-center h-96 px-4">
                    <Empty
                        description={`本体加载失败：${loadError}`}
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    >
                        <Button type="primary" icon={<ReloadOutlined />} onClick={loadProjectsByDomain}>
                            重试
                        </Button>
                    </Empty>
                </div>
            ) : filteredProjects.length === 0 ? (
                /* 空态收敛成一处：antd Empty 自带的插图 + 一句说明 + 一个次要操作。
                   原来套了白卡，额外又画了圆形图标块，再叠 Empty 自己的图标，三层重复 */
                <div className="flex items-center justify-center h-96 px-4">
                    <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={
                            <span className="text-gray-500">
                                {searchText ? '没有找到匹配的本体' : '该知识域下暂无公开本体'}
                            </span>
                        }
                    >
                        {selectedDomain && selectedDomain.id !== 0 && (
                            <Button onClick={handleBackToDomains}>选择其他知识域</Button>
                        )}
                    </Empty>
                </div>
            ) : (
                /* 项目卡片（2026-11-13 重设计）：原来是大封面竖卡，列窄、卡高、信息散；
                   改成缩略图 + 信息区的紧凑横卡，一屏能放更多，统计信息也不再抢戏 */
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3 sm:gap-4">
                    {filteredProjects.map((project) => {
                        const nodes = project.graph_data?.nodes?.length || 0;
                        const edges = project.graph_data?.edges?.length || 0;
                        // 只有「全部知识域」视图才需要再标一次领域名；进入单域后是重复信息
                        const showDomain = !!selectedDomain && selectedDomain.id === 0 && !!project.domain;
                        return (
                            <div
                                key={project.id}
                                role="button"
                                tabIndex={0}
                                aria-label={`查看本体 ${project.name}`}
                                onClick={() => navigate(`/asset-center/${project.id}`)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        navigate(`/asset-center/${project.id}`);
                                    }
                                }}
                                className="group flex items-start gap-3 rounded-2xl border border-[#E9EEF8] bg-white p-3.5 cursor-pointer transition-[border-color,box-shadow] duration-200 hover:border-[#CBD9F2] hover:shadow-[0_16px_32px_-20px_rgba(15,23,42,0.30)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                            >
                                {/* 本体缩略图：64px 里画整张图谱会被裁得零碎、认不出是什么；
                                    改成「淡蓝底 + 层级图标」的图块，语义清楚又不抢信息 */}
                                <div
                                    className="relative w-16 h-16 shrink-0 rounded-xl overflow-hidden flex items-center justify-center"
                                    style={{ background: 'linear-gradient(135deg, #F1F6FF 0%, #EAF1FE 55%, #F4F0FF 100%)' }}
                                >
                                    <div className="absolute inset-0 ac-cover-grid" />
                                    <div className="absolute inset-0 ac-glow" />
                                    <ApartmentOutlined className="relative text-[22px]" style={{ color: '#5B8DEF' }} />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="text-[15px] font-semibold text-[#0F172A] truncate group-hover:text-[#2F62D8] transition-colors" title={project.name}>
                                        {project.name}
                                    </div>
                                    <div className="text-[12px] text-[#8A94AB] truncate mt-0.5" title={project.description || undefined}>
                                        {project.description || '暂无描述'}
                                    </div>
                                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-[11px] text-[#8A94AB]">
                                        <span className="inline-flex items-center gap-1">
                                            <NodeIndexOutlined style={{ color: '#5B8DEF' }} />
                                            <span><span className="tk-mono">{nodes}</span> 节点</span>
                                        </span>
                                        <span className="inline-flex items-center gap-1">
                                            <span style={{ color: '#8B5CF6' }}>⚡</span>
                                            <span><span className="tk-mono">{edges}</span> 关系</span>
                                        </span>
                                        <span className="inline-flex items-center gap-1 min-w-0">
                                            <UserOutlined />
                                            <span className="truncate max-w-[90px]">{project.owner?.username || '未知'}</span>
                                        </span>
                                        {showDomain && (
                                            <span className="inline-flex items-center gap-1 min-w-0">
                                                <DatabaseOutlined style={{ color: '#5B8DEF' }} />
                                                <span className="truncate max-w-[110px]">{project.domain?.name}</span>
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                .ac-cover-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.09) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.09) 1px, transparent 1px);
                    background-size: 24px 24px;
                }
                /* 浅色封面（2026-11-13）：辉光用径向渐变一次画好，比两块 blur 大圆更省合成 */
                .ac-glow {
                    background:
                        radial-gradient(160px 140px at 92% 0%, rgba(91,141,239,0.22), transparent 72%),
                        radial-gradient(170px 150px at 0% 108%, rgba(181,133,242,0.18), transparent 72%);
                }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div ref={scrollRef} className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title="资产中心"
                    titleSuffix="PUBLIC ONTOLOGIES"
                    subtitle="浏览已发布的公共本体资产：按知识域归类 · 只读快照 · 支持在线检索与图谱预览"
                    showConstellation={currentView === 'domain'}
                />
                {currentView === 'domain' ? renderDomainSelectionView() : renderProjectListView()}
            </div>
        </div>
    );
};

export default AssetCenterPage;
