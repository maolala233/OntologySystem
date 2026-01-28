import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, {
    Background,
    Controls,
    MiniMap,
    ReactFlowProvider,
    Panel,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Button, Spin, message, Descriptions, Tag, Card } from 'antd';
import { ArrowLeftOutlined, UserOutlined, CalendarOutlined } from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { projectsAPI } from '../api/projects';
import { ProjectData } from '../types/ontology';

const AssetDetailPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const [project, setProject] = useState<ProjectData | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const data = await projectsAPI.getProject(Number(projectId));
            setProject(data);
        } catch (error: any) {
            message.error('加载本体详情失败');
            navigate('/asset-center');
        } finally {
            setLoading(false);
        }
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '资产中心', path: '/asset-center' },
        { title: project?.name || '本体详情' },
    ];

    if (loading) {
        return (
            <div className="h-screen flex items-center justify-center">
                <Spin size="large" />
            </div>
        );
    }

    if (!project) {
        return null;
    }

    return (
        <div className="h-screen flex flex-col bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="flex-1 flex">
                {/* 左侧信息面板 */}
                <div className="w-80 bg-white border-r border-gray-200 p-6 overflow-y-auto">
                    <div className="space-y-6">
                        {/* 项目标题 */}
                        <div>
                            <h1 className="text-2xl font-bold text-gray-800 mb-2">
                                {project.name}
                            </h1>
                            <Tag color="green">已发布</Tag>
                        </div>

                        {/* 项目描述 */}
                        <Card title="项目描述" size="small">
                            <p className="text-gray-600 text-sm">
                                {project.description || '暂无描述'}
                            </p>
                        </Card>

                        {/* 统计信息 */}
                        <Card title="统计信息" size="small">
                            <Descriptions column={1} size="small">
                                <Descriptions.Item label="节点数量">
                                    <span className="font-semibold text-blue-600">
                                        {project.graph_data?.nodes?.length || 0}
                                    </span>
                                </Descriptions.Item>
                                <Descriptions.Item label="关系数量">
                                    <span className="font-semibold text-purple-600">
                                        {project.graph_data?.edges?.length || 0}
                                    </span>
                                </Descriptions.Item>
                            </Descriptions>
                        </Card>

                        {/* 创建者信息 */}
                        <Card title="创建者" size="small">
                            <div className="flex items-center space-x-2">
                                <UserOutlined className="text-gray-400" />
                                <span className="text-gray-700">
                                    {project.owner?.username || '未知'}
                                </span>
                            </div>
                        </Card>

                        {/* 节点类型统计 */}
                        {project.graph_data?.nodes && (
                            <Card title="节点类型分布" size="small">
                                {(() => {
                                    const typeCount: Record<string, number> = {};
                                    project.graph_data.nodes.forEach((node) => {
                                        const type = node.data?.type || 'Unknown';
                                        typeCount[type] = (typeCount[type] || 0) + 1;
                                    });
                                    return (
                                        <div className="space-y-2">
                                            {Object.entries(typeCount).map(([type, count]) => (
                                                <div
                                                    key={type}
                                                    className="flex items-center justify-between text-sm"
                                                >
                                                    <span className="text-gray-600">{type}</span>
                                                    <Tag color="blue">{count}</Tag>
                                                </div>
                                            ))}
                                        </div>
                                    );
                                })()}
                            </Card>
                        )}
                    </div>
                </div>

                {/* 右侧图谱展示 */}
                <div className="flex-1 relative">
                    <ReactFlowProvider>
                        <ReactFlow
                            nodes={project.graph_data?.nodes || []}
                            edges={project.graph_data?.edges || []}
                            fitView
                            nodesDraggable={false}
                            nodesConnectable={false}
                            elementsSelectable={true}
                            className="bg-gray-50"
                        >
                            <Background color="#d1d5db" gap={16} />
                            <Controls />
                            <MiniMap
                                nodeColor={(node) => {
                                    switch (node.data?.type) {
                                        case 'Class':
                                            return '#3b82f6';
                                        case 'Property':
                                            return '#10b981';
                                        case 'Concept':
                                            return '#f59e0b';
                                        default:
                                            return '#6366f1';
                                    }
                                }}
                            />

                            <Panel position="top-left">
                                <Button
                                    icon={<ArrowLeftOutlined />}
                                    onClick={() => navigate('/asset-center')}
                                >
                                    返回资产中心
                                </Button>
                            </Panel>

                            <Panel position="bottom-left">
                                <div className="bg-white px-4 py-2 rounded-lg shadow-md text-sm">
                                    <span className="text-gray-600">
                                        只读模式 - 此本体已发布到图数据库
                                    </span>
                                </div>
                            </Panel>
                        </ReactFlow>
                    </ReactFlowProvider>
                </div>
            </div>
        </div>
    );
};

export default AssetDetailPage;
