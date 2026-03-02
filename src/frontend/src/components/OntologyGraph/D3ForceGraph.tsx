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
    width = window.innerWidth - 300,
    height = window.innerHeight - 150,
    className = '',
    highlightNodeId = null
}) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const simulationRef = useRef<any>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [zoomLevel, setZoomLevel] = useState(1);

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

        // 创建力模拟 - 使用更稳定的参数
        const simulation = forceSimulation(d3Nodes)
            .force("link", forceLink(d3Links)
                .id((d: any) => d.id)
                .distance(150)
                .strength(0.6))
            .force("charge", forceManyBody()
                .strength(-300)
                .distanceMax(400))
            .force("center", forceCenter(centerX, centerY)
                .strength(0.05))
            .force("collision", forceCollide()
                .radius((d: any) => d.radius + 10)
                .strength(0.7)
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
            className={`relative ${className}`}
            style={{ width, height, position: 'relative' }}
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
            <div className="absolute bottom-4 right-4 flex gap-2 items-center">
                <div className="bg-white bg-opacity-90 text-gray-700 px-3 py-1.5 rounded shadow text-sm pointer-events-none z-10">
                    {nodes.length} 个节点，{edges.length} 条边 {zoomLevel !== 1 && `(缩放：${Math.round(zoomLevel * 100)}%)`}
                </div>
                <div className="flex gap-1">
                    <button 
                        className="bg-white bg-opacity-90 hover:bg-white text-gray-700 px-2 py-1.5 rounded shadow text-sm z-10"
                        onClick={() => {
                            if (svgRef.current) {
                                const svg = select(svgRef.current);
                                svg.transition().duration(300).call((d3Zoom<SVGSVGElement, unknown>()).scaleBy, 1.2);
                            }
                        }}
                        title="放大"
                    >
                        +
                    </button>
                    <button 
                        className="bg-white bg-opacity-90 hover:bg-white text-gray-700 px-2 py-1.5 rounded shadow text-sm z-10"
                        onClick={() => {
                            if (svgRef.current) {
                                const svg = select(svgRef.current);
                                svg.transition().duration(300).call((d3Zoom<SVGSVGElement, unknown>()).scaleBy, 0.8);
                            }
                        }}
                        title="缩小"
                    >
                        −
                    </button>
                    <button 
                        className="bg-white bg-opacity-90 hover:bg-white text-gray-700 px-2 py-1.5 rounded shadow text-sm z-10"
                        onClick={() => {
                            if (svgRef.current) {
                                const svg = select(svgRef.current);
                                svg.transition().duration(300).call((d3Zoom<SVGSVGElement, unknown>()).scaleTo, 1);
                            }
                        }}
                        title="重置缩放"
                    >
                        ⟲
                    </button>
                </div>
            </div>
        </div>
    );
};

export default D3ForceGraph;