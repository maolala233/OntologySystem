/**
 * M4 图视图 API（03 §13 契约 / 06 §4）——graph-explorer 数据源。
 */
import apiClient from './client';

export interface GraphNode {
    id: string;
    uri: string;
    label: string;
    kind: 'class' | 'instance';
    group: string;
    class_label: string;
    degree: number;
    status: string;
    confidence: number | null;
    /** S16：资产来源四态 deterministic|ai|steward_edited|steward_specified */
    source_type?: string;
    certified?: boolean;
    x?: number | null;
    y?: number | null;
}

export interface GraphEdge {
    id: string;
    source: string;
    target: string;
    label: string;
    kind: 'relation' | 'type';
    directed: boolean;
    bidirectional: boolean;
    pairIndex: number;
    weight: number | null;
    group: string;
    status: string;
    /** S16：边来源四态 + 认证位 */
    source_type?: string; certified?: boolean;
    is_class_edge: boolean;
}

export interface GraphMeta {
    node_count: number;
    edge_count: number;
    class_distribution: { class_label: string; count: number }[];
    sources: string[];
    layout_available: boolean;
    layout_id: number | null;
    readonly: boolean;
}

export interface ProvenanceRow {
    id: number;
    document_id: number | null;
    document_name: string | null;
    chunk_index: number | null;
    evidence: string | null;
    char_start: number | null;
    char_end: number | null;
    method: string | null;
    checksum: string | null;
    created_at: string | null;
}

export interface NodeDetail {
    node: GraphNode;
    entity: {
        id: string; uri: string; label: string; label_normalized: string;
        class_label: string; kind: string; status: string;
        confidence: number | null; created_at: string | null; updated_at: string | null;
        instance_count: number | null;
        /** S16：来源四态 + 认证位 */
        source_type?: string; certified?: boolean;
    };
    properties: Record<string, any>;
    raw_props: Record<string, any>;
    provenance: ProvenanceRow[];
    relations: {
        id: string; predicate: string; direction: 'out' | 'in';
        other_id: string | null; other_label: string | null; other_class: string | null;
        confidence: number | null; status: string; is_class_edge: boolean;
    }[];
    degree: number;
    merge: {
        canonical_id: string | null;
        merged_children: { id: string; label: string }[];
        merge_reviews: { review_id: number; status: string; decided_at: string | null;
                         canonical_id: number | null; merged_ids: number[] | null }[];
    };
    reviews: { review_id: number; item_type: string; priority: string; status: string; reason: string | null }[];
    timeline: { valid_from: string | null; valid_until: string | null };
    source_docs: { document_id: number | null; name: string | null }[];
}

export interface EdgeDetail {
    edge: GraphEdge;
    subject: { id: string; label: string; class_label: string; kind: string; degree: number; uri: string; status: string } | null;
    object: { id: string; label: string; class_label: string; kind: string; degree: number; uri: string; status: string } | null;
    props: Record<string, any>;
    confidence: number | null;
    status: string;
    /** S16：边认证（certify 后展示/撤销） */
    source_type?: string; certified?: boolean;
    provenance: ProvenanceRow[];
}

export const graphApi = {
    meta: async (projectId: number): Promise<GraphMeta> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/meta`)).data,

    nodes: async (projectId: number, options?: {
        cursor?: number; limit?: number; class?: string; status?: string;
        source_doc?: string; q?: string;
    }): Promise<{ items: GraphNode[]; next_cursor: number | null }> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/nodes`, { params: options || {} })).data,

    edges: async (projectId: number, options?: {
        node_ids?: string; cursor?: number; limit?: number;
    }): Promise<{ items: GraphEdge[]; next_cursor: number | null }> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/edges`, { params: options || {} })).data,

    neighbors: async (projectId: number, nodeId: string, hops = 1): Promise<{
        root_id: string; nodes: GraphNode[]; edges: GraphEdge[];
    }> => (await apiClient.get(`/api/projects/${projectId}/graph/neighbors/${nodeId}?hops=${hops}`)).data,

    search: async (projectId: number, q: string, classFilter?: string): Promise<{ items: GraphNode[] }> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/search`, {
            params: { q, class: classFilter },
        })).data,

    nodeDetail: async (projectId: number, nodeId: string): Promise<NodeDetail> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/node/${nodeId}/detail`)).data,

    edgeDetail: async (projectId: number, edgeId: string): Promise<EdgeDetail> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/edge/${edgeId}/detail`)).data,

    layoutCoords: async (projectId: number): Promise<{ available: boolean; coords: Record<string, [number, number]> }> =>
        (await apiClient.get(`/api/projects/${projectId}/graph/layout`)).data,

    /** S16：一键人工认证/撤销（节点）；写 blob 后监听器同步行表 */
    certifyNode: async (projectId: number, nodeId: string, certified = true):
        Promise<{ id: string; certified: boolean; source_type: string }> =>
        (await apiClient.post(`/api/projects/${projectId}/graph/node/${nodeId}/certify?certified=${certified}`)).data,

    /** S16：一键人工认证/撤销（关系） */
    certifyEdge: async (projectId: number, edgeId: string, certified = true):
        Promise<{ id: string; certified: boolean; source_type: string }> =>
        (await apiClient.post(`/api/projects/${projectId}/graph/edge/${edgeId}/certify?certified=${certified}`)).data,
};

// ── R13：实例探索手动编辑（每次编辑后端落一版 kind=manual，可在「时间轴」追溯）──
export interface GraphEditResult {
    version_recorded: boolean;
    version_no: number | null;
    node_id?: string;
    edge_id?: string;
    class_label?: string;
    changes?: string[];
    deleted?: boolean;
    removed_edges?: number;
}

export const graphEditApi = {
    /** 新增实例 {class_node_id, label, properties?, position?} */
    addInstance: async (projectId: number, body: {
        class_node_id: string; label: string; properties?: Record<string, string>;
        position?: { x: number; y: number };
    }): Promise<GraphEditResult> =>
        (await apiClient.post(`/api/projects/${projectId}/graph/instances`, body)).data,

    /** 修改实例 {label?, class_node_id?, properties?} */
    updateInstance: async (projectId: number, nodeId: string, body: {
        label?: string; class_node_id?: string; properties?: Record<string, string>;
    }): Promise<GraphEditResult> =>
        (await apiClient.patch(`/api/projects/${projectId}/graph/instances/${encodeURIComponent(nodeId)}`, body)).data,

    /** 删除实例（连带其全部关系边） */
    deleteInstance: async (projectId: number, nodeId: string): Promise<GraphEditResult> =>
        (await apiClient.delete(`/api/projects/${projectId}/graph/instances/${encodeURIComponent(nodeId)}`)).data,

    /** 新增关系 {source_node_id, target_node_id, predicate} */
    addRelation: async (projectId: number, body: {
        source_node_id: string; target_node_id: string; predicate: string;
    }): Promise<GraphEditResult> =>
        (await apiClient.post(`/api/projects/${projectId}/graph/relations`, body)).data,

    /** 修改关系谓词 {predicate} */
    updateRelation: async (projectId: number, edgeId: string, body: {
        predicate: string;
    }): Promise<GraphEditResult> =>
        (await apiClient.patch(`/api/projects/${projectId}/graph/relations/${encodeURIComponent(edgeId)}`, body)).data,

    /** 删除关系 */
    deleteRelation: async (projectId: number, edgeId: string): Promise<GraphEditResult> =>
        (await apiClient.delete(`/api/projects/${projectId}/graph/relations/${encodeURIComponent(edgeId)}`)).data,
};
