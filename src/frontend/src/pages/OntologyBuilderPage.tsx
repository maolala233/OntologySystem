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
} from 'antd';
import {
    SaveOutlined,
    CloudUploadOutlined,
    PlusOutlined,
    DeleteOutlined,
    CloudServerOutlined,
    ArrowLeftOutlined,
    FileTextOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { OntologyNode, OntologyEdge } from '../types/ontology';
import { projectsAPI } from '../api/projects';

const OntologyBuilderPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();

    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [selectedElement, setSelectedElement] = useState<OntologyNode | OntologyEdge | null>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [projectName, setProjectName] = useState('');
    const [isPublished, setIsPublished] = useState(false);
    const [form] = Form.useForm();

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const project = await projectsAPI.getProject(Number(projectId));
            setProjectName(project.name);
            setIsPublished(project.is_published);

            if (project.graph_data?.nodes) {
                setNodes(project.graph_data.nodes);
            }
            if (project.graph_data?.edges) {
                setEdges(project.graph_data.edges);
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

    // 点击节点或连线
    const onElementClick = (_: React.MouseEvent, element: Node | Edge) => {
        setSelectedElement(element as OntologyNode | OntologyEdge);
        setIsDrawerOpen(true);

        if ('position' in element) {
            // 节点
            form.setFieldsValue({
                label: element.data?.label || '',
                type: element.data?.type || '',
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

        if (isNode) {
            setNodes((nds) =>
                nds.map((node) => {
                    if (node.id === selectedElement.id) {
                        return {
                            ...node,
                            data: {
                                ...node.data,
                                label: values.label,
                                type: values.type,
                                properties: { ...values },
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
                                label: values.label,
                                relation: values.relation,
                            },
                            label: values.label,
                        };
                    }
                    return edge;
                })
            );
        }
        setIsDrawerOpen(false);
        message.success('属性已更新');
    };

    // 新增节点
    const addNewNode = () => {
        const newNode: OntologyNode = {
            id: `node_${Date.now()}`,
            type: 'default',
            position: { x: Math.random() * 400 + 100, y: Math.random() * 400 + 100 },
            data: { label: '新实体', type: 'Entity', properties: {} },
            style: {
                background: '#fff',
                border: '2px solid #3b82f6',
                borderRadius: '8px',
                padding: '10px',
                fontSize: '14px',
            },
        };
        setNodes((nds) => nds.concat(newNode));
        message.success('已添加新节点');
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

    // 保存草稿
    const handleSaveDraft = async () => {
        if (!projectId) return;

        setLoading(true);
        try {
            await projectsAPI.updateProject(Number(projectId), {
                graph_data: { nodes, edges },
            });
            message.success('草稿已保存');
        } catch (error) {
            message.error('保存失败');
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
                    // 先保存当前图数据
                    await projectsAPI.updateProject(Number(projectId), {
                        graph_data: { nodes, edges },
                    });

                    // 发布
                    await projectsAPI.publishProject(Number(projectId));
                    message.success('发布成功！本体已同步到图数据库');
                    setIsPublished(true);
                } catch (error: any) {
                    message.error(error.response?.data?.detail || '发布失败');
                } finally {
                    setLoading(false);
                }
            },
        });
    };

    // 上传文档
    const handleUploadDocument = async (file: File) => {
        if (!projectId) return false;

        setLoading(true);
        try {
            const result = await projectsAPI.uploadDocument(Number(projectId), file);

            // 将提取的本体数据渲染到画布
            if (result.nodes) {
                setNodes(result.nodes);
            }
            if (result.edges) {
                setEdges(result.edges);
            }

            message.success('文档上传成功，本体已自动提取！');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '上传失败');
        } finally {
            setLoading(false);
        }

        return false; // 阻止自动上传
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目', path: '/my-projects' },
        { title: projectName || '本体构建' },
    ];

    const nodeTypes = [
        { label: '实体 (Entity)', value: 'Entity' },
        { label: '类 (Class)', value: 'Class' },
        { label: '属性 (Property)', value: 'Property' },
        { label: '概念 (Concept)', value: 'Concept' },
    ];

    const relationTypes = [
        { label: '关联 (related_to)', value: 'related_to' },
        { label: '子类 (subclass_of)', value: 'subclass_of' },
        { label: '属于 (instance_of)', value: 'instance_of' },
        { label: '包含 (contains)', value: 'contains' },
        { label: '依赖 (depends_on)', value: 'depends_on' },
    ];

    return (
        <div className="h-screen flex flex-col bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="flex-1 relative">
                {loading && (
                    <div className="absolute inset-0 bg-white bg-opacity-75 flex items-center justify-center z-50">
                        <Spin size="large" tip="处理中..." />
                    </div>
                )}

                <ReactFlowProvider>
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        onConnect={onConnect}
                        onNodeClick={onElementClick}
                        onEdgeClick={onElementClick}
                        fitView
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

                        {/* 顶部工具栏 */}
                        <Panel position="top-left">
                            <Button
                                icon={<ArrowLeftOutlined />}
                                onClick={() => navigate('/my-projects')}
                            >
                                返回
                            </Button>
                        </Panel>

                        <Panel position="top-right">
                            <Space>
                                <Upload
                                    accept=".txt,.pdf,.doc,.docx"
                                    showUploadList={false}
                                    beforeUpload={handleUploadDocument}
                                >
                                    <Tooltip title="上传文档自动提取本体">
                                        <Button icon={<CloudUploadOutlined />}>
                                            上传文档
                                        </Button>
                                    </Tooltip>
                                </Upload>

                                <Button
                                    type="primary"
                                    icon={<PlusOutlined />}
                                    onClick={addNewNode}
                                    className="bg-blue-600"
                                >
                                    新增实体
                                </Button>

                                <Button
                                    icon={<DeleteOutlined />}
                                    danger
                                    onClick={deleteSelectedElement}
                                    disabled={!selectedElement}
                                >
                                    删除选中
                                </Button>

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
                                    disabled={isPublished}
                                >
                                    {isPublished ? '已发布' : '发布到图数据库'}
                                </Button>
                            </Space>
                        </Panel>

                        {/* 底部统计信息 */}
                        <Panel position="bottom-left">
                            <div className="bg-white px-4 py-2 rounded-lg shadow-md text-sm">
                                <Space split="|">
                                    <span>节点: {nodes.length}</span>
                                    <span>关系: {edges.length}</span>
                                    <span className={isPublished ? 'text-green-600' : 'text-orange-600'}>
                                        {isPublished ? '已发布' : '草稿'}
                                    </span>
                                </Space>
                            </div>
                        </Panel>
                    </ReactFlow>
                </ReactFlowProvider>

                {/* 属性编辑抽屉 */}
                <Drawer
                    title={
                        <div className="flex items-center">
                            <FileTextOutlined className="mr-2" />
                            {selectedElement && 'position' in selectedElement ? '节点属性' : '关系属性'}
                        </div>
                    }
                    placement="right"
                    onClose={() => setIsDrawerOpen(false)}
                    open={isDrawerOpen}
                    width={400}
                >
                    <Form form={form} layout="vertical" onFinish={handleSaveProperties}>
                        <Form.Item
                            name="label"
                            label="显示名称"
                            rules={[{ required: true, message: '请输入显示名称' }]}
                        >
                            <Input placeholder="例如: 人员、产品" />
                        </Form.Item>

                        {selectedElement && 'position' in selectedElement ? (
                            <Form.Item
                                name="type"
                                label="本体类型"
                                rules={[{ required: true, message: '请选择类型' }]}
                            >
                                <Select options={nodeTypes} placeholder="选择节点类型" />
                            </Form.Item>
                        ) : (
                            <Form.Item
                                name="relation"
                                label="关系类型"
                                rules={[{ required: true, message: '请选择关系类型' }]}
                            >
                                <Select options={relationTypes} placeholder="选择关系类型" />
                            </Form.Item>
                        )}

                        <Form.Item>
                            <Space className="w-full justify-end">
                                <Button onClick={() => setIsDrawerOpen(false)}>取消</Button>
                                <Button type="primary" htmlType="submit" className="bg-blue-600">
                                    保存修改
                                </Button>
                            </Space>
                        </Form.Item>
                    </Form>
                </Drawer>
            </div>
        </div>
    );
};

export default OntologyBuilderPage;
