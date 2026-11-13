import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Button } from 'antd';
import {
    RocketOutlined,
    FileTextOutlined,
    TeamOutlined,
    DatabaseOutlined,
    ArrowRightOutlined,
    ApartmentOutlined,
    ShareAltOutlined,
    BookOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../shared/auth/authStore';
import Navbar from '../components/Layout/Navbar';
import TechHero from '../components/TechHero';
import { projectsApi } from '../api/projects';

const C = {
    bgDark: '#0F1420',
    purple: '#B585F2',
    blue: '#5B8DEF',
    cyan: '#5AC8FA',
    amber: '#F2B950',
};

/** 工作台（首页）：深色科技风 Hero + 渐变统计卡，数据逻辑与旧版一致 */
const HomePage: React.FC = () => {
    const navigate = useNavigate();
    const [stats, setStats] = useState({
        myProjects: 0,
        publishedOntologies: 0,
        publicAssets: 0,
        totalNodes: 0
    });

    // 获取首页统计数据（使用新的API端点）
    useEffect(() => {
        const fetchStats = async () => {
            try {
                const [myProjects, publicProjects] = await Promise.all([
                    projectsApi.getMyProjects(),
                    projectsApi.getPublicProjects()
                ]);

                let totalNodes = 0;
                // 只统计已发布的项目节点数（去重：按项目ID去重，避免个人项目在my和public中重复计算）
                const allPublishedProjects = [...myProjects, ...publicProjects]
                    .filter(project => project.is_published);

                const uniqueProjects = Array.from(new Map(allPublishedProjects.map(p => [p.id, p])).values());

                uniqueProjects.forEach(project => {
                    totalNodes += project.nodes_total ?? project.graph_data?.nodes?.length ?? 0;
                });

                setStats({
                    myProjects: myProjects.length,
                    publishedOntologies: myProjects.filter(p => p.is_published).length,
                    publicAssets: publicProjects.length,
                    totalNodes
                });
            } catch (error) {
                console.error('获取统计数据失败:', error);
                if (error instanceof Error && error.message.includes('401')) {
                    console.warn('请先登录以获取个人项目数据');
                }
            }
        };

        fetchStats();
    }, []);

    const { user, modules } = useAuthStore();
    // 模块可见性：admin 全量；普通用户按授权矩阵；矩阵未加载前放行（避免首屏闪烁）
    const canUse = (code: string) =>
        user?.role === 'admin' ? true : modules.length > 0 ? modules.includes(code) : true;

    // 三张卡各去不同页面：智能提取/可视化编辑本是同一项目内的前后两步，合并为「本体构建」，
    // 第三张补上平台已具备、首页原先缺入口的「本体问答」（2026-11-11）
    // 2026-11-11：按模块授权过滤——入口指向的页面在左侧菜单没有对应项时不再展示，
    // 避免点进去落到一个「没有归属菜单」的页面（路由侧另加了 RequireModule 兜底）
    const features = [
        {
            module: 'projects',
            icon: <ApartmentOutlined />,
            color: C.blue,
            ink: '#2F62D8',
            title: '本体构建',
            description: '上传文档 AI 提取结构 · 骨架画布精修 · 实例图谱探索',
            action: () => navigate('/my-projects'),
        },
        {
            module: 'asset_center',
            icon: <ShareAltOutlined />,
            color: C.cyan,
            ink: '#1B87AD',
            title: '协作共享',
            description: '版本快照、审核门禁，发布到公共资产中心',
            action: () => navigate('/asset-center'),
        },
        {
            module: 'qa',
            icon: <BookOutlined />,
            color: C.purple,
            ink: '#6D3FD8',
            title: '本体问答',
            description: '基于已建本体的自然语言问答，答案可溯源到图谱',
            action: () => navigate('/qa'),
        },
    ].filter((f) => canUse(f.module));

    const statCards = [
        { label: '我的项目', value: stats.myProjects, icon: <FileTextOutlined /> },
        { label: '已发布本体', value: stats.publishedOntologies, icon: <DatabaseOutlined /> },
        { label: '公共资产', value: stats.publicAssets, icon: <TeamOutlined /> },
        { label: '图谱节点', value: stats.totalNodes, icon: <ApartmentOutlined /> },
    ];

    const steps = [
        { color: C.blue, ink: '#2F62D8', title: '创建项目', desc: '在「我的项目」中创建一个新的本体建模项目' },
        { color: C.purple, ink: '#6D3FD8', title: '上传文档', desc: '上传相关文档，AI 将自动提取本体结构' },
        { color: C.cyan, ink: '#1B87AD', title: '可视化调整', desc: '在画布上拖拽、编辑节点和关系，完善本体模型' },
        { color: C.amber, ink: '#B8790F', title: '发布共享', desc: '发布到图数据库，并在资产中心公开展示' },
    ];

    const breadcrumbs = [{ title: '工作台' }];

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                .hp-mono { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace; }
                /* 统计卡（2026-11-13）：四张卡统一用品牌蓝的极淡晕染，不再蓝/紫/青/琥珀各来一套——
                   一屏四个色相会让页面显花。彩色留给下面的功能入口卡，数据层保持冷静。
                   只读卡片，不做 hover 抬升，避免"可点击"的误导 */
                .hp-stat-card { position: relative; overflow: hidden; border-radius: 14px;
                    border: 1px solid #E7EEF9;
                    background-image: linear-gradient(135deg, rgba(91,141,239,.07) 0%, #FFFFFF 62%);
                    box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 12px 26px -22px rgba(15,23,42,.30); }
                /* 功能卡：底部压状态色淡晕 + 右下角一组同心圆环（不使用图标本身——
                   图标在左侧已经出现过一次，重复会显得堆砌）。
                   卡片高于统计卡，作为首屏的第二层视觉重心 */
                .hp-feature-card { position: relative; overflow: hidden; border-radius: 14px;
                    border: 1px solid #E7EEF9; min-height: 128px;
                    display: flex; flex-direction: column;
                    transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
                    cursor: pointer;
                    box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 12px 26px -22px rgba(15,23,42,.30); }
                .hp-feature-card:hover { transform: translateY(-3px); border-color: #D3E1F5;
                    box-shadow: 0 20px 38px -24px rgba(15, 23, 42, 0.36); }
                /* body 撑满卡片高度，把「查看详情」压到卡片底部，底部不再是空白 */
                .hp-feature-card > .ant-card-body { flex: 1; display: flex; flex-direction: column;
                    justify-content: space-between; }
                .hp-feature-ring { position: absolute; right: -34px; bottom: -54px;
                    width: 158px; height: 158px; opacity: .13; pointer-events: none;
                    transition: opacity .2s ease;
                    background: radial-gradient(circle at center,
                        transparent 27%, currentColor 28.2%, transparent 29.6%,
                        transparent 47%, currentColor 48.2%, transparent 49.6%,
                        transparent 67%, currentColor 68.2%, transparent 69.6%); }
                .hp-feature-card:hover .hp-feature-ring { opacity: .22; }
                /* 底部「查看详情」：常显淡灰，hover 时变成该卡的主题色，箭头右移 */
                .hp-feature-cta { color: #6F7B96; transition: color .2s ease; }
                .hp-feature-card:hover .hp-feature-cta { color: var(--hp-ink, #2A57C6); }
                .hp-feature-cta-arrow { transition: transform .2s ease; }
                .hp-feature-card:hover .hp-feature-cta-arrow { transform: translateX(3px); }
                /* Hero 内的「浏览资产中心」幽灵按钮：浅色卡片上用品牌蓝文字 + 白底，
                   hover 时只加深淡蓝底和描边，不再刷成白底 */
                .hp-hero-ghost.ant-btn.ant-btn-default:hover,
                .hp-hero-ghost.ant-btn.ant-btn-default:focus-visible {
                    background: rgba(91, 141, 239, 0.12) !important;
                    border-color: rgba(91, 141, 239, 0.50) !important;
                    color: #2A57C6 !important;
                }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {/* ── Hero：浅色玻璃面板（与全屏背景、侧栏、二级页同一套材质） ── */}
                <TechHero
                    tone="light"
                    className="!mb-4"
                    title="企业级语义知识及本体治理平台"
                    subtitle="基于 AI 的本体构建 · 知识图谱 · 溯源治理一站式流水线，让知识资产沉淀为可计算的服务"
                    actions={
                        (canUse('projects') || canUse('asset_center')) ? (
                            <>
                                {canUse('projects') && (
                                    <Button
                                        type="primary"
                                        icon={<RocketOutlined />}
                                        onClick={() => navigate('/my-projects')}
                                        className="h-9 sm:h-10 px-4 sm:px-6 border-0"
                                        style={{ background: `linear-gradient(95deg, ${C.blue}, ${C.purple})`, boxShadow: '0 8px 20px -10px rgba(91,141,239,0.55)' }}
                                    >
                                        开始使用
                                    </Button>
                                )}
                                {canUse('asset_center') && (
                                    <Button
                                        icon={<ArrowRightOutlined />}
                                        onClick={() => navigate('/asset-center')}
                                        className="hp-hero-ghost h-9 sm:h-10 px-4 sm:px-6"
                                        style={{ color: '#2A57C6', borderColor: 'rgba(91,141,239,0.35)', background: 'rgba(255,255,255,0.62)' }}
                                    >
                                        浏览资产中心
                                    </Button>
                                )}
                            </>
                        ) : undefined
                    }
                />

                {/* ── 统计卡片 ── */}
                <Row gutter={[12, 12]} className="mb-6">
                    {statCards.map((s) => (
                        <Col xs={24} sm={12} xl={6} key={s.label}>
                            <Card className="hp-stat-card" styles={{ body: { padding: '16px 20px' } }}>
                                <div className="flex items-center justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="text-[12.5px] leading-5 text-[#66738F]">{s.label}</div>
                                        {/* 与「我的项目」统计卡一致：先渲染 0，接口返回后原地替换，不做加载占位 */}
                                        <div
                                            className="mt-0.5 text-[26px] font-bold leading-none text-[#0F172A]"
                                            style={{ fontFamily: "'JetBrains Mono', Consolas, monospace" }}
                                        >
                                            {Number(s.value).toLocaleString('en-US')}
                                        </div>
                                    </div>
                                    <span
                                        className="w-10 h-10 shrink-0 rounded-xl flex items-center justify-center text-[17px] text-[#2F62D8]"
                                        style={{
                                            background: 'linear-gradient(135deg, rgba(91,141,239,0.18), rgba(139,92,246,0.10))',
                                            boxShadow: '0 8px 18px -10px rgba(91, 141, 239, 0.55), inset 0 1px 0 rgba(255,255,255,0.7)',
                                        }}
                                    >
                                        {s.icon}
                                    </span>
                                </div>
                            </Card>
                        </Col>
                    ))}
                </Row>

                {/* ── 核心功能（权限过滤后少于 2 张时整块隐藏：只剩 1 张会空出半栏，
                    0 张只剩一个空标题，都不好看，2026-11-12） ── */}
                {features.length >= 2 && (
                    <div className="mb-6">
                        <h2 className="text-[15px] font-semibold text-[#0F172A] mb-3">核心功能</h2>
                        <Row gutter={[12, 12]}>
                            {features.map((feature, index) => (
                                <Col xs={24} sm={24} md={12} lg={features.length === 3 ? 8 : 12} key={index}>
                                    <Card
                                        className="hp-feature-card h-full"
                                        style={{
                                            backgroundImage: `linear-gradient(155deg, #FFFFFF 0%, ${feature.color}1F 100%)`,
                                            '--hp-ink': feature.ink,
                                        } as React.CSSProperties}
                                        styles={{ body: { padding: '18px 22px' } }}
                                        onClick={feature.action}
                                    >
                                        <span className="hp-feature-ring" style={{ color: feature.color }} aria-hidden />
                                        <div className="relative flex items-center gap-4">
                                            <span
                                                className="w-11 h-11 shrink-0 rounded-xl flex items-center justify-center text-[19px]"
                                                style={{
                                                    color: '#fff',
                                                    background: `linear-gradient(135deg, ${feature.color}, ${feature.color}AA)`,
                                                    boxShadow: `0 8px 18px -10px ${feature.color}`,
                                                }}
                                            >
                                                {feature.icon}
                                            </span>
                                            <div className="min-w-0">
                                                <h3 className="text-[16px] font-semibold leading-tight text-[#0F172A]">{feature.title}</h3>
                                                <p className="mt-1.5 text-[12.5px] leading-[1.6] text-[#66738F]">{feature.description}</p>
                                            </div>
                                        </div>
                                        <div className="relative mt-3 inline-flex items-center gap-1.5 text-[12.5px] font-medium hp-feature-cta">
                                            查看详情 <ArrowRightOutlined className="text-[10px] hp-feature-cta-arrow" />
                                        </div>
                                    </Card>
                                </Col>
                            ))}
                        </Row>
                    </div>
                )}

                {/* ── 快速开始指南（2026-11-13 重做）：上面已经是 4 张统计卡 + 3 张功能卡，
                    这里再摊成 4 张小卡会让整页都是碎块；收进一张大卡里横排四步，
                    用极淡的竖线分栏，既整齐又不增加卡片数量 ── */}
                <Card
                    className="shadow-sm"
                    styles={{ body: { padding: '20px 24px' } }}
                    style={{ backgroundImage: 'radial-gradient(420px 160px at 100% 0%, rgba(91,141,239,.07), transparent 72%)' }}
                >
                    <h2 className="text-[15px] font-semibold text-[#0F172A] mb-4">快速开始指南</h2>
                    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-y-5">
                        {steps.map((step, i) => (
                            <div
                                key={i}
                                className={`flex items-start gap-3 ${i > 0 ? 'xl:pl-5 xl:border-l xl:border-[#ECF1F8]' : ''}`}
                            >
                                <span
                                    className="hp-mono mt-0.5 w-7 h-7 shrink-0 rounded-full flex items-center justify-center text-[11px] font-bold"
                                    style={{ color: step.ink, background: `${step.color}1F` }}
                                >
                                    {i + 1}
                                </span>
                                <div className="min-w-0">
                                    <h4 className="text-[13.5px] font-semibold leading-tight text-[#0F172A]">{step.title}</h4>
                                    <p className="mt-1 text-[12px] leading-[1.6] text-[#66738F]">{step.desc}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                </Card>
            </div>
        </div>
    );
};

export default HomePage;
