/**
 * 本体图谱查看页（2026-11-05 改版）：`/projects/:id/result` —— 只读图谱。
 * 构建结果明细（类/关系/实例/公理）已在项目详情页直接展示，本页专注图谱呈现：
 * D3 力导图（类常显、右键类节点展开实例、点击节点/边看详情）。
 * 头部保留：返回 / 机检与自检标签 / 专家精修 / 取消发布 / 发布到资产中心。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { message } from '../shared/feedback';
import { Button, Input, Modal, Space, Spin, Tag, Tooltip, Tree } from 'antd';
import type { TreeProps } from 'antd/es/tree';
import {
    ArrowLeftOutlined, CloudUploadOutlined, EditOutlined, ExpandOutlined,
    LeftOutlined, SearchOutlined, ShrinkOutlined, UnorderedListOutlined,
} from '@ant-design/icons';

import { pipelineApi } from '../api/pipeline';
import { projectsApi } from '../api/projects';
import { useAuthStore } from '../shared/auth/authStore';
import { useBuilderStore } from '../features/builder/store';
import QualityCheckTag from '../features/builder/components/QualityCheckTag';
import D3ForceGraph from '../components/OntologyGraph/D3ForceGraph';
import DetailDrawer from '../features/graph-explorer/DetailDrawer';

const CLASS_TYPES = new Set(['owl:Class', 'Class']);

const BuildResultPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const pid = Number(projectId);
    const { projectName } = useBuilderStore();
    const { hasModule } = useAuthStore();
    const isExpert = hasModule('builder_pro');

    const [summary, setSummary] = useState<import('../api/pipeline').BuildSummary | null>(null);
    const [loading, setLoading] = useState(true);

    // 只读图谱（数据源 = 项目 graph_data）
    const [graphNodes, setGraphNodes] = useState<any[]>([]);
    const [graphEdges, setGraphEdges] = useState<any[]>([]);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [selectedNode, setSelectedNode] = useState<{ type: 'node'; id: string } | null>(null);
    const [selectedEdge, setSelectedEdge] = useState<any>(null);
    // 类/实例列表树（与资产中心同款）+ 展开收起所有实例
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    const [treeSearchValue, setTreeSearchValue] = useState('');
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setSummary(await pipelineApi.buildSummary(pid));
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载构建结果失败');
        } finally {
            setLoading(false);
        }
        try {
            const data = await projectsApi.getProject(pid);
            setGraphNodes(data.graph_data?.nodes || []);
            setGraphEdges(data.graph_data?.edges || []);
        } catch { /* 图谱数据失败不阻塞 */ }
    }, [pid]);

    useEffect(() => { load(); }, [load]);

    // 与资产中心/实例探索同规则：类节点常显；实例仅在其所属类被展开后显示
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
                visible.add(String(n.id));
                if (expandedNodeIds.has(String(n.id))) {
                    (classToInstances.get(String(n.id)) || []).forEach((iid) => visible.add(iid));
                }
            } else if (t === 'owl:NamedIndividual') {
                const owner = instanceToClass.get(String(n.id));
                if (owner && expandedNodeIds.has(owner)) visible.add(String(n.id));
            }
        });
        return {
            displayNodes: graphNodes.filter((n) => visible.has(String(n.id))),
            displayEdges: graphEdges.filter((e) => visible.has(String(e.source))
                && visible.has(String(e.target))),
        };
    }, [graphNodes, graphEdges, expandedNodeIds]);

    // ── 左侧列表树（与资产中心同款：类 → 实例）──
    const buildTreeData = useCallback(() => {
        const classNodes = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type));
        const classToInstances: Record<string, string[]> = {};
        const linkedInstances = new Set<string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            if ((label === 'rdf:type' || e.data?.relation === 'instance_of')
                && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                (classToInstances[classId] = classToInstances[classId] || []).push(String(e.source));
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
                if (cid) (classToInstances[cid] = classToInstances[cid] || []).push(String(n.id));
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

    const expandAllInstances = () => {
        const classIdsWithInstances = new Set(
            graphEdges
                .filter((e) => (e.label === 'rdf:type' || e.data?.relation === 'instance_of')
                    && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type))
                .map((e) => String(e.target))
        );
        setExpandedNodeIds(new Set(classIdsWithInstances));
        message.success(classIdsWithInstances.size > 0 ? `已展开 ${classIdsWithInstances.size} 个类的实例` : '当前没有实例节点');
    };

    const collapseAllInstances = () => {
        setExpandedNodeIds(new Set());
        message.info('已收起所有实例');
    };

    if (loading || !summary) {
        return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
    }

    const sc = summary.pipeline?.selfcheck;

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col">
            <div className="flex items-center gap-3 px-4 py-2 bg-white border-b border-gray-200">
                <Button icon={<ArrowLeftOutlined />} size="small"
                        onClick={() => navigate(`/projects/${pid}`)}>返回</Button>
                <span className="font-semibold text-gray-800">本体图谱 · {projectName || `#${pid}`}</span>
                {summary.checks && <QualityCheckTag checks={summary.checks} />}
                {sc && (
                    <Tag color="geekblue">
                        自检修复 {sc.rounds} 轮 · 修复 {sc.fixed} 处
                        {sc.remaining > 0 ? ` · 剩余 ${sc.remaining} 硬伤` : ''}
                    </Tag>
                )}
                <div className="ml-auto">
                    <Space>
                        {isExpert && (
                            <Button icon={<EditOutlined />}
                                    onClick={() => navigate(`/projects/${pid}/schema`)}>
                                专家精修
                            </Button>
                        )}
                        {summary.is_published && (
                            <Button onClick={async () => {
                                try {
                                    await projectsApi.unpublishProject(pid);
                                    message.success('已取消发布（资产中心不再展示）');
                                    load();
                                } catch (e: any) {
                                    message.error(e.response?.data?.error?.message || '取消发布失败');
                                }
                            }}>
                                取消发布
                            </Button>
                        )}
                        <Button type="primary" icon={<CloudUploadOutlined />}
                                onClick={() => navigate(`/projects/${pid}?stage=publish`)}>
                            发布到资产中心
                        </Button>
                    </Space>
                </div>
            </div>

            <div className="flex-1 p-3">
                <div className="bg-gray-900 rounded-lg relative flex overflow-hidden" style={{ height: 'calc(100vh - 110px)', minHeight: 480 }}>
                    {/* 左侧类/实例列表（与资产中心同款，可收起） */}
                    <div
                        className={`transition-all duration-300 ease-in-out flex-shrink-0 relative ${
                            isLeftPanelExpanded ? 'w-[340px] shadow-lg' : 'w-0'
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
                        <div className="h-full flex flex-col overflow-hidden" style={{ minWidth: isLeftPanelExpanded ? '340px' : '0' }}>
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
                                    prefix={<SearchOutlined className="text-gray-500" />}
                                />
                            </div>
                            <div className="flex-1 overflow-auto p-2">
                                <style>{`
                                    .result-inst-tree, .result-inst-tree .ant-tree-list, .result-inst-tree .ant-tree-list-holder,
                                    .result-inst-tree .ant-tree-list-holder-inner { background: transparent !important; }
                                    .result-inst-tree .ant-tree-treenode { padding: 2px 0; color: #E6EAF2; }
                                    .result-inst-tree .ant-tree-node-content-wrapper { padding: 2px 8px; min-height: 24px; line-height: 20px; color: #E6EAF2; }
                                    .result-inst-tree .ant-tree-node-content-wrapper:hover { background: rgba(91, 141, 239, 0.15) !important; border-radius: 4px; }
                                    .result-inst-tree .ant-tree-node-content-wrapper-selected { background: rgba(91, 141, 239, 0.28) !important; }
                                    .result-inst-tree .ant-tree-switcher { width: 20px; height: 24px; line-height: 24px; }
                                `}</style>
                                <Tree
                                    showIcon
                                    expandedKeys={Array.from(manualExpandedKeys)}
                                    onExpand={(keys) => setManualExpandedKeys(new Set(keys as string[]))}
                                    selectedKeys={selectedNode ? [selectedNode.id] : []}
                                    onSelect={onTreeSelect}
                                    treeData={buildTreeData() as any}
                                    blockNode
                                    className="result-inst-tree"
                                />
                            </div>
                            <div className="p-3 border-t flex-shrink-0" style={{ borderColor: '#1A2233', background: 'rgba(15, 20, 32, 0.6)' }}>
                                <div className="flex items-center justify-between text-xs" style={{ color: '#8B94AB' }}>
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#B585F2' }} />
                                        类：{summary.schema.classes_total}
                                    </span>
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#5B8DEF' }} />
                                        实例：{summary.instances.total}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 画布区 */}
                    <div className="flex-1 relative min-w-0">
                        {/* 悬浮工具条：展开/收起所有实例 + 列表 */}
                        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
                            <div className="flex items-center gap-1 px-2 py-1.5 rounded-lg border"
                                 style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#1A2233', backdropFilter: 'blur(6px)' }}>
                                <Tooltip title={Array.from(expandedNodeIds).length > 0 ? '收起所有实例' : '展开所有实例'}>
                                    <Button size="small" type="primary" ghost
                                            icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />}
                                            onClick={Array.from(expandedNodeIds).length > 0 ? collapseAllInstances : expandAllInstances}>
                                        展开/收起实例
                                    </Button>
                                </Tooltip>
                                <Tooltip title="类与实例列表">
                                    <Button size="small" type="primary" ghost icon={<UnorderedListOutlined />}
                                            onClick={() => setIsLeftPanelExpanded((v) => !v)}>
                                        列表
                                    </Button>
                                </Tooltip>
                            </div>
                        </div>
                        <div className="absolute inset-0">
                        <D3ForceGraph
                            theme="dark"
                            nodes={displayNodes}
                            edges={displayEdges}
                            onNodeClick={(node: any) => {
                                setSelectedEdge(null);
                                setSelectedNode(node ? { type: 'node', id: node.id } : null);
                            }}
                            onEdgeClick={(edge: any) => {
                                setSelectedNode(null);
                                setSelectedEdge(edge);
                            }}
                            onNodeRightClick={(node: any) => {
                                if (!node) return;
                                const nid = String(node.id);
                                setExpandedNodeIds((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(nid)) {
                                        next.delete(nid);
                                    } else {
                                        next.add(nid);
                                    }
                                    return next;
                                });
                            }}
                        />
                    <div className="absolute bottom-3 left-3 text-xs text-gray-400 bg-gray-800/80 rounded px-2 py-1 pointer-events-none">
                        只读 · 右键类节点展开/收起实例 · 点击节点查看详情
                    </div>
                    <DetailDrawer
                        projectId={pid}
                        selection={selectedNode}
                        onClose={() => setSelectedNode(null)}
                        onNavigateNode={(nid: string) => setSelectedNode({ type: 'node', id: nid })}
                        onExpandNode={(nid: string) => {
                            setExpandedNodeIds((prev) => {
                                const next = new Set(prev);
                                next.add(String(nid));
                                return next;
                            });
                        }}
                    />
                    {selectedEdge && (
                        <Modal
                            title={`关系 - ${selectedEdge.data?.label || selectedEdge.data?.relation || '未命名'}`}
                            open onCancel={() => setSelectedEdge(null)}
                            footer={<Button onClick={() => setSelectedEdge(null)}>关闭</Button>}
                        >
                            <div className="text-sm space-y-1">
                                <div>起点：{graphNodes.find((n) => n.id === selectedEdge.source)?.data?.label || selectedEdge.source}</div>
                                <div>终点：{graphNodes.find((n) => n.id === selectedEdge.target)?.data?.label || selectedEdge.target}</div>
                                {selectedEdge.data?.source_quote && (
                                    <div className="text-xs text-gray-500">证据：{selectedEdge.data.source_quote}</div>
                                )}
                            </div>
                        </Modal>
                    )}
                        </div>
                    </div>
                </div>
            </div>

        </div>
    );
};

export default BuildResultPage;
