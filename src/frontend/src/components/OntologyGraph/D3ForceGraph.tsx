import React, { useEffect, useRef, useState, useCallback } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';
import { select } from 'd3-selection';
import { drag as d3Drag } from 'd3-drag';
import { zoom as d3Zoom, zoomTransform } from 'd3-zoom';
import { message } from 'antd';
import { OntologyNode, OntologyEdge } from '../../types/ontology';

// 节点类型常量
const NODE_TYPES = {
    CLASS: 'owl:Class',
    INDIVIDUAL: 'owl:NamedIndividual',
    PROPERTY: 'owl:ObjectProperty'
};

// 节点颜色映射
const NODE_COLORS = {
    [NODE_TYPES.CLASS]: '#4cc9f0',      // 蓝色
    [NODE_TYPES.INDIVIDUAL]: '#f79767', // 橙色
    [NODE_TYPES.PROPERTY]: '#c990c0',   // 紫色
    DEFAULT: '#666'
};

// 节点大小映射
const NODE_SIZES = {
    [NODE_TYPES.CLASS]: 50,
    [NODE_TYPES.INDIVIDUAL]: 40,
    [NODE_TYPES.PROPERTY]: 40
};

// 浅色主题配置
const LIGHT_THEME = {
    background: '#ffffff',
    text: '#333333',
    edge: '#cccccc',      // 更浅的边颜色
    edgeHighlight: '#999999',
    stroke: '#ffffff'
};

// 边点击区域宽度（扩大点击范围）- 增加到 30 更容易选中
const EDGE_CLICK_WIDTH = 30;

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
    onForceParamsChange?: (params: ForceParams) => void;
}

// 力导向参数接口
interface ForceParams {
    linkDistance: number;
    chargeStrength: number;
    chargeDistanceMax: number;
    collisionRadius: number;
    centerStrength: number;
    linkStrength: number;
    collisionStrength: number;
}

/**
 * 基于 D3.js 力导向图的本体可视化组件
 */
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
    highlightNodeId = null
}) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const simulationRef = useRef<any>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [zoomLevel, setZoomLevel] = useState(1);
    
    // 滑轨控制状态 (0-100, 50 为自动/标准模式)
    const [spacingSlider, setSpacingSlider] = useState(50);
    const [showSlider, setShowSlider] = useState(false);
    
    // 响应式容器大小
    const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
    
    // 监听窗口大小变化
    useEffect(() => {
        const updateContainerSize = () => {
            if (containerRef.current) {
                const rect = containerRef.current.getBoundingClientRect();
                // 获取父容器的实际尺寸，确保填满整个可用空间
                const parentElement = containerRef.current.parentElement;
                let parentHeight = rect.height;
                let parentWidth = rect.width;
                
                // 如果当前尺寸为0，尝试从父元素获取
                if (parentElement) {
                    const parentRect = parentElement.getBoundingClientRect();
                    if (rect.height === 0) parentHeight = parentRect.height;
                    if (rect.width === 0) parentWidth = parentRect.width;
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
        
        // 初始化
        updateContainerSize();
        
        // 监听窗口大小变化
        window.addEventListener('resize', updateContainerSize);
        return () => window.removeEventListener('resize', updateContainerSize);
    }, [propWidth, propHeight]);
    
    // 使用容器大小或 props 传入的大小
    const width = propWidth || containerSize.width;
    const height = propHeight || containerSize.height;

    // 获取节点半径
    const getNodeRadius = useCallback((node: OntologyNode) => {
        const baseSize = NODE_SIZES[node.data?.type || NODE_TYPES.CLASS] || NODE_SIZES[NODE_TYPES.CLASS];
        return baseSize / 2;
    }, []);

    // 获取节点颜色
    const getNodeColor = useCallback((node: OntologyNode) => {
        return NODE_COLORS[node.data?.type || NODE_TYPES.CLASS] || NODE_COLORS.DEFAULT;
    }, []);

    // 判断边是否为实例关系（类与实例之间的边）
    const isInstanceEdge = useCallback((edge: any) => {
        const sourceType = edge.source?.data?.type || edge.source?.type;
        const targetType = edge.target?.data?.type || edge.target?.type;
        const sourceIsIndividual = sourceType === NODE_TYPES.INDIVIDUAL;
        const targetIsIndividual = targetType === NODE_TYPES.INDIVIDUAL;
        return sourceIsIndividual || targetIsIndividual;
    }, []);

    // 渲染节点和边
    const renderGraph = useCallback(() => {
        if (!svgRef.current || nodes.length === 0) return;

        const svg = select(svgRef.current);
        const centerX = width / 2;
        const centerY = height / 2;

        // 创建或获取主分组（用于缩放）
        let g = svg.select<SVGGElement>("g.main-group");
        if (g.empty()) {
            svg.selectAll("*").remove(); // 清空现有内容
            g = svg.append("g").attr("class", "main-group");
        }

        // 准备节点数据 - 保留现有位置或初始化新位置
        const nodeMap = new Map<string, any>();
        const d3Nodes = nodes.map(node => {
            const existingNode = simulationRef.current?.nodes().find((n: any) => n.id === node.id);
            const nodeData = {
                id: node.id,
                x: existingNode?.x || centerX + (Math.random() - 0.5) * width * 0.3,
                y: existingNode?.y || centerY + (Math.random() - 0.5) * height * 0.3,
                vx: existingNode?.vx || 0,
                vy: existingNode?.vy || 0,
                fx: existingNode?.fx || null,
                fy: existingNode?.fy || null,
                data: node.data,
                type: node.data?.type || NODE_TYPES.CLASS,
                radius: getNodeRadius(node),
                originalNode: node
            };
            nodeMap.set(node.id, nodeData);
            return nodeData;
        });

        // 准备边数据 - 确保 source 和 target 引用正确的节点对象，过滤掉无效边
        const d3Links = edges
            .filter(edge => {
                // 只保留两端节点都存在的边
                const sourceExists = nodeMap.has(edge.source);
                const targetExists = nodeMap.has(edge.target);
                return sourceExists && targetExists;
            })
            .map(edge => {
                const sourceNode = nodeMap.get(edge.source);
                const targetNode = nodeMap.get(edge.target);
                return {
                    source: sourceNode,
                    target: targetNode,
                    data: edge.data,
                    id: `${edge.source}-${edge.target}`,
                    originalEdge: edge
                };
            });

        // 获取自适应力导向参数
        const forceParams = getAdaptiveForceParams();

        // 创建力模拟 - 使用自适应参数
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
                .iterations(2));

        simulationRef.current = simulation;

        // 定义箭头标记
        const markers = svg.select<SVGGElement>("defs.markers");
        if (markers.empty()) {
            svg.append("defs").attr("class", "markers")
                .html(`
                    <marker id="arrowhead-class" markerWidth="10" markerHeight="7" refX="28" refY="3.5" orient="auto">
                        <polygon points="0 0, 10 3.5, 0 7" fill="#cccccc" opacity="0.8" />
                    </marker>
                    <marker id="arrowhead-instance" markerWidth="10" markerHeight="7" refX="28" refY="3.5" orient="auto">
                        <polygon points="0 0, 10 3.5, 0 7" fill="#cccccc" opacity="0.6" />
                    </marker>
                `);
        }

        // 渲染边 - 使用 path 而不是 line，以便更好地控制箭头位置
        // 先移除旧的边和标签
        g.selectAll("path.link").remove();
        g.selectAll("path.invisible-link").remove();
        svg.selectAll("g.edge-label-group").remove();
        
        const linkSelection = g.selectAll<SVGPathElement, any>("path.link")
            .data(d3Links, (d: any) => d.id);

        const linkEnter = linkSelection.enter()
            .append("path")
            .attr("class", "link")
            .attr("fill", "none")
            .attr("stroke", LIGHT_THEME.edge)
            .attr("stroke-opacity", 0.8)
            .attr("stroke-width", 1.5)
            .attr("data-edge-id", (d: any) => d.id);

        const linkMerge = linkEnter.merge(linkSelection as any);

        // 移除不再需要的边元素
        linkSelection.exit().remove();

        // 设置边的样式：类 - 类为实线，类 - 实例为虚线
        linkMerge.each(function(this: SVGPathElement, d: any) {
            const isInstance = isInstanceEdge(d);
            const edgeLabel = d.data?.label || d.data?.relation || '';
            select(this)
                .attr("stroke-dasharray", isInstance ? "5,5" : "none")
                .attr("marker-end", isInstance ? "url(#arrowhead-instance)" : "url(#arrowhead-class)")
                .style("cursor", "pointer")
                .on("click", (event: MouseEvent) => {
                    event.stopPropagation();
                    if (onEdgeClick && d.originalEdge) {
                        onEdgeClick(d.originalEdge);
                    }
                })
                // 鼠标悬停时显示关系标签
                .on("mouseenter", function(this: SVGPathElement, event: MouseEvent) {
                    event.stopPropagation();
                    // 高亮边
                    select(this)
                        .attr("stroke", LIGHT_THEME.edgeHighlight)
                        .attr("stroke-width", 2.5);
                    
                    // 获取边的中点位置
                    const pathElement = this as SVGPathElement;
                    const pathLength = pathElement.getTotalLength();
                    const midPoint = pathElement.getPointAtLength(pathLength / 2);
                    
                    // 创建或显示标签 - 使用唯一 ID 避免重复
                    const labelGroupId = `edge-label-${d.id}`;
                    let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                    
                    // 如果标签组不存在，创建它
                    if (labelGroup.empty()) {
                        labelGroup = svg.append("g")
                            .attr("id", labelGroupId)
                            .attr("class", "edge-label-group")
                            .style("pointer-events", "none")
                            .raise(); // 放在最上层
                        
                        // 先创建背景矩形
                        const labelBg = labelGroup.append("rect")
                            .attr("class", "edge-label-bg")
                            .attr("fill", "#fff")
                            .attr("stroke", "#999")
                            .attr("stroke-width", 1)
                            .attr("rx", 3)
                            .attr("ry", 3)
                            .style("opacity", 0.95);
                        
                        // 再创建文本
                        const labelText = labelGroup.append("text")
                            .attr("class", "edge-label-text")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "central")
                            .style("fill", "#333")
                            .style("font-size", "11px")
                            .style("font-weight", "500")
                            .style("pointer-events", "none");
                    }
                    
                    // 获取标签元素
                    const labelBg = labelGroup.select<SVGRectElement>("rect.edge-label-bg");
                    const labelText = labelGroup.select<SVGTextElement>("text.edge-label-text");
                    
                    // 设置文本内容并立即获取边界框
                    const displayLabel = edgeLabel || "关系";
                    labelText.text(displayLabel);
                    
                    // 同步获取文本边界框（不使用 setTimeout）
                    const textNode = labelText.node();
                    if (textNode) {
                        const textBBox = textNode.getBBox();
                        const padding = 4;
                        
                        // 设置背景矩形位置和大小
                        labelBg
                            .attr("x", midPoint.x - textBBox.width / 2 - padding)
                            .attr("y", midPoint.y - textBBox.height / 2 - padding)
                            .attr("width", textBBox.width + padding * 2)
                            .attr("height", textBBox.height + padding * 2)
                            .style("display", "block");
                        
                        // 设置文本位置（使用 central baseline，y 坐标就是中点）
                        labelText
                            .attr("x", midPoint.x)
                            .attr("y", midPoint.y);
                    }
                    
                    // 显示标签组（放在最上层）
                    labelGroup.style("display", "block").raise();
                })
                .on("mouseleave", function(this: SVGPathElement, event: MouseEvent) {
                    event.stopPropagation();
                    // 恢复边的样式
                    select(this)
                        .attr("stroke", LIGHT_THEME.edge)
                        .attr("stroke-width", 1.5);
                    
                    // 隐藏所有标签组
                    svg.selectAll("g.edge-label-group").style("display", "none");
                });
        });

        // 添加透明的点击区域（扩大边的点击范围）- 在节点之上渲染，确保能捕获鼠标事件
        // 先移除旧的透明边
        g.selectAll("g.invisible-link-group").remove();
        
        // 在节点之后添加透明边组（确保在节点之上）
        const invisibleLinkGroup = g.append("g").attr("class", "invisible-link-group").raise();
        
        const invisibleLinkSelection = invisibleLinkGroup
            .selectAll<SVGPathElement, any>("path.invisible-link")
            .data(d3Links, (d: any) => d.id);

        const invisibleLinkEnter = invisibleLinkSelection.enter()
            .append("path")
            .attr("class", "invisible-link")
            .attr("fill", "none")
            .attr("stroke", "transparent")
            .attr("stroke-width", EDGE_CLICK_WIDTH)
            .style("pointer-events", "stroke")
            .style("cursor", "crosshair");

        const invisibleLinkMerge = invisibleLinkEnter.merge(invisibleLinkSelection as any);

        invisibleLinkMerge.each(function(this: SVGPathElement, d: any) {
            const invisibleEdgeLabel = d.originalEdge?.data?.label || d.originalEdge?.data?.relation || '';
            select(this)
                .on("mouseenter", function(event: MouseEvent) {
                    event.stopPropagation();
                    // 鼠标悬停时高亮对应的可见边
                    const visibleEdge = g.select(`path.link[data-edge-id="${d.id}"]`);
                    if (!visibleEdge.empty()) {
                        visibleEdge
                            .attr("stroke", LIGHT_THEME.edgeHighlight)
                            .attr("stroke-width", 2.5);
                    }
                    
                    // 获取边的中点位置
                    const pathElement = this as SVGPathElement;
                    const pathLength = pathElement.getTotalLength();
                    const midPoint = pathElement.getPointAtLength(pathLength / 2);
                    
                    // 创建或显示标签 - 使用唯一 ID 避免重复
                    const labelGroupId = `edge-label-${d.id}`;
                    let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                    
                    // 如果标签组不存在，创建它
                    if (labelGroup.empty()) {
                        labelGroup = svg.append("g")
                            .attr("id", labelGroupId)
                            .attr("class", "edge-label-group")
                            .style("pointer-events", "none")
                            .raise(); // 放在最上层
                        
                        // 先创建背景矩形
                        const labelBg = labelGroup.append("rect")
                            .attr("class", "edge-label-bg")
                            .attr("fill", "#fff")
                            .attr("stroke", "#999")
                            .attr("stroke-width", 1)
                            .attr("rx", 3)
                            .attr("ry", 3)
                            .style("opacity", 0.95);
                        
                        // 再创建文本
                        const labelText = labelGroup.append("text")
                            .attr("class", "edge-label-text")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "middle")
                            .style("fill", "#333")
                            .style("font-size", "11px")
                            .style("font-weight", "500")
                            .style("pointer-events", "none");
                    }
                    
                    // 获取标签元素
                    const labelBg = labelGroup.select<SVGRectElement>("rect.edge-label-bg");
                    const labelText = labelGroup.select<SVGTextElement>("text.edge-label-text");
                    
                    // 设置文本内容并立即获取边界框
                    const displayLabel = invisibleEdgeLabel || "关系";
                    labelText.text(displayLabel);
                    
                    // 同步获取文本边界框（不使用 setTimeout）
                    const textNode = labelText.node();
                    if (textNode) {
                        const textBBox = textNode.getBBox();
                        const padding = 4;
                        
                        // 设置背景矩形位置和大小
                        labelBg
                            .attr("x", midPoint.x - textBBox.width / 2 - padding)
                            .attr("y", midPoint.y - textBBox.height / 2 - padding)
                            .attr("width", textBBox.width + padding * 2)
                            .attr("height", textBBox.height + padding * 2)
                            .style("display", "block");
                        
                        // 设置文本位置（使用 central baseline，y 坐标就是中点）
                        labelText
                            .attr("x", midPoint.x)
                            .attr("y", midPoint.y);
                    }
                    
                    // 显示标签组（放在最上层）
                    labelGroup.style("display", "block").raise();
                })
                .on("mouseleave", function(event: MouseEvent) {
                    event.stopPropagation();
                    // 恢复边的样式
                    const visibleEdge = g.select(`path.link[data-edge-id="${d.id}"]`);
                    if (!visibleEdge.empty()) {
                        visibleEdge
                            .attr("stroke", LIGHT_THEME.edge)
                            .attr("stroke-width", 1.5);
                    }
                    
                    // 隐藏所有标签组
                    svg.selectAll("g.edge-label-group").style("display", "none");
                })
                .on("click", (event: MouseEvent) => {
                    event.stopPropagation();
                    if (onEdgeClick && d.originalEdge) {
                        onEdgeClick(d.originalEdge);
                    }
                });
        });

        invisibleLinkSelection.exit().remove();

        // 渲染节点组（在边之后渲染，确保节点在边之上）
        const nodeSelection = g.selectAll<SVGGElement, any>("g.node-group")
            .data(d3Nodes, (d: any) => d.id);

        const nodeEnter = nodeSelection.enter()
            .append("g")
            .attr("class", "node-group")
            .style("cursor", "grab")
            .call(d3Drag<any, any>()
                .on("start", (event: any, d: any) => {
                    d.fx = d.x;
                    d.fy = d.y;
                    setIsDragging(true);
                })
                .on("drag", (event: any, d: any) => {
                    d.fx = event.x;
                    d.fy = event.y;
                })
                .on("end", (event: any, d: any) => {
                    d.fx = null;
                    d.fy = null;
                    setIsDragging(false);
                    
                    if (onNodesChange) {
                        onNodesChange([{
                            ...d.originalNode,
                            position: { x: d.x || 0, y: d.y || 0 }
                        }]);
                    }
                })
            );

        // 添加圆形节点
        nodeEnter.append("circle")
            .attr("class", "node")
            .attr("r", (d: any) => NODE_SIZES[d.type] / 2)
            .attr("fill", (d: any) => getNodeColor(d))
            .attr("stroke", "#fff")
            .attr("stroke-width", 2)
            .attr("filter", (d: any) => d.id === highlightNodeId ? "drop-shadow(0 0 8px rgba(255, 165, 0, 0.8))" : null);

        // 添加文字标签
        nodeEnter.append("text")
            .attr("class", "node-label")
            .attr("dy", (d: any) => NODE_SIZES[d.type] / 2 + 18)
            .attr("text-anchor", "middle")
            .style("fill", LIGHT_THEME.text)
            .style("font-size", "12px")
            .style("font-weight", "500")
            .style("pointer-events", "none")
            .text((d: any) => d.originalNode.data?.label || d.id);

        // 绑定节点点击事件
        // 更新高亮节点的外发光效果
        nodeEnter.merge(nodeSelection as any)
            .each(function(this: SVGGElement, d: any) {
                const circle = select(this).select("circle.node");
                circle.attr("filter", d.id === highlightNodeId ? "drop-shadow(0 0 8px rgba(255, 165, 0, 0.8))" : null);
            })
            .on("click", (event: MouseEvent, d: any) => {
                event.stopPropagation();
                if (onNodeClick) {
                    onNodeClick(d.originalNode);
                }
            })
            .on("dblclick", (event: MouseEvent, d: any) => {
                event.stopPropagation();
                if (d.data?.type === NODE_TYPES.CLASS && onNodeClick) {
                    onNodeClick(d.originalNode);
                }
            })
            // 右键点击类节点展开实例
            .on("contextmenu", (event: MouseEvent, d: any) => {
                event.preventDefault();
                event.stopPropagation();
                if (d.data?.type === NODE_TYPES.CLASS && onNodeRightClick) {
                    onNodeRightClick(d.originalNode);
                } else if (d.data?.type !== NODE_TYPES.CLASS) {
                    message.info('只有类节点支持右键展开实例');
                }
            });

        nodeSelection.exit().remove();

        // 每一帧更新位置
        simulation.on("tick", () => {
            // 更新边的位置 - 使用 path 连接到节点边缘
            const updatePath = (path: any, strokeWidth: number) => {
                path.attr("d", (d: any) => {
                    const source = d.source as any;
                    const target = d.target as any;
                    
                    // 计算从源节点到目标节点的角度
                    const dx = target.x - source.x;
                    const dy = target.y - source.y;
                    const angle = Math.atan2(dy, dx);
                    
                    // 计算源节点边缘的点（源节点半径）
                    const sourceRadius = source.radius || NODE_SIZES[source.type] / 2;
                    const sourceX = source.x + Math.cos(angle) * sourceRadius;
                    const sourceY = source.y + Math.sin(angle) * sourceRadius;
                    
                    // 计算目标节点边缘的点（目标节点半径，减去箭头长度）
                    const targetRadius = target.radius || NODE_SIZES[target.type] / 2;
                    const targetX = target.x - Math.cos(angle) * (targetRadius + 5);
                    const targetY = target.y - Math.sin(angle) * (targetRadius + 5);
                    
                    return `M${sourceX},${sourceY}L${targetX},${targetY}`;
                });
            };

            updatePath(linkMerge, 1.5);
            updatePath(invisibleLinkMerge, EDGE_CLICK_WIDTH);

            // 更新节点组的位置
            g.selectAll<SVGGElement, any>("g.node-group")
                .attr("transform", (d: any) => `translate(${d.x || 0}, ${d.y || 0})`);
        });

    }, [nodes, edges, width, height, getNodeColor, getNodeRadius, onNodeClick, onNodesChange, onEdgeClick, isInstanceEdge]);

    // 初始化缩放行为
    useEffect(() => {
        if (!svgRef.current) return;

        const svg = select(svgRef.current);
        
        // 创建缩放行为
        const zoomBehavior = d3Zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.1, 4])
            .on("zoom", (event: any) => {
                const transform = event.transform;
                setZoomLevel(transform.k);
                
                svg.select("g.main-group")
                    .attr("transform", transform.toString());
            });

        svg.call(zoomBehavior);

        return () => {
            svg.on(".zoom", null);
        };
    }, []);

    // 根据节点数量和滑轨设置计算力导向参数
    const getAdaptiveForceParams = useCallback(() => {
        const nodeCount = nodes.length;
        
        // 基础参数（基于节点数量的自适应值）
        let baseLinkDistance = 150;
        let baseChargeStrength = -300;
        let baseChargeDistanceMax = 400;
        let baseCollisionRadius = 10;
        let baseCenterStrength = 0.05;
        let baseLinkStrength = 0.6;
        let baseCollisionStrength = 0.7;
        
        // 根据节点数量确定基础值
        if (nodeCount <= 20) {
            baseLinkDistance = 120;
            baseChargeStrength = -200;
            baseChargeDistanceMax = 300;
            baseCollisionRadius = 8;
            baseCenterStrength = 0.1;
            baseLinkStrength = 0.7;
        } else if (nodeCount <= 50) {
            baseLinkDistance = 150;
            baseChargeStrength = -300;
            baseChargeDistanceMax = 400;
            baseCollisionRadius = 10;
            baseCenterStrength = 0.05;
            baseLinkStrength = 0.6;
        } else if (nodeCount <= 100) {
            baseLinkDistance = 180;
            baseChargeStrength = -400;
            baseChargeDistanceMax = 500;
            baseCollisionRadius = 12;
            baseCenterStrength = 0.03;
            baseLinkStrength = 0.5;
        } else {
            baseLinkDistance = 200 + Math.log2(nodeCount - 100) * 20;
            baseChargeStrength = -500 - (nodeCount - 100) * 2;
            baseChargeDistanceMax = 600 + (nodeCount - 100) * 3;
            baseCollisionRadius = 15 + Math.log2(nodeCount - 100) * 2;
            baseCenterStrength = 0.02;
            baseLinkStrength = 0.4;
            baseCollisionStrength = 0.9;
        }
        
        // 滑轨调节因子 (0-100, 50 为标准模式)
        // 0 = 最紧凑，50 = 自适应标准，100 = 最宽松
        const sliderFactor = (spacingSlider - 50) / 50; // -1 到 1
        
        // 优化调节范围，确保节点间距可控，不会太分散
        // linkDistance: 控制所有边（连接）的长度 - 影响有边连接的节点间距
        // 紧凑时缩短边，宽松时适度延长边
        const linkDistance = baseLinkDistance * (1 + sliderFactor * 0.6); // ±60% 调节，适中效果
        // chargeStrength: 控制所有节点之间的排斥力 - 影响所有节点间距（包括没有边的节点）
        // 紧凑时增加排斥力让节点不重叠，宽松时适度减少排斥力但保持一定距离
        const chargeStrength = baseChargeStrength * (1 + sliderFactor * 0.4); // ±40% 调节，温和效果
        // chargeDistanceMax: 排斥力作用的最大距离 - 决定多远距离内的节点会相互排斥
        // 紧凑时增加作用距离防止节点聚集，宽松时适度增加
        const chargeDistanceMax = baseChargeDistanceMax * (1 + sliderFactor * 0.3); // ±30% 调节
        // collisionRadius: 控制节点碰撞半径 - 防止节点重叠
        const collisionRadius = baseCollisionRadius * (1 + sliderFactor * 0.3); // ±30% 调节
        // centerStrength: 中心引力强度 - 控制簇与簇之间的聚集程度
        // 紧凑时（slider < 50）：增强中心引力，让各簇更聚集在中心
        // 宽松时（slider > 50）：也保持一定中心引力，防止节点飞散太远
        const centerStrength = baseCenterStrength * (1 - sliderFactor * 0.3); // ±30% 调节，保持适度引力
        // linkStrength: 边的拉力强度
        const linkStrength = baseLinkStrength * (1 - sliderFactor * 0.1); // ±10% 调节，轻微变化
        const collisionStrength = baseCollisionStrength;
        
        return {
            linkDistance: Math.round(linkDistance),
            chargeStrength: Math.round(chargeStrength),
            chargeDistanceMax: Math.round(chargeDistanceMax),
            collisionRadius: Math.round(collisionRadius),
            centerStrength: parseFloat(centerStrength.toFixed(3)),
            linkStrength: parseFloat(linkStrength.toFixed(2)),
            collisionStrength: parseFloat(collisionStrength.toFixed(2))
        };
    }, [nodes.length, spacingSlider]);
    
    // 处理滑轨变化
    const handleSliderChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const newValue = parseInt(e.target.value, 10);
        setSpacingSlider(newValue);
        
        // 延迟重新计算布局，避免频繁更新
        setTimeout(() => {
            if (simulationRef.current) {
                const forceParams = getAdaptiveForceParams();
                simulationRef.current.force("link")?.distance(forceParams.linkDistance);
                simulationRef.current.force("charge")?.strength(forceParams.chargeStrength);
                simulationRef.current.alpha(0.3).restart();
            }
        }, 50);
    }, [getAdaptiveForceParams]);
    
    // 获取滑轨标签文本
    const getSliderLabel = () => {
        if (spacingSlider < 25) return '紧凑';
        if (spacingSlider < 50) return '较紧';
        if (spacingSlider === 50) return '标准';
        if (spacingSlider < 75) return '较松';
        return '宽松';
    };

    // 渲染图表
    useEffect(() => {
        if (nodes.length > 0) {
            renderGraph();
        }
    }, [nodes, edges, renderGraph, highlightNodeId]);

    // 重新计算布局
    const forceLayout = useCallback(() => {
        if (nodes.length === 0) {
            message.warning('没有节点可布局');
            return;
        }

        message.loading('正在计算力导向布局...', 0);

        setTimeout(() => {
            renderGraph();
            message.destroy();
            message.success('力导向布局完成！');
        }, 100);
    }, [nodes, renderGraph]);

    // 清理
    useEffect(() => {
        return () => {
            if (simulationRef.current) {
                simulationRef.current.stop();
            }
        };
    }, []);

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
                    background: LIGHT_THEME.background,
                    cursor: isDragging ? 'grabbing' : 'grab',
                    display: 'block'
                }}
            />
            {/* 节点间距调节滑轨 - 使用 fixed 定位固定在屏幕右上角，不随左侧面板移动 */}
            <div className="fixed top-[80px] right-4 bg-white bg-opacity-95 rounded-lg shadow-lg p-3 z-[1000] w-64">
                <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600">节点间距</span>
                    <span className="text-xs font-medium text-gray-500">
                        {getSliderLabel()}
                    </span>
                </div>
                <input
                    type="range"
                    min="0"
                    max="100"
                    value={spacingSlider}
                    onChange={handleSliderChange}
                    className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer slider"
                    style={{
                        background: `linear-gradient(to right, #bae6fd 0%, #0ea5e9 50%, #0369a1 100%)`
                    }}
                />
                <div className="flex justify-between mt-1 text-xs text-gray-400">
                    <span>紧凑</span>
                    <span>标准</span>
                    <span>宽松</span>
                </div>
            </div>
            
            <div className="absolute bottom-4 right-4">
                <div className="bg-white bg-opacity-90 text-gray-700 px-3 py-1.5 rounded shadow text-sm pointer-events-none z-10">
                    {nodes.length} 个节点，{edges.length} 条边 {zoomLevel !== 1 && `(缩放：${Math.round(zoomLevel * 100)}%)`}
                </div>
            </div>
        </div>
    );
};

export default D3ForceGraph;