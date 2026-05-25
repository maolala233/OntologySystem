import React, { useEffect, useRef, useState, useCallback } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY } from 'd3-force';
import { select } from 'd3-selection';
import { drag as d3Drag } from 'd3-drag';
import { zoom as d3Zoom } from 'd3-zoom';
import { transition } from 'd3-transition';
import { message } from 'antd';
import { OntologyNode, OntologyEdge } from '../../types/ontology';

const NODE_TYPES = {
    CLASS: 'owl:Class',
    INDIVIDUAL: 'owl:NamedIndividual',
    PROPERTY: 'owl:ObjectProperty'
};

const NODE_COLORS = {
    [NODE_TYPES.CLASS]: { fill: '#4a90d9', stroke: '#2d6cb4', text: '#ffffff' },
    [NODE_TYPES.INDIVIDUAL]: { fill: '#f79767', stroke: '#d4703f', text: '#ffffff' },
    [NODE_TYPES.PROPERTY]: { fill: '#c990c0', stroke: '#9e6b96', text: '#ffffff' },
    DEFAULT: { fill: '#666', stroke: '#444', text: '#ffffff' }
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
    edgeAction: '#a0a0a0',
};

const EDGE_CLICK_WIDTH = 20;

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
    const [spacingSlider, setSpacingSlider] = useState(50);
    const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
    const pinnedNodesRef = useRef<Set<string>>(new Set());
    const selectedNodeIdRef = useRef<string | null>(null);
    const prevNodeIdsRef = useRef<Set<string>>(new Set());
    const prevEdgeIdsRef = useRef<Set<string>>(new Set());
    const d3NodesRef = useRef<Map<string, any>>(new Map());
    const zoomBehaviorRef = useRef<any>(null);

    const onNodeClickRef = useRef(onNodeClick);
    onNodeClickRef.current = onNodeClick;
    const onEdgeClickRef = useRef(onEdgeClick);
    onEdgeClickRef.current = onEdgeClick;
    const onNodesChangeRef = useRef(onNodesChange);
    onNodesChangeRef.current = onNodesChange;
    const onNodeRightClickRef = useRef(onNodeRightClick);
    onNodeRightClickRef.current = onNodeRightClick;

    useEffect(() => {
        const updateContainerSize = () => {
            if (containerRef.current) {
                const rect = containerRef.current.getBoundingClientRect();
                const parentElement = containerRef.current.parentElement;
                let parentHeight = rect.height;
                let parentWidth = rect.width;
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
        updateContainerSize();
        window.addEventListener('resize', updateContainerSize);
        return () => window.removeEventListener('resize', updateContainerSize);
    }, [propWidth, propHeight]);

    const width = propWidth || containerSize.width;
    const height = propHeight || containerSize.height;

    const getNodeRadius = useCallback((node: OntologyNode) => {
        const rawId = node.data?.raw_id || '';
        const isAction = rawId.startsWith('AT_') || node.id?.startsWith('AT_');
        const baseRadius = NODE_RADII[node.data?.type || NODE_TYPES.CLASS] || NODE_RADII[NODE_TYPES.CLASS];
        return isAction ? baseRadius * 0.85 : baseRadius;
    }, []);

    const isInstanceEdge = useCallback((edge: any) => {
        const sourceType = edge.source?.data?.type || edge.source?.type;
        const targetType = edge.target?.data?.type || edge.target?.type;
        return sourceType === NODE_TYPES.INDIVIDUAL || targetType === NODE_TYPES.INDIVIDUAL;
    }, []);

    const isActionEdge = useCallback((edge: any) => {
        const rel = edge.data?.relation || edge.originalEdge?.data?.relation;
        return rel === 'action';
    }, []);

    const getAdaptiveForceParams = useCallback(() => {
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

        const sliderFactor = (spacingSlider - 50) / 50;

        return {
            linkDistance: Math.round(baseLinkDistance * (1 + sliderFactor * 0.5)),
            chargeStrength: Math.round(baseChargeStrength * (1 + sliderFactor * 0.3)),
            chargeDistanceMax: Math.round(baseChargeDistanceMax * (1 + sliderFactor * 0.3)),
            collisionRadius: Math.round(baseCollisionRadius * (1 + sliderFactor * 0.3)),
            centerStrength: parseFloat((baseCenterStrength * (1 - sliderFactor * 0.2)).toFixed(3)),
            linkStrength: parseFloat((baseLinkStrength * (1 - sliderFactor * 0.1)).toFixed(2)),
            collisionStrength: parseFloat(baseCollisionStrength.toFixed(2))
        };
    }, [nodes.length, spacingSlider]);

    const truncateLabel = (label: string, maxLen: number) => {
        if (!label) return '';
        return label.length > maxLen ? label.substring(0, maxLen) + '…' : label;
    };

    const getNodeColors = useCallback((d: any) => {
        const isClass = d.type === NODE_TYPES.CLASS;
        const isInstance = d.type === NODE_TYPES.INDIVIDUAL;
        const rawId = d.originalNode?.data?.raw_id || '';
        const nodeId = d.originalNode?.id || '';
        const isActionInstance = isInstance && (d.originalNode?.data?._is_action_instance || rawId.startsWith('AT_'));
        const isActionType = isClass && (rawId.startsWith('AT_') || nodeId.startsWith('AT_'));

        if (isActionType) return { fill: '#555555', stroke: '#3a3a3a', text: '#ffffff' };
        if (isActionInstance) return { fill: '#8a8a8a', stroke: '#6a6a6a', text: '#ffffff' };
        return NODE_COLORS[d.type] || NODE_COLORS.DEFAULT;
    }, []);

    const getEdgeColor = useCallback((d: any) => {
        if (isActionEdge(d)) return LIGHT_THEME.edgeAction;
        return isInstanceEdge(d) ? LIGHT_THEME.edgeInstance : LIGHT_THEME.edge;
    }, [isInstanceEdge, isActionEdge]);

    const getEdgeDash = useCallback((d: any) => {
        if (isActionEdge(d)) return "6,3";
        return isInstanceEdge(d) ? "4,4" : "none";
    }, [isInstanceEdge, isActionEdge]);

    const getEdgeMarker = useCallback((d: any) => {
        if (isActionEdge(d)) return "url(#arrowhead-action)";
        return isInstanceEdge(d) ? "url(#arrowhead-instance)" : "url(#arrowhead-class)";
    }, [isInstanceEdge, isActionEdge]);

    const setupNodeContent = useCallback((nodeG: any, d: any) => {
        nodeG.selectAll("*").remove();

        const isClass = d.type === NODE_TYPES.CLASS;
        const isInstance = d.type === NODE_TYPES.INDIVIDUAL;
        const rawId = d.originalNode?.data?.raw_id || '';
        const nodeId = d.originalNode?.id || '';
        const isActionInstance = isInstance && (d.originalNode?.data?._is_action_instance || rawId.startsWith('AT_'));
        const isActionType = isClass && (rawId.startsWith('AT_') || nodeId.startsWith('AT_'));
        const isActionNode = isActionType || isActionInstance;

        const colors = getNodeColors(d);
        const label = d.originalNode?.data?.label || d.id;
        const classLabel = d.originalNode?.data?.class_label || '';
        const nodeRadius = d.radius;

        nodeG.append("circle")
            .attr("class", "node-shape")
            .attr("r", 0)
            .attr("fill", colors.fill)
            .attr("stroke", colors.stroke)
            .attr("stroke-width", isActionNode ? 1.5 : (isClass ? 2.5 : 1.5))
            .attr("filter", d.id === highlightNodeId ? "url(#glow-selected)" : null)
            .transition()
            .duration(400)
            .ease((t: number) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
            .attr("r", nodeRadius);

        if (isActionType) {
            nodeG.append("text")
                .attr("class", "node-action-icon")
                .attr("text-anchor", "middle")
                .attr("dy", -nodeRadius - 6)
                .style("fill", '#888')
                .style("font-size", "8px")
                .style("pointer-events", "none")
                .style("opacity", 0)
                .text("⚡")
                .transition()
                .delay(200)
                .duration(300)
                .style("opacity", 1);
        }

        nodeG.append("text")
            .attr("class", "node-label")
            .attr("text-anchor", "middle")
            .attr("dominant-baseline", "central")
            .style("fill", colors.text)
            .style("font-size", isActionNode ? "10px" : (isClass ? "11px" : "9px"))
            .style("font-weight", isActionNode ? "500" : (isClass ? "600" : "500"))
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
                .style("fill", isActionInstance ? '#777' : '#999')
                .style("font-size", "9px")
                .style("pointer-events", "none")
                .style("opacity", 0)
                .text(truncateLabel(classLabel, 10))
                .transition()
                .delay(200)
                .duration(300)
                .style("opacity", 1);
        }
    }, [highlightNodeId, getNodeColors]);

    const renderGraph = useCallback(() => {
        if (!svgRef.current || nodes.length === 0) return;

        const svg = select(svgRef.current);
        const centerX = width / 2;
        const centerY = height / 2;

        let g = svg.select<SVGGElement>("g.main-group");
        if (g.empty()) {
            svg.selectAll("*").remove();
            g = svg.append("g").attr("class", "main-group");
        }

        const markers = svg.select<SVGGElement>("defs.markers");
        if (markers.empty()) {
            svg.append("defs").attr("class", "markers")
                .html(`
                    <marker id="arrowhead-class" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                        <polygon points="0 0, 8 3, 0 6" fill="#c0c4cc" />
                    </marker>
                    <marker id="arrowhead-instance" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                        <polygon points="0 0, 8 3, 0 6" fill="#e0c8b8" />
                    </marker>
                    <marker id="arrowhead-action" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                        <polygon points="0 0, 8 3, 0 6" fill="#a0a0a0" />
                    </marker>
                    <marker id="arrowhead-hl" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                        <polygon points="0 0, 8 3, 0 6" fill="#909399" />
                    </marker>
                    <filter id="glow-selected" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="3" result="blur" />
                        <feFlood flood-color="#409eff" flood-opacity="0.6" result="color" />
                        <feComposite in="color" in2="blur" operator="in" result="shadow" />
                        <feMerge>
                            <feMergeNode in="shadow" />
                            <feMergeNode in="SourceGraphic" />
                        </feMerge>
                    </filter>
                    <filter id="glow-hover" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="2" result="blur" />
                        <feFlood flood-color="#66b1ff" flood-opacity="0.4" result="color" />
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
                type: node.data?.type || NODE_TYPES.CLASS,
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
                .iterations(3))
            .force("x", forceX(centerX).strength(0.03))
            .force("y", forceY(centerY).strength(0.03))
            .alphaDecay(0.02)
            .velocityDecay(0.4);

        if (newNodeIds.size > 0 && prevNodeIdsRef.current.size > 0) {
            simulation.alpha(0.5);
        } else if (prevNodeIdsRef.current.size > 0) {
            simulation.alpha(0.3);
        }

        simulationRef.current = simulation;

        // ── Edges: enter/update/exit ──
        const linkSelection = g.selectAll<SVGPathElement, any>("path.link")
            .data(d3Links, (d: any) => d.id);

        linkSelection.exit()
            .transition()
            .duration(200)
            .style("opacity", 0)
            .remove();

        const linkEnter = linkSelection.enter()
            .append("path")
            .attr("class", "link")
            .attr("fill", "none")
            .style("opacity", 0)
            .attr("stroke", (d: any) => getEdgeColor(d))
            .attr("stroke-opacity", 0.7)
            .attr("stroke-width", 1.5)
            .attr("stroke-dasharray", (d: any) => getEdgeDash(d))
            .attr("marker-end", (d: any) => getEdgeMarker(d))
            .attr("data-edge-id", (d: any) => d.id)
            .style("cursor", "pointer");

        linkEnter.transition()
            .duration(400)
            .style("opacity", 1);

        const linkMerge = linkEnter.merge(linkSelection as any);

        linkMerge
            .attr("stroke", (d: any) => getEdgeColor(d))
            .attr("stroke-dasharray", (d: any) => getEdgeDash(d))
            .attr("marker-end", (d: any) => getEdgeMarker(d));

        linkMerge.each(function(this: SVGPathElement, d: any) {
            const edgeLabel = d.data?.label || d.data?.relation || '';
            select(this)
                .on("click", (event: MouseEvent) => {
                    event.stopPropagation();
                    if (onEdgeClickRef.current && d.originalEdge) onEdgeClickRef.current(d.originalEdge);
                })
                .on("mouseenter", function(this: SVGPathElement, event: MouseEvent) {
                    event.stopPropagation();
                    select(this)
                        .attr("stroke", LIGHT_THEME.edgeHighlight)
                        .attr("stroke-width", 2.5)
                        .attr("marker-end", "url(#arrowhead-hl)");

                    const pathElement = this as SVGPathElement;
                    const pathLength = pathElement.getTotalLength();
                    if (pathLength === 0) return;
                    const midPoint = pathElement.getPointAtLength(pathLength / 2);

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
                            .attr("fill", "#fff")
                            .attr("stroke", "#bbb")
                            .attr("stroke-width", 0.5)
                            .attr("rx", 3)
                            .attr("ry", 3)
                            .style("opacity", 0.95);
                        labelGroup.append("text")
                            .attr("class", "edge-label-text")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "central")
                            .style("fill", "#555")
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
                            .attr("x", midPoint.x - textBBox.width / 2 - padding)
                            .attr("y", midPoint.y - textBBox.height / 2 - padding)
                            .attr("width", textBBox.width + padding * 2)
                            .attr("height", textBBox.height + padding * 2)
                            .style("display", "block");
                        labelText.attr("x", midPoint.x).attr("y", midPoint.y);
                    }
                    labelGroup.style("display", "block").raise();
                })
                .on("mouseleave", function(this: SVGPathElement, event: MouseEvent) {
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
            });

        // ── Nodes: enter/update/exit ──
        const dragStartPos = { x: 0, y: 0 };

        const nodeSelection = g.selectAll<SVGGElement, any>("g.node-group")
            .data(d3Nodes, (d: any) => d.id);

        nodeSelection.exit()
            .transition()
            .duration(250)
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
                            .attr("filter", function(this: SVGElement) {
                                const parentG = this.parentElement;
                                const nodeData = select(parentG).datum() as any;
                                return nodeData?.id === d.id ? "url(#glow-selected)" : null;
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

        nodeEnter.each(function(this: SVGGElement, d: any) {
            setupNodeContent(select(this), d);
        });

        nodeEnter.transition()
            .duration(400)
            .ease((t: number) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
            .style("opacity", 1);

        // ── Update existing nodes (e.g. highlightNodeId changed) ──
        nodeSelection.each(function(this: SVGGElement, d: any) {
            const nodeG = select(this);
            const shape = nodeG.select(".node-shape");
            if (!shape.empty()) {
                shape.attr("filter", d.id === highlightNodeId ? "url(#glow-selected)" : null);
            }
        });

        g.selectAll("g.node-group").raise();

        const allNodes = nodeEnter.merge(nodeSelection as any);

        allNodes
            .on("contextmenu", (event: MouseEvent, d: any) => {
                event.preventDefault();
                event.stopPropagation();
                if (d.data?.type === NODE_TYPES.CLASS && onNodeRightClickRef.current) {
                    onNodeRightClickRef.current(d.originalNode);
                } else if (d.data?.type !== NODE_TYPES.CLASS) {
                    message.info('只有类节点支持右键展开实例');
                }
            })
            .on("mouseenter", function(this: SVGGElement, event: MouseEvent, d: any) {
                if (d.id !== selectedNodeIdRef.current) {
                    select(this).select(".node-shape").attr("filter", "url(#glow-hover)");
                }
                const tooltip = d.originalNode?.data?.properties || {};
                const propKeys = Object.keys(tooltip).filter(k => !k.startsWith('_'));
                const sourceDoc = d.originalNode?.data?.source_document || d.originalNode?.data?._source_file;
                const lines: string[] = [];
                if (sourceDoc) {
                    lines.push(`📄 ${sourceDoc}`);
                }
                lines.push(...propKeys.slice(0, 5).map(k => `${k}: ${String(tooltip[k]).substring(0, 20)}`));
                if (lines.length > 0) {
                    const labelGroupId = `node-tooltip-${d.id}`;
                    let labelGroup = svg.select<SVGGElement>(`g#${labelGroupId}`);
                    if (labelGroup.empty()) {
                        labelGroup = svg.append("g")
                            .attr("id", labelGroupId)
                            .attr("class", "edge-label-group")
                            .style("pointer-events", "none")
                            .raise();
                        labelGroup.append("rect")
                            .attr("class", "tooltip-bg")
                            .attr("fill", "#fff")
                            .attr("stroke", "#ddd")
                            .attr("stroke-width", 0.5)
                            .attr("rx", 4)
                            .attr("ry", 4)
                            .style("opacity", 0.95);
                    }
                    const tooltipBg = labelGroup.select<SVGRectElement>("rect.tooltip-bg");
                    labelGroup.selectAll("text.tooltip-line").remove();

                    const lineHeight = 14;
                    const startY = -((lines.length - 1) * lineHeight) / 2;

                    lines.forEach((line, i) => {
                        labelGroup.append("text")
                            .attr("class", "tooltip-line")
                            .attr("text-anchor", "middle")
                            .attr("dominant-baseline", "central")
                            .attr("dy", startY + i * lineHeight)
                            .style("fill", i === 0 && sourceDoc ? "#1890ff" : "#666")
                            .style("font-size", "9px")
                            .style("pointer-events", "none")
                            .text(line);
                    });

                    const offsetY = -(d.radius) - 10 - (lines.length * lineHeight) / 2;

                    setTimeout(() => {
                        const textBBox = (labelGroup.node() as SVGGElement)?.getBBox();
                        if (textBBox) {
                            const padding = 6;
                            tooltipBg
                                .attr("x", textBBox.x - padding)
                                .attr("y", textBBox.y - padding)
                                .attr("width", textBBox.width + padding * 2)
                                .attr("height", textBBox.height + padding * 2);
                        }
                        labelGroup
                            .attr("transform", `translate(${d.x || 0}, ${(d.y || 0) + offsetY})`)
                            .style("display", "block").raise();
                    }, 0);
                }
            })
            .on("mouseleave", function(this: SVGGElement, event: MouseEvent, d: any) {
                if (d.id !== selectedNodeIdRef.current) {
                    select(this).select(".node-shape").attr("filter", null);
                }
                svg.selectAll(`g#node-tooltip-${d.id}`).style("display", "none");
            });

        // ── Tick handler ──
        simulation.on("tick", () => {
            const updatePath = (path: any) => {
                path.attr("d", (d: any) => {
                    const source = d.source as any;
                    const target = d.target as any;
                    if (!source || !target || source.x == null || target.x == null) return "";

                    const dx = target.x - source.x;
                    const dy = target.y - source.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist === 0) return "";

                    const angle = Math.atan2(dy, dx);
                    const sourceR = source.radius || NODE_RADII[NODE_TYPES.CLASS];
                    const targetR = target.radius || NODE_RADII[NODE_TYPES.CLASS];

                    const sourceX = source.x + Math.cos(angle) * sourceR;
                    const sourceY = source.y + Math.sin(angle) * sourceR;
                    const targetX = target.x - Math.cos(angle) * (targetR + 6);
                    const targetY = target.y - Math.sin(angle) * (targetR + 6);

                    return `M${sourceX},${sourceY}L${targetX},${targetY}`;
                });
            };

            updatePath(linkMerge);
            updatePath(invisibleLinkGroup.selectAll("path.invisible-link"));

            g.selectAll<SVGGElement, any>("g.node-group")
                .attr("transform", (d: any) => `translate(${d.x || 0}, ${d.y || 0})`);
        });

        svg.on("click", () => {
            selectedNodeIdRef.current = null;
            svg.selectAll("g.node-group .node-shape").attr("filter", null);
            svg.selectAll("g.edge-label-group").style("display", "none");
        });

    }, [nodes, edges, width, height, getNodeRadius, isInstanceEdge, isActionEdge, highlightNodeId, getAdaptiveForceParams, getEdgeColor, getEdgeDash, getEdgeMarker, getNodeColors, setupNodeContent]);

    useEffect(() => {
        if (!svgRef.current) return;
        const svg = select(svgRef.current);
        const zoomBehavior = d3Zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.1, 4])
            .on("zoom", (event: any) => {
                const transform = event.transform;
                setZoomLevel(transform.k);
                svg.select("g.main-group").attr("transform", transform.toString());
            });
        svg.call(zoomBehavior);
        zoomBehaviorRef.current = zoomBehavior;
        return () => { svg.on(".zoom", null); };
    }, []);

    const handleSliderChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const newValue = parseInt(e.target.value, 10);
        setSpacingSlider(newValue);
        setTimeout(() => {
            if (simulationRef.current) {
                const forceParams = getAdaptiveForceParams();
                simulationRef.current.force("link")?.distance(forceParams.linkDistance);
                simulationRef.current.force("charge")?.strength(forceParams.chargeStrength);
                simulationRef.current.alpha(0.3).restart();
            }
        }, 50);
    }, [getAdaptiveForceParams]);

    const getSliderLabel = () => {
        if (spacingSlider < 25) return '紧凑';
        if (spacingSlider < 50) return '较紧';
        if (spacingSlider === 50) return '标准';
        if (spacingSlider < 75) return '较松';
        return '宽松';
    };

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
            <div className="fixed top-[80px] right-4 bg-white bg-opacity-95 rounded-lg shadow-lg p-3 z-[1000] w-64">
                <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600">节点间距</span>
                    <span className="text-xs font-medium text-gray-500">{getSliderLabel()}</span>
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

            <div className="absolute bottom-4 left-4 bg-white bg-opacity-90 rounded shadow px-3 py-2 z-10 text-xs text-gray-500">
                <div className="flex items-center gap-3 flex-wrap">
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 12, height: 12, backgroundColor: '#4a90d9', borderRadius: '50%', border: '2px solid #2d6cb4' }}></span>
                        类
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 10, height: 10, backgroundColor: '#f79767', borderRadius: '50%', border: '1.5px solid #d4703f' }}></span>
                        实例
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 10, height: 10, backgroundColor: '#555555', borderRadius: '50%', border: '1.5px solid #3a3a3a' }}></span>
                        动作类型
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 9, height: 9, backgroundColor: '#8a8a8a', borderRadius: '50%', border: '1.5px solid #6a6a6a' }}></span>
                        动作实例
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 16, height: 0, borderTop: '1.5px dashed #e0c8b8' }}></span>
                        类型
                    </span>
                    <span className="flex items-center gap-1">
                        <span style={{ display: 'inline-block', width: 16, height: 0, borderTop: '1.5px solid #c0c4cc' }}></span>
                        关系
                    </span>
                </div>
            </div>
        </div>
    );
};

export default D3ForceGraph;
