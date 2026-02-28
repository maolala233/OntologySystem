import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Spin, message, Descriptions, Tag, Card, Drawer, Form, Input, Divider } from 'antd';
import { ArrowLeftOutlined, UserOutlined, InfoCircleOutlined, DeploymentUnitOutlined, UnorderedListOutlined, RightOutlined, LeftOutlined } from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import D3ForceGraph from '../components/OntologyGraph/D3ForceGraph';
import { Tree } from 'antd';

const { TreeNode } = Tree;

const AssetDetailPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const [project, setProject] = useState<ProjectData | null>(null);
    const [loading, setLoading] = useState(true);
    const [nodes, setNodes] = useState<any[]>([]);
    const [edges, setEdges] = useState<any[]>([]);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [selectedElement, setSelectedElement] = useState<any>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [isAllExpanded, setIsAllExpanded] = useState(false);
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    const [form] = Form.useForm();

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
            setExpandedNodeIds(new Set());
            setIsAllExpanded(false);
            message.success('已收起所有实例');
        } else {
            const newExpandedSet = new Set<string>();
            nodes.forEach((node: any) => {
                if (node.data?.type === 'owl:NamedIndividual') {
                    const parentClassEdge = edges.find((e: any) =>
                        e.source === node.id &&
                        (e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of')
                    );
                    if (parentClassEdge && parentClassEdge.target) {
                        newExpandedSet.add(parentClassEdge.target);
                    }
                }
            });
            setExpandedNodeIds(newExpandedSet);
            setIsAllExpanded(true);
            message.success('已展开所有实例相关的类');
        }
    };

    // 点击节点
    const onNodeClick = (node: any) => {
        setSelectedElement(node);
        setIsDrawerOpen(true);
        const propsObj = node.data?.properties || {};
        const propsArray = Object.entries(propsObj).map(([key, value]) => ({
            name: key,
            value: String(value)
        }));
        form.setFieldsValue({
            label: node.data?.label || '',
            type: node.data?.type || 'owl:Class',
            properties: propsArray
        });
    };

    // 点击边
    const onEdgeClick = (edge: any) => {
        setSelectedElement(edge);
        setIsDrawerOpen(true);
        form.setFieldsValue({
            label: edge.data?.label || edge.data?.relation || '',
            relation: edge.data?.relation || edge.data?.label || '',
        });
    };

    // 计算当前显示的元素
    const getDisplayElements = useCallback(() => {
        const isExpanded = expandedNodeIds.size > 0;
        const visibleNodeIds = new Set<string>();

        nodes.forEach(node => {
            if (node.data?.type === 'owl:Class') {
                visibleNodeIds.add(node.id);
            } else if (node.data?.type === 'owl:NamedIndividual') {
                if (isExpanded) {
                    visibleNodeIds.add(node.id);
                }
            } else {
                visibleNodeIds.add(node.id);
            }
        });

        const displayNodes = nodes.filter(n => visibleNodeIds.has(n.id));
        const displayEdges = edges.filter(e => {
            const sourceId = String(e.source);
            const targetId = String(e.target);
            const sourceVisible = visibleNodeIds.has(sourceId);
            const targetVisible = visibleNodeIds.has(targetId);
            return sourceVisible && targetVisible;
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds]);

    const { displayNodes, displayEdges } = getDisplayElements();

    // 构建树形数据
    const buildTreeData = useCallback(() => {
        const classNodes = nodes.filter(n => n.data?.type === 'owl:Class');
        const instanceNodes = nodes.filter(n => n.data?.type === 'owl:NamedIndividual');
        const classToInstances: Record<string, any[]> = {};
        instanceNodes.forEach(instance => {
            const parentClassEdge = edges.find(e =>
                e.source === instance.id &&
                (e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of')
            );
            if (parentClassEdge && parentClassEdge.target) {
                if (!classToInstances[parentClassEdge.target]) {
                    classToInstances[parentClassEdge.target] = [];
                }
                classToInstances[parentClassEdge.target].push(instance);
            }
        });
        return classNodes.map(classNode => ({
            title: classNode.data?.label || '未命名类',
            key: classNode.id,
            icon: <span className="inline-block w-3 h-3 rounded-full bg-[#4cc9f0] mr-2" />,
            children: classToInstances[classNode.id]?.map(instance => ({
                title: instance.data?.label || '未命名实例',
                key: instance.id,
                icon: <span className="inline-block w-3 h-3 rounded-full bg-[#f79767] mr-2" />,
                isLeaf: true,
            })) || [],
        }));
    }, [nodes, edges]);

    // 点击树节点
    const onTreeSelect = (selectedKeys: React.Key[]) => {
        if (selectedKeys.length === 0) return;
        const key = selectedKeys[0] as string;
        const node = nodes.find(n => n.id === key);
        if (node) {
            onNodeClick(node);
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
            <div className="flex-1 flex relative">
                {/* 左侧展开面板 */}
                <div
                    className={`absolute left-0 top-0 bottom-0 bg-white shadow-lg z-20 transition-all duration-300 ${
                        isLeftPanelExpanded ? 'w-[420px]' : 'w-0'
                    } overflow-hidden`}
                >
                    {isLeftPanelExpanded && (
                        <div className="p-4 h-full flex flex-col">
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="font-semibold text-gray-700 flex items-center">
                                    <UnorderedListOutlined className="mr-2" />
                                    类与实例列表
                                </h3>
                            </div>
                            <div className="flex-1 overflow-auto">
                                <Tree
                                    showIcon
                                    defaultExpandAll
                                    selectedKeys={selectedElement ? [selectedElement.id] : []}
                                    onSelect={onTreeSelect}
                                    treeData={buildTreeData()}
                                />
                            </div>
                            <div className="mt-4 pt-4 border-t border-gray-200">
                                <div className="flex items-center text-sm text-gray-500">
                                    <InfoCircleOutlined className="mr-2" />
                                    <span>共 {nodes.filter(n => n.data?.type === 'owl:Class').length} 个类，{nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length} 个实例</span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* 左侧展开/收起按钮 */}
                <button
                    className={`absolute left-0 top-1/2 -translate-y-1/2 z-30 bg-white shadow-md rounded-r-lg p-2 hover:bg-gray-50 transition-all duration-300 ${
                        isLeftPanelExpanded ? 'left-[420px]' : 'left-0'
                    }`}
                    onClick={() => setIsLeftPanelExpanded(!isLeftPanelExpanded)}
                    title={isLeftPanelExpanded ? '收起列表' : '展开列表'}
                >
                    {isLeftPanelExpanded ? <LeftOutlined /> : <RightOutlined />}
                </button>

                {/* 左侧信息面板 - 固定宽度 */}
                <div className={`w-[420px] bg-white border-r border-gray-200 p-6 overflow-y-auto transition-all duration-300 ${isLeftPanelExpanded ? 'ml-[420px]' : ''}`}>
                    <div className="space-y-6">
                        <div>
                            <h1 className="text-2xl font-bold text-gray-800 mb-2">{project.name}</h1>
                            <Tag color="green">已发布</Tag>
                        </div>
                        <Card title="项目描述" size="small">
                            <p className="text-gray-600 text-sm">{project.description || '暂无描述'}</p>
                        </Card>
                        <Card title="统计信息" size="small">
                            <Descriptions column={1} size="small">
                                <Descriptions.Item label="节点数量">
                                    <span className="font-semibold text-blue-600">{project.graph_data?.nodes?.length || 0}</span>
                                </Descriptions.Item>
                                <Descriptions.Item label="关系数量">
                                    <span className="font-semibold text-purple-600">{project.graph_data?.edges?.length || 0}</span>
                                </Descriptions.Item>
                            </Descriptions>
                        </Card>
                        <Card title="创建者" size="small">
                            <div className="flex items-center space-x-2">
                                <UserOutlined className="text-gray-400" />
                                <span className="text-gray-700">{project.owner?.username || '未知'}</span>
                            </div>
                        </Card>
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
                        <div className="space-y-2">
                            <Button block onClick={toggleAllInstances} icon={<DeploymentUnitOutlined />}>
                                {isAllExpanded ? '收起所有实例' : '展开所有实例'}
                            </Button>
                        </div>
                    </div>
                </div>

                {/* 返回按钮 */}
                <div 
                    className={`absolute top-4 z-10 transition-all duration-300 ${
                        isLeftPanelExpanded ? 'left-[660px]' : 'left-[240px]'
                    }`}
                >
                    <Button
                        icon={<ArrowLeftOutlined />}
                        onClick={() => navigate('/asset-center')}
                        className="shadow-sm bg-white"
                    >
                        返回资产中心
                    </Button>
                </div>

                {/* 右侧图谱展示区 */}
                <div className="flex-1 relative">
                    {loading && (
                        <div className="absolute inset-0 bg-white bg-opacity-75 flex items-center justify-center z-50">
                            <Spin size="large" tip="正在加载..." />
                        </div>
                    )}
                    <D3ForceGraph
                        nodes={displayNodes}
                        edges={displayEdges}
                        onNodeClick={onNodeClick}
                        onEdgeClick={onEdgeClick}
                        onNodesChange={(updatedNodes) => {
                            if (Array.isArray(updatedNodes)) {
                                setNodes((prevNodes) =>
                                    prevNodes.map(node => {
                                        const updated = updatedNodes.find(n => n.id === node.id);
                                        return updated ? { ...updated } : node;
                                    })
                                );
                            }
                        }}
                        width={window.innerWidth - (isLeftPanelExpanded ? 880 : 460)}
                        height={window.innerHeight - 60}
                    />
                </div>
            </div>

            {/* 抽屉 - 元素详情 */}
            <Drawer
                title={selectedElement ? (
                    'position' in selectedElement
                        ? `编辑节点属性 - ${selectedElement.data?.label || '未命名'}`
                        : `编辑关系属性 - ${selectedElement.data?.label || '未命名'}`
                ) : "元素详情"}
                placement="right"
                onClose={() => {
                    setIsDrawerOpen(false);
                    setSelectedElement(null);
                    form.resetFields();
                }}
                open={isDrawerOpen}
                width={450}
                destroyOnClose={true}
            >
                {selectedElement && (
                    <Form
                        form={form}
                        layout="vertical"
                        initialValues={{
                            label: selectedElement.data?.label || '',
                            type: selectedElement.data?.type || 'owl:Class',
                            relation: selectedElement.data?.relation || selectedElement.data?.label || ''
                        }}
                    >
                        {'position' in selectedElement ? (
                            <>
                                <div className="flex items-center space-x-2 mb-4 font-medium text-gray-700">
                                    <InfoCircleOutlined />
                                    <span>基本信息</span>
                                </div>
                                <Form.Item name="label" label="节点名称">
                                    <Input disabled />
                                </Form.Item>
                                <Form.Item name="type" label="节点类型">
                                    <Input disabled />
                                </Form.Item>
                                {selectedElement.properties && Object.keys(selectedElement.properties).length > 0 && (
                                    <>
                                        <Divider />
                                        <div className="flex items-center space-x-2 mb-4 font-medium text-gray-700">
                                            <InfoCircleOutlined />
                                            <span>属性</span>
                                        </div>
                                        <div className="space-y-1">
                                            {Object.entries(selectedElement.properties).map(([key, value]) => (
                                                <p key={key} className="text-sm">
                                                    <strong>{key}:</strong> {String(value)}
                                                </p>
                                            ))}
                                        </div>
                                    </>
                                )}
                            </>
                        ) : (
                            <>
                                <div className="flex items-center space-x-2 mb-4 font-medium text-gray-700">
                                    <InfoCircleOutlined />
                                    <span>关系属性</span>
                                </div>
                                <Form.Item name="label" label="关系标签">
                                    <Input disabled />
                                </Form.Item>
                                <Form.Item name="relation" label="关系类型">
                                    <Input disabled />
                                </Form.Item>
                            </>
                        )}
                    </Form>
                )}
            </Drawer>
        </div>
    );
};

export default AssetDetailPage;