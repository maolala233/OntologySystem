import React, { useState, useCallback, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, {
    addEdge,
    Background,
    Controls,
    MiniMap,
    Connection,
    Edge,
    useNodesState,
    useEdgesState,
    Panel,
    ReactFlowProvider,
    Node,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
    Drawer,
    Form,
    Input,
    Button,
    message,
    Space,
    Upload,
    Modal,
    Spin,
    Tooltip,
    Select,
    Tag,
    Divider,
    Switch,
} from 'antd';
import {
    SaveOutlined,
    CloudUploadOutlined,
    PlusOutlined,
    DeleteOutlined,
    CloudServerOutlined,
    ArrowLeftOutlined,
    FileTextOutlined,
    SettingOutlined,
    InfoCircleOutlined,
    DownloadOutlined,
    DeploymentUnitOutlined,
    EyeOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { OntologyNode, OntologyEdge } from '../types/ontology';
import { projectsApi } from '../api/projects';
import Neo4jNode from '../components/OntologyGraph/Neo4jNode';
import { getLayoutedElements } from '../utils/layoutUtils';
import { MarkerType as RFMarkerType, BackgroundVariant } from 'reactflow';
const nodeTypesMap = { custom: Neo4jNode };

const OntologyBuilderPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();

    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [selectedElement, setSelectedElement] = useState<OntologyNode | OntologyEdge | null>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [isRuleModalOpen, setIsRuleModalOpen] = useState(false);
    const [pendingFile, setPendingFile] = useState<File | null>(null);
    const [loading, setLoading] = useState(false);
    const [projectName, setProjectName] = useState('');
    const [isPublished, setIsPublished] = useState(false);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [showAllInstances, setShowAllInstances] = useState(false); // 新增：显示所有实例开关
    const [lastSavedNodes, setLastSavedNodes] = useState<any[]>([]);
    const [lastSavedEdges, setLastSavedEdges] = useState<any[]>([]);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false); // 追踪是否有未保存的更改
    const [isAddRelationModalOpen, setIsAddRelationModalOpen] = useState(false); // 新增关系对话框
    const [relationForm] = Form.useForm(); // 关系表单
    const [form] = Form.useForm();
    const [ruleForm] = Form.useForm();

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    // 监听节点和边的变化，更新是否有未保存更改的状态
    useEffect(() => {
        if (projectId) {
            const hasChanged = JSON.stringify(nodes) !== JSON.stringify(lastSavedNodes) || 
                             JSON.stringify(edges) !== JSON.stringify(lastSavedEdges);
            setHasUnsavedChanges(hasChanged);
        }
    }, [nodes, edges, lastSavedNodes, lastSavedEdges, projectId]);

    // 页面卸载前的确认
    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (hasUnsavedChanges) {
                e.preventDefault();
                e.returnValue = '您有未保存的更改，确定要离开吗？';
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => {
            window.removeEventListener('beforeunload', handleBeforeUnload);
        };
    }, [hasUnsavedChanges]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const project = await projectsApi.getProject(Number(projectId));
            setProjectName(project.name);
            setIsPublished(project.is_published);

            if (project.graph_data?.nodes) {
                setNodes(project.graph_data.nodes);
                setLastSavedNodes(project.graph_data.nodes); // 记录初始状态
            }
            if (project.graph_data?.edges) {
                setEdges(project.graph_data.edges);
                setLastSavedEdges(project.graph_data.edges); // 记录初始状态
            }
        } catch (error: any) {
            message.error('加载项目失败');
            navigate('/my-projects');
        } finally {
            setLoading(false);
        }
    };

    // 处理连线
    const onConnect = useCallback(
        (params: Connection) => {
            const newEdge: OntologyEdge = {
                ...params,
                id: `edge_${Date.now()}`,
                type: 'smoothstep',
                animated: true,
                data: { label: '关联', relation: 'related_to' },
            } as OntologyEdge;
            setEdges((eds) => addEdge(newEdge, eds));
        },
        [setEdges]
    );

    // 双击节点：展开/折叠
    const onNodeDoubleClick = (_: React.MouseEvent, node: Node) => {
        if (node.data?.type === 'owl:Class') {
            setExpandedNodeIds((prev) => {
                const newSet = new Set(prev);
                if (newSet.has(node.id)) {
                    newSet.delete(node.id);
                } else {
                    newSet.add(node.id);
                }
                return newSet;
            });
            // 展开后自动重新布局以避免重叠
            setTimeout(() => handleAutoLayout(), 100);
        }
    };

    // 点击节点或连线
    const onElementClick = (_: React.MouseEvent, element: Node | Edge) => {
        setSelectedElement(element as OntologyNode | OntologyEdge);
        setIsDrawerOpen(true);

        if ('position' in element) {
            // 节点：将 properties 对象转换为 [{key, value}] 数组供 Form.List 使用
            const propsObj = element.data?.properties || {};
            const propsArray = Object.entries(propsObj).map(([key, value]) => ({
                name: key,
                value: String(value)
            }));

            form.setFieldsValue({
                label: element.data?.label || '',
                type: element.data?.type || 'owl:Class',
                properties: propsArray
            });
        } else {
            // 边
            form.setFieldsValue({
                label: element.data?.label || '',
                relation: element.data?.relation || '',
            });
        }
    };

    // 保存属性修改
    const handleSaveProperties = (values: any) => {
        if (!selectedElement) return;

        const isNode = 'position' in selectedElement;
        const { label, type, relation, properties } = values;

        if (isNode) {
            // 将 properties 数组转换回对象
            const propsObj: Record<string, any> = {};
            if (Array.isArray(properties)) {
                properties.forEach((p: any) => {
                    if (p && p.name) {
                        propsObj[p.name] = p.value;
                    }
                });
            }

            setNodes((nds) =>
                nds.map((node) => {
                    if (node.id === selectedElement.id) {
                        return {
                            ...node,
                            data: {
                                ...node.data,
                                label: label,
                                type: type,
                                properties: propsObj,
                            },
                        };
                    }
                    return node;
                })
            );
        } else {
            setEdges((eds) =>
                eds.map((edge) => {
                    if (edge.id === selectedElement.id) {
                        return {
                            ...edge,
                            data: {
                                ...edge.data,
                                label: label,
                                relation: relation,
                            },
                        };
                    }
                    return edge;
                })
            );
        }
        setIsDrawerOpen(false);
        message.success('属性已更新');
    };

    // 新增节点 - 重构为可选择类型的函数
    const addNewNodeOfType = (nodeType: string) => {
        const newNode: OntologyNode = {
            id: `node_${Date.now()}`,
            type: 'custom',
            position: { x: Math.random() * 400 + 100, y: Math.random() * 400 + 100 },
            data: { 
                label: nodeType === 'owl:Class' ? '新类' : '新实例', 
                type: nodeType, 
                properties: {} 
            },
        };
        setNodes((nds) => nds.concat(newNode));
        message.success(`已添加新${nodeType === 'owl:Class' ? '类' : '实例'}`);
    };

    // 新增类
    const addNewClass = () => {
        addNewNodeOfType('owl:Class');
    };

    // 新增实例
    const addNewInstance = () => {
        const newNode: OntologyNode = {
            id: `node_${Date.now()}`,
            type: 'custom',
            position: { x: Math.random() * 400 + 100, y: Math.random() * 400 + 100 },
            data: { 
                label: '新实例', 
                type: 'owl:NamedIndividual', 
                properties: {} 
            },
        };
        
        // 尝试找到最近的类节点作为父类
        const classNodes = nodes.filter(node => node.data?.type === 'owl:Class');
        let newEdges: OntologyEdge[] = [];
        
        if (classNodes.length > 0) {
            // 创建 rdf:type 关系到最近的类
            const targetClass = classNodes[classNodes.length - 1];
            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${newNode.id}_${targetClass.id}`,
                source: newNode.id,
                target: targetClass.id,
                type: 'smoothstep',
                animated: true,
                data: { label: 'rdf:type', relation: 'instance_of' },
            } as OntologyEdge;
            newEdges.push(newEdge);
            
            // 自动展开该类
            setExpandedNodeIds(prev => {
                const newSet = new Set(prev);
                newSet.add(targetClass.id);
                return newSet;
            });
        }
        
        // 添加新节点和边
        setNodes((nds) => nds.concat(newNode));
        if (newEdges.length > 0) {
            setEdges((eds) => [...eds, ...newEdges]);
        }
        
        message.success('已添加新实例');
    };

    // 自动布局
    const handleAutoLayout = () => {
        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(nodes, edges);
        setNodes([...layoutedNodes]);
        setEdges([...layoutedEdges]);
        message.success('已完成自动布局');
    };

    // 删除选中元素
    const deleteSelectedElement = () => {
        if (!selectedElement) {
            message.warning('请先选择要删除的元素');
            return;
        }

        Modal.confirm({
            title: '确认删除',
            content: '确定要删除选中的元素吗？',
            okText: '确定',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: () => {
                const isNode = 'position' in selectedElement;
                if (isNode) {
                    setNodes((nds) => nds.filter((node) => node.id !== selectedElement.id));
                } else {
                    setEdges((eds) => eds.filter((edge) => edge.id !== selectedElement.id));
                }
                setIsDrawerOpen(false);
                setSelectedElement(null);
                message.success('删除成功');
            },
        });
    };

    // 保存草稿 - 修改为同时更新TTL文件
    const handleSaveDraft = async () => {
        if (!projectId) return;

        setLoading(true);
        try {
            // 先更新项目图数据（保持原有逻辑）
            await projectsApi.updateProject(Number(projectId), {
                graph_data: { nodes, edges },
            });

            // 然后触发TTL文件重新生成（关键修复）
            const updateResponse = await projectsApi.updateOntology(Number(projectId), { nodes, edges });

            // 更新最后保存状态
            setLastSavedNodes([...nodes]);
            setLastSavedEdges([...edges]);
            setHasUnsavedChanges(false);

            message.success('草稿已保存，TTL文件已同步更新');
        } catch (error) {
            message.error('保存失败，请重试');
        } finally {
            setLoading(false);
        }
    };

    // 发布项目
    const handlePublish = async () => {
        if (!projectId) return;

        Modal.confirm({
            title: '确认发布',
            content: '发布后，本体将同步到 Neo4j 图数据库，并在资产中心公开展示。确定要发布吗？',
            okText: '确定发布',
            cancelText: '取消',
            onOk: async () => {
                setLoading(true);
                try {
                    // 先保存当前图数据并更新TTL文件（关键修复）
                    await projectsApi.updateProject(Number(projectId), {
                        graph_data: { nodes, edges },
                    });

                    // 然后触发TTL文件重新生成（确保Neo4j同步的是最新数据）
                    const updateResponse = await projectsApi.updateOntology(Number(projectId), { nodes, edges });

                    // 最后发布到Neo4j
                    await projectsApi.publishProject(Number(projectId));
                    message.success('发布成功！本体已同步到图数据库');
                    
                    // 重新获取项目信息，更新isPublished状态
                    const project = await projectsApi.getProject(Number(projectId));
                    setIsPublished(project.is_published);
                    
                    // 更新最后保存状态
                    setLastSavedNodes([...nodes]);
                    setLastSavedEdges([...edges]);
                    setHasUnsavedChanges(false);
                } catch (error: any) {
                    message.error(error.response?.data?.detail || '发布失败');
                } finally {
                    setLoading(false);
                }
            },
        });
    };

    // 准备上传：弹出规则定义框
    const beforeUpload = (file: File) => {
        setPendingFile(file);
        setIsRuleModalOpen(true);
        return false; // 阻止自动上传
    };

    // 执行带规则的上传
    const handleStartExtraction = async () => {
        if (!projectId || !pendingFile) return;

        const values = await ruleForm.validateFields();
        setIsRuleModalOpen(false);
        setLoading(true);

        try {
            const response = await projectsApi.uploadDocument(Number(projectId), pendingFile, values.rules);

            // 将提取的本体数据渲染到画布
            if (response.nodes) {
                setNodes(response.nodes);
            }
            if (response.edges) {
                setEdges(response.edges);
            }

            message.success(response.message || '本体处理成功！');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '提取失败');
        } finally {
            setLoading(false);
            setPendingFile(null);
        }
    };

    // 处理TTL文件上传
    const handleUploadTTL = async (file: File) => {
        if (!projectId) return;

        setLoading(true);
        try {
            const response = await projectsApi.uploadTTLFile(Number(projectId), file);

            // 将提取的本体数据渲染到画布
            if (response.nodes) {
                setNodes(response.nodes);
            }
            if (response.edges) {
                setEdges(response.edges);
            }

            message.success(response.message || 'TTL文件解析成功！');
        } catch (error: any) {
            message.error(error.response?.data?.detail || 'TTL文件解析失败');
        } finally {
            setLoading(false);
        }
        return false; // 阻止自动上传
    };

    // 下载TTL文件
    const handleDownloadTTL = async () => {
        if (!projectId) return;

        try {
            await projectsApi.downloadTTL(Number(projectId));
            message.success('TTL文件已开始下载');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '下载TTL文件失败');
        }
    };

    // 一键展开所有实例
    const expandAllInstances = () => {
        const newExpandedSet = new Set<string>(expandedNodeIds);
        
        // 找到所有类节点
        const classNodes = nodes.filter(node => node.data?.type === 'owl:Class');
        
        // 找到所有实例节点及其关联的类
        nodes.forEach(node => {
            if (node.data?.type === 'owl:NamedIndividual') {
                // 查找该实例关联的类
                const parentClassEdge = edges.find(e =>
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
        message.success('已展开所有实例相关的类');
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目', path: '/my-projects' },
        { title: projectName || '本体构建' },
    ];

    const nodeTypes = [
        { label: '类 (Class)', value: 'owl:Class' },
        { label: '实例 (Individual)', value: 'owl:NamedIndividual' },
        { label: '属性 (Property)', value: 'owl:ObjectProperty' },
    ];

    const relationTypes = [
        { label: '关联 (related_to)', value: 'related_to' },
        { label: '子类 (subclass_of)', value: 'subclass_of' },
        { label: '属于 (instance_of)', value: 'instance_of' },
        { label: '包含 (contains)', value: 'contains' },
        { label: '依赖 (depends_on)', value: 'depends_on' },
    ];

    // 默认连线样式
    const defaultEdgeOptions = {
        type: 'smoothstep',
        markerEnd: { type: RFMarkerType.ArrowClosed, color: '#b1b1b7' },
        style: { stroke: '#b1b1b7', strokeWidth: 1.5 },
    };

    // --- 计算当前显示的元素 ---
    // 逻辑：类始终显示；实例仅在其关联的类被展开时显示；隐藏 rdf:type 连线
    const getDisplayElements = useCallback(() => {
        const visibleNodeIds = new Set<string>();

        // 1. 确定哪些节点应该显示
        nodes.forEach(node => {
            if (node.data.type === 'owl:Class') {
                visibleNodeIds.add(node.id);
            } else if (node.data.type === 'owl:NamedIndividual') {
                // 检查该实例是否有关联的已展开的类
                const parentClassEdge = edges.find(e =>
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

        const displayNodes = nodes.filter(n => visibleNodeIds.has(n.id));

        // 2. 确定哪些连线应该显示 (排除 rdf:type)
        const displayEdges = edges.filter(e => {
            const isVisible = visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target);
            const isTypeRelation = e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of';
            return isVisible && !isTypeRelation;
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds, showAllInstances]);

    const { displayNodes, displayEdges } = getDisplayElements();

    // 创建新关系
    const addNewRelation = () => {
        // 重置表单
        relationForm.resetFields();
        // 设置节点选项
        const nodeOptions = nodes.map(node => ({
            label: `${node.data.label} (${node.data.type})`,
            value: node.id
        }));
        // 显示对话框
        setIsAddRelationModalOpen(true);
    };

    // 确认创建新关系
    const handleConfirmNewRelation = async () => {
        try {
            const values = await relationForm.validateFields();
            const { sourceNodeId, targetNodeId, relationType } = values;

            // 处理自定义关系名称
            let finalRelationType = relationType;
            if (relationType && !relationTypes.some(opt => opt.value === relationType)) {
                // 如果是自定义关系，使用输入的值
                finalRelationType = relationType;
            }

            // 创建新边
            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${sourceNodeId}_${targetNodeId}`,
                source: sourceNodeId,
                target: targetNodeId,
                type: 'smoothstep',
                animated: true,
                data: { label: finalRelationType, relation: finalRelationType },
            } as OntologyEdge;

            // 添加到边列表
            setEdges((eds) => addEdge(newEdge, eds));
            message.success('关系已创建');

            // 关闭对话框
            setIsAddRelationModalOpen(false);
        } catch (error) {
            console.error('创建关系失败:', error);
        }
    };

    return (
        <div className="h-screen flex flex-col bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="flex-1 relative">
                {loading && (
                    <div className="absolute inset-0 bg-white bg-opacity-75 flex items-center justify-center z-50">
                        <Spin size="large" tip="正在通过大模型提取本体中..." />
                    </div>
                )}

                <ReactFlowProvider>
                    <ReactFlow
                        nodes={displayNodes}
                        edges={displayEdges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        onConnect={onConnect}
                        onNodeClick={onElementClick}
                        onEdgeClick={onElementClick}
                        onNodeDoubleClick={onNodeDoubleClick}
                        nodeTypes={nodeTypesMap}
                        defaultEdgeOptions={defaultEdgeOptions}
                        fitView
                        className="bg-gray-50"
                    >
                        <Background color="#cbd5e1" gap={20} variant={BackgroundVariant.Dots} />
                        <Controls />
                        <MiniMap
                            nodeStrokeWidth={3}
                            nodeColor={(node) => {
                                switch (node.data?.type) {
                                    case 'owl:Class': return '#68bdf6';
                                    case 'owl:NamedIndividual': return '#f79767';
                                    default: return '#c990c0';
                                }
                            }}
                        />

                        {/* 顶部工具栏 */}
                        <Panel position="top-left">
                            <Button
                                icon={<ArrowLeftOutlined />}
                                onClick={() => navigate('/my-projects')}
                                className="shadow-sm"
                            >
                                返回项目列表
                            </Button>
                        </Panel>

                        <Panel position="top-right">
                            <Space className="bg-white p-2 rounded-lg shadow-md border border-gray-100">
                                <Upload
                                    accept=".txt,.pdf,.doc,.docx"
                                    showUploadList={false}
                                    beforeUpload={beforeUpload}
                                >
                                    <Tooltip title="定义规则并上传文档提取">
                                        <Button type="primary" icon={<CloudUploadOutlined />} className="bg-indigo-600">
                                            自动提取构建
                                        </Button>
                                    </Tooltip>
                                </Upload>

                                {/* 新增：上传TTL文件按钮 */}
                                <Upload
                                    accept=".ttl"
                                    showUploadList={false}
                                    beforeUpload={handleUploadTTL}
                                >
                                    <Tooltip title="上传TTL文件直接解析">
                                        <Button icon={<FileTextOutlined />} className="bg-purple-600 text-white">
                                            上传TTL文件
                                        </Button>
                                    </Tooltip>
                                </Upload>

                                <Button
                                    icon={<DeploymentUnitOutlined />}
                                    onClick={handleAutoLayout}
                                    title="自动布局"
                                >
                                    自动布局
                                </Button>

                                {/* 新增：下拉菜单选择添加不同类型的节点 */}
                                <Select
                                    defaultValue="class"
                                    style={{ width: 120 }}
                                    onChange={(value) => {
                                        if (value === 'class') addNewClass();
                                        else if (value === 'instance') addNewInstance();
                                    }}
                                    options={[
                                        { value: 'class', label: '新增类 (蓝色)' },
                                        { value: 'instance', label: '新增实例 (橙色)' },
                                    ]}
                                />

                                {/* 新增：显示所有实例开关 */}
                                <Switch
                                    checked={showAllInstances}
                                    onChange={setShowAllInstances}
                                    checkedChildren="显示所有实例"
                                    unCheckedChildren="仅展开类的实例"
                                    size="small"
                                />

                                {/* 新增：展开所有实例按钮 */}
                                <Button
                                    icon={<EyeOutlined />}
                                    onClick={expandAllInstances}
                                    disabled={nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length === 0}
                                >
                                    展开所有实例
                                </Button>

                                {/* 新增：创建新关系按钮 */}
                                <Button
                                    icon={<PlusOutlined />}
                                    onClick={addNewRelation}
                                >
                                    创建新关系
                                </Button>

                                <Button
                                    icon={<DeleteOutlined />}
                                    danger
                                    onClick={deleteSelectedElement}
                                    disabled={!selectedElement}
                                >
                                    删除
                                </Button>

                                <Divider type="vertical" />

                                <Button
                                    icon={<SaveOutlined />}
                                    onClick={handleSaveDraft}
                                    loading={loading}
                                >
                                    保存草稿
                                </Button>

                                <Button
                                    type="primary"
                                    icon={<CloudServerOutlined />}
                                    onClick={handlePublish}
                                    loading={loading}
                                    className="bg-green-600 hover:bg-green-700"
                                    disabled={!hasUnsavedChanges && !loading} /* 仅在有未保存更改时启用 */
                                >
                                    {hasUnsavedChanges ? '同步至 Neo4j' : '已同步 Neo4j'}
                                </Button>

                                {/* 新增：下载TTL按钮 */}
                                <Button
                                    icon={<DownloadOutlined />}
                                    onClick={handleDownloadTTL}
                                >
                                    下载TTL
                                </Button>
                            </Space>
                        </Panel>

                        {/* 底部统计 */}
                        <Panel position="bottom-left">
                            <div className="bg-white px-4 py-2 rounded-lg shadow-md border border-gray-100 flex flex-col space-y-1">
                                <div className="flex items-center space-x-4">
                                    <span className="text-gray-500 font-medium"><InfoCircleOutlined className="mr-1" /> 视图统计:</span>
                                    <span><Tag color="blue">{displayNodes.length} / {nodes.length}</Tag> 实体</span>
                                    <span><Tag color="green">{displayEdges.length} / {edges.length}</Tag> 关系</span>
                                </div>
                                <div className="text-[10px] text-gray-400">
                                    提示：双击类节点可 展开/收起 实例；已隐藏 rdf:type 关系线。
                                </div>
                            </div>
                        </Panel>
                    </ReactFlow>
                </ReactFlowProvider>

                {/* 新增：创建关系对话框 */}
                <Modal
                    title="创建新关系"
                    open={isAddRelationModalOpen}
                    onOk={handleConfirmNewRelation}
                    onCancel={() => setIsAddRelationModalOpen(false)}
                    okText="创建"
                    cancelText="取消"
                >
                    <Form form={relationForm} layout="vertical">
                        <Form.Item
                            name="sourceNodeId"
                            label="源节点"
                            rules={[{ required: true, message: '请选择源节点' }]}
                        >
                            <Select
                                placeholder="选择源节点"
                                options={nodes.map(node => ({
                                    label: `${node.data.label} (${node.data.type})`,
                                    value: node.id
                                }))}
                                showSearch
                                filterOption={(input, option) => 
                                    (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                                }
                                allowClear
                            />
                        </Form.Item>

                        <Form.Item
                            name="targetNodeId"
                            label="目标节点"
                            rules={[{ required: true, message: '请选择目标节点' }]}
                        >
                            <Select
                                placeholder="选择目标节点"
                                options={nodes.filter(node => 
                                    // 排除已选择的源节点
                                    relationForm.getFieldValue('sourceNodeId') !== node.id
                                ).map(node => ({
                                    label: `${node.data.label} (${node.data.type})`,
                                    value: node.id
                                }))}
                                showSearch
                                filterOption={(input, option) => 
                                    (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                                }
                                allowClear
                            />
                        </Form.Item>

                        <Form.Item
                            name="relationType"
                            label="关系类型"
                            rules={[{ required: true, message: '请选择关系类型' }]}
                        >
                            <Select
                                placeholder="选择关系类型"
                                options={[
                                    ...relationTypes,
                                    { value: 'custom', label: '自定义关系' }
                                ]}
                                showSearch
                                filterOption={(input, option) => 
                                    (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                                }
                                allowClear
                                onSearch={(value) => {
                                    // 当用户输入时
                                    if (value.trim() && !relationTypes.some(opt => 
                                        opt.value.toLowerCase() === value.toLowerCase() || 
                                        opt.label.toLowerCase().includes(value.toLowerCase())
                                    )) {
                                        relationForm.setFieldValue('relationType', value);
                                    }
                                }}
                                onChange={(value) => {
                                    // 如果选择"自定义关系"，清空输入框
                                    if (value === 'custom') {
                                        relationForm.setFieldValue('relationType', '');
                                    }
                                }}
                            />
                        </Form.Item>

                        {/* 自定义关系名称输入框 */}
                        {relationForm.getFieldValue('relationType') && 
                         !relationTypes.some(opt => opt.value === relationForm.getFieldValue('relationType')) &&
                         relationForm.getFieldValue('relationType') !== 'custom' && (
                            <Form.Item
                                name="customRelationName"
                                label="自定义关系名称"
                                rules={[{ required: true, message: '请输入自定义关系名称' }]}
                            >
                                <Input 
                                    placeholder="输入自定义关系名称"
                                    value={relationForm.getFieldValue('relationType')}
                                    onChange={(e) => relationForm.setFieldValue('relationType', e.target.value)}
                                />
                            </Form.Item>
                        )}
                    </Form>
                </Modal>

                {/* 定义提取规则 Modal */}
                <Modal
                    title={<Space><SettingOutlined /> 定义本体提取规则</Space>}
                    open={isRuleModalOpen}
                    onOk={handleStartExtraction}
                    onCancel={() => setIsRuleModalOpen(false)}
                    okText="开始自动提取"
                    cancelText="取消"
                    width={600}
                >
                    <div className="mb-4 text-gray-500 text-sm">
                        您可以指定关注的实体类型、属性或关系描述，大模型将根据此规则从文档中提取。
                    </div>
                    <Form form={ruleForm} layout="vertical">
                        <Form.Item
                            name="rules"
                            label="提取规则描述"
                            initialValue={projectName + " 相关领域的本体提取"}
                        >
                            <Input.TextArea
                                rows={6}
                                placeholder="例如：重点提取关于'制造工艺'的实体，包含其'参数'属性，以及'组成部分'的关系。"
                            />
                        </Form.Item>
                    </Form>
                </Modal>

                {/* 详情编辑抽屉 */}
                <Drawer
                    title={
                        <div className="flex items-center justify-between w-full pr-8">
                            <span>{selectedElement && 'position' in selectedElement ? '实体详情' : '关系详情'}</span>
                            {selectedElement && <Tag color="blue">{selectedElement.id}</Tag>}
                        </div>
                    }
                    placement="right"
                    onClose={() => setIsDrawerOpen(false)}
                    open={isDrawerOpen}
                    width={450}
                >
                    <Form form={form} layout="vertical" onFinish={handleSaveProperties}>
                        <SectionTitle icon={<InfoCircleOutlined />} title="基础信息" />
                        <Form.Item
                            name="label"
                            label="显示名称"
                            rules={[{ required: true, message: '请输入名称' }]}
                        >
                            <Input placeholder="输入名称" />
                        </Form.Item>

                        {selectedElement && 'position' in selectedElement ? (
                            <>
                                <Form.Item
                                    name="type"
                                    label="节点类型"
                                    rules={[{ required: true }]}
                                >
                                    <Select options={nodeTypes} />
                                </Form.Item>

                                <Divider />
                                <SectionTitle icon={<FileTextOutlined />} title="属性列表" />

                                <Form.List name="properties">
                                    {(fields, { add, remove }) => (
                                        <>
                                            {fields.map(({ key, name, ...restField }) => (
                                                <Space key={key} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
                                                    <Form.Item
                                                        {...restField}
                                                        name={[name, 'name']}
                                                        rules={[{ required: true, message: '属性名' }]}
                                                    >
                                                        <Input placeholder="属性名" />
                                                    </Form.Item>
                                                    <Form.Item
                                                        {...restField}
                                                        name={[name, 'value']}
                                                        rules={[{ required: true, message: '属性值' }]}
                                                    >
                                                        <Input placeholder="属性值" />
                                                    </Form.Item>
                                                    <DeleteOutlined onClick={() => remove(name)} className="text-red-500 cursor-pointer" />
                                                </Space>
                                            ))}
                                            <Form.Item>
                                                <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
                                                    添加属性
                                                </Button>
                                            </Form.Item>
                                        </>
                                    )}
                                </Form.List>

                                <div className="text-gray-400 text-xs mb-4">
                                    * 提示：大模型自动提取的属性将展示在此处，您可以手动添加或修改。
                                </div>
                            </>
                        ) : (
                            <Form.Item
                                name="relation"
                                label="关系类型"
                                rules={[{ required: true }]}
                            >
                                <Select options={relationTypes} />
                            </Form.Item>
                        )}

                        <div className="flex space-x-2 mt-8">
                            <Button type="primary" htmlType="submit" className="flex-1 bg-blue-600">
                                更新并保存
                            </Button>
                            <Button danger icon={<DeleteOutlined />} onClick={deleteSelectedElement}>
                                删除
                            </Button>
                        </div>
                    </Form>
                </Drawer>
            </div>
        </div>
    );
};

const SectionTitle = ({ icon, title }: { icon: any, title: string }) => (
    <div className="flex items-center space-x-2 mb-4 font-medium text-gray-700">
        {icon}
        <span>{title}</span>
    </div>
);

export default OntologyBuilderPage;
