import { Node, Edge } from 'reactflow';

export interface OntologyNodeData {
  label: string;
  type: string;
  properties: Record<string, any>;
}

export type OntologyNode = Node<OntologyNodeData>;

export interface OntologyEdgeData {
  label: string;
  relation: string;
}

export type OntologyEdge = Edge<OntologyEdgeData>;

export interface User {
  id: number;
  username: string;
  email: string;
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
  created_at?: string;
  updated_at?: string;
}
