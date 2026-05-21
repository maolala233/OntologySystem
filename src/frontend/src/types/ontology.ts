import { Node, Edge } from 'reactflow';
import { KnowledgeDomain } from '../api/domains';

export interface DataPropertyDef {
    name: string;
    description?: string;
    data_type: string;
}

export interface OntologyNodeData {
    label: string;
    labelEn?: string;
    type: string;
    class_label?: string;
    properties?: Record<string, any>;
    property_definitions?: DataPropertyDef[];
    currentLang?: 'zh' | 'en';
}

export type OntologyNode = Node<OntologyNodeData>;

export interface OntologyEdgeData {
    label: string;
    prop_id?: string;
    cardinality?: string;
    description?: string;
    relation?: string;
}

export type OntologyEdge = Edge<OntologyEdgeData>;

export interface ExtractionMetadata {
    total_chunks?: number;
    successful_chunks?: number;
    failed_chunks?: number;
    success_rate?: number;
    total_classes?: number;
    total_object_properties?: number;
    total_instances?: number;
    total_edges?: number;
    discarded_edges_count?: number;
    deduplication_stats?: Record<string, any>;
}

export interface User {
  id: number;
  username: string;
}

export interface ProjectData {
  id: number;
  name: string;
  description?: string;
  graph_data?: {
    nodes: OntologyNode[];
    edges: OntologyEdge[];
  };
  is_published: boolean;
  owner?: User;
  owner_id?: number;
  domain_id?: number;
  domain?: KnowledgeDomain;
  created_at?: string;
  updated_at?: string;
}
