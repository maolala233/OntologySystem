/**
 * Sigma.js 3 图探索器（06 册 M4-a/b）：分页装载 + FA2/网格布局 + LOD + 深色主题 +
 * 语义着色 + 图例/过滤/搜索 + ★节点/边详情抽屉。构建器实例探索 Tab 与资产详情复用。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Empty, Input, Select, Space, Spin, Tag, Tooltip, message } from 'antd';
import { AimOutlined, BgColorsOutlined, ExpandAltOutlined, ReloadOutlined } from '@ant-design/icons';
import Graph from 'graphology';
import Sigma from 'sigma';

import { GraphMeta } from '../../api/graphview';
import { GRAPH_THEME, SEMANTIC_PALETTE, confidenceBand, groupColor } from './colors';
import DetailDrawer from './DetailDrawer';
import { ColorField, gridLayout, loadGraph } from './useGraphData';

interface Props {
    projectId: number;
    /** 抽取/消解/冲突完成后外部改 key 触发重载 */
    refreshKey?: number;
    onChanged?: () => void;
}

const KIND_LABEL: Record<string, string> = {
    class: '类', instance: '实例',
};

const GraphExplorer: React.FC<Props> = ({ projectId, refreshKey = 0, onChanged }) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const sigmaRef = useRef<Sigma | null>(null);
    const graphRef = useRef<Graph | null>(null);
    const metaRef = useRef<GraphMeta | null>(null);

    const [loading, setLoading] = useState(true);
    const [meta, setMeta] = useState<GraphMeta | null>(null);
    const [colorField, setColorField] = useState<ColorField>('class');
    const [classFilter, setClassFilter] = useState<string | undefined>();
    const [statusFilter, setStatusFilter] = useState<string | undefined>();
    const [searchHit, setSearchHit] = useState<string | null>(null);
    const [selected, setSelected] = useState<{ type: 'node' | 'edge'; id: string } | null>(null);
    const [visibleGroups, setVisibleGroups] = useState<Record<string, boolean>>({});

    // ── 着色：信息熵默认选 class（06 §6.2；前端简化为固定 class/置信分带切换）──
    const rebuildColors = useCallback((graph: Graph, field: ColorField) => {
        graph.forEachNode((n, attr) => {
            const isClass = attr.kind === 'class';
            const base = isClass ? GRAPH_THEME.classNode
                : field === 'confidence' ? groupColor(confidenceBand(attr.confidence))
                : groupColor(attr.group || '未分类');
            graph.setNodeAttribute(n, 'color', base);
        });
    }, []);

    // ── 装载 + 渲染 ──
    const load = useCallback(async () => {
        setLoading(true);
        try {
            const { graph, meta: m } = await loadGraph(projectId, {
                colorField,
                filters: { class: classFilter, status: statusFilter },
            });
            graphRef.current = graph;
            metaRef.current = m;
            setMeta(m);
            const groups: Record<string, boolean> = {};
            graph.forEachNode((n, attr) => { groups[attr.group] = true; });
            setVisibleGroups(groups);

            if (sigmaRef.current) {
                sigmaRef.current.kill();
                sigmaRef.current = null;
            }
            if (!containerRef.current) return;
            if (graph.order === 0) return;

            const renderer = new Sigma(graph, containerRef.current, {
                allowInvalidContainer: true,
                defaultEdgeType: 'arrow',
                    labelDensity: 4,
                    labelGridCellSize: 90,
                    labelRenderedSizeThreshold: 8,
                    renderEdgeLabels: false,
                    edgeLabelSize: 10,
                    minCameraRatio: 0.05,
                    maxCameraRatio: 10,
                    nodeReducer: (node: string, data: any) => {
                        const res = { ...data, ...({ highlighted: data.highlighted || false } as any) };
                        const s = sigmaRef.current;
                        const hovered = (s as any)?.__hovered;
                        const selectedNode = (s as any)?.__selectedNode;
                        if (selectedNode && selectedNode !== node) {
                            // 邻域高亮：非邻域淡出
                            const graph2 = s?.getGraph();
                            if (graph2 && !graph2.areNeighbors(selectedNode, node) && node !== selectedNode) {
                                res.color = GRAPH_THEME.grid;
                                res.label = null;
                                res.zIndex = 0;
                            } else {
                                res.forceLabel = true;
                                res.zIndex = 2;
                            }
                        }
                        if (hovered && hovered !== node && selectedNode !== node) {
                            const graph2 = s?.getGraph();
                            if (graph2 && graph2.hasNode(hovered) && !graph2.areNeighbors(hovered, node)) {
                                res.color = GRAPH_THEME.grid;
                                res.label = null;
                            }
                        }
                        if (node === (s as any)?.__searchHit) {
                            res.color = GRAPH_THEME.selected;
                            res.forceLabel = true;
                            res.zIndex = 3;
                        }
                        return res;
                    },
                    edgeReducer: (edge: string, data: any) => {
                        const res: any = { ...data };
                        const s = sigmaRef.current;
                        const camera = s?.getCamera();
                        const ratio = camera ? camera.ratio : 1;
                        // LOD：structure 档以下类型边淡出（06 §5）
                        if (data.kind === 'type' && ratio < 0.5) {
                            res.hidden = true;
                            return res;
                        }
                        const hovered = (s as any)?.__hovered;
                        const selectedNode = (s as any)?.__selectedNode;
                        if (selectedNode || hovered) {
                            const focus = selectedNode || hovered;
                            const graph2 = s?.getGraph();
                            if (graph2) {
                                try {
                                    const extremity = graph2.extremities(edge);
                                    if (!extremity.includes(focus)) {
                                        res.color = GRAPH_THEME.grid;
                                        res.label = null;
                                        res.forceLabel = false;
                                    } else {
                                        res.color = GRAPH_THEME.edgeHighlight;
                                        res.forceLabel = true;
                                        res.size = 2;
                                    }
                                } catch { /* 边端点缺失 */ }
                            }
                        }
                        return res;
                    },
            });
            sigmaRef.current = renderer;
            // 事件挂载（hover/选中）
            (renderer as any).__hovered = null;
            (renderer as any).__selectedNode = null;
            renderer.on('enterNode', ({ node }) => {
                (renderer as any).__hovered = node;
                renderer.refresh();
            });
            renderer.on('leaveNode', () => {
                (renderer as any).__hovered = null;
                renderer.refresh();
            });
            renderer.on('clickNode', ({ node }) => {
                (renderer as any).__selectedNode = node;
                renderer.refresh();
                setSelected({ type: 'node', id: node });
            });
            renderer.on('clickEdge', ({ edge }) => {
                setSelected({ type: 'edge', id: edge });
            });
            renderer.on('clickStage', () => {
                (renderer as any).__selectedNode = null;
                renderer.refresh();
            });
            rebuildColors(graph, colorField);
            renderer.refresh();
            renderer.getCamera().animate({ ratio: 0.9, x: 0.5, y: 0.5 }, { duration: 300 });
        } catch (e) {
            console.error('[graph-explorer] 装载失败', e);
        } finally {
            setLoading(false);
        }
    }, [projectId, colorField, classFilter, statusFilter, rebuildColors]);

    useEffect(() => {
        load();
        return () => {
            if (sigmaRef.current) {
                sigmaRef.current.kill();
                sigmaRef.current = null;
            }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, colorField, classFilter, statusFilter, refreshKey]);

    // ── 交互 ──
    const handleSearch = useCallback(async (value: string) => {
        if (!value.trim()) {
            setSearchHit(null);
            return;
        }
        const { graphApi } = await import('../../api/graphview');
        const r = await graphApi.search(projectId, value.trim(), classFilter);
        const renderer = sigmaRef.current;
        const graph = graphRef.current;
        if (r.items.length > 0 && renderer && graph && graph.hasNode(r.items[0].id)) {
            setSearchHit(r.items[0].id);
            (renderer as any).__searchHit = r.items[0].id;
            (renderer as any).__selectedNode = r.items[0].id;
            renderer.refresh();
            renderer.getCamera().animate({ x: 0.5, y: 0.5, ratio: 0.3 }, { duration: 400 });
            setSelected({ type: 'node', id: r.items[0].id });  // 搜索命中即打开详情
        } else {
            setSearchHit(null);
            if (renderer) {
                (renderer as any).__searchHit = null;
                renderer.refresh();
            }
            message.info('未找到匹配节点');
        }
    }, [projectId, classFilter]);

    const expandNeighbors = useCallback(async (nodeId: string) => {
        const { graphApi } = await import('../../api/graphview');
        const r = await graphApi.neighbors(projectId, nodeId, 1);
        const graph = graphRef.current;
        const renderer = sigmaRef.current;
        if (!graph || !renderer) return;
        let added = 0;
        for (const n of r.nodes) {
            if (graph.hasNode(n.id)) continue;
            const { x, y } = graph.getNodeAttributes(nodeId);
            graph.addNode(n.id, {
                label: n.label, kind: n.kind, group: n.group, degree: n.degree,
                status: n.status, confidence: n.confidence,
                size: Math.max(5, Math.min(14, 5 + n.degree * 0.8)),
                color: n.kind.startsWith('class') ? GRAPH_THEME.classNode : groupColor(n.group || '未分类'),
                x: x + (Math.random() - 0.5) * 2,
                y: y + (Math.random() - 0.5) * 2,
            });
            added += 1;
        }
        for (const e of r.edges) {
            if (graph.hasNode(e.source) && graph.hasNode(e.target) && !graph.hasEdge(e.id)) {
                graph.addDirectedEdgeWithKey(e.id, e.source, e.target, {
                    label: e.label, kind: e.kind, size: 1,
                    color: GRAPH_THEME.edge, weight: e.weight ?? 1,
                });
                added += 1;
            }
        }
        renderer.refresh();
        message.info(`邻域展开完成：新增 ${added} 项`);
    }, [projectId]);

    const relayout = useCallback(() => {
        load();  // 重装载即重新布局（服务端预布局可用时直接取坐标）
    }, [load]);

    const legendItems = useMemo(() => {
        if (!meta) return [];
        return meta.class_distribution.slice(0, 8).map((d, i) => ({
            label: d.class_label, count: d.count, color: SEMANTIC_PALETTE[i % SEMANTIC_PALETTE.length],
        }));
    }, [meta]);

    return (
        <div className="relative" style={{ background: GRAPH_THEME.bg, borderRadius: 8, overflow: 'hidden' }}>
            {/* 工具栏（深色） */}
            <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b"
                 style={{ background: '#131A2A', borderColor: GRAPH_THEME.grid }}>
                <SearchBox onSearch={handleSearch} />
                <Select
                    allowClear placeholder="按类过滤" size="small" style={{ minWidth: 130 }}
                    value={classFilter}
                    onChange={(v) => setClassFilter(v)}
                    options={(meta?.class_distribution || []).map((d) => ({ value: d.class_label, label: `${d.class_label} (${d.count})` }))}
                />
                <Select
                    allowClear placeholder="状态" size="small" style={{ width: 110 }}
                    value={statusFilter}
                    onChange={(v) => setStatusFilter(v)}
                    options={[
                        { value: 'auto', label: 'auto' },
                        { value: 'merged', label: 'merged' },
                        { value: 'pending_review', label: '待审核' },
                        { value: 'approved', label: 'approved' },
                    ]}
                />
                <Tooltip title="切换着色字段（06 §6.2）">
                    <Select
                        size="small" style={{ width: 120 }} value={colorField}
                        onChange={(v) => setColorField(v)}
                        suffixIcon={<BgColorsOutlined />}
                        options={[
                            { value: 'class', label: '按类着色' },
                            { value: 'confidence', label: '按置信分带' },
                        ]}
                    />
                </Tooltip>
                <Button size="small" ghost icon={<ReloadOutlined />} onClick={relayout}>重新布局</Button>
                <Button size="small" ghost icon={<AimOutlined />}
                        onClick={() => sigmaRef.current?.getCamera().animate({ x: 0.5, y: 0.5, ratio: 1 }, { duration: 300 })}>
                    复位视图
                </Button>
                {meta && <Tag className="ml-auto" style={{ background: 'transparent', color: GRAPH_THEME.textDim }}>
                    {meta.node_count} 节点 · {meta.edge_count} 关系
                </Tag>}
            </div>

            {/* 画布 */}
            <div className="relative">
                <div ref={containerRef} style={{ height: 520 }} />
                {loading && (
                    <div className="absolute inset-0 flex items-center justify-center" style={{ background: 'rgba(15,20,32,.7)' }}>
                        <Spin tip="装载图谱中…" />
                    </div>
                )}
                {!loading && (!meta || meta.node_count === 0) && (
                    <div className="absolute inset-0 flex items-center justify-center">
                        <Empty description={<span style={{ color: GRAPH_THEME.textDim }}>图谱为空：先完成实例抽取</span>} />
                    </div>
                )}
                {/* 图例（当前可见分组 Top8，点击=过滤切换） */}
                {legendItems.length > 0 && (
                    <div className="absolute left-2 bottom-2 px-2 py-1.5 rounded"
                         style={{ background: 'rgba(19,26,42,.9)', border: `1px solid ${GRAPH_THEME.grid}` }}>
                        <div className="flex items-center gap-2 flex-wrap max-w-[360px]">
                            {legendItems.map((l) => (
                                <Tooltip key={l.label} title={`${l.label} · ${l.count}`}>
                                    <button
                                        className="flex items-center gap-1 text-xs"
                                        style={{
                                            color: visibleGroups[l.label] === false ? GRAPH_THEME.textDim : GRAPH_THEME.text,
                                            opacity: visibleGroups[l.label] === false ? 0.4 : 1,
                                        }}
                                        onClick={() => {
                                            // 点击图例项 = 按类过滤切换（06 §6.2）
                                            const next = { ...visibleGroups, [l.label]: visibleGroups[l.label] === false };
                                            setVisibleGroups(next);
                                            setClassFilter(visibleGroups[l.label] === false ? undefined : l.label);
                                        }}
                                    >
                                        <span className="inline-block w-2.5 h-2.5 rounded-full"
                                              style={{ background: l.color }} />
                                        {l.label}
                                    </button>
                                </Tooltip>
                            ))}
                        </div>
                    </div>
                )}
                <div className="absolute right-2 bottom-2 text-[10px]" style={{ color: GRAPH_THEME.textDim }}>
                    滚轮缩放 · 拖拽平移 · 点击节点/边查看详情
                </div>
            </div>

            <DetailDrawer
                projectId={projectId}
                selection={selected}
                onClose={() => setSelected(null)}
                onNavigateNode={(nid) => {
                    setSelected({ type: 'node', id: nid });
                    const renderer = sigmaRef.current;
                    const graph = graphRef.current;
                    if (renderer && graph?.hasNode(nid)) {
                        (renderer as any).__selectedNode = nid;
                        renderer.refresh();
                        renderer.getCamera().animate({ x: 0.5, y: 0.5, ratio: 0.4 }, { duration: 300 });
                    }
                }}
                onExpandNode={(nid) => expandNeighbors(nid)}
            />
        </div>
    );
};

/** 搜索框（回车触发画布内搜索 + 聚焦命中节点） */
const SearchBox: React.FC<{ onSearch: (v: string) => void }> = ({ onSearch }) => {
    const [v, setV] = useState('');
    return (
        <Input.Search
            size="small" allowClear style={{ width: 200 }}
            placeholder="搜索节点（回车）"
            value={v}
            onChange={(e) => {
                setV(e.target.value);
                if (!e.target.value) onSearch('');
            }}
            onSearch={(val) => onSearch(val)}
            prefix={<ExpandAltOutlined />}
        />
    );
};

export default GraphExplorer;
