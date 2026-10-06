import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Button } from 'antd';
import {
    RocketOutlined,
    FileTextOutlined,
    TeamOutlined,
    DatabaseOutlined,
    ArrowRightOutlined,
    ApartmentOutlined,
    ShareAltOutlined,
    ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
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
    const [loading, setLoading] = useState(true);

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
                    if (project.graph_data?.nodes) {
                        totalNodes += project.graph_data.nodes.length;
                    }
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
            } finally {
                setLoading(false);
            }
        };

        fetchStats();
    }, []);

    const features = [
        {
            icon: <ThunderboltOutlined />,
            color: C.blue,
            title: '智能提取',
            description: '上传文档，AI 自动提取本体结构与实例',
            action: () => navigate('/my-projects'),
        },
        {
            icon: <ApartmentOutlined />,
            color: C.purple,
            title: '可视化编辑',
            description: '骨架画布 + Sigma 图谱探索，双模式编辑',
            action: () => navigate('/my-projects'),
        },
        {
            icon: <ShareAltOutlined />,
            color: C.cyan,
            title: '协作共享',
            description: '版本快照、审核门禁，发布到公共资产中心',
            action: () => navigate('/asset-center'),
        },
    ];

    const statCards = [
        { label: '我的项目', value: stats.myProjects, icon: <FileTextOutlined />, color: C.blue },
        { label: '已发布本体', value: stats.publishedOntologies, icon: <DatabaseOutlined />, color: C.purple },
        { label: '公共资产', value: stats.publicAssets, icon: <TeamOutlined />, color: C.cyan },
        { label: '图谱节点', value: stats.totalNodes, icon: <ApartmentOutlined />, color: C.amber },
    ];

    const steps = [
        { color: C.blue, title: '创建项目', desc: '在「我的项目」中创建一个新的本体建模项目' },
        { color: C.purple, title: '上传文档', desc: '上传相关文档，AI 将自动提取本体结构' },
        { color: C.cyan, title: '可视化调整', desc: '在画布上拖拽、编辑节点和关系，完善本体模型' },
        { color: C.amber, title: '发布共享', desc: '发布到图数据库，并在资产中心公开展示' },
    ];

    const breadcrumbs = [{ title: '首页' }];

    return (
        <div className="min-h-screen bg-gray-50">
            <style>{`
                .hp-mono { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace; }
                .hp-stat-card { position: relative; overflow: hidden; border-radius: 14px; border: 1px solid #EEF1F8;
                    transition: all .25s ease; }
                .hp-stat-card:hover { transform: translateY(-3px); box-shadow: 0 12px 28px -10px rgba(15,20,32,.18); border-color: #E2E8F8; }
                .hp-stat-bar { position:absolute; top:0; left:0; right:0; height:3px; }
                .hp-feature-card { position: relative; overflow: hidden; border-radius: 14px; border: 1px solid #EEF1F8;
                    transition: all .25s ease; cursor: pointer; }
                .hp-feature-card:hover { transform: translateY(-4px); box-shadow: 0 16px 34px -12px rgba(15,20,32,.2); }
                .hp-feature-card::after { content:''; position:absolute; top:0; right:0; width:64px; height:64px;
                    background: radial-gradient(circle at top right, rgba(181,133,242,.12), transparent 70%); }
                .hp-feature-card:hover .hp-feature-arrow { opacity: 1 !important; }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="w-full max-w-[1920px] mx-auto p-4 sm:p-6 lg:p-8">
                {/* ── Hero：深色科技面板（TechHero 共享组件） ── */}
                <TechHero
                    chips={['SEMANTIC KNOWLEDGE PLATFORM', 'ENGINE · v2.0']}
                    title="企业级语义知识及本体治理平台"
                    subtitle="基于 AI 的本体构建 · 知识图谱 · 溯源治理一站式流水线，让知识资产沉淀为可计算的服务"
                    actions={
                        <>
                            <Button
                                type="primary"
                                size="large"
                                icon={<RocketOutlined />}
                                onClick={() => navigate('/my-projects')}
                                className="h-10 sm:h-12 px-4 sm:px-8 border-0"
                                style={{ background: `linear-gradient(95deg, ${C.blue}, ${C.purple})`, boxShadow: `0 8px 24px -8px ${C.purple}` }}
                            >
                                开始使用
                            </Button>
                            <Button
                                size="large"
                                icon={<ArrowRightOutlined />}
                                onClick={() => navigate('/asset-center')}
                                className="h-10 sm:h-12 px-4 sm:px-8 bg-transparent"
                                style={{ color: '#C9D4FF', borderColor: '#2A3550' }}
                            >
                                浏览资产中心
                            </Button>
                        </>
                    }
                />

                {/* ── 统计卡片 ── */}
                <Row gutter={[16, 16]} className="mb-6 sm:mb-8">
                    {statCards.map((s) => (
                        <Col xs={24} sm={12} xl={6} key={s.label}>
                            <Card loading={loading} className="hp-stat-card shadow-sm" styles={{ body: { padding: '20px 22px' } }}>
                                <div className="hp-stat-bar" style={{ background: `linear-gradient(90deg, ${s.color}, transparent)` }} />
                                <div className="flex items-center justify-between">
                                    <Statistic
                                        title={<span className="text-gray-500 text-[13px]">{s.label}</span>}
                                        value={s.value}
                                        valueStyle={{
                                            color: '#0F1420',
                                            fontFamily: "'JetBrains Mono', Consolas, monospace",
                                            fontSize: 30,
                                            fontWeight: 700,
                                        }}
                                    />
                                    <span
                                        className="w-11 h-11 rounded-xl flex items-center justify-center text-lg"
                                        style={{ color: s.color, background: `${s.color}1A`, boxShadow: `0 0 18px -6px ${s.color}` }}
                                    >
                                        {s.icon}
                                    </span>
                                </div>
                            </Card>
                        </Col>
                    ))}
                </Row>

                {/* ── 核心功能 ── */}
                <div className="mb-6 sm:mb-8">
                    <h2 className="text-xl sm:text-2xl font-bold text-gray-800 mb-4 sm:mb-6">核心功能</h2>
                    <Row gutter={[16, 16]}>
                        {features.map((feature, index) => (
                            <Col xs={24} sm={24} md={12} lg={8} key={index}>
                                <Card className="hp-feature-card h-full shadow-sm" styles={{ body: { padding: 24 } }} onClick={feature.action}>
                                    <div>
                                        <span
                                            className="w-12 h-12 rounded-xl flex items-center justify-center text-xl mb-4"
                                            style={{
                                                color: '#fff',
                                                background: `linear-gradient(135deg, ${feature.color}, ${feature.color}99)`,
                                                boxShadow: `0 8px 20px -8px ${feature.color}`,
                                            }}
                                        >
                                            {feature.icon}
                                        </span>
                                        <h3 className="text-lg font-semibold mb-2 text-gray-800">{feature.title}</h3>
                                        <p className="text-sm text-gray-500">{feature.description}</p>
                                        <div
                                            className="mt-4 inline-flex items-center gap-1 text-xs font-medium opacity-0 transition-opacity duration-200 hp-feature-arrow"
                                            style={{ color: feature.color }}
                                        >
                            查看详情 <ArrowRightOutlined className="text-[10px]" />
                                        </div>
                                    </div>
                                </Card>
                            </Col>
                        ))}
                    </Row>
                </div>

                {/* ── 快速开始指南：渐变连接线步骤条 ── */}
                <Card className="shadow-sm" styles={{ body: { padding: '24px 28px', borderRadius: 14 } }}>
                    <h2 className="text-xl sm:text-2xl font-bold text-gray-800 mb-6">快速开始指南</h2>
                    <div className="relative pl-2">
                        <div
                            className="absolute left-[19px] top-3 bottom-3 w-px"
                            style={{ background: `linear-gradient(180deg, ${C.blue}, ${C.purple}, ${C.cyan}, ${C.amber})` }}
                        />
                        <div className="space-y-6">
                            {steps.map((step, i) => (
                                <div key={i} className="flex items-start gap-4 relative">
                                    <span
                                        className="hp-mono flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-white text-sm font-bold z-10"
                                        style={{
                                            background: `linear-gradient(135deg, ${step.color}, ${step.color}AA)`,
                                            boxShadow: `0 0 0 5px #fff, 0 6px 16px -6px ${step.color}`,
                                        }}
                                    >
                                        {String(i + 1).padStart(2, '0')}
                                    </span>
                                    <div className="pt-1">
                                        <h4 className="font-semibold text-gray-800 text-sm sm:text-base">{step.title}</h4>
                                        <p className="text-xs sm:text-sm text-gray-500 mt-0.5">{step.desc}</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </Card>
            </div>
        </div>
    );
};

export default HomePage;
