import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Button } from 'antd';
import {
    RocketOutlined,
    FileTextOutlined,
    TeamOutlined,
    DatabaseOutlined,
    ArrowRightOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';

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
                // 方案1：优先尝试使用 projectsApi（已内置 token 拦截器）
                const [myProjects, publicProjects] = await Promise.all([
                    projectsApi.getMyProjects(),
                    projectsApi.getPublicProjects()
                ]);

                let totalNodes = 0;
                // 只统计已发布的项目节点数（去重：按项目ID去重，避免个人项目在my和public中重复计算）
                const allPublishedProjects = [...myProjects, ...publicProjects]
                    .filter(project => project.is_published);
                        
                // 按项目ID去重，确保每个项目只计算一次
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
                // 明确提示：可能是未登录或网络问题
                if (error instanceof Error && error.message.includes('401')) {
                    console.warn('请先登录以获取个人项目数据');
                }
                // 保持默认值 0，避免页面异常
            } finally {
                setLoading(false);
            }
        };

        fetchStats();
    }, []);

    const features = [
        {
            icon: <FileTextOutlined className="text-4xl text-blue-500" />,
            title: '智能提取',
            description: '上传文档，AI 自动提取本体结构',
            action: () => navigate('/my-projects'),
        },
        {
            icon: <DatabaseOutlined className="text-4xl text-purple-500" />,
            title: '可视化编辑',
            description: '拖拽式图谱编辑，直观便捷',
            action: () => navigate('/ontology-builder'),
        },
        {
            icon: <TeamOutlined className="text-4xl text-green-500" />,
            title: '协作共享',
            description: '发布本体到公共资产中心',
            action: () => navigate('/asset-center'),
        },
    ];

    const breadcrumbs = [{ title: '首页' }];

    return (
        <div className="min-h-screen bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="w-full max-w-[1920px] mx-auto p-4 sm:p-6 lg:p-8">
                {/* Hero Section */}
                <div className="bg-gradient-to-r from-blue-600 to-purple-600 rounded-2xl p-6 sm:p-8 lg:p-12 mb-6 sm:mb-8 text-white shadow-xl">
                    <div className="max-w-7xl">
                        <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold mb-4 leading-tight">
                            企业级语义知识及本体治理平台
                        </h1>
                        <p className="text-base sm:text-lg lg:text-xl mb-6 sm:mb-8 text-blue-100">
                            基于 AI 的智能本体构建工具，让知识图谱建设更简单、更高效
                        </p>
                        <div className="flex flex-wrap gap-3 sm:gap-4">
                            <Button
                                type="primary"
                                size="large"
                                icon={<RocketOutlined />}
                                onClick={() => navigate('/my-projects')}
                                className="bg-white text-blue-600 hover:bg-gray-100 border-0 h-10 sm:h-12 px-4 sm:px-8"
                            >
                                开始使用
                            </Button>
                            <Button
                                size="large"
                                icon={<ArrowRightOutlined />}
                                onClick={() => navigate('/asset-center')}
                                className="bg-transparent text-white border-2 border-white hover:bg-white hover:text-blue-600 h-10 sm:h-12 px-4 sm:px-8"
                            >
                                浏览资产中心
                            </Button>
                        </div>
                    </div>
                </div>

                {/* 统计卡片 - 响应式布局 */}
                <Row gutter={[16, 16]} className="mb-6 sm:mb-8">
                    <Col xs={24} sm={12} lg={12} xl={6}>
                        <Card className="shadow-md hover:shadow-lg transition-shadow">
                            <Statistic
                                title="我的项目"
                                value={stats.myProjects}
                                prefix={<FileTextOutlined />}
                                valueStyle={{ color: '#3b82f6' }}
                            />
                        </Card>
                    </Col>
                    <Col xs={24} sm={12} lg={12} xl={6}>
                        <Card className="shadow-md hover:shadow-lg transition-shadow">
                            <Statistic
                                title="已发布本体"
                                value={stats.publishedOntologies}
                                prefix={<DatabaseOutlined />}
                                valueStyle={{ color: '#10b981' }}
                            />
                        </Card>
                    </Col>
                    <Col xs={24} sm={12} lg={12} xl={6}>
                        <Card className="shadow-md hover:shadow-lg transition-shadow">
                            <Statistic
                                title="公共资产"
                                value={stats.publicAssets}
                                prefix={<TeamOutlined />}
                                valueStyle={{ color: '#f59e0b' }}
                            />
                        </Card>
                    </Col>
                    <Col xs={24} sm={12} lg={12} xl={6}>
                        <Card className="shadow-md hover:shadow-lg transition-shadow">
                            <Statistic
                                title="总节点数"
                                value={stats.totalNodes}
                                prefix={<DatabaseOutlined />}
                                valueStyle={{ color: '#8b5cf6' }}
                            />
                        </Card>
                    </Col>
                </Row>

                {/* 功能特性 - 响应式布局 */}
                <div className="mb-6 sm:mb-8">
                    <h2 className="text-xl sm:text-2xl font-bold text-gray-800 mb-4 sm:mb-6">核心功能</h2>
                    <Row gutter={[16, 16]}>
                        {features.map((feature, index) => (
                            <Col xs={24} sm={24} md={12} lg={8} key={index}>
                                <Card
                                    hoverable
                                    className="h-full shadow-md hover:shadow-xl transition-all duration-300 transform hover:-translate-y-1"
                                    onClick={feature.action}
                                >
                                    <div className="text-center">
                                        <div className="mb-4">{feature.icon}</div>
                                        <h3 className="text-lg sm:text-xl font-semibold mb-2 text-gray-800">
                                            {feature.title}
                                        </h3>
                                        <p className="text-sm sm:text-base text-gray-600">
                                            {feature.description}
                                        </p>
                                    </div>
                                </Card>
                            </Col>
                        ))}
                    </Row>
                </div>

                {/* 快速开始指南 - 响应式布局 */}
                <Card className="shadow-md">
                    <h2 className="text-xl sm:text-2xl font-bold text-gray-800 mb-4 px-2 sm:px-0">
                        快速开始指南
                    </h2>
                    <div className="space-y-3 sm:space-y-4 px-2 sm:px-0">
                        <div className="flex items-start space-x-3 sm:space-x-4">
                            <div className="flex-shrink-0 w-6 h-6 sm:w-8 sm:h-8 bg-blue-500 text-white rounded-full flex items-center justify-center font-bold text-sm sm:text-base">
                                1
                            </div>
                            <div>
                                <h4 className="font-semibold text-gray-800 text-sm sm:text-base">创建项目</h4>
                                <p className="text-xs sm:text-sm text-gray-600">
                                    在"我的项目"中创建一个新的本体建模项目
                                </p>
                            </div>
                        </div>
                        <div className="flex items-start space-x-3 sm:space-x-4">
                            <div className="flex-shrink-0 w-6 h-6 sm:w-8 sm:h-8 bg-purple-500 text-white rounded-full flex items-center justify-center font-bold text-sm sm:text-base">
                                2
                            </div>
                            <div>
                                <h4 className="font-semibold text-gray-800 text-sm sm:text-base">上传文档</h4>
                                <p className="text-xs sm:text-sm text-gray-600">
                                    上传相关文档，AI 将自动提取本体结构
                                </p>
                            </div>
                        </div>
                        <div className="flex items-start space-x-3 sm:space-x-4">
                            <div className="flex-shrink-0 w-6 h-6 sm:w-8 sm:h-8 bg-green-500 text-white rounded-full flex items-center justify-center font-bold text-sm sm:text-base">
                                3
                            </div>
                            <div>
                                <h4 className="font-semibold text-gray-800 text-sm sm:text-base">可视化调整</h4>
                                <p className="text-xs sm:text-sm text-gray-600">
                                    在画布上拖拽、编辑节点和关系，完善本体模型
                                </p>
                            </div>
                        </div>
                        <div className="flex items-start space-x-3 sm:space-x-4">
                            <div className="flex-shrink-0 w-6 h-6 sm:w-8 sm:h-8 bg-orange-500 text-white rounded-full flex items-center justify-center font-bold text-sm sm:text-base">
                                4
                            </div>
                            <div>
                                <h4 className="font-semibold text-gray-800 text-sm sm:text-base">发布共享</h4>
                                <p className="text-xs sm:text-sm text-gray-600">
                                    发布到图数据库，并在资产中心公开展示
                                </p>
                            </div>
                        </div>
                    </div>
                </Card>
            </div>
        </div>
    );
};

export default HomePage;
