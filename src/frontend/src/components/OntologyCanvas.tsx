import React, { useState, useCallback, useRef, useEffect } from 'react';
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
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Drawer, Form, Input, Button, select, message, Space } from 'antd';
import { OntologyNode, OntologyEdge } from '../types/ontology';

const initialNodes: OntologyNode[] = [];
const initialEdges: OntologyEdge[] = [];

const OntologyCanvas: React.FC = () => {
    const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
    const [selectedElement, setSelectedElement] = useState<OntologyNode | OntologyEdge | null>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [form] = Form.useForm();

    // 处理连线
    const onConnect = useCallback(
        (params: Connection) => setEdges((eds) => addEdge(params, eds)),
        [setEdges]
    );

    // 点击节点或连线
    const onElementClick = (_: React.MouseEvent, element: OntologyNode | OntologyEdge) => {
        setSelectedElement(element);
        setIsDrawerOpen(true);
        form.setFieldsValue({
            label: element.data?.label || '',
            type: (element as OntologyNode).data?.type || '',
            ...element.data?.properties
        });
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
                            data: { ...node.data, label: values.label, type: values.type, properties: { ...values } },
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
                            data: { ...edge.data, label: values.label },
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
            position: { x: Math.random() * 400, y: Math.random() * 400 },
            data: { label: '新实体', type: 'Entity', properties: {} },
        };
        setNodes((nds) => nds.concat(newNode));
    };

    return (
        <div className="flex h-screen w-full bg-[#f0f2f5]">
            <ReactFlowProvider>
                <div className="flex-grow h-full relative">
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        onConnect={onConnect}
                        onNodeClick={onElementClick}
                        onEdgeClick={onElementClick}
                        fitView
                    >
                        <Background color="#aaa" gap={20} />
                        <Controls />
                        <MiniMap />
                        <Panel position="top-right">
                            <Space>
                                <Button type="primary" onClick={addNewNode}>新增实体</Button>
                                <Button onClick={() => console.log('Save Draft', { nodes, edges })}>保存草稿</Button>
                                <Button type="dashed" danger onClick={() => console.log('Publish')}>发布</Button>
                            </Space>
                        </Panel>
                    </ReactFlow>
                </div>
            </ReactFlowProvider>

            <Drawer
                title="属性编辑"
                placement="right"
                onClose={() => setIsDrawerOpen(false)}
                open={isDrawerOpen}
                width={400}
            >
                <Form form={form} layout="vertical" onFinish={handleSaveProperties}>
                    <Form.Item name="label" label="显示名称" rules={[{ required: true }]}>
                        <Input />
                    </Form.Item>
                    {selectedElement && 'position' in selectedElement && (
                        <Form.Item name="type" label="本体类型">
                            <Input placeholder="例如: Person, Product..." />
                        </Form.Item>
                    )}
                    {/* 这里可以根据 metadata 动态渲染更多 key-value 表单项 */}
                    <Button type="primary" htmlType="submit" block>
                        保存修改
                    </Button>
                </Form>
            </Drawer>
        </div>
    );
};

export default OntologyCanvas;
