import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Empty, Spin, message, Input, Select, Button } from 'antd';
import { UserOutlined, NodeIndexOutlined, DatabaseOutlined, ApartmentOutlined, ArrowLeftOutlined, ArrowRightOutlined, ReloadOutlined, SearchOutlined, ShareAltOutlined } from '@ant-design/icons';
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

/** 卡片里只显示 月/日，完整时间留给详情页 */
const fmtShortDate = (v?: string | null) =>
    v ? new Date(v).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) : '—';

/**
 * 知识域色相轮转（2026-11-13）：所有域共用同一个图标、同一种白底，
 * 六张卡排在一起彼此分不出来。按索引轮转四种相邻品牌色——蓝/紫/青/琥珀，
 * 相邻两张必然不同色；wash 是极淡的底色晕染（与工作台功能卡同一手法），
 * ink 用于图标和计数胶囊上的文字。
 */
const DOMAIN_HUES = [
    { ink: '#2F62D8', wash: 'rgba(91, 141, 239, 0.10)', tint: 'rgba(91, 141, 239, 0.16)' },
    { ink: '#6D3FD8', wash: 'rgba(139, 92, 246, 0.09)', tint: 'rgba(139, 92, 246, 0.15)' },
    { ink: '#1B87AD', wash: 'rgba(90, 200, 250, 0.12)', tint: 'rgba(90, 200, 250, 0.18)' },
    { ink: '#B8790F', wash: 'rgba(242, 185, 80, 0.13)', tint: 'rgba(242, 185, 80, 0.20)' },
] as const;


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
                /* 知识域卡片（2026-11-13 再设计）：一行 5–6 个的前提下，卡片净宽只有 ~163px。
                   原来六张卡同一个图标同一张白底，扫视时没有任何锚点——
                   现在色相按索引轮转做区分，域名与数量各占一层：
                   右上角 22px 等宽数字是"这个域有多少东西"的第一信号，
                   下方 14.5px 域名回答"是哪个域"，描述保留并放宽到两行
                   （6 列下原先必然被截成一个省略号）。
                   底部补「更新于」+ hover 浮现的箭头，给整卡可点一个暗示。
                   不用顶部横线做色带——顶部横线在这版界面里已经被否过一次。
                   空域（0 个已发布本体）整卡降权且不可点击：点进去只有空白，
                   与其让用户白跑一趟，不如直接置灰、去掉手型光标和 hover 反馈 */
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3 sm:gap-4">
                    {domains.map((domain, index) => {
                        const hue = DOMAIN_HUES[index % DOMAIN_HUES.length];
                        const isEmpty = domain.projectCount === 0;
                        return (
                            <div
                                key={domain.id}
                                role={isEmpty ? undefined : 'button'}
                                tabIndex={isEmpty ? undefined : 0}
                                aria-label={isEmpty ? undefined : `进入知识域 ${domain.name}`}
                                aria-disabled={isEmpty || undefined}
                                onClick={isEmpty ? undefined : () => handleDomainSelect(domain)}
                                onKeyDown={isEmpty ? undefined : (e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        handleDomainSelect(domain);
                                    }
                                }}
                                className={`group flex flex-col rounded-2xl border p-3.5 transition-[border-color,box-shadow] duration-200 ${
                                    isEmpty
                                        ? 'border-[#EDF1F8] cursor-default'
                                        : 'border-[#E9EEF8] cursor-pointer hover:border-[#CBD9F2] hover:shadow-[0_16px_32px_-20px_rgba(15,23,42,0.30)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300'
                                }`}
                                style={{
                                    backgroundImage: isEmpty
                                        ? 'linear-gradient(160deg, #FFFFFF 0%, #FAFBFE 100%)'
                                        : `linear-gradient(160deg, #FFFFFF 0%, ${hue.wash} 100%)`,
                                }}
                            >
                                <div className="flex items-start justify-between gap-2">
                                    <span
                                        className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-[16px]"
                                        style={{
                                            color: isEmpty ? '#A9B4C9' : hue.ink,
                                            background: isEmpty
                                                ? 'rgba(15, 23, 42, 0.04)'
                                                : `linear-gradient(135deg, ${hue.tint}, rgba(255, 255, 255, 0.55))`,
                                        }}
                                    >
                                        <DatabaseOutlined />
                                    </span>
                                    {/* 项目数量：卡片的第一视觉信号，22px 等宽数字 + 小标签 */}
                                    <div className="shrink-0 text-right leading-none">
                                        <div
                                            className="tk-mono text-[22px] font-bold leading-none"
                                            style={{ color: isEmpty ? '#A9B4C9' : hue.ink }}
                                        >
                                            {domain.projectCount}
                                        </div>
                                        <div className="mt-1 text-[10px] tracking-wider text-[#8A94AB]">个本体</div>
                                    </div>
                                </div>
                                <div className="mt-2.5 min-w-0">
                                    <div
                                        className={`line-clamp-2 text-[14.5px] font-semibold leading-snug transition-colors ${
                                            isEmpty ? 'text-[#5A6680]' : 'text-[#0F172A] group-hover:text-[#2F62D8]'
                                        }`}
                                    >
                                        {domain.name}
                                    </div>
                                    {/* 域名和描述都放宽到两行，正常数据下一屏就能读完，
                                        不再挂原生 title——文字可见时再弹一个同样的气泡只是干扰 */}
                                    <div className="line-clamp-2 mt-1 text-[12px] leading-[1.6] text-[#8A94AB]">
                                        {domain.description || '暂无描述'}
                                    </div>
                                </div>
                                <div className="mt-auto flex items-center justify-between gap-2 pt-2.5">
                                    <span className="truncate text-[11px] text-[#A3ADC2]">
                                        {isEmpty ? '暂无已发布本体' : `更新于 ${fmtShortDate(domain.updated_at)}`}
                                    </span>
                                    {!isEmpty && (
                                        <ArrowRightOutlined className="shrink-0 text-[10px] text-[#B7C1D4] opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100" />
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );

    // 渲染项目列表视图（第二层）
    const renderProjectListView = () => (
        <div>
            {/* 工具条（2026-11-13）：与「我的项目」同构——左侧返回 + 当前知识域，
                右侧把搜索和排序收进一条浅色查询条，计数作为结果读数跟在查询条外侧。
                原来三个控件逐一散着放、颜色走 Tailwind 中性灰，和全站冷调对不上；
                返回按钮也从实心默认样式降成浅底描边，把最重的位置让给搜索 */}
            <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-2">
                    <Button className="ac-back-btn" icon={<ArrowLeftOutlined />} onClick={handleBackToDomains}>
                        返回知识域
                    </Button>
                    {selectedDomain && selectedDomain.id !== 0 && (
                        <span className="inline-flex min-w-0 items-center gap-1.5 text-[13px] font-medium text-[#1F2A44]">
                            <DatabaseOutlined className="shrink-0 text-[#5B8DEF]" />
                            <span className="truncate max-w-[180px]">{selectedDomain.name}</span>
                        </span>
                    )}
                    {/* 结果读数跟着域名放在左侧，读成一个短语「金融 · 3 个本体」；
                        原来孤零零贴在右侧查询条外面，既不属于控件也不属于标题。
                        数字走等宽字体、标签走中文字体，两套字形高度不同，
                        用 items-baseline 对齐基线，否则数字会看着往上蹿 */}
                    <span
                        className="inline-flex shrink-0 items-baseline gap-1 rounded-full px-2.5 py-[3px] text-[11.5px] leading-[16px] text-[#52607A]"
                        style={{ background: 'rgba(91, 141, 239, 0.10)' }}
                    >
                        <span
                            className="tk-mono text-[12.5px] font-semibold leading-[16px]"
                            style={{ color: filteredProjects.length > 0 ? '#2F62D8' : '#8A94AB' }}
                        >
                            {filteredProjects.length}
                        </span>
                        个本体
                    </span>
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto">
                    <div className="ac-query flex w-full flex-wrap items-center gap-1 rounded-[12px] border border-[#E7EEF9] bg-white/70 p-1 sm:w-auto">
                        <Input
                            allowClear
                            value={searchText}
                            onChange={(e) => setSearchText(e.target.value)}
                            placeholder="搜索本体名称或描述…"
                            prefix={<SearchOutlined className="text-[#5B8DEF]" />}
                            className="w-full sm:w-[215px]"
                        />
                        <div className="flex items-center gap-1 pl-1">
                            <span className="px-1 text-[12.5px] whitespace-nowrap text-[#66738F]">排序</span>
                            <Select
                                value={sortBy}
                                onChange={setSortBy}
                                style={{ width: 118 }}
                                options={[
                                    { label: '最新发布', value: 'latest' },
                                    { label: '节点数量', value: 'nodes' },
                                ]}
                            />
                        </div>
                    </div>
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
                /* 项目卡片（2026-11-13 重排）：原来的 64px 缩略图是纯装饰——
                   每张卡都是同一张网格 + 同一层辉光 + 同一个图标，项目之间零差异，
                   却吃掉约 1/4 的卡宽，把描述挤成一行省略号。
                   改成 36px 图标块 + 两行描述 + 底部 meta，
                   与「我的项目」卡片视图、知识域卡同一套结构。
                   meta 分两组：规模数据（节点/关系）做成淡底小胶囊，
                   归属信息（作者 · 领域）走更淡的 11px 灰字，扫视时先看到体量 */
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
                                className="group flex flex-col rounded-2xl border border-[#E9EEF8] p-3.5 cursor-pointer transition-[border-color,box-shadow] duration-200 hover:border-[#CBD9F2] hover:shadow-[0_16px_32px_-20px_rgba(15,23,42,0.30)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                                style={{ backgroundImage: 'linear-gradient(160deg, #FFFFFF 0%, rgba(91, 141, 239, 0.07) 100%)' }}
                            >
                                <div className="flex items-start gap-2.5">
                                    <span
                                        className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-[16px] text-[#2F62D8]"
                                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                    >
                                        <ApartmentOutlined />
                                    </span>
                                    <div className="min-w-0 flex-1 pt-0.5">
                                        <div className="truncate text-[14.5px] font-semibold text-[#0F172A] transition-colors group-hover:text-[#2F62D8]">
                                            {project.name}
                                        </div>
                                        <div className="mt-1 truncate text-[11px] text-[#A3ADC2]">
                                            <UserOutlined className="mr-1 text-[10px]" />
                                            {project.owner?.username || '未知作者'}
                                            {showDomain && <span> · {project.domain?.name}</span>}
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-2 line-clamp-2 text-[12px] leading-[1.6] text-[#8A94AB]">
                                    {project.description || '暂无描述'}
                                </div>
                                <div className="mt-auto flex items-center justify-between gap-2 border-t border-[#F0F4FB] pt-2.5">
                                    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                                        <span
                                            className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-[2px] text-[10.5px] leading-[14px] text-[#52607A]"
                                            style={{ background: 'rgba(91, 141, 239, 0.10)' }}
                                        >
                                            <NodeIndexOutlined className="text-[11px]" style={{ color: '#5B8DEF' }} />
                                            <span className="tk-mono text-[11.5px] font-semibold leading-[14px] text-[#2F62D8]">{nodes}</span>
                                            节点
                                        </span>
                                        <span
                                            className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-[2px] text-[10.5px] leading-[14px] text-[#52607A]"
                                            style={{ background: 'rgba(139, 92, 246, 0.10)' }}
                                        >
                                            <ShareAltOutlined className="text-[11px]" style={{ color: '#8B5CF6' }} />
                                            <span className="tk-mono text-[11.5px] font-semibold leading-[14px] text-[#6D3FD8]">{edges}</span>
                                            关系
                                        </span>
                                    </div>
                                    {/* 箭头常驻占位、只在 hover 显形，避免出现时把左侧胶囊挤动 */}
                                    <ArrowRightOutlined className="shrink-0 text-[10px] text-[#B7C1D4] opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100" />
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
                /* 二级工具条（2026-11-13）：与「我的项目」的查询条同构——
                   搜索/排序收进一条浅色容器，内部控件去边框、透明底，
                   整条读起来像一个查询栏，而不是几个孤立的框 */
                .ac-query .ant-input-affix-wrapper,
                .ac-query .ant-select:not(.ant-select-customize-input) .ant-select-selector,
                .ac-query .ant-btn {
                    background-color: transparent !important;
                    border-color: transparent !important;
                    box-shadow: none !important;
                }
                .ac-query .ant-input-affix-wrapper:hover,
                .ac-query .ant-select:not(.ant-select-disabled):not(.ant-select-customize-input):hover .ant-select-selector,
                .ac-query .ant-btn:hover {
                    background-color: #FFFFFF !important;
                    border-color: #DCE7F7 !important;
                }
                /* 聚焦态只留 1px 品牌蓝描边，不叠 3px 外光晕：
                   查询条本身已经有容器边，两层框套在一起会显得又粗又重。
                   注意 antd 5.29 的 affix wrapper 已不挂 -focused 类，焦点态走 :focus-within，
                   只写 -focused 的话输入时命中的是上面的 hover 规则，边框一直是灰的 */
                .ac-query .ant-input-affix-wrapper:focus-within,
                .ac-query .ant-select-focused .ant-select-selector {
                    background-color: #FFFFFF !important;
                    border-color: #5B8DEF !important;
                    box-shadow: none !important;
                }
                /* 返回按钮：次要动作，降到低对比的浅底描边，不和右侧搜索抢注意力 */
                .ac-back-btn.ant-btn-default {
                    background: rgba(255, 255, 255, 0.72);
                    border-color: #E1E8F4;
                    color: #52607A;
                }
                .ac-back-btn.ant-btn-default:not(:disabled):hover {
                    color: #2A57C6 !important;
                    border-color: rgba(91, 141, 239, 0.42) !important;
                    background: rgba(255, 255, 255, 0.92) !important;
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
