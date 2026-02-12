import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useNodesState, useEdgesState } from 'reactflow';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, {
    Background,
    Controls,
    MiniMap,
    ReactFlowProvider,
    Panel,
    Node as RFNode,
    Edge as RFEdge,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Button, Spin, message, Descriptions, Tag, Card } from 'antd';
import { ArrowLeftOutlined, UserOutlined, CalendarOutlined, InfoCircleOutlined, FileTextOutlined, DeploymentUnitOutlined, EyeOutlined } from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import Neo4jNode from '../components/OntologyGraph/Neo4jNode';
import { getLayoutedElements } from '../utils/layoutUtils';
import { MarkerType as RFMarkerType, BackgroundVariant } from 'reactflow';
import { Drawer, Form, Input, Divider } from 'antd';

const AssetDetailPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const [project, setProject] = useState<ProjectData | null>(null);
    const [loading, setLoading] = useState(true);

    // 扩展状态管理 - 使用 any 类型避免复杂类型问题
    const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [selectedElement, setSelectedElement] = useState<any>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);

    // 新增：显示所有实例开关状态（只读模式下不需要开关，直接提供展开/收起功能）
    const [showAllInstances, setShowAllInstances] = useState(false);
    const [isAllExpanded, setIsAllExpanded] = useState(false); // 新增：是否所有实例已展开的状态

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const data = await projectsApi.getProject(Number(projectId));
            setProject(data);
            if (data.graph_data?.nodes) setNodes(data.graph_data.nodes);
            if (data.graph_data?.edges) setEdges(data.graph_data.edges);
        } catch (error: any) {
            message.error('加载本体详情失败');
            navigate('/asset-center');
        } finally {
            setLoading(false);
        }
    };

    // 一键展开/收起所有实例
    const toggleAllInstances = () => {
        if (isAllExpanded) {
            // 收起所有实例：清空 expandedNodeIds
            setExpandedNodeIds(new Set());
            setIsAllExpanded(false);
            message.success('已收起所有实例');
        } else {
            // 展开所有实例
            const newExpandedSet = new Set<string>(expandedNodeIds);
            
            // 找到所有类节点
            const classNodes = nodes.filter((node: any) => node.data?.type === 'owl:Class');
            
            // 找到所有实例节点及其关联的类
            nodes.forEach((node: any) => {
                if (node.data?.type === 'owl:NamedIndividual') {
                    // 查找该实例关联的类
                    const parentClassEdge = edges.find((e: any) =>
                        e.source === node.id &&
                        (e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of')
                    );
                    
                    if (parentClassEdge && parentClassEdge.target) {
                        // 将关联的类添加到展开集合中
                        newExpandedSet.add(parentClassEdge.target);
                    }
                }
            });
            
            setExpandedNodeIds(newExpandedSet);
            setIsAllExpanded(true);
            message.success('已展开所有实例相关的类');
        }
    };

    // 自动布局
    const handleAutoLayout = useCallback((currentNodes: any[], currentEdges: any[]) => {
        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(currentNodes, currentEdges);
        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
    }, []);

    // 双击节点：展开/折叠
    const onNodeDoubleClick = useCallback((_: React.MouseEvent, node: RFNode) => {
        if ((node.data as any)?.type === 'owl:Class') {
            setExpandedNodeIds((prev) => {
                const newSet = new Set(prev);
                if (newSet.has(node.id)) {
                    newSet.delete(node.id);
                } else {
                    newSet.add(node.id);
                }
                return newSet;
            });
            // 展开后自动适配视图
            setTimeout(() => {
                message.info('双击已展开/折叠类节点');
            }, 100);
        }
    }, []);

    // 单击节点：查看详情
    const onNodeClick = useCallback((_: React.MouseEvent, node: RFNode) => {
        setSelectedElement(node.data);
        setIsDrawerOpen(true);
    }, []);

    // 计算当前显示的元素
    const { displayNodes, displayEdges } = useMemo(() => {
        const visibleNodeIds = new Set<string>();

        nodes.forEach((node: any) => {
            if (node.data?.type === 'owl:Class') {
                visibleNodeIds.add(node.id);
            } else if (node.data?.type === 'owl:NamedIndividual') {
                // 检查该实例是否有关联的已展开的类
                const parentClassEdge = edges.find((e: any) =>
                    e.source === node.id &&
                    (e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of') &&
                    expandedNodeIds.has(e.target)
                );
                
                // 如果开启"显示所有实例"或有展开的关联类，则显示实例
                if (showAllInstances || parentClassEdge) {
                    visibleNodeIds.add(node.id);
                }
            } else {
                // 其他类型默认显示
                visibleNodeIds.add(node.id);
            }
        });

        const displayNodes = nodes.filter((n: any) => visibleNodeIds.has(n.id));
        const displayEdges = edges.filter((e: any) => {
            const isVisible = visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target);
            const isTypeRelation = e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of';
            return isVisible && !isTypeRelation;
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds, showAllInstances]);

    const nodeTypesMap = useMemo(() => ({ custom: Neo4jNode }), []);
    const defaultEdgeOptions = useMemo(() => ({
        type: 'smoothstep',
        markerEnd: { type: RFMarkerType.ArrowClosed, color: '#b1b1b7' },
        style: { stroke: '#b1b1b7', strokeWidth: 1.5 },
    }), []);

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
                                    project.graph_data.nodes.forEach((node: any) => {
                                        const type = node.data?.type || 'Unknown';
                                        typeCount[type] = (typeCount[type] || 0) + 1;
                                    });
                                    return (
                                        <div className="space-y-2">
                                            {Object.entries(typeCount).map(([type, count]) => (
                                                <div key={type} className="flex justify-between items-center">
                                                    <span className="text-gray-600">{type}</span>
                                                    <span className="font-semibold">{count}</span>
                                                </div>
                                            ))}
                                        </div>
                                    );
                                })()}
                            </Card>
                        )}

                        {/* 控制按钮 */}
                        <div className="space-y-2">
                            <Button 
                                block 
                                onClick={toggleAllInstances}
                                icon={<DeploymentUnitOutlined />}
                            >
                                {isAllExpanded ? '收起所有实例' : '展开所有实例'}
                            </Button>
                        </div>
                    </div>
                </div>

                {/* 右侧图谱展示区 */}
                <div className="flex-1 relative">
                    <ReactFlowProvider>
                        <ReactFlow
                            nodes={displayNodes}
                            edges={displayEdges}
                            onNodesChange={onNodesChange}
                            onEdgesChange={onEdgesChange}
                            onNodeClick={onNodeClick}
                            onNodeDoubleClick={onNodeDoubleClick}
                            nodeTypes={nodeTypesMap}
                            defaultEdgeOptions={defaultEdgeOptions}
                            fitView
                            attributionPosition="bottom-left"
                        >
                            <Controls />
                            <MiniMap />
                            <Background variant={BackgroundVariant.Lines} gap={20} size={1} />
                        </ReactFlow>
                    </ReactFlowProvider>

                    {/* 抽屉 - 元素详情 */}
                    <Drawer
                        title="元素详情"
                        placement="right"
                        onClose={() => setIsDrawerOpen(false)}
                        open={isDrawerOpen}
                        width={400}
                    >
                        {selectedElement && (
                            <div className="space-y-4">
                                <div>
                                    <h3 className="font-semibold mb-2">基本信息</h3>
                                    <p><strong>标签:</strong> {selectedElement.label || 'N/A'}</p>
                                    <p><strong>类型:</strong> {selectedElement.type || 'N/A'}</p>
                                </div>
                                
                                {selectedElement.properties && Object.keys(selectedElement.properties).length > 0 && (
                                    <div>
                                        <h3 className="font-semibold mb-2">属性</h3>
                                        <div className="space-y-1">
                                            {Object.entries(selectedElement.properties).map(([key, value]) => (
                                                <p key={key} className="text-sm">
                                                    <strong>{key}:</strong> {String(value)}
                                                </p>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </Drawer>
                </div>
            </div>
        </div>
    );
};

export default AssetDetailPage;