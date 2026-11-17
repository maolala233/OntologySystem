import React, { useEffect, useRef, useState, useCallback } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY } from 'd3-force';
import { select } from 'd3-selection';
import { drag as d3Drag } from 'd3-drag';
import { zoom as d3Zoom, zoomIdentity } from 'd3-zoom';
import { transition } from 'd3-transition';
import { message } from '../../shared/feedback';

import { OntologyNode, OntologyEdge } from '../../types/ontology';

const NODE_TYPES = {
    CLASS: 'owl:Class',
    INDIVIDUAL: 'owl:NamedIndividual',
    PROPERTY: 'owl:ObjectProperty'
};

// 抽取链路写 type='Class'（与后端 graph_rows.CLASS_TYPES 对齐），画布统一归一到 owl:Class，
// 保证配色/半径/形状与左下角图例一致
const normalizeNodeType = (t?: string): string =>
    t === 'Class' ? NODE_TYPES.CLASS : (t || NODE_TYPES.CLASS);

const NODE_COLORS = {
    [NODE_TYPES.CLASS]: { fill: '#4a90d9', stroke: '#2d6cb4', text: '#ffffff', fillOpacity: 1 },
    [NODE_TYPES.INDIVIDUAL]: { fill: '#f79767', stroke: '#d4703f', text: '#ffffff', fillOpacity: 1 },
    [NODE_TYPES.PROPERTY]: { fill: '#c990c0', stroke: '#9e6b96', text: '#ffffff', fillOpacity: 1 },
    DEFAULT: { fill: '#666', stroke: '#444', text: '#ffffff', fillOpacity: 1 }
};

/**
 * 深色档的节点配色（2026-11-17 调）：
 * - 类：径向渐变填充（左上亮、右下深）+ 半透明细描边 —— 描边只负责收边，
 *   原来是 2.5px 的高饱和实心亮紫，和球体渐变不是一种材质，看着像贴了一圈贴纸；
 * - 实例：半透明填充 + 细边 —— 一眼看出它从属于类，而不是和类一样重的色块；
 * - 颜色仍是品牌紫/蓝，只是换了明度分工。
 */
const DARK_NODE_COLORS = {
    [NODE_TYPES.CLASS]: { fill: '#7A4CD6', stroke: 'rgba(168, 124, 243, 0.60)', text: '#FFFFFF', fillOpacity: 1 },
    [NODE_TYPES.INDIVIDUAL]: { fill: '#5B8DEF', stroke: 'rgba(127, 178, 255, 0.55)', text: '#DCE8FF', fillOpacity: 0.32 },
    [NODE_TYPES.PROPERTY]: { fill: '#34C3A4', stroke: 'rgba(95, 224, 192, 0.55)', text: '#D8FFF5', fillOpacity: 0.32 },
    DEFAULT: { fill: '#56679B', stroke: 'rgba(107, 123, 168, 0.55)', text: '#E6EAF2', fillOpacity: 0.35 }
};

const NODE_RADII = {
    [NODE_TYPES.CLASS]: 32,
    [NODE_TYPES.INDIVIDUAL]: 22,
    [NODE_TYPES.PROPERTY]: 20
};

const LIGHT_THEME = {
    background: '#fafbfc',
    text: '#333333',
    edge: '#c0c4cc',
    edgeHighlight: '#909399',
    edgeInstance: '#e0c8b8',
    panel: 'rgba(255, 255, 255, 0.95)',
    panelBorder: '#e5e7eb',
    panelText: '#6b7280',
};

// 深色主题（06 §6.3）：与 graph-explorer 的 GRAPH_THEME 同源，骨架画布与实例探索器视觉统一
const DARK_THEME = {
    background: '#0F1420',
    text: '#E6EAF2',
    edge: '#56679B',
    edgeHighlight: '#F2B950',
    edgeInstance: '#5B8DEF',
    panel: 'rgba(19, 26, 42, 0.95)',
    panelBorder: '#1A2233',
    panelText: '#8B94AB',
};

const EDGE_CLICK_WIDTH = 20;

/** 「节点间距」档位的人话标签（0=紧凑 … 100=宽松）：画布内的卡片与外部工具条共用这一套口径 */
export const spacingLabel = (value: number): string => {
    if (value < 25) return '紧凑';
    if (value < 50) return '较紧';
    if (value === 50) return '标准';
    if (value < 75) return '较松';
    return '宽松';
};

// 边公理摘要（2 期可视化）：悬停标签追加后缀；带公理的边整条换紫色描边
const edgeAxiomSuffix = (data: any): string => {
    const ax = data?.axioms || {};
    const parts: string[] = [];
    if (ax.functional) parts.push('函数');
    if (ax.transitive) parts.push('传递');
    if (ax.symmetric) parts.push('对称');
    if (ax.inverse_of) parts.push(`逆:${ax.inverse_of}`);
    if (data?.min_cardinality != null || data?.max_cardinality != null) {
        parts.push(`[${data?.min_cardinality ?? 0}..${data?.max_cardinality ?? '*'}]`);
    }
    return parts.length ? ' · ' + parts.join(' ') : '';
};

const edgeHasAxiom = (data: any): boolean => {
    const ax = data?.axioms || {};
    return !!(ax.functional || ax.transitive || ax.symmetric || ax.inverse_of ||
        data?.min_cardinality != null || data?.max_cardinality != null);
};

interface D3ForceGraphProps {
    nodes: OntologyNode[];
    edges: OntologyEdge[];
    onNodeClick?: (node: OntologyNode | null) => void;
    onEdgeClick?: (edge: OntologyEdge) => void;
    onNodesChange?: (newNodes: OntologyNode[]) => void;
    onNodeRightClick?: (node: OntologyNode) => void;
    width?: number;
    height?: number;
    className?: string;
    highlightNodeId?: string | null;
    /** 固定邻域高亮（"在画布中聚焦"的持续版悬停效果）；画布点击其他节点时由外部清除 */
    neighborhoodPinId?: string | null;
    /** 聚焦请求：seq 递增触发画布平移缩放定位到节点（详情页"在画布中聚焦/邻域展开"） */
    focusRequest?: { nodeId: string; seq: number } | null;
    theme?: 'light' | 'dark';
    /** 是否在画布左下角渲染图例（资产详情页把图例放进左侧信息栏，2026-11-16） */
    showLegend?: boolean;
    /**
     * 「节点间距」控件（2026-11-17，0–100）：
     * - card：画布内常驻滑块卡片（构建器沿用）
     * - none：画布内不渲染，由外部自己放控件（资产详情放进顶部工具条）
     */
    spacingControl?: 'card' | 'none';
    /** 受控的间距值：传了就以它为准（配套 onSpacingChange），不传则组件内部自持 */
    spacing?: number;
    onSpacingChange?: (value: number) => void;
}

const D3ForceGraph: React.FC<D3ForceGraphProps> = ({
    nodes,
    edges,
    onNodeClick,
    onEdgeClick,
    onNodesChange,
    onNodeRightClick,
    width: propWidth,
    height: propHeight,
    className = '',
    highlightNodeId = null,
    neighborhoodPinId = null,
    focusRequest = null,
    showLegend = true,
    spacingControl = 'card',
    spacing,
    onSpacingChange,
    theme = 'light'
}) => {
    const T = theme === 'dark' ? DARK_THEME : LIGHT_THEME;
    const nodeTheme = theme === 'dark' ? DARK_NODE_COLORS : NODE_COLORS;
    // SVG 资源 id 唯一化：两个画布同挂时避免 defs ID 冲突（隐藏 SVG 的 marker/filter 不渲染）
    const uidRef = useRef<string>(`ah${Math.random().toString(36).slice(2, 8)}`);
    const uid = uidRef.current;
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const simulationRef = useRef<any>(null);
    // S13 性能：tick 每帧全量 selectAll 查询 → 渲染时缓存一次选择集（元素集仅在 renderGraph 重建时变化）
    const tickSelsRef = useRef<{ links: any; inv: any; nodes: any }>({ links: null, inv: null, nodes: null });
    // S13 性能：hover 邻域高亮 rAF 合帧——快速扫过多个节点时，每帧至多一次全量透明度重绘
    const hoverRafRef = useRef<number | null>(null);
    const pendingHoverRef = useRef<string | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [zoomLevel, setZoomLevel] = useState(1);
    const [spacingInternal, setSpacingInternal] = useState(50);
    // 受控时以外部值为准（资产详情把滑杆放进工具条，值由页面持有）
    const spacingSlider = spacing ?? spacingInternal;
    const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
    const pinnedNodesRef = useRef<Set<string>>(new Set());
    const selectedNodeIdRef = useRef<string | null>(null);
    const prevNodeIdsRef = useRef<Set<string>>(new Set());
    const prevEdgeIdsRef = useRef<Set<string>>(new Set());
    const d3NodesRef = useRef<Map<string, any>>(new Map());
    const zoomBehaviorRef = useRef<any>(null);
    // 当前缩放平移变换（提示框/标签定位需从数据坐标换算到屏幕坐标）
    const zoomTransformRef = useRef<any>(zoomIdentity);

    const onNodeClickRef = useRef(onNodeClick);
    onNodeClickRef.current = onNodeClick;
    const onEdgeClickRef = useRef(onEdgeClick);
    onEdgeClickRef.current = onEdgeClick;
    const onNodesChangeRef = useRef(onNodesChange);
    onNodesChangeRef.current = onNodesChange;
    // 固定邻域高亮：悬停临时的持续版（详见 applyHoverHighlight）
    const neighborhoodPinRef = useRef<string | null>(neighborhoodPinId);
    neighborhoodPinRef.current = neighborhoodPinId;
    const applyHoverRef = useRef<((id: string | null) => void) | null>(null);
    const onNodeRightClickRef = useRef(onNodeRightClick);
    onNodeRightClickRef.current = onNodeRightClick;

    useEffect(() => {
        const updateContainerSize = () => {
            if (containerRef.current) {
                const rect = containerRef.current.getBoundingClientRect();
                const parentElement = containerRef.current.parentElement;
                // 容器宽高被写死为上次测量值（反馈环），以父级弹性空间为准
                let parentHeight = rect.height;
                let parentWidth = rect.width;
                if (parentElement) {
                    const parentRect = parentElement.getBoundingClientRect();
                    if (parentRect.height > 0) parentHeight = parentRect.height;
                    if (parentRect.width > 0) parentWidth = parentRect.width;
                }
                setContainerSize({
                    width: propWidth || parentWidth || window.innerWidth - 300,
                    height: propHeight || parentHeight || window.innerHeight - 150
                });
            } else {
                setContainerSize({
                    width: propWidth || window.innerWidth - 300,
                    height: propHeight || window.innerHeight - 150
                });
            }
        };
        updateContainerSize();
        window.addEventListener('resize', updateContainerSize);
        // 画布容器自身尺寸变化（如左侧列表展开/收起）不会触发 window resize，需单独观察；
        // 容器宽度被写死为测量像素值，会随父级收缩而溢出，故观察父级（弹性尺寸）
        let ro: ResizeObserver | null = null;
        const roTarget = containerRef.current?.parentElement;
        if (typeof ResizeObserver !== 'undefined' && roTarget) {
            ro = new ResizeObserver(() => updateContainerSize());
            ro.observe(roTarget);
        }
        return () => {
            window.removeEventListener('resize', updateContainerSize);
            ro?.disconnect();
        };
    }, [propWidth, propHeight]);

    const width = propWidth || containerSize.width;
    const height = propHeight || containerSize.height;

    const getNodeRadius = useCallback((node: OntologyNode) => {
        return NODE_RADII[normalizeNodeType(node.data?.type)] || NODE_RADII[NODE_TYPES.CLASS];
    }, []);

    const isInstanceEdge = useCallback((edge: any) => {
        const sourceType = edge.source?.data?.type || edge.source?.type;
        const targetType = edge.target?.data?.type || edge.target?.type;
        return sourceType === NODE_TYPES.INDIVIDUAL || targetType === NODE_TYPES.INDIVIDUAL;
    }, []);

    const getAdaptiveForceParams = useCallback((value?: number) => {
        const nodeCount = nodes.length;
        let baseLinkDistance = 180;
        let baseChargeStrength = -400;
        let baseChargeDistanceMax = 500;
        let baseCollisionRadius = 15;
        let baseCenterStrength = 0.08;
        let baseLinkStrength = 0.5;
        let baseCollisionStrength = 0.8;

        if (nodeCount <= 20) {
            baseLinkDistance = 160;
            baseChargeStrength = -350;
            baseChargeDistanceMax = 400;
            baseCollisionRadius = 12;
            baseCenterStrength = 0.12;
            baseLinkStrength = 0.6;
        } else if (nodeCount <= 50) {
            baseLinkDistance = 180;
            baseChargeStrength = -400;
            baseChargeDistanceMax = 500;
            baseCollisionRadius = 15;
            baseCenterStrength = 0.08;
            baseLinkStrength = 0.5;
        } else if (nodeCount <= 100) {
            baseLinkDistance = 220;
            baseChargeStrength = -500;
            baseChargeDistanceMax = 600;
            baseCollisionRadius = 18;
            baseCenterStrength = 0.05;
            baseLinkStrength = 0.4;
        } else {
            baseLinkDistance = 250 + Math.log2(nodeCount - 100) * 20;
            baseChargeStrength = -600 - (nodeCount - 100) * 2;
            baseChargeDistanceMax = 700 + (nodeCount - 100) * 3;
            baseCollisionRadius = 20 + Math.log2(nodeCount - 100) * 2;
            baseCenterStrength = 0.03;
            baseLinkStrength = 0.3;
            baseCollisionStrength = 0.9;
        }

        // 允许显式传入间距值：拖动滑杆后要立刻用新值重算力参数，
        // 直接用闭包里的 spacingSlider 拿到的是改之前的那一版
        const sliderFactor = ((value ?? spacingSlider) - 50) / 50;
        // 大图降载（S13）：碰撞检测迭代 3→1、alpha 起始低 + 衰减快 —— 仿真更早收敛，
        // DOM 全量更新的 tick 帧数从 ~340 帧降到 ~130 帧
        const collisionIterations = nodeCount > 150 ? 1 : 3;
        const simAlpha = nodeCount > 150 ? 0.6 : 1;
        const simAlphaDecay = nodeCount > 150 ? 0.04 : 0.02;

        return {
            linkDistance: Math.round(baseLinkDistance * (1 + sliderFactor * 0.5)),
            chargeStrength: Math.round(baseChargeStrength * (1 + sliderFactor * 0.3)),
            chargeDistanceMax: Math.round(baseChargeDistanceMax * (1 + sliderFactor * 0.3)),
            collisionRadius: Math.round(baseCollisionRadius * (1 + sliderFactor * 0.3)),
            centerStrength: parseFloat((baseCenterStrength * (1 - sliderFactor * 0.2)).toFixed(3)),
            linkStrength: parseFloat((baseLinkStrength * (1 - sliderFactor * 0.1)).toFixed(2)),
            collisionStrength: parseFloat(baseCollisionStrength.toFixed(2)),
            collisionIterations, simAlpha, simAlphaDecay,
        };
    }, [nodes.length, spacingSlider]);

    const truncateLabel = (label: string, maxLen: number) => {
        if (!label) return '';
        return label.length > maxLen ? label.substring(0, maxLen) + '…' : label;
    };

    const getNodeColors = useCallback((d: any) => {
        // 实例节点统一单色（与类节点紫色 #B585F2 搭配的主题蓝），不再按所属类分色
        return nodeTheme[d.type] || nodeTheme.DEFAULT;
    }, [nodeTheme]);

    const getEdgeColor = useCallback((d: any) => {
        // 带公理的关系边用紫色描边，与互斥徽标同色系（浅色深紫/深色亮紫）
        if (edgeHasAxiom(d.data)) return theme === 'dark' ? '#B585F2' : '#8F5BD9';
        return isInstanceEdge(d) ? T.edgeInstance : T.edge;
    }, [isInstanceEdge, T, theme]);

    const getEdgeDash = useCallback((d: any) => {
        return isInstanceEdge(d) ? "4,4" : "none";
    }, [isInstanceEdge]);

    const getEdgeMarker = useCallback((d: any) => {
        return isInstanceEdge(d) ? `url(#${uid}-arrowhead-instance)` : `url(#${uid}-arrowhead-class)`;
    }, [isInstanceEdge]);

    // 类节点默认带一层柔和投影（2026-11-17）：让它浮在画布上，而不是像贴纸一样贴在底上。
    // 节点超过 150 个时关掉——SVG 滤镜在大量节点下很吃性能
    const nodeShadowOn = theme === 'dark' && nodes.length <= 150;
    const defaultNodeFilter = (d: any) =>
        nodeShadowOn && normalizeNodeType(d?.data?.type) === NODE_TYPES.CLASS
            ? `url(#${uid}-node-shadow)`
            : null;

    const setupNodeContent = useCallback((nodeG: any, d: any) => {
        nodeG.selectAll("*").remove();

        const isClass = d.type === NODE_TYPES.CLASS;
        const isInstance = d.type === NODE_TYPES.INDIVIDUAL;

        const colors = getNodeColors(d);
        // 深色画布改用径向渐变填充（左上亮、右下深）：纯色实心圆看着"平"
        const gradientId = (() => {
            if (theme !== 'dark') return null;
            if (d.type === NODE_TYPES.CLASS) return `${uid}-grad-class`;
            if (d.type === NODE_TYPES.INDIVIDUAL) return `${uid}-grad-instance`;
            if (d.type === NODE_TYPES.PROPERTY) return `${uid}-grad-property`;
            return null;
        })();
        const label = d.originalNode?.data?.label || d.id;
        const classLabel = d.originalNode?.data?.class_label || '';
        const nodeRadius = d.radius;

        nodeG.append("circle")
            .attr("class", "node-shape")
            .attr("r", 0)
            .attr("fill", gradientId ? `url(#${gradientId})` : colors.fill)
            .attr("fill-opacity", colors.fillOpacity ?? 1)
            .attr("stroke", colors.stroke)
            .attr("stroke-width", isClass ? 1.5 : 1)
            .attr("filter", d.id === highlightNodeId ? `url(#${uid}-glow-selected)` : defaultNodeFilter(d))
            .transition()
            .duration(400)
            .ease((t: number) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
            .attr("r", nodeRadius);

        // 不做顶部高光椭圆（2026-11-17 用户否掉）：每个圆上浮一块半透明的白，看着像气泡；
        // 深紫底 + 亮紫描边已经够立住了

        nodeG.append("text")
            .attr("class", "node-label")
            .attr("text-anchor", "middle")
            .attr("dominant-baseline", "central")
            .style("fill", colors.text)
            .style("font-size", isClass ? "11px" : "9px")
            .style("font-weight", isClass ? "600" : "500")
            .style("pointer-events", "none")
            .style("opacity", 0)
            .text(truncateLabel(label, isClass ? 6 : 5))
            .transition()
            .delay(150)
            .duration(300)
            .style("opacity", 1);

        if (!isClass && classLabel) {
            nodeG.append("text")
                .attr("class", "node-sublabel")
                .attr("text-anchor", "middle")
                .attr("dy", nodeRadius + 12)
                .style("fill", theme === 'dark' ? '#8B94AB' : '#999')
                .style("font-size", "9px")
                .style("pointer-events", "none")
                .style("opacity", 0)
                .text(truncateLabel(classLabel, 10))
                .transition()
                .delay(200)
                .duration(300)
                .style("opacity", 1);
        }

        // 公理徽标：类节点声明了 disjointWith → 紫色 ⇎ 徽标（2 期可视化）
        const disjointCount = (d.originalNode?.data?.axioms?.disjoint_with || []).length;
        if (isClass && disjointCount > 0) {
            nodeG.append("text")
                .attr("class", "node-axiom-badge")
                .attr("text-anchor", "middle")
                .attr("dy", nodeRadius + 12)
                .style("fill", '#B585F2')
                .style("font-size", "9px")
                .style("font-weight", "600")
                .style("pointer-events", "none")
                .style("opacity", 0)
                .text(`⇎ 互斥×${disjointCount}`)
                .transition()
                .delay(200)
                .duration(300)
                .style("opacity", 1);
        }
    }, [highlightNodeId, getNodeColors, theme, uid, nodeShadowOn]);

    const renderGraph = useCallback(() => {
        if (!svgRef.current || nodes.length === 0) return;

        if (simulationRef.current) {
            simulationRef.current.stop();
            simulationRef.current = null;
        }

        const svg = select(svgRef.current);
        const centerX = width / 2;
        const centerY = height / 2;

        let g = svg.select<SVGGElement>("g.main-group");
        if (g.empty()) {
            svg.selectAll("*").remove();
            g = svg.append("g").attr("class", "main-group");
        }

        const markers = svg.select<SVGGElement>("defs.markers");
        if (markers.empty() || markers.attr("data-theme") !== theme) {
            svg.select("defs.markers").remove();
            // 箭头比原来小一档（6.5×5.5 → 5×4.4）：边只有 1.5px，原尺寸在短边上几乎占满整条线；
            // 颜色跟随边的语义（类边偏亮的冷灰、实例边蓝），不再一律浅灰
            const arrowClass = theme === 'dark' ? '#6B7BA8' : '#94a3b8';
            const arrowInstance = theme === 'dark' ? '#5B8DEF' : '#e0c8b8';
            const arrowHl = T.edgeHighlight;
            const glowSelected = theme === 'dark' ? '#F2B950' : '#409eff';
            const glowHover = theme === 'dark' ? '#5AC8FA' : '#5AC8FA'; // F6：浅深两主题统一品牌 hover 青（与 graph-explorer colors.ts 一致）
            svg.append("defs").attr("class", "markers").attr("data-theme", theme)
                .html(`
                    <marker id="${uid}-arrowhead-class" markerWidth="5.5" markerHeight="4.4" refX="5" refY="2.2" orient="auto">
                        <polygon points="0 0, 5 2.2, 0 4.4" fill="${arrowClass}" />
                    </marker>
                    <marker id="${uid}-arrowhead-instance" markerWidth="5.5" markerHeight="4.4" refX="5" refY="2.2" orient="auto">
                        <polygon points="0 0, 5 2.2, 0 4.4" fill="${arrowInstance}" />
                    </marker>
                    <marker id="${uid}-arrowhead-hl" markerWidth="5.5" markerHeight="4.4" refX="5" refY="2.2" orient="auto">
                        <polygon points="0 0, 5 2.2, 0 4.4" fill="${arrowHl}" />
                    </marker>
                    <radialGradient id="${uid}-grad-class" cx="35%" cy="28%" r="78%">
                        <stop offset="0%" stop-color="#A87CF3" />
                        <stop offset="100%" stop-color="#6337B8" />
                    </radialGradient>
                    <radialGradient id="${uid}-grad-instance" cx="35%" cy="28%" r="78%">
                        <stop offset="0%" stop-color="#7FB2FF" />
                        <stop offset="100%" stop-color="#3A66C4" />
                    </radialGradient>
                    <radialGradient id="${uid}-grad-property" cx="35%" cy="28%" r="78%">
                        <stop offset="0%" stop-color="#5FE0C0" />
                        <stop offset="100%" stop-color="#1F8E76" />
                    </radialGradient>
                    <filter id="${uid}-node-shadow" x="-50%" y="-50%" width="200%" height="200%">
                        <feDropShadow dx="0" dy="5" stdDeviation="5" flood-color="#05070D" flood-opacity="0.55" />
                    </filter>
                    <filter id="${uid}-glow-selected" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="3" result="blur" />
                        <feFlood flood-color="${glowSelected}" flood-opacity="0.6" result="color" />
                        <feComposite in="color" in2="blur" operator="in" result="shadow" />
                        <feMerge>
                            <feMergeNode in="shadow" />
                            <feMergeNode in="SourceGraphic" />
                        </feMerge>
                    </filter>
                    <filter id="${uid}-glow-hover" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="2" result="blur" />
                        <feFlood flood-color="${glowHover}" flood-opacity="0.4" result="color" />
                        <feComposite in="color" in2="blur" operator="in" result="shadow" />
                        <feMerge>
                            <feMergeNode in="shadow" />
                            <feMergeNode in="SourceGraphic" />
                        </feMerge>
                    </filter>
                `);
        }

        const currentNodeIds = new Set(nodes.map(n => n.id));
        const newNodeIds = new Set([...currentNodeIds].filter(id => !prevNodeIdsRef.current.has(id)));
        const removedNodeIds = new Set([...prevNodeIdsRef.current].filter(id => !currentNodeIds.has(id)));

        const nodeMap = new Map<string, any>();
        const d3Nodes = nodes.map(node => {
            const existingNode = d3NodesRef.current.get(node.id);
            const radius = getNodeRadius(node);
            const isPinned = pinnedNodesRef.current.has(node.id);
            const isNew = newNodeIds.has(node.id);

            const nodeData = {
                id: node.id,
                x: existingNode?.x ?? (node.position?.x || centerX + (Math.random() - 0.5) * width * 0.3),
                y: existingNode?.y ?? (node.position?.y || centerY + (Math.random() - 0.5) * height * 0.3),
                vx: existingNode?.vx || 0,
                vy: existingNode?.vy || 0,
                fx: isPinned ? (existingNode?.x ?? node.position?.x) : (existingNode?.fx || null),
                fy: isPinned ? (existingNode?.y ?? node.position?.y) : (existingNode?.fy || null),
                data: node.data,
                type: normalizeNodeType(node.data?.type),
                radius: radius,
                originalNode: node,
                isNew: isNew
            };
            nodeMap.set(node.id, nodeData);
            return nodeData;
        });

        d3NodesRef.current = new Map(d3Nodes.map(n => [n.id, n]));
        prevNodeIdsRef.current = currentNodeIds;

        const d3Links = edges
            .filter(edge => nodeMap.has(String(edge.source)) && nodeMap.has(String(edge.target)))
            .map(edge => ({
                source: nodeMap.get(String(edge.source)),
                target: nodeMap.get(String(edge.target)),
                data: edge.data,
                id: edge.id || `${edge.source}-${edge.target}-${edge.data?.label || ''}`,
                originalEdge: edge
            }));

        const currentEdgeIds = new Set(d3Links.map(l => l.id));
        prevEdgeIdsRef.current = currentEdgeIds;

        const forceParams = getAdaptiveForceParams();

        const simulation = forceSimulation(d3Nodes)
            .force("link", forceLink(d3Links)
                .id((d: any) => d.id)
                .distance(forceParams.linkDistance)
                .strength(forceParams.linkStrength))
            .force("charge", forceManyBody()
                .strength(forceParams.chargeStrength)
                .distanceMax(forceParams.chargeDistanceMax))
            .force("center", forceCenter(centerX, centerY)
                .strength(forceParams.centerStrength))
            .force("collision", forceCollide()
                .radius((d: any) => d.radius + forceParams.collisionRadius)
                .strength(forceParams.collisionStrength)
                .iterations(forceParams.collisionIterations))
            .force("x", forceX(centerX).strength(0.03))
            .force("y", forceY(centerY).strength(0.03))
            .alpha(forceParams.simAlpha)
            .alphaDecay(forceParams.simAlphaDecay)
            .velocityDecay(0.4);

        if (newNodeIds.size > 0 && prevNodeIdsRef.current.size > 0) {
            simulation.alpha(0.5);
        } else if (prevNodeIdsRef.current.size > 0) {
            simulation.alpha(0.3);
        }

        simulationRef.current = simulation;

        // 大图降载（S13）：>300 节点时进入/退出不做过渡动画（transition 每元素逐帧插值，SVG 下代价高）
        const largeGraph = d3Nodes.length > 300;

        // ── Edges: enter/update/exit ──
        const linkSelection = g.selectAll<SVGPathElement, any>("path.link")
            .data(d3Links, (d: any) => d.id);

        linkSelection.exit()
            .transition()
            .duration(largeGraph ? 0 : 200)
            .style("opacity", 0)
            .remove();

        const linkEnter = linkSelection.enter()
            .append("path")
            .attr("class", "link")
            .attr("fill", "none")
            .style("opacity", 0)
            .attr("stroke", (d: any) => getEdgeColor(d))
            .attr("stroke-opacity", 0.55)
            .attr("stroke-width", 1.5)
            .attr("stroke-dasharray", (d: any) => getEdgeDash(d))
            .attr("marker-end", (d: any) => getEdgeMarker(d))
            .attr("data-edge-id", (d: any) => d.id)
            .style("cursor", "pointer");

        linkEnter.transition()
            .duration(largeGraph ? 0 : 400)
            .style("opacity", 1);

        const linkMerge = linkEnter.merge(linkSelection as any);

        linkMerge
            .attr("stroke", (d: any) => getEdgeColor(d))
            .attr("stroke-dasharray", (d: any) => getEdgeDash(d))
            .attr("marker-end", (d: any) => getEdgeMarker(d));

        linkMerge.each(function (this: SVGPathElement, d: any) {
            const edgeLabel = (d.data?.label || d.data?.relation || '') + edgeAxiomSuffix(d.data);
            select(this)
                .on("click", (event: MouseEvent) => {
                    event.stopPropagation();
                    if (onEdgeClickRef.current && d.originalEdge) onEdgeClickRef.current(d.originalEdge);
                })
                .on("mouseenter", function (this: SVGPathElement, event: MouseEvent) {
                    event.stopPropagation();
                    select(this)
                        .attr("stroke", T.edgeHighlight)
                        .attr("stroke-width", 2.5)
                        .attr("marker-end", `url(#${uid}-arrowhead-hl)`);

                    if (layoutBusy()) return;  // S13：布局中不建悬浮标签（锚点抖 + DOM 建删放大卡顿）
                    const pathElement = this as SVGPathElement;
                    const pathLength = pathElement.getTotalLength();
                    if (pathLength === 0) return;
                    const midPoint = pathElement.getPointAtLength(pathLength / 2);
                    const _zt = zoomTransformRef.current;
                    const midScreenX = _zt.applyX(midPoint.x);
                    const midScreenY = _zt.applyY(midPoint.y);

                    const labelGroupId = `edge-label-${d.id}`;
                    let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                    if (labelGroup.empty()) {
                        labelGroup = svg.append("g")
                            .attr("id", labelGroupId)
                            .attr("class", "edge-label-group")
                            .style("pointer-events", "none")
                            .style("filter", "drop-shadow(0 8px 18px rgba(0, 0, 0, 0.5))")
                            .raise();
                        // 悬浮谓词卡（2026-11-17）：和节点悬浮卡同一套——深色玻璃底 + 细亮边 + 大圆角，
                        // 原来是个 rx3、0.5px 描边的直角小方块
                        labelGroup.append("rect")
                            .attr("class", "edge-label-bg")
                            .attr("fill", theme === 'dark' ? 'rgba(19, 26, 42, 0.96)' : 'rgba(255, 255, 255, 0.97)')
                            .attr("stroke", theme === 'dark' ? 'rgba(255, 255, 255, 0.12)' : '#E2E8F0')
                            .attr("stroke-width", 1)
                            .attr("rx", 7)
                            .attr("ry", 7);
                        labelGroup.append("text")
                            .attr("class", "edge-label-text")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "central")
                            .style("fill", theme === 'dark' ? '#E6EAF2' : '#334155')
                            .style("font-size", "10.5px")
                            .style("font-weight", "500")
                            .style("pointer-events", "none");
                    }

                    const labelBg = labelGroup.select<SVGRectElement>("rect.edge-label-bg");
                    const labelText = labelGroup.select<SVGTextElement>("text.edge-label-text");
                    labelText.text(edgeLabel || "关系");
                    const textNode = labelText.node();
                    if (textNode) {
                        const textBBox = textNode.getBBox();
                        const padding = 6;
                        labelBg
                            .attr("x", midScreenX - textBBox.width / 2 - padding)
                            .attr("y", midScreenY - textBBox.height / 2 - padding)
                            .attr("width", textBBox.width + padding * 2)
                            .attr("height", textBBox.height + padding * 2)
                            .style("display", "block");
                        labelText.attr("x", midScreenX).attr("y", midScreenY);
                    }
                    labelGroup.style("display", "block").raise();
                })
                .on("mouseleave", function (this: SVGPathElement, event: MouseEvent) {
                    event.stopPropagation();
                    select(this)
                        .attr("stroke", getEdgeColor(d))
                        .attr("stroke-width", 1.5)
                        .attr("marker-end", getEdgeMarker(d));
                    svg.selectAll("g.edge-label-group").style("display", "none");
                });
        });

        // ── Invisible links for click target ──
        let invisibleLinkGroup = g.select<SVGGElement>("g.invisible-link-group");
        if (invisibleLinkGroup.empty()) {
            invisibleLinkGroup = g.append("g").attr("class", "invisible-link-group");
        }

        const invisibleLinkSelection = invisibleLinkGroup
            .selectAll<SVGPathElement, any>("path.invisible-link")
            .data(d3Links, (d: any) => d.id);

        invisibleLinkSelection.exit().remove();

        invisibleLinkSelection.enter()
            .append("path")
            .attr("class", "invisible-link")
            .attr("fill", "none")
            .attr("stroke", "transparent")
            .attr("stroke-width", EDGE_CLICK_WIDTH)
            .style("pointer-events", "stroke")
            .style("cursor", "crosshair")
            .on("click", (event: MouseEvent, d: any) => {
                event.stopPropagation();
                if (onEdgeClickRef.current && d.originalEdge) onEdgeClickRef.current(d.originalEdge);
            })
            .on("mouseenter", function (this: SVGPathElement, event: MouseEvent, d: any) {
                event.stopPropagation();
                const visibleLink = g.select<SVGPathElement>(`path.link[data-edge-id="${d.id}"]`);
                visibleLink
                    .attr("stroke", T.edgeHighlight)
                    .attr("stroke-width", 2.5)
                    .attr("marker-end", `url(#${uid}-arrowhead-hl)`);

                if (layoutBusy()) return;  // S13：布局中不建悬浮标签（锚点抖 + DOM 建删放大卡顿）
                const pathElement = this as SVGPathElement;
                const pathLength = pathElement.getTotalLength();
                if (pathLength === 0) return;
                const midPoint = pathElement.getPointAtLength(pathLength / 2);
                const _zt = zoomTransformRef.current;
                const midScreenX = _zt.applyX(midPoint.x);
                const midScreenY = _zt.applyY(midPoint.y);

                const edgeLabel = (d.data?.label || d.data?.relation || '') + edgeAxiomSuffix(d.data);
                const labelGroupId = `edge-label-${d.id}`;
                let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                if (labelGroup.empty()) {
                    labelGroup = svg.append("g")
                        .attr("id", labelGroupId)
                        .attr("class", "edge-label-group")
                        .style("pointer-events", "none")
                        .raise();
                    labelGroup.append("rect")
                        .attr("class", "edge-label-bg")
                        .attr("fill", theme === 'dark' ? '#1A2233' : '#fff')
                        .attr("stroke", theme === 'dark' ? '#2A3550' : '#bbb')
                        .attr("stroke-width", 0.5)
                        .attr("rx", 3)
                        .attr("ry", 3)
                        .style("opacity", 0.95);
                    labelGroup.append("text")
                        .attr("class", "edge-label-text")
                        .attr("text-anchor", "middle")
                        .attr("dominant-baseline", "central")
                        .style("fill", theme === 'dark' ? '#E6EAF2' : '#555')
                        .style("font-size", "10px")
                        .style("font-weight", "500")
                        .style("pointer-events", "none");
                }

                const labelBg = labelGroup.select<SVGRectElement>("rect.edge-label-bg");
                const labelText = labelGroup.select<SVGTextElement>("text.edge-label-text");
                labelText.text(edgeLabel || "关系");
                const textNode = labelText.node();
                if (textNode) {
                    const textBBox = textNode.getBBox();
                    const padding = 4;
                    labelBg
                        .attr("x", midScreenX - textBBox.width / 2 - padding)
                        .attr("y", midScreenY - textBBox.height / 2 - padding)
                        .attr("width", textBBox.width + padding * 2)
                        .attr("height", textBBox.height + padding * 2)
                        .style("display", "block");
                    labelText.attr("x", midScreenX).attr("y", midScreenY);
                }
                labelGroup.style("display", "block").raise();
            })
            .on("mouseleave", function (this: SVGPathElement, event: MouseEvent, d: any) {
                event.stopPropagation();
                const visibleLink = g.select<SVGPathElement>(`path.link[data-edge-id="${d.id}"]`);
                visibleLink
                    .attr("stroke", getEdgeColor(d))
                    .attr("stroke-width", 1.5)
                    .attr("marker-end", getEdgeMarker(d));
                svg.selectAll("g.edge-label-group").style("display", "none");
            });

        // ── Nodes: enter/update/exit ──
        const dragStartPos = { x: 0, y: 0 };

        const nodeSelection = g.selectAll<SVGGElement, any>("g.node-group")
            .data(d3Nodes, (d: any) => d.id);

        nodeSelection.exit()
            .transition()
            .duration(largeGraph ? 0 : 250)
            .style("opacity", 0)
            .attr("transform", (d: any) => {
                const cx = d.x || 0;
                const cy = d.y || 0;
                return `translate(${cx}, ${cy}) scale(0.3)`;
            })
            .remove();

        const nodeEnter = nodeSelection.enter()
            .append("g")
            .attr("class", "node-group")
            .style("cursor", "pointer")
            .style("opacity", 0)
            .call(d3Drag<any, any>()
                .on("start", (event: any, d: any) => {
                    dragStartPos.x = event.x;
                    dragStartPos.y = event.y;
                    if (!event.active) simulation.alphaTarget(0.1).restart();
                    d.fx = d.x;
                    d.fy = d.y;
                    setIsDragging(true);
                })
                .on("drag", (event: any, d: any) => {
                    d.fx = event.x;
                    d.fy = event.y;
                })
                .on("end", (event: any, d: any) => {
                    if (!event.active) simulation.alphaTarget(0);

                    const dx = event.x - dragStartPos.x;
                    const dy = event.y - dragStartPos.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist < 5) {
                        d.fx = null;
                        d.fy = null;
                        selectedNodeIdRef.current = d.id;

                        svg.selectAll<SVGElement, any>("g.node-group .node-shape")
                            .attr("filter", function (this: SVGElement) {
                                const parentG = this.parentElement;
                                const nodeData = select(parentG).datum() as any;
                                return nodeData?.id === d.id ? `url(#${uid}-glow-selected)` : defaultNodeFilter(nodeData);
                            });

                        if (onNodeClickRef.current) onNodeClickRef.current(d.originalNode);
                    } else {
                        pinnedNodesRef.current.add(d.id);
                        d.fx = d.x;
                        d.fy = d.y;

                        if (onNodesChangeRef.current) {
                            onNodesChangeRef.current([{
                                ...d.originalNode,
                                position: { x: d.x || 0, y: d.y || 0 }
                            }]);
                        }
                    }

                    setIsDragging(false);
                })
            );

        nodeEnter.each(function (this: SVGGElement, d: any) {
            setupNodeContent(select(this), d);
        });

        nodeEnter.transition()
            .duration(largeGraph ? 0 : 400)
            .ease((t: number) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
            .style("opacity", 1);

        // ── Update existing nodes (e.g. highlightNodeId changed) ──
        nodeSelection.each(function (this: SVGGElement, d: any) {
            const nodeG = select(this);
            const shape = nodeG.select(".node-shape");
            if (!shape.empty()) {
                shape.attr("filter", d.id === highlightNodeId ? `url(#${uid}-glow-selected)` : defaultNodeFilter(d));
            }
            // 公理徽标同步：节点数据更新（如编辑面板保存互斥类）时，enter 路径不会重跑，这里增量维护
            const badgeText = `⇎ 互斥×${(d.originalNode?.data?.axioms?.disjoint_with || []).length}`;
            const showBadge = d.type === NODE_TYPES.CLASS
                && (d.originalNode?.data?.axioms?.disjoint_with || []).length > 0;
            const existingBadge = nodeG.select(".node-axiom-badge");
            if (showBadge) {
                if (!existingBadge.empty()) {
                    existingBadge.text(badgeText);
                } else {
                    nodeG.append("text")
                        .attr("class", "node-axiom-badge")
                        .attr("text-anchor", "middle")
                        .attr("dy", d.radius + 12)
                        .style("fill", '#B585F2')
                        .style("font-size", "9px")
                        .style("font-weight", "600")
                        .style("pointer-events", "none")
                        .text(badgeText);
                }
            } else if (!existingBadge.empty()) {
                existingBadge.remove();
            }
        });

        g.selectAll("g.node-group").raise();

        const allNodes = nodeEnter.merge(nodeSelection as any);

        // ── 悬停/固定邻域高亮：hoverId 非空=临时悬停；否则落到固定 pin（"在画布中聚焦"）──
        const applyHoverHighlight = (hoverId: string | null) => {
            const effective = hoverId ?? neighborhoodPinRef.current;
            if (effective) {
                const neighborIds = new Set<string>([effective]);
                const edgeConnected = new Set<string>();
                d3Links.forEach((l: any) => {
                    const s = typeof l.source === 'object' ? l.source.id : l.source;
                    const t = typeof l.target === 'object' ? l.target.id : l.target;
                    if (s === effective || t === effective) {
                        neighborIds.add(s);
                        neighborIds.add(t);
                        edgeConnected.add(l.id);
                    }
                });
                g.selectAll<SVGGElement, any>("g.node-group")
                    .style("opacity", (d: any) => (neighborIds.has(d.id) ? 1 : 0.15));
                g.selectAll<SVGPathElement, any>("path.link")
                    .attr("stroke-opacity", (d: any) => (edgeConnected.has(d.id) ? 0.95 : 0.06))
                    .attr("stroke-width", (d: any) => (edgeConnected.has(d.id) ? 2.2 : 1.5));
            } else {
                g.selectAll<SVGGElement, any>("g.node-group").style("opacity", 1);
                g.selectAll<SVGPathElement, any>("path.link")
                    .attr("stroke-opacity", 0.55)
                    .attr("stroke-width", 1.5);
            }
        };
        // 布局未静止（仿真还在跑）时跳过悬浮标签/提示框：节点在动，标签锚点会抖，
        // 且每帧 DOM 建删叠加 tick 更新会放大卡顿；高亮本身（单元素+全量透明度）不跳过
        const layoutBusy = () => {
            try { return (simulationRef.current?.alpha?.() ?? 0) > 0.08; } catch { return false; }
        };
        const applyHoverThrottled = (id: string | null) => {
            pendingHoverRef.current = id;
            if (hoverRafRef.current != null) return;
            hoverRafRef.current = requestAnimationFrame(() => {
                hoverRafRef.current = null;
                applyHoverHighlight(pendingHoverRef.current);
            });
        };
        applyHoverRef.current = applyHoverThrottled;

        allNodes
            .on("contextmenu", (event: MouseEvent, d: any) => {
                event.preventDefault();
                event.stopPropagation();
                if (normalizeNodeType(d.data?.type) === NODE_TYPES.CLASS && onNodeRightClickRef.current) {
                    onNodeRightClickRef.current(d.originalNode);
                } else if (normalizeNodeType(d.data?.type) !== NODE_TYPES.CLASS) {
                    message.info('只有类节点支持右键展开实例');
                }
            })
            .on("mouseenter", function (this: SVGGElement, event: MouseEvent, d: any) {
                if (d.id !== selectedNodeIdRef.current) {
                    select(this).select(".node-shape").attr("filter", `url(#${uid}-glow-hover)`);
                }
                applyHoverHighlight(d.id);
                const props = d.originalNode?.data?.properties || {};
                const propKeys = Object.keys(props).filter((k: string) => !k.startsWith('_'));
                const sourceDoc = d.originalNode?.data?.source_document || d.originalNode?.data?._source_file;
                const nodeLabel = d.originalNode?.data?.label || d.id;
                // 悬浮卡片（2026-11-17 重做）：名称（圆内是截断的，这里给全）+ 来源 + 最多 4 条属性。
                // 三类文本各用自己的字号和颜色，不再是一整块 9px 灰字；卡片圆角、描边和投影也换了一档
                const lines: { text: string; kind: 'title' | 'doc' | 'prop' }[] = [
                    { text: truncateLabel(String(nodeLabel), 22), kind: 'title' },
                ];
                if (sourceDoc) lines.push({ text: `来源 · ${sourceDoc}`, kind: 'doc' });
                propKeys.slice(0, 4).forEach((k: string) =>
                    lines.push({ text: `${k}：${String(props[k]).substring(0, 18)}`, kind: 'prop' }));
                if (!layoutBusy()) {
                    const labelGroupId = `node-tooltip-${d.id}`;
                    let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                    if (labelGroup.empty()) {
                        labelGroup = svg.append("g")
                            .attr("id", labelGroupId)
                            .attr("class", "edge-label-group")
                            .style("pointer-events", "none")
                            .style("filter", "drop-shadow(0 10px 22px rgba(0, 0, 0, 0.55))")
                            .raise();
                        labelGroup.append("rect")
                            .attr("class", "tooltip-bg")
                            .attr("fill", theme === 'dark' ? 'rgba(19, 26, 42, 0.96)' : 'rgba(255, 255, 255, 0.97)')
                            .attr("stroke", theme === 'dark' ? 'rgba(255, 255, 255, 0.12)' : '#E2E8F0')
                            .attr("stroke-width", 1)
                            .attr("rx", 8)
                            .attr("ry", 8);
                    }
                    const tooltipBg = labelGroup.select<SVGRectElement>("rect.tooltip-bg");
                    labelGroup.selectAll("text.tooltip-line").remove();

                    const lineHeight = 15;
                    const startY = -((lines.length - 1) * lineHeight) / 2;

                    lines.forEach((line, i) => {
                        labelGroup.append("text")
                            .attr("class", "tooltip-line")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "central")
                            .attr("dy", startY + i * lineHeight)
                            .style("fill", line.kind === 'title'
                                ? (theme === 'dark' ? '#F2F6FF' : '#0F172A')
                                : line.kind === 'doc'
                                    ? (theme === 'dark' ? '#5AC8FA' : '#1890ff')
                                    : (theme === 'dark' ? '#A9B7D9' : '#475569'))
                            .style("font-size", line.kind === 'title' ? "11.5px" : "10px")
                            .style("font-weight", line.kind === 'title' ? "600" : "400")
                            .style("pointer-events", "none")
                            .text(line.text);
                    });

                    const offsetY = -(d.radius) - 12 - (lines.length * lineHeight) / 2;

                    setTimeout(() => {
                        // 背景矩形不参与测量：上一轮已撑大的 rect 会被 getBBox 计入，导致背景越悬停越大
                        tooltipBg.style("display", "none");
                        const textBBox = (labelGroup.node() as SVGGElement)?.getBBox();
                        tooltipBg.style("display", null);
                        if (textBBox) {
                            const padding = 8;
                            tooltipBg
                                .attr("x", textBBox.x - padding)
                                .attr("y", textBBox.y - padding)
                                .attr("width", textBBox.width + padding * 2)
                                .attr("height", textBBox.height + padding * 2);
                        }
                        const _nt = zoomTransformRef.current;
                        labelGroup
                            .attr("transform", `translate(${_nt.applyX(d.x || 0)}, ${_nt.applyY((d.y || 0) + offsetY)})`)
                            .style("display", "block").raise();
                    }, 0);
                }
            })
            .on("mouseleave", function (this: SVGGElement, event: MouseEvent, d: any) {
                if (d.id !== selectedNodeIdRef.current) {
                    select(this).select(".node-shape").attr("filter", defaultNodeFilter(d));
                }
                applyHoverHighlight(null);
                svg.selectAll(`g#node-tooltip-${d.id}`).style("display", "none");
            });

        // ── Pre-compute bidirectional offset per link ──
        // KEY INSIGHT: For a pair A<->B, edge A→B has (nx,ny) and edge B→A has (-nx,-ny).
        // If we multiply by +1 and -1 respectively they end up on the SAME side.
        // Fix: both edges must use the SAME canonical perpendicular direction,
        // derived from the lexicographically-first node as "canonical source".
        const pairIndexMap = new Map<string, number>();
        d3Links.forEach((link: any) => {
            const sId = typeof link.source === 'object' ? link.source.id : link.source;
            const tId = typeof link.target === 'object' ? link.target.id : link.target;
            const sortedPair = [sId, tId].sort();
            const key = sortedPair.join('<->');
            const idx = pairIndexMap.get(key) ?? 0;
            pairIndexMap.set(key, idx + 1);
            // idx 0 → +1 (one side), idx 1 → -1 (other side)
            link._bidirOffset = idx === 0 ? 1 : -1;
            link._pairKey = key;
            // Store the canonical source id (lexicographically smaller)
            // Both edges in the pair will compute their perpendicular based on
            // the direction from _canonicalSourceId to the other node,
            // ensuring they always point to opposite sides.
            link._canonicalSourceId = sortedPair[0];
        });
        // If only one edge exists for a pair (unidirectional), remove offset.
        d3Links.forEach((link: any) => {
            const count = pairIndexMap.get(link._pairKey) ?? 1;
            if (count < 2) link._bidirOffset = 0;
        });

        // Helper to compute offset path endpoints using a canonical perpendicular.
        const computeEdgePath = (d: any) => {
            const source = d.source as any;
            const target = d.target as any;
            if (!source || !target || source.x == null || target.x == null) return null;

            const dx = target.x - source.x;
            const dy = target.y - source.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return null;

            const angle = Math.atan2(dy, dx);
            const sourceR = source.radius || NODE_RADII[NODE_TYPES.CLASS];
            const targetR = target.radius || NODE_RADII[NODE_TYPES.CLASS];

            let offsetX = 0;
            let offsetY = 0;

            if (d._bidirOffset !== 0 && d._canonicalSourceId != null) {
                // Always compute perpendicular from canonical source → other node.
                // This ensures both edges in a pair share the same perpendicular axis,
                // so +offset and -offset point to genuinely opposite sides.
                const isCanonicalDir = source.id === d._canonicalSourceId;
                // canonical dx/dy: from canonical source toward the other node
                const cdx = isCanonicalDir ? dx : -dx;
                const cdy = isCanonicalDir ? dy : -dy;
                // perpendicular (rotate 90° CCW)
                const cnx = -cdy / dist;
                const cny = cdx / dist;
                const off = d._bidirOffset * 12;
                offsetX = cnx * off;
                offsetY = cny * off;
            }

            const sx = source.x + Math.cos(angle) * sourceR + offsetX;
            const sy = source.y + Math.sin(angle) * sourceR + offsetY;
            const tx = target.x - Math.cos(angle) * (targetR + 6) + offsetX;
            const ty = target.y - Math.sin(angle) * (targetR + 6) + offsetY;

            return { sx, sy, tx, ty };
        };

        // S13：缓存 tick 选择集（renderGraph 时元素集已定，tick 内不再每帧 querySelectorAll）
        tickSelsRef.current = {
            links: g.selectAll<SVGPathElement, any>("path.link"),
            inv: g.selectAll<SVGPathElement, any>("path.invisible-link"),
            nodes: g.selectAll<SVGGElement, any>("g.node-group"),
        };
        simulation.on("tick", () => {
            const sels = tickSelsRef.current;
            (sels.links || g.selectAll<SVGPathElement, any>("path.link")).each(function (this: SVGPathElement, d: any) {
                const pts = computeEdgePath(d);
                if (!pts) return;
                this.setAttribute('d', `M${pts.sx},${pts.sy}L${pts.tx},${pts.ty}`);
            });

            (sels.inv || g.selectAll<SVGPathElement, any>("path.invisible-link")).each(function (this: SVGPathElement, d: any) {
                const pts = computeEdgePath(d);
                if (!pts) return;
                this.setAttribute('d', `M${pts.sx},${pts.sy}L${pts.tx},${pts.ty}`);
            });

            (sels.nodes || g.selectAll<SVGGElement, any>("g.node-group"))
                .attr("transform", (d: any) => `translate(${d.x || 0}, ${d.y || 0})`);
        });

        svg.on("click", () => {
            selectedNodeIdRef.current = null;
            svg.selectAll<SVGElement, any>("g.node-group .node-shape")
                .attr("filter", function (this: SVGElement) {
                    const nodeData = select(this.parentElement).datum() as any;
                    return defaultNodeFilter(nodeData);
                });
            svg.selectAll("g.edge-label-group").style("display", "none");
        });

    }, [nodes, edges, width, height, getNodeRadius, isInstanceEdge, highlightNodeId, getAdaptiveForceParams, getEdgeColor, getEdgeDash, getEdgeMarker, getNodeColors, setupNodeContent, theme]);

    useEffect(() => {
        if (!svgRef.current) return;
        const svg = select(svgRef.current);
        const zoomBehavior = d3Zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.1, 4])
            .on("zoom", (event: any) => {
                const transform = event.transform;
                zoomTransformRef.current = transform;
                setZoomLevel(transform.k);
                svg.select("g.main-group").attr("transform", transform.toString());
                // 平移缩放时隐藏悬停提示/标签，避免停留陈旧位置
                svg.selectAll("g.edge-label-group").style("display", "none");
            });
        svg.call(zoomBehavior);
        zoomBehaviorRef.current = zoomBehavior;
        return () => { svg.on(".zoom", null); };
    }, []);

    // 聚焦请求：把目标节点平移缩放到画布中心（seq 变化触发）
    const focusSeqRef = useRef(0);
    useEffect(() => {
        if (!focusRequest?.nodeId) return;
        if (focusRequest.seq === focusSeqRef.current) return;
        focusSeqRef.current = focusRequest.seq;
        const nd = d3NodesRef.current.get(focusRequest.nodeId);
        const zoomBehavior = zoomBehaviorRef.current;
        if (!nd || nd.x == null || !zoomBehavior) return;  // 布局未就绪时只高亮不缩放
        const k = 1.4;
        const t = zoomIdentity.translate(width / 2 - k * nd.x, height / 2 - k * nd.y).scale(k);
        select(svgRef.current)
            .transition().duration(500)
            .call(zoomBehavior.transform, t);
    }, [focusRequest?.seq, focusRequest?.nodeId, width, height]);

    // 固定邻域变化（设置/清除）时重刷高亮：hoverId=null → 落到 pin 或还原
    useEffect(() => {
        applyHoverRef.current?.(null);
    }, [neighborhoodPinId]);

    // 间距变化的唯一提交口：受控时回传外部、非受控时写内部 state；
    // 两种情况都用「新值」重算力参数并让仿真重新热起来（旧实现读闭包里的旧值，重算落后一拍）
    const commitSpacing = useCallback((newValue: number) => {
        if (spacing === undefined) setSpacingInternal(newValue);
        onSpacingChange?.(newValue);
        setTimeout(() => {
            if (simulationRef.current) {
                const forceParams = getAdaptiveForceParams(newValue);
                simulationRef.current.force("link")?.distance(forceParams.linkDistance);
                simulationRef.current.force("charge")?.strength(forceParams.chargeStrength);
                simulationRef.current.alpha(0.3).restart();
            }
        }, 50);
    }, [spacing, onSpacingChange, getAdaptiveForceParams]);

    const handleSliderChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        commitSpacing(parseInt(e.target.value, 10));
    }, [commitSpacing]);

    const getSliderLabel = () => spacingLabel(spacingSlider);

    // 受控间距（外部工具条）变化时，同样要重算力参数并让仿真重新热起来
    useEffect(() => {
        if (spacing === undefined || !simulationRef.current) return;
        const forceParams = getAdaptiveForceParams(spacing);
        simulationRef.current.force("link")?.distance(forceParams.linkDistance);
        simulationRef.current.force("charge")?.strength(forceParams.chargeStrength);
        simulationRef.current.alpha(0.3).restart();
    }, [spacing, getAdaptiveForceParams]);

    useEffect(() => {
        if (nodes.length > 0) {
            renderGraph();
        } else {
            if (svgRef.current) {
                const svg = select(svgRef.current);
                svg.selectAll("*").remove();
                if (simulationRef.current) {
                    simulationRef.current.stop();
                    simulationRef.current = null;
                }
                d3NodesRef.current.clear();
                prevNodeIdsRef.current.clear();
                prevEdgeIdsRef.current.clear();
            }
        }
    }, [nodes, edges, renderGraph, highlightNodeId]);

    useEffect(() => {
        return () => {
            if (simulationRef.current) simulationRef.current.stop();
        };
    }, []);

    // 「节点间距」滑杆本体：card / pill 两种外框共用，避免两处各写一份
    const spacingBody = (
        <>
            <input
                type="range"
                min="0"
                max="100"
                value={spacingSlider}
                onChange={handleSliderChange}
                className="w-full h-2 rounded-full appearance-none cursor-pointer slider"
                style={{ background: 'linear-gradient(to right, #bae6fd 0%, #0ea5e9 50%, #0369a1 100%)' }}
            />
            <div className="flex justify-between mt-1 text-xs" style={{ color: theme === 'dark' ? '#56679B' : '#9ca3af' }}>
                <span>紧凑</span>
                <span>标准</span>
                <span>宽松</span>
            </div>
        </>
    );

    return (
        <div
            ref={containerRef}
            className={`relative w-full h-full ${className}`}
            style={{ width: width || '100%', height: height || '100%', position: 'relative' }}
        >
            <svg
                ref={svgRef}
                width={width}
                height={height}
                style={{
                    background: T.background,
                    cursor: isDragging ? 'grabbing' : 'grab',
                    display: 'block'
                }}
            />
            {spacingControl === 'card' && (
                <div
                    className="absolute top-16 right-4 rounded-lg shadow-lg p-3 z-[1000] w-64"
                    style={theme === 'dark' ? { background: T.panel, border: `1px solid ${T.panelBorder}` } : { background: 'rgba(255,255,255,0.95)' }}
                >
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-medium" style={{ color: T.text }}>节点间距</span>
                        <span className="text-xs font-medium" style={{ color: T.panelText }}>{getSliderLabel()}</span>
                    </div>
                    {spacingBody}
                </div>
            )}

            <div className="absolute bottom-4 right-4">
                <div
                    className="px-3 py-1.5 rounded shadow text-sm pointer-events-none z-10"
                    style={theme === 'dark'
                        ? { background: T.panel, border: `1px solid ${T.panelBorder}`, color: T.panelText }
                        : { background: 'rgba(255,255,255,0.9)', color: '#4b5563' }}
                >
                    当前视图：{nodes.length} 个节点，{edges.length} 条边 {zoomLevel !== 1 && `(缩放：${Math.round(zoomLevel * 100)}%)`}
                </div>
            </div>

            {showLegend && (
            <div
                className="absolute bottom-4 left-4 rounded shadow px-3 py-2 z-10 text-xs"
                style={theme === 'dark'
                    ? { background: T.panel, border: `1px solid ${T.panelBorder}`, color: T.panelText }
                    : { background: 'rgba(255,255,255,0.9)', color: '#6b7280' }}
            >
                <div className="flex items-center gap-3 flex-wrap">
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 12, height: 12, backgroundColor: nodeTheme[NODE_TYPES.CLASS].fill, borderRadius: '50%', border: `2px solid ${nodeTheme[NODE_TYPES.CLASS].stroke}` }}></span>
                        类
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 12, height: 12, backgroundColor: nodeTheme[NODE_TYPES.INDIVIDUAL].fill, borderRadius: '50%', border: `2px solid ${nodeTheme[NODE_TYPES.INDIVIDUAL].stroke}` }}></span>
                        实例
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 16, height: 0, borderTop: `1.5px dashed ${T.edgeInstance}` }}></span>
                        类型
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 16, height: 0, borderTop: `1.5px solid ${T.edge}` }}></span>
                        关系
                    </span>
                </div>
            </div>
            )}
        </div>
    );
};

export default D3ForceGraph;
