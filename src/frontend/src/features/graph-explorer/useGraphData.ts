/**
 * 图数据装载（06 §4.3）：meta → nodes×N 页 → edges → graphology Graph → 布局。
 * 布局策略（06 §8/§10）：服务端预布局坐标优先；≤48 节点确定性网格；否则 FA2。
 */
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Graph from 'graphology';

import { graphApi, GraphEdge, GraphMeta, GraphNode } from '../../api/graphview';
import { GRAPH_THEME, confidenceBand, groupColor } from './colors';

export type ColorField = 'class' | 'confidence';

export interface LoadResult {
    graph: Graph;
    meta: GraphMeta;
    colorField: ColorField;
}

/** 确定性网格布局（≤48 节点；连通分量排序无随机，06 §8.2 简化版） */
export function gridLayout(graph: Graph): void {
    const nodes = graph.nodes();
    const visited = new Set<string>();
    const components: string[][] = [];
    for (const n of nodes) {
        if (visited.has(n)) continue;
        const comp: string[] = [];
        const stack = [n];
        while (stack.length) {
            const cur = stack.pop() as string;
            if (visited.has(cur)) continue;
            visited.add(cur);
            comp.push(cur);
            graph.forEachNeighbor(cur, (nb) => stack.push(nb));
        }
        comp.sort((a, b) => graph.degree(b) - graph.degree(a) || a.localeCompare(b));
        components.push(comp);
    }
    components.sort((a, b) => b.length - a.length);
    const colGap = 320;
    const rowGap = 240;
    let col = 0;
    let row = 0;
    for (const comp of components) {
        const radius = 96;
        comp.forEach((n, i) => {
            const cx = col * colGap;
            const cy = row * rowGap;
            if (comp.length === 1) {
                graph.setNodeAttribute(n, 'x', cx);
                graph.setNodeAttribute(n, 'y', cy);
            } else {
                const angle = (2 * Math.PI * i) / comp.length - Math.PI / 2;
                graph.setNodeAttribute(n, 'x', cx + radius * Math.cos(angle));
                graph.setNodeAttribute(n, 'y', cy + radius * Math.sin(angle));
            }
        });
        col += 1;
        if (col >= Math.ceil(Math.sqrt(components.length))) {
            col = 0;
            row += 1;
        }
    }
}

/** FA2 分块渐进布局（同步迭代分片，避免长阻塞；≥1k 节点 Barnes-Hut） */
export function fa2Layout(graph: Graph, totalIterations = 300, chunk = 50): void {
    const count = graph.order;
    const settings = forceAtlas2.inferSettings(count);
    Object.assign(settings, {
        gravity: 0.6,
        scalingRatio: count > 500 ? 4 : 2,
        barnesHutOptimize: count > 1000,
        barnesHutTheta: 0.9,
        edgeWeightInfluence: 0.5,
        adjustSizes: true,
        slowDown: { 20: 12, 200: 8, 2000: 4 }[Math.min(count, 2000)] ?? 6,
    });
    // 初始环形位（确定性）
    const ns = graph.nodes();
    ns.forEach((n, i) => {
        const a = (2 * Math.PI * i) / ns.length;
        graph.setNodeAttribute(n, 'x', 100 * Math.cos(a));
        graph.setNodeAttribute(n, 'y', 100 * Math.sin(a));
    });
    let done = 0;
    while (done < totalIterations) {
        forceAtlas2.assign(graph, { iterations: Math.min(chunk, totalIterations - done), settings });
        done += chunk;
    }
}

export function nodeBaseColor(n: GraphNode, colorField: ColorField): string {
    if (n.kind === 'class') return GRAPH_THEME.classNode;
    if (colorField === 'confidence') return groupColor(confidenceBand(n.confidence));
    return groupColor(n.class_label || '未分类');
}

/**
 * 装载主流程：分页拉全（节点上限 maxNodes），edges 按 node_ids 批拉。
 * sourceOf 映射保留给"按来源文档着色"字段。
 */
export async function loadGraph(
    projectId: number,
    options: {
        colorField: ColorField;
        filters?: { class?: string; status?: string; source_doc?: string };
        maxNodes?: number;
        pageLimit?: number;
    },
): Promise<LoadResult> {
    const { colorField, filters = {}, maxNodes = 8000, pageLimit = 1000 } = options;
    const meta = await graphApi.meta(projectId);
    const graph = new Graph({ multi: true, type: 'directed' });

    // 1) 节点分页拉取
    const nodes: GraphNode[] = [];
    let cursor: number | null = 0;
    while (cursor !== null && nodes.length < maxNodes) {
        const page = await graphApi.nodes(projectId, {
            cursor, limit: pageLimit,
            class: filters.class, status: filters.status, source_doc: filters.source_doc,
        });
        nodes.push(...page.items);
        cursor = page.next_cursor;
    }
    // 2) 边（按已装载节点批拉）
    let edgeCursor: number | null = 0;
    const edges: GraphEdge[] = [];
    while (edgeCursor !== null && edges.length < maxNodes * 3) {
        const page = await graphApi.edges(projectId, {
            node_ids: nodes.map((n) => n.id).join(','),
            cursor: edgeCursor, limit: 2000,
        });
        edges.push(...page.items);
        edgeCursor = page.next_cursor;
    }

    // 3) 组装 graphology
    for (const n of nodes) {
        if (graph.hasNode(n.id)) continue;
        graph.addNode(n.id, {
            label: n.label,
            kind: n.kind,
            group: n.group,
            degree: n.degree,
            status: n.status,
            confidence: n.confidence,
            size: n.kind === 'class'
                ? Math.max(9, Math.min(18, 10 + n.degree / 4))
                : Math.max(5, Math.min(14, 5 + n.degree * 0.8)),
            color: nodeBaseColor(n, colorField),
            x: 0, y: 0,
        });
    }
    for (const e of edges) {
        if (!graph.hasNode(e.source) || !graph.hasNode(e.target) || graph.hasEdge(e.id)) continue;
        graph.addDirectedEdgeWithKey(e.id, e.source, e.target, {
            label: e.label,
            kind: e.kind,
            size: 1,
            color: GRAPH_THEME.edge,
            weight: e.weight ?? 1,
            hidden: false,
        });
    }
    // 4) 布局：服务端预布局坐标优先；小图网格；其余 FA2
    let usedServer = false;
    if (meta.layout_available) {
        try {
            const lay = await graphApi.layoutCoords(projectId);
            if (lay.available) {
                for (const n of graph.nodes()) {
                    const c = lay.coords[n];
                    if (c) {
                        graph.setNodeAttribute(n, 'x', c[0] * 100);
                        graph.setNodeAttribute(n, 'y', c[1] * 100);
                    }
                }
                usedServer = true;
            }
        } catch {
            /* 布局拉取失败走本地布局 */
        }
    }
    if (!usedServer) {
        if (graph.order <= 48) {
            gridLayout(graph);
        } else {
            fa2Layout(graph);
        }
    }
    return { graph, meta, colorField };
}
