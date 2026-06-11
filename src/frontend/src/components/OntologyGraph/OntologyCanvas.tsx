import { useState, useEffect, useCallback } from 'react';
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
    NodeProps,
    EdgeProps,
    useReactFlow,
    MarkerType,
    Node,
    ConnectionMode
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Drawer, Form, Input, Button, Select, message, Space, Switch, Typography, Divider, Spin } from 'antd';
import {
    DownloadOutlined,
    SaveOutlined,
    UploadOutlined,
    DeploymentUnitOutlined,
    PlusOutlined
} from '@ant-design/icons';

// --- 引入自定义组件和工具 ---
// 请确保路径正确指向你存放文件的位置
import Neo4jNode from './Neo4jNode';
import { getLayoutedElements } from '../../utils/layoutUtils';
import { projectsApi } from '../../api/projects';
import { OntologyNode, OntologyEdge } from '../../types/ontology'; // 假设你有定义类型，如果没有可用 any

const { Text } = Typography;

// --- 1. 自定义边组件 (贝塞尔曲线，双向边自动偏移) ---
const CustomEdge = ({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style, source, target }: EdgeProps) => {
    const isBidir = data?._isBidirectional;
    let edgePath: string;
    let labelX: number;
    let labelY: number;

    if (isBidir) {
        const dx = targetX - sourceX;
        const dy = targetY - sourceY;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const nx = -dy / len;
        const ny = dx / len;
        const isReverse = source && target && source > target;
        const parallelOffset = 12;
        const sign = isReverse ? 1 : -1;
        const ox = nx * parallelOffset * sign;
        const oy = ny * parallelOffset * sign;
        const sx = sourceX + ox;
        const sy = sourceY + oy;
        const tx = targetX + ox;
        const ty = targetY + oy;
        edgePath = `M ${sx} ${sy} L ${tx} ${ty}`;
        labelX = (sx + tx) / 2;
        labelY = (sy + ty) / 2;
    } else {
        edgePath = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`;
        labelX = (sourceX + targetX) / 2;
        labelY = (sourceY + targetY) / 2;
    }

    return (
        <>
            <path
                id={id}
                style={style}
                className="react-flow__edge-path"
                d={edgePath}
                markerEnd={markerEnd}
            />
            {data?.label && (
                <g>
                    <rect
                        x={labelX - 35}
                        y={labelY - 10}
                        width={70}
                        height={20}
                        fill="white"
                        fillOpacity={0.85}
                        rx={4}
                        stroke="#e0e0e0"
                        strokeWidth={0.5}
                    />
                    <text
                        x={labelX}
                        y={labelY + 4}
                        textAnchor="middle"
                        fontSize={10}
                        fill="#666"
                        fontWeight={400}
                    >
                        {data.label}
                    </text>
                </g>
            )}
        </>
    );
};

// 定义节点和边的类型映射 (在组件外定义避免重新渲染)
const markBidirectionalEdges = (edges: any[]): any[] => {
    const pairCount = new Map<string, number>();
    edges.forEach((e: any) => {
        const key = [e.source, e.target].sort().join('<->');
        pairCount.set(key, (pairCount.get(key) || 0) + 1);
    });
    return edges.map((e: any) => {
        const key = [e.source, e.target].sort().join('<->');
        const isBidir = (pairCount.get(key) || 0) >= 2;
        return {
            ...e,
            data: { ...e.data, _isBidirectional: isBidir }
        };
    });
};

const nodeTypes = { custom: Neo4jNode };
const edgeTypes = { custom: CustomEdge };

// 默认连线样式
const defaultEdgeOptions = {
    type: 'custom',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#b1b1b7' },
    style: { stroke: '#b1b1b7', strokeWidth: 1.5 },
};

const OntologyCanvas: React.FC<{ projectId: number }> = ({ projectId }) => {
    // React Flow 状态
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);

    // UI 状态
    const [selectedElement, setSelectedElement] = useState<any>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [lang, setLang] = useState<'zh' | 'en'>('zh'); // 语言状态

    // Ant Design Hooks
    const [form] = Form.useForm();

    // React Flow Hooks
    const { fitView, getNodes, getEdges } = useReactFlow();

    // ----------------------------------------------------------------
    // 1. 核心布局逻辑
    // ----------------------------------------------------------------
    const applyAutoLayout = useCallback((rawNodes: any[], rawEdges: any[]) => {
        if (!rawNodes || rawNodes.length === 0) return;

        // 1. 深度清理数据，确保没有残留的 position
        const cleanNodes = rawNodes.map(n => ({
            ...n,
            type: 'custom', // 再次强制确保类型为 custom
            width: 80, // 给 dagre 一个初始宽高参考
            height: 80
        }));

        // 2. 计算布局
        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
            cleanNodes,
            rawEdges,
            'LR' // 建议用 LR (从左到右) 或者 TB (从上到下)
        );

        // 3. 设置状态
        setNodes([...layoutedNodes]);
        setEdges(markBidirectionalEdges([...layoutedEdges]));

        // 4. 强制适配视图 (延迟执行以确保 DOM 已渲染)
        setTimeout(() => {
            window.requestAnimationFrame(() => {
                fitView({ padding: 0.2, duration: 800 });
            });
        }, 100);
    }, [fitView, setNodes, setEdges]);


    // ----------------------------------------------------------------
    // 2. 初始化数据加载
    // ----------------------------------------------------------------
    useEffect(() => {
        const fetchProjectData = async () => {
            if (!projectId) return;
            try {
                setLoading(true);
                const project = await projectsApi.getProject(projectId);

                if (project.graph_data && project.graph_data.nodes && project.graph_data.nodes.length > 0) {
                    const { nodes: savedNodes, edges: savedEdges } = project.graph_data;

                    // 检查是否需要自动布局 (如果所有节点坐标都是 0,0，说明是刚解析还没布局过)
                    const needsLayout = savedNodes.every((n: any) => n.position.x === 0 && n.position.y === 0);

                    if (needsLayout) {
                        applyAutoLayout(savedNodes, savedEdges);
                    } else {
                        // 如果已有坐标，直接恢复，但要确保 currentLang 被注入
                        setNodes(savedNodes.map((n: any) => ({
                            ...n,
                            type: 'custom',
                            data: { ...n.data, currentLang: lang }
                        })));
                        setEdges(markBidirectionalEdges(savedEdges.map((e: any) => ({
                            ...e,
                            type: 'custom',
                            markerEnd: { type: MarkerType.ArrowClosed, color: '#b1b1b7' },
                            style: { stroke: '#b1b1b7', strokeWidth: 1.5 },
                        }))));
                        setTimeout(() => fitView({ padding: 0.2 }), 100);
                    }
                }
            } catch (error) {
                console.error("加载项目失败:", error);
                message.error("加载项目数据失败");
            } finally {
                setLoading(false);
            }
        };

        fetchProjectData();
    }, [projectId]); // 注意：这里移除了 applyAutoLayout 依赖，防止死循环，只在 id 变动时加载

    // ----------------------------------------------------------------
    // 3. 监听语言切换，实时更新节点显示
    // ----------------------------------------------------------------
    useEffect(() => {
        setNodes((nds) => nds.map((node) => ({
            ...node,
            data: { ...node.data, currentLang: lang }
        })));
    }, [lang, setNodes]);

    // ----------------------------------------------------------------
    // 4. 交互处理函数
    // ----------------------------------------------------------------

    // 连线
    const onConnect = useCallback(
        (params: Connection) => setEdges((eds) => addEdge({ ...params, ...defaultEdgeOptions }, eds)),
        [setEdges]
    );

    // 点击元素 (打开抽屉)
    const onElementClick = (_: React.MouseEvent, element: any) => {
        setSelectedElement(element);
        setIsDrawerOpen(true);

        // 填充表单
        form.setFieldsValue({
            label: element.data?.label || '',
            type: element.data?.type || 'owl:Class',
            // 将 properties 对象展平以便 Form 处理，或者直接作为对象处理
            ...element.data?.properties
        });
    };

    // 保存属性修改
    const handleSaveProperties = (values: any) => {
        if (!selectedElement) return;
        const isNode = !selectedElement.source; // 简单判断是否为节点

        if (isNode) {
            setNodes((nds) => nds.map((node) => {
                if (node.id === selectedElement.id) {
                    // 提取除 label/type 外的自定义属性
                    const { label, type, ...restProps } = values;
                    return {
                        ...node,
                        data: {
                            ...node.data,
                            label,
                            type,
                            properties: { ...node.data.properties, ...restProps }, // 合并属性
                            currentLang: lang
                        }
                    };
                }
                return node;
            }));
        } else {
            // 更新边
            setEdges((eds) => eds.map((edge) => {
                if (edge.id === selectedElement.id) {
                    return {
                        ...edge,
                        data: { ...edge.data, label: values.label }
                    };
                }
                return edge;
            }));
        }
        setIsDrawerOpen(false);
        message.success('已更新本地视图，请记得点击"保存"同步到服务器');
    };

    // 新增实体
    const addNewNode = () => {
        const id = `new_node_${Date.now()}`;
        const newNode: Node = {
            id,
            type: 'custom',
            // 在视图中心附近随机生成
            position: { x: Math.random() * 100, y: Math.random() * 100 },
            data: {
                label: '新实体',
                type: 'owl:Class',
                properties: {},
                currentLang: lang
            },
        };
        setNodes((nds) => nds.concat(newNode));
        message.info('已添加新实体，请拖动位置或编辑属性');
    };

    // ----------------------------------------------------------------
    // 5. API 操作 (上传、保存、下载)
    // ----------------------------------------------------------------

    // 上传文档/TTL
    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        try {
            setLoading(true);
            let result;
            if (file.name.endsWith('.ttl') || file.name.endsWith('.json')) {
                result = await projectsApi.uploadTTLFile(projectId, file);
            } else {
                result = await projectsApi.uploadDocument(projectId, file);
            }

            if (result.nodes && result.edges) {
                // 上传成功后，应用自动布局
                applyAutoLayout(result.nodes, result.edges);
                message.success(`成功导入: ${result.nodes.length} 个节点`);
            }
        } catch (error) {
            console.error('上传失败', error);
            message.error('文件处理失败');
        } finally {
            setLoading(false);
            // 清空 input 允许重复上传同名文件
            e.target.value = '';
        }
    };

    // 保存当前画布到后端
    const handleSaveGraph = async () => {
        try {
            setLoading(true);
            // 获取当前的节点和边
            const currentNodes = getNodes();
            const currentEdges = getEdges();

            // 可以在这里清理掉一些不必要的前端临时数据再传给后端
            await projectsApi.updateOntology(projectId, {
                nodes: currentNodes,
                edges: currentEdges
            });
            message.success('图谱已保存');
        } catch (error) {
            console.error('保存失败', error);
            message.error('保存失败');
        } finally {
            setLoading(false);
        }
    };

    // 下载 TTL
    const handleDownloadTTL = async () => {
        try {
            await projectsApi.downloadTTL(projectId);
            message.success('正在下载 TTL 文件');
        } catch (error) {
            message.error('下载失败');
        }
    };

    // ----------------------------------------------------------------
    // 6. 渲染界面
    // ----------------------------------------------------------------
    return (
        <div className="flex h-screen w-full bg-[#f8f9fa] relative">
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onNodeClick={onElementClick}
                onEdgeClick={onElementClick}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                defaultEdgeOptions={defaultEdgeOptions}
                connectionMode={ConnectionMode.Loose}
                fitView
                minZoom={0.1}
            >
                <Background color="#e1e1e1" gap={16} size={1} />
                <Controls />
                <MiniMap
                    zoomable
                    pannable
                    nodeColor={(n) => {
                        const type = n.data?.type;
                        if (type === 'owl:Class') return '#68bdf6';
                        if (type === 'owl:NamedIndividual') return '#f79767';
                        return '#c990c0';
                    }}
                />

                {/* 顶部工具栏 */}
                <Panel position="top-right" className="bg-white p-2 rounded shadow-md">
                    <Space>
                        <Switch
                            checkedChildren="EN"
                            unCheckedChildren="中"
                            checked={lang === 'en'}
                            onChange={(checked) => setLang(checked ? 'en' : 'zh')}
                        />
                        <Divider type="vertical" />

                        <Button
                            icon={<DeploymentUnitOutlined />}
                            onClick={() => applyAutoLayout(nodes, edges)}
                            title="重新自动布局"
                        />

                        <Button
                            icon={<PlusOutlined />}
                            onClick={addNewNode}
                        >
                            新增实体
                        </Button>

                        {/* 上传按钮封装 */}
                        <div className="relative inline-block">
                            <Button icon={<UploadOutlined />} loading={loading}>
                                导入文档/TTL
                            </Button>
                            <input
                                type="file"
                                accept=".txt,.pdf,.doc,.docx,.ttl,.json,.md,.xlsx,.xls,.csv"
                                onChange={handleUpload}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                            />
                        </div>

                        <Button
                            type="primary"
                            icon={<SaveOutlined />}
                            onClick={handleSaveGraph}
                            loading={loading}
                        >
                            保存
                        </Button>

                        <Button
                            icon={<DownloadOutlined />}
                            onClick={handleDownloadTTL}
                        />
                    </Space>
                </Panel>

                {loading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-white/50 z-50">
                        <Spin size="large" tip="处理中..." />
                    </div>
                )}
            </ReactFlow>

            {/* 属性编辑侧边栏 */}
            <Drawer
                title={selectedElement ? (!selectedElement.source ? '实体属性' : '关系属性') : ''}
                open={isDrawerOpen}
                onClose={() => setIsDrawerOpen(false)}
                mask={false}
                width={350}
            >
                <Form form={form} layout="vertical" onFinish={handleSaveProperties}>
                    <Form.Item name="label" label="显示名称 (Label)" rules={[{ required: true }]}>
                        <Input />
                    </Form.Item>

                    {/* 仅节点显示类型选择 */}
                    {selectedElement && !selectedElement.source && (
                        <>
                            <Form.Item name="type" label="本体类型 (Type)">
                                <Select>
                                    <Select.Option value="owl:Class">Class (类)</Select.Option>
                                    <Select.Option value="owl:NamedIndividual">NamedIndividual (实例)</Select.Option>
                                    <Select.Option value="owl:ObjectProperty">ObjectProperty (属性)</Select.Option>
                                </Select>
                            </Form.Item>

                            <Divider>自定义属性</Divider>

                            {/* 动态渲染该节点的所有其他属性 */}
                            {selectedElement.data?.properties && Object.keys(selectedElement.data.properties).map((key) => (
                                <Form.Item key={key} name={key} label={key}>
                                    <Input.TextArea autoSize={{ minRows: 1, maxRows: 4 }} />
                                </Form.Item>
                            ))}

                            <div className="mt-4 p-2 bg-gray-50 rounded text-xs text-gray-400">
                                提示：如需添加新属性字段，请直接修改生成的 TTL 或联系管理员开启高级编辑模式。
                            </div>
                        </>
                    )}

                    <div className="mt-6">
                        <Button type="primary" htmlType="submit" block>
                            应用修改
                        </Button>
                    </div>
                </Form>
            </Drawer>
        </div>
    );
};

// 导出组件 (确保包裹 Provider)
export default (props: { projectId: number }) => (
    <ReactFlowProvider>
        <OntologyCanvas {...props} />
    </ReactFlowProvider>
);