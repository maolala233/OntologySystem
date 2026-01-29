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
    Table,
    Tag,
    Divider,
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
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { OntologyNode, OntologyEdge } from '../types/ontology';
import { projectsApi } from '../api/projects';

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
    const [form] = Form.useForm();
    const [ruleForm] = Form.useForm();

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const project = await projectsApi.getProject(Number(projectId));
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
                ...element.data?.properties
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
        const { label, type, relation, ...otherProps } = values;

        if (isNode) {
            setNodes((nds) =>
                nds.map((node) => {
                    if (node.id === selectedElement.id) {
                        return {
                            ...node,
                            data: {
                                ...node.data,
                                label: label,
                                type: type,
                                properties: otherProps,
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
                boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)'
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
                    // 先保存当前图数据
                    await projectsApi.updateProject(Number(projectId), {
                        graph_data: { nodes, edges },
                    });

                    // 发布
                    await projectsApi.publishProject(Number(projectId));
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

    // 属性表格列定义
    const propertyColumns = [
        { title: '属性名', dataIndex: 'key', key: 'key' },
        { title: '属性值', dataIndex: 'value', key: 'value', render: (text: string) => <Tag color="blue">{text}</Tag> },
    ];

    // 格式化当前选中的属性数据
    const getPropertyData = () => {
        if (!selectedElement || !('position' in selectedElement)) return [];
        const props = selectedElement.data?.properties || {};
        return Object.entries(props).map(([key, value]) => ({
            key,
            value: String(value),
        }));
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
                        <Background color="#d1d5db" gap={20} />
                        <Controls />
                        <MiniMap
                            nodeStrokeWidth={3}
                            nodeColor={(node) => {
                                switch (node.data?.type) {
                                    case 'Class': return '#3b82f6';
                                    case 'Property': return '#10b981';
                                    case 'Concept': return '#f59e0b';
                                    default: return '#6366f1';
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
                                    icon={<PlusOutlined />}
                                    onClick={addNewNode}
                                >
                                    新增实体
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
                                    disabled={isPublished}
                                >
                                    {isPublished ? '已同步 Neo4j' : '同步至 Neo4j'}
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
                            <div className="bg-white px-4 py-2 rounded-lg shadow-md border border-gray-100 flex items-center space-x-4">
                                <span className="text-gray-500"><InfoCircleOutlined className="mr-1" /> 统计:</span>
                                <span><Tag color="blue">{nodes.length}</Tag> 实体</span>
                                <span><Tag color="green">{edges.length}</Tag> 关系</span>
                                <span className="text-xs text-gray-400">| 点击节点编辑属性</span>
                            </div>
                        </Panel>
                    </ReactFlow>
                </ReactFlowProvider>

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
                                <Table
                                    dataSource={getPropertyData()}
                                    columns={propertyColumns}
                                    pagination={false}
                                    size="small"
                                    className="mb-4"
                                    locale={{ emptyText: '暂无属性' }}
                                />

                                <div className="text-gray-400 text-xs mb-4">
                                    * 提示：大模型自动提取的属性将展示在此处。
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
