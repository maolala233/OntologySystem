import dagre from 'dagre';
import { Node, Edge } from 'reactflow';

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

    return { nodes: layoutedNodes, edges };
};