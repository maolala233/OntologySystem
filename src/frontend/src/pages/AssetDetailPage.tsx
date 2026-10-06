import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Spin, message, Descriptions, Tag, Card, Modal, Input, Tooltip, Tree } from 'antd';
import type { TreeProps } from 'antd';
import {
    ArrowLeftOutlined, UserOutlined, InfoCircleOutlined, DatabaseOutlined,
    UnorderedListOutlined, RightOutlined, LeftOutlined, ExportOutlined,
    ExpandOutlined, ShrinkOutlined, SearchOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import { graphApi } from '../api/graphview';
import D3ForceGraph from '../components/OntologyGraph/D3ForceGraph';
import DetailDrawer from '../features/graph-explorer/DetailDrawer';
import ExportDialog from '../features/builder/components/ExportDialog';

const CLASS_TYPES = new Set(['owl:Class', 'Class']);

/** 资产中心 · 已发布本体详情：展示逻辑与构建器「实例探索」一致（深色画布 + 类/实例列表 + DetailDrawer），
 *  但全部只读——不提供画布编辑回写、不展示任何编辑入口。 */
const AssetDetailPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const [project, setProject] = useState<ProjectData | null>(null);
    const [loading, setLoading] = useState(true);
    const [graphNodes, setGraphNodes] = useState<any[]>([]);
    const [graphEdges, setGraphEdges] = useState<any[]>([]);
    // 类节点展开状态（右键类节点展开/收起其实例）
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    // 节点详情走与实例探索一致的 DetailDrawer；边用轻量弹窗
    const [selectedNode, setSelectedNode] = useState<{ type: 'node'; id: string } | null>(null);
    const [selectedEdge, setSelectedEdge] = useState<any>(null);
    const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
    const [neighborhoodPinId, setNeighborhoodPinId] = useState<string | null>(null);
    const [focusRequest, setFocusRequest] = useState<{ nodeId: string; seq: number } | null>(null);
    const focusSeqRef = useRef(0);
    // 左侧类/实例列表面板
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    const [treeSearchValue, setTreeSearchValue] = useState('');
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    const [exportOpen, setExportOpen] = useState(false);

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
            setGraphNodes(data.graph_data?.nodes || []);
            setGraphEdges(data.graph_data?.edges || []);
        } catch (error: any) {
            message.error('加载本体详情失败');
            navigate('/asset-center');
        } finally {
            setLoading(false);
        }
    };

    const classCount = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type)).length;
    const instanceCount = graphNodes.filter((n) => (n.data || {}).type === 'owl:NamedIndividual').length;

    // 与实例探索同规则：类节点常显；实例仅在其所属类被右键展开后显示
    const { displayNodes, displayEdges } = useMemo(() => {
        const classToInstances = new Map<string, string[]>();
        const instanceToClass = new Map<string, string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            const isTypeEdge = label === 'rdf:type' || e.data?.relation === 'instance_of';
            if (isTypeEdge && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                if (!classToInstances.has(classId)) classToInstances.set(classId, []);
                classToInstances.get(classId)!.push(String(e.source));
                instanceToClass.set(String(e.source), classId);
            }
        });
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退挂到类
        const classIdByLabel = new Map<string, string>();
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (CLASS_TYPES.has(d.type) && d.label) classIdByLabel.set(String(d.label), String(n.id));
        });
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (d.type === 'owl:NamedIndividual' && !instanceToClass.has(String(n.id))) {
                const cid = classIdByLabel.get(String(d.class_label || ''));
                if (cid) {
                    instanceToClass.set(String(n.id), cid);
                    if (!classToInstances.has(cid)) classToInstances.set(cid, []);
                    classToInstances.get(cid)!.push(String(n.id));
                }
            }
        });
        const visible = new Set<string>();
        graphNodes.forEach((n) => {
            const t = (n.data || {}).type;
            if (CLASS_TYPES.has(t)) {
                visible.add(n.id);
                if (expandedNodeIds.has(n.id)) {
                    (classToInstances.get(n.id) || []).forEach((iid) => visible.add(iid));
                }
            } else if (t === 'owl:NamedIndividual') {
                const pid = instanceToClass.get(n.id);
                if (pid && expandedNodeIds.has(pid)) visible.add(n.id);
            } else {
                visible.add(n.id);
            }
        });
        return {
            displayNodes: graphNodes.filter((n) => visible.has(n.id)),
            displayEdges: graphEdges.filter((e) => visible.has(String(e.source)) && visible.has(String(e.target))),
        };
    }, [graphNodes, graphEdges, expandedNodeIds]);

    // ── 左侧列表树（与实例探索同款：类 → 实例）──
    const buildTreeData = useCallback(() => {
        const classNodes = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type));
        const classToInstances: Record<string, any[]> = {};
        const linkedInstances = new Set<string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            if ((label === 'rdf:type' || e.data?.relation === 'instance_of') && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                (classToInstances[classId] = classToInstances[classId] || []).push(e.source);
                linkedInstances.add(String(e.source));
            }
        });
        const classIdByLabel: Record<string, string> = {};
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (CLASS_TYPES.has(d.type) && d.label) classIdByLabel[String(d.label)] = String(n.id);
        });
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (d.type === 'owl:NamedIndividual' && !linkedInstances.has(String(n.id))) {
                const cid = classIdByLabel[String(d.class_label || '')];
                if (cid) (classToInstances[cid] = classToInstances[cid] || []).push(n.id);
            }
        });
        const q = treeSearchValue.trim().toLowerCase();
        const nodes = classNodes.map((classNode) => {
            const title = classNode.data?.label || '未命名类';
            const children = (classToInstances[classNode.id] || []).map((iid) => {
                const inst = graphNodes.find((n) => n.id === iid);
                return {
                    title: inst?.data?.label || '未命名实例',
                    key: iid,
                    icon: <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ background: '#5B8DEF' }} />,
                    isLeaf: true,
                    searchableTitle: inst?.data?.label || '未命名实例',
                };
            });
            return { title, key: classNode.id, icon: <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ background: '#B585F2' }} />, children, searchableTitle: title };
        });
        if (!q) return nodes;
        return nodes
            .map((n: any) => {
                const children = (n.children || []).filter((c: any) => c.searchableTitle?.toLowerCase().includes(q));
                const self = n.searchableTitle?.toLowerCase().includes(q);
                if (!self && children.length === 0) return null;
                return { ...n, children: self ? n.children : children };
            })
            .filter(Boolean);
    }, [graphNodes, graphEdges, treeSearchValue]);

    const onTreeSelect: TreeProps['onSelect'] = (selectedKeys) => {
        if (selectedKeys.length === 0) return;
        setSelectedEdge(null);
        setSelectedNode({ type: 'node', id: selectedKeys[0] as string });
    };

    const toggleExpand = (node: any) => {
        const classId = node.id;
        setExpandedNodeIds((prev) => {
            const next = new Set(prev);
            if (next.has(classId)) {
                next.delete(classId);
                message.info(`已收起 "${node.data?.label}" 的实例`);
            } else {
                next.add(classId);
                message.success(`已展开 "${node.data?.label}" 的实例`);
            }
            return next;
        });
    };

    const expandAll = () => {
        const classIdsWithInstances = new Set(
            graphEdges
                .filter((e) => (e.label === 'rdf:type' || e.data?.relation === 'instance_of')
                    && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type))
                .map((e) => String(e.target))
        );
        setExpandedNodeIds(new Set(classIdsWithInstances));
        message.success(classIdsWithInstances.size > 0 ? `已展开 ${classIdsWithInstances.size} 个类的实例` : '当前没有实例节点');
    };

    const collapseAll = () => {
        setExpandedNodeIds(new Set());
        message.info('已收起所有实例');
    };

    // DetailDrawer 导航/聚焦用的是行表 id（数字或 URI），画布节点 id 存于实体 uri 尾段 → 解析回画布并聚焦
    const focusCanvasNode = useCallback(async (rowId: string, opts?: { expand?: boolean }) => {
        if (!projectId) return;
        try {
            const d = await graphApi.nodeDetail(Number(projectId), rowId);
            const uri = d.entity?.uri || '';
            if (!uri.includes(':entity/')) return;
            const canvasId = decodeURIComponent(uri.split(':entity/')[1]);
            const node = graphNodes.find((n) => n.id === canvasId);
            if (node?.data?.type === 'owl:NamedIndividual') {
                const typeEdge = graphEdges.find((e) => String(e.source) === canvasId
                    && (e.label === 'rdf:type' || e.data?.relation === 'instance_of'));
                if (typeEdge && !expandedNodeIds.has(String(typeEdge.target))) {
                    setExpandedNodeIds((prev) => new Set(prev).add(String(typeEdge.target)));
                }
            }
            if (opts?.expand && node && node.data?.type !== 'owl:NamedIndividual') {
                setExpandedNodeIds((prev) => new Set(prev).add(canvasId));
            }
            setHighlightNodeId(canvasId);
            setNeighborhoodPinId(canvasId);
            focusSeqRef.current += 1;
            setFocusRequest({ nodeId: canvasId, seq: focusSeqRef.current });
        } catch { /* 聚焦失败不阻断详情浏览 */ }
    }, [projectId, graphNodes, graphEdges, expandedNodeIds]);

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
        <div className="h-screen flex flex-col" style={{ background: '#0F1420' }}>
            <Navbar breadcrumbs={breadcrumbs} />
            <div className="flex-1 flex relative overflow-hidden">
                {/* 左侧信息面板 - 固定宽度 */}
                <div className="w-[420px] bg-white border-r border-gray-200 p-6 overflow-y-auto flex-shrink-0">
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
                                    <span className="font-semibold text-blue-600">{graphNodes.length}</span>
                                </Descriptions.Item>
                                <Descriptions.Item label="关系数量">
                                    <span className="font-semibold text-purple-600">{graphEdges.length}</span>
                                </Descriptions.Item>
                            </Descriptions>
                        </Card>
                        <Card title="创建者" size="small">
                            <div className="flex items-center space-x-2">
                                <UserOutlined className="text-gray-400" />
                                <span className="text-gray-700">{project.owner?.username || '未知'}</span>
                            </div>
                        </Card>
                        {project.domain && (
                            <Card title="知识域" size="small">
                                <div className="flex items-start space-x-2">
                                    <DatabaseOutlined className="text-indigo-500 mt-0.5 flex-shrink-0" />
                                    <div className="flex-1">
                                        <div className="font-medium text-gray-800">{project.domain.name}</div>
                                        {project.domain.description && (
                                            <div className="text-xs text-gray-500 mt-1">{project.domain.description}</div>
                                        )}
                                    </div>
                                </div>
                            </Card>
                        )}
                        {graphNodes.length > 0 && (
                            <Card title="节点类型分布" size="small">
                                {(() => {
                                    const typeCount: Record<string, number> = {};
                                    graphNodes.forEach((node: any) => {
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
                            <Button block onClick={() => setExportOpen(true)} icon={<ExportOutlined />}>
                                导出图谱…
                            </Button>
                        </div>
                    </div>
                </div>

                {/* 右侧图谱展示区（与实例探索一致的深色展示） */}
                <div className="flex-1 flex flex-col min-w-0 relative h-full" style={{ background: '#0F1420' }}>
                    {/* 返回按钮（悬浮于深色画布左上） */}
                    <div className="absolute top-3 left-3 z-30">
                        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/asset-center')}
                            style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#2A3550', color: '#E6EAF2' }}>
                            返回资产中心
                        </Button>
                    </div>

                    <div className="flex-1 flex overflow-hidden">
                        {/* 左侧展开面板（与实例探索同款） */}
                        <div
                            className={`transition-all duration-300 ease-in-out flex-shrink-0 relative ${
                                isLeftPanelExpanded ? 'w-[380px] shadow-lg' : 'w-0'
                            }`}
                            style={{
                                background: '#131A2A',
                                boxShadow: isLeftPanelExpanded ? '4px 0 12px rgba(0, 0, 0, 0.35)' : 'none',
                                borderRight: isLeftPanelExpanded ? '1px solid #1A2233' : 'none',
                            }}
                        >
                            {isLeftPanelExpanded && (
                                <button
                                    className="absolute top-1/2 -translate-y-1/2 z-[100] shadow-md rounded-r-lg p-2 transition-all duration-300"
                                    style={{ right: '-32px', background: '#1A2233', color: '#E6EAF2', border: '1px solid #2A3550', borderLeft: 'none' }}
                                    onClick={() => setIsLeftPanelExpanded(false)}
                                    title="收起列表"
                                >
                                    <LeftOutlined />
                                </button>
                            )}

                            <div className="h-full flex flex-col overflow-hidden" style={{ minWidth: isLeftPanelExpanded ? '380px' : '0' }}>
                                <div className="p-3 border-b flex-shrink-0" style={{ borderColor: '#1A2233' }}>
                                    <div className="flex items-center justify-between mb-2">
                                        <h3 className="font-semibold flex items-center text-sm" style={{ color: '#E6EAF2' }}>
                                            <UnorderedListOutlined className="mr-2" style={{ color: '#8B94AB' }} />
                                            类与实例列表
                                        </h3>
                                        <div className="flex items-center gap-1">
                                            <Tooltip title="全部展开">
                                                <Button type="text" size="small" icon={<ExpandOutlined />}
                                                    onClick={() => setManualExpandedKeys(new Set(buildTreeData().map((n: any) => n.key)))}
                                                    style={{ color: '#C6CEDF' }} />
                                            </Tooltip>
                                            <Tooltip title="全部收起">
                                                <Button type="text" size="small" icon={<ShrinkOutlined />}
                                                    onClick={() => setManualExpandedKeys(new Set())}
                                                    style={{ color: '#C6CEDF' }} />
                                            </Tooltip>
                                        </div>
                                    </div>
                                    <Input
                                        placeholder="搜索类或实例..."
                                        size="small"
                                        value={treeSearchValue}
                                        onChange={(e) => setTreeSearchValue(e.target.value)}
                                        allowClear
                                        prefix={<SearchOutlined className="text-gray-400" />}
                                    />
                                </div>
                                <div className="flex-1 overflow-auto p-2">
                                    <style>{`
                                        .asset-inst-tree, .asset-inst-tree .ant-tree-list, .asset-inst-tree .ant-tree-list-holder,
                                        .asset-inst-tree .ant-tree-list-holder-inner { background: transparent !important; }
                                        .asset-inst-tree .ant-tree-treenode { padding: 2px 0; color: #E6EAF2; }
                                        .asset-inst-tree .ant-tree-node-content-wrapper { padding: 2px 8px; min-height: 24px; line-height: 20px; color: #E6EAF2; }
                                        .asset-inst-tree .ant-tree-node-content-wrapper:hover { background: rgba(91, 141, 239, 0.15) !important; border-radius: 4px; }
                                        .asset-inst-tree .ant-tree-node-content-wrapper-selected { background: rgba(91, 141, 239, 0.28) !important; }
                                        .asset-inst-tree .ant-tree-switcher { width: 20px; height: 24px; line-height: 24px; }
                                    `}</style>
                                    <Tree
                                        showIcon
                                        expandedKeys={Array.from(manualExpandedKeys)}
                                        onExpand={(keys) => setManualExpandedKeys(new Set(keys as string[]))}
                                        selectedKeys={selectedNode ? [selectedNode.id] : []}
                                        onSelect={onTreeSelect}
                                        treeData={buildTreeData() as any}
                                        blockNode
                                        className="asset-inst-tree"
                                    />
                                </div>
                                <div className="p-3 border-t flex-shrink-0" style={{ borderColor: '#1A2233', background: 'rgba(15, 20, 32, 0.6)' }}>
                                    <div className="flex items-center justify-between text-xs" style={{ color: '#8B94AB' }}>
                                        <span className="flex items-center">
                                            <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#B585F2' }} />
                                            类：{classCount}
                                        </span>
                                        <span className="flex items-center">
                                            <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#5B8DEF' }} />
                                            实例：{instanceCount}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* 主内容区 */}
                        <div className="flex-1 flex flex-col min-w-0 relative h-full">
                            {/* 画布悬浮工具条（与实例探索同款） */}
                            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
                                <div
                                    className="flex items-center gap-1 px-2 py-1.5 rounded-lg border"
                                    style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#1A2233', backdropFilter: 'blur(6px)' }}
                                >
                                    <Tooltip title={Array.from(expandedNodeIds).length > 0 ? '收起所有实例' : '展开所有实例'}>
                                        <Button
                                            size="small" type="primary" ghost
                                            icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />}
                                            onClick={Array.from(expandedNodeIds).length > 0 ? collapseAll : expandAll}
                                        >
                                            展开/收起实例
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title="类与实例列表">
                                        <Button size="small" type="primary" ghost icon={<UnorderedListOutlined />} onClick={() => setIsLeftPanelExpanded((v) => !v)}>
                                            列表
                                        </Button>
                                    </Tooltip>
                                </div>
                            </div>

                            {/* 画布 */}
                            <div className="flex-1 relative min-h-0">
                                <div className="absolute inset-0">
                                    <D3ForceGraph
                                        theme="dark"
                                        nodes={displayNodes}
                                        edges={displayEdges}
                                        onNodeClick={(node) => {
                                            setSelectedEdge(null);
                                            setNeighborhoodPinId(null);
                                            setHighlightNodeId(null);
                                            setSelectedNode(node ? { type: 'node', id: node.id } : null);
                                        }}
                                        onEdgeClick={(edge) => {
                                            setSelectedNode(null);
                                            setSelectedEdge(edge);
                                        }}
                                        onNodeRightClick={toggleExpand}
                                        highlightNodeId={highlightNodeId}
                                        neighborhoodPinId={neighborhoodPinId}
                                        focusRequest={focusRequest}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* 节点详情：与实例探索一致的 DetailDrawer（只读展示） */}
            <DetailDrawer
                projectId={Number(projectId)}
                selection={selectedNode}
                onClose={() => setSelectedNode(null)}
                onNavigateNode={(nid) => {
                    setSelectedNode({ type: 'node', id: nid });
                    focusCanvasNode(nid);
                }}
                onExpandNode={(nid) => focusCanvasNode(nid)}
            />

            {/* 边详情：轻量弹窗（只读） */}
            {selectedEdge && (
                <Modal
                    title={`关系 - ${selectedEdge.data?.label || selectedEdge.data?.relation || '未命名'}`}
                    open
                    onCancel={() => setSelectedEdge(null)}
                    footer={null}
                    width={420}
                >
                    <div className="space-y-2 text-sm">
                        <div>源头：{String(selectedEdge.source)}</div>
                        <div>目标：{String(selectedEdge.target)}</div>
                        {selectedEdge.data?.confidence != null && (
                            <div>置信度：{Number(selectedEdge.data.confidence).toFixed(2)}</div>
                        )}
                    </div>
                </Modal>
            )}

            <ExportDialog
                open={exportOpen}
                onClose={() => setExportOpen(false)}
                projectId={Number(projectId)}
                title="导出图谱（资产中心）"
            />
        </div>
    );
};

export default AssetDetailPage;
