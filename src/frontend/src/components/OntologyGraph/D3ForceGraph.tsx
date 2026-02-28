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
    edge: '#999999',
    stroke: '#ffffff'
};

interface D3ForceGraphProps {
    nodes: OntologyNode[];
    edges: OntologyEdge[];
    onNodeClick?: (node: OntologyNode | null) => void;
    onEdgeClick?: (edge: OntologyEdge) => void;
    onNodesChange?: (newNodes: OntologyNode[]) => void;
    width?: number;
    height?: number;
    className?: string;
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
    width = window.innerWidth - 300,
    height = window.innerHeight - 150,
    className = ''
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

        // 准备节点数据
        const nodeMap = new Map<string, any>();
        const d3Nodes = nodes.map(node => {
            const existingNode = simulationRef.current?.nodes().find((n: any) => n.id === node.id);
            const nodeData = {
                id: node.id,
                x: existingNode?.x || centerX + (Math.random() - 0.5) * width * 0.5,
                y: existingNode?.y || centerY + (Math.random() - 0.5) * height * 0.5,
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

        // 准备边数据 - 确保 source 和 target 引用正确的节点对象
        const d3Links = edges.map(edge => {
            const sourceNode = nodeMap.get(edge.source);
            const targetNode = nodeMap.get(edge.target);
            return {
                source: sourceNode || edge.source,
                target: targetNode || edge.target,
                data: edge.data,
                id: `${edge.source}-${edge.target}`
            };
        });

        // 创建力模拟 - 使用更稳定的参数
        const simulation = forceSimulation(d3Nodes)
            .force("link", forceLink(d3Links)
                .id((d: any) => d.id)
                .distance(200)
                .strength(0.8))  // 增加链接强度，让节点更稳定
            .force("charge", forceManyBody()
                .strength(-500)   // 减少斥力，避免节点过度分散
                .distanceMax(500)) // 限制斥力作用距离
            .force("center", forceCenter(centerX, centerY)
                .strength(0.1))    // 增加中心引力
            .force("collision", forceCollide()
                .radius((d: any) => d.radius + 5)
                .strength(0.5)
                .iterations(2));

        simulationRef.current = simulation;

        // 渲染边
        const linkSelection = g.selectAll<SVGLineElement, any>("line.link")
            .data(d3Links, (d: any) => d.id);

        const linkEnter = linkSelection.enter()
            .append("line")
            .attr("class", "link")
            .attr("stroke", LIGHT_THEME.edge)
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", 2);

        const linkMerge = linkEnter.merge(linkSelection as any);

        linkSelection.exit().remove();

        // 渲染节点组
        const nodeSelection = g.selectAll<SVGGElement, any>("g.node-group")
            .data(d3Nodes, (d: any) => d.id);

        const nodeEnter = nodeSelection.enter()
            .append("g")
            .attr("class", "node-group")
            .style("cursor", "grab")
            .call(d3Drag<any, any>()
                .on("start", (event: any, d: any) => {
                    // 拖动时不重启模拟，只固定当前节点位置
                    d.fx = d.x;
                    d.fy = d.y;
                    setIsDragging(true);
                })
                .on("drag", (event: any, d: any) => {
                    // 直接更新固定位置，不触发模拟重新计算
                    d.fx = event.x;
                    d.fy = event.y;
                })
                .on("end", (event: any, d: any) => {
                    // 释放节点，但不重启模拟
                    d.fx = null;
                    d.fy = null;
                    setIsDragging(false);
                    
                    // 只更新被拖动的节点位置，避免影响其他节点的状态
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
            .attr("stroke-width", 2);

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
        nodeEnter.merge(nodeSelection as any)
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
            });

        nodeSelection.exit().remove();

        // 每一帧更新位置
        simulation.on("tick", () => {
            // 更新边的位置
            linkMerge
                .attr("x1", (d: any) => (d.source as any).x || 0)
                .attr("y1", (d: any) => (d.source as any).y || 0)
                .attr("x2", (d: any) => (d.target as any).x || 0)
                .attr("y2", (d: any) => (d.target as any).y || 0);

            // 更新节点组的位置
            g.selectAll<SVGGElement, any>("g.node-group")
                .attr("transform", (d: any) => `translate(${d.x || 0}, ${d.y || 0})`);
        });

    }, [nodes, edges, width, height, getNodeColor, getNodeRadius, onNodeClick, onNodesChange]);

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
    }, [nodes, edges, renderGraph]);

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
            style={{ width, height, position: 'relative', border: '1px solid #e0e0e0' }}
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
                onClick={() => {
                    if (onNodeClick) {
                        onNodeClick(null);
                    }
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