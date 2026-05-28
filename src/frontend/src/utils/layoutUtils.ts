import dagre from 'dagre';
import { Node, Edge, MarkerType } from 'reactflow';

const normalizeEdge = (edge: Edge): Edge => ({
    ...edge,
    type: 'custom',
    markerEnd: edge.markerEnd || { type: MarkerType.ArrowClosed, color: '#b1b1b7' },
    style: edge.style || { stroke: '#b1b1b7', strokeWidth: 1.5 },
});

export const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = 'LR') => {
    if (nodes.length === 0) return { nodes: [], edges: [] };

    const dagreGraph = new dagre.graphlib.Graph();
    dagreGraph.setDefaultEdgeLabel(() => ({}));

    const nodeWidth = 180;
    const nodeHeight = 60;

    dagreGraph.setGraph({
        rankdir: direction,
        ranksep: 100,
        nodesep: 80,
        marginx: 100,
        marginy: 100
    });

    const connectedNodeIds = new Set<string>();
    edges.forEach((edge) => {
        connectedNodeIds.add(edge.source);
        connectedNodeIds.add(edge.target);
    });

    const connectedNodes = nodes.filter(n => connectedNodeIds.has(n.id));
    const isolatedNodes = nodes.filter(n => !connectedNodeIds.has(n.id));

    connectedNodes.forEach((node) => {
        dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
    });

    edges.forEach((edge) => {
        dagreGraph.setEdge(edge.source, edge.target);
    });

    dagre.layout(dagreGraph);

    let maxX = 0;
    let maxY = 0;

    const layoutedConnectedNodes = connectedNodes.map((node) => {
        const nodeWithPosition = dagreGraph.node(node.id);
        const noiseX = Math.random() * 20 - 10;
        const noiseY = Math.random() * 20 - 10;
        const x = nodeWithPosition.x - nodeWidth / 2 + noiseX;
        const y = nodeWithPosition.y - nodeHeight / 2 + noiseY;
        if (x + nodeWidth > maxX) maxX = x + nodeWidth;
        if (y + nodeHeight > maxY) maxY = y + nodeHeight;
        return {
            ...node,
            position: { x, y },
        };
    });

    const cols = Math.max(1, Math.ceil(Math.sqrt(isolatedNodes.length)));
    const gridStartX = maxX > 0 ? maxX + 150 : 100;
    const gridStartY = 100;
    const colSpacing = 280;
    const rowSpacing = 160;

    const layoutedIsolatedNodes = isolatedNodes.map((node, idx) => {
        const col = idx % cols;
        const row = Math.floor(idx / cols);
        return {
            ...node,
            position: {
                x: gridStartX + col * colSpacing,
                y: gridStartY + row * rowSpacing,
            },
        };
    });

    const layoutedNodes = [...layoutedConnectedNodes, ...layoutedIsolatedNodes];
    const layoutedEdges = edges.map(normalizeEdge);

    return { nodes: layoutedNodes, edges: layoutedEdges };
};
