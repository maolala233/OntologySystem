import dagre from 'dagre';
import { Node, Edge, MarkerType } from 'reactflow';

// 规范化边的类型，确保使用直线类型
const normalizeEdge = (edge: Edge): Edge => ({
    ...edge,
    type: 'straight',
    markerEnd: edge.markerEnd || { type: MarkerType.ArrowClosed, color: '#b1b1b7' },
    style: edge.style || { stroke: '#b1b1b7', strokeWidth: 1.5 },
});

export const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = 'LR') => {
    const dagreGraph = new dagre.graphlib.Graph();
    dagreGraph.setDefaultEdgeLabel(() => ({}));

    const nodeWidth = 180;
    const nodeHeight = 60;

    dagreGraph.setGraph({
        rankdir: direction,
        ranksep: 100, // 层级间距
        nodesep: 80,  // 节点间距
        marginx: 100,
        marginy: 100
    });

    nodes.forEach((node) => {
        dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
    });

    edges.forEach((edge) => {
        dagreGraph.setEdge(edge.source, edge.target);
    });

    dagre.layout(dagreGraph);

    const layoutedNodes = nodes.map((node) => {
        const nodeWithPosition = dagreGraph.node(node.id);

        // 加上随机偏移量，避免直线排列太死板，更像Neo4j
        const noiseX = Math.random() * 20 - 10;
        const noiseY = Math.random() * 20 - 10;

        return {
            ...node,
            position: {
                x: nodeWithPosition.x - nodeWidth / 2 + noiseX,
                y: nodeWithPosition.y - nodeHeight / 2 + noiseY,
            },
        };
    });

    // 规范化所有边为直线类型
    const layoutedEdges = edges.map(normalizeEdge);

    return { nodes: layoutedNodes, edges: layoutedEdges };
};
