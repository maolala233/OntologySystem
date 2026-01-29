import { Node, Edge } from 'reactflow';

export interface OntologyNodeData {
  label: string;
  type: string;
  properties: Record<string, any>;
}

export type OntologyNode = Node<OntologyNodeData>;

export interface OntologyNodeData {
    label: string;
    labelEn?: string; // 支持英文
    type: string; // 'owl:Class' | 'owl:NamedIndividual' 等
    properties?: Record<string, any>;
    currentLang?: 'zh' | 'en';
}

export type OntologyEdge = Edge;

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
  created_at?: string;
  updated_at?: string;
}
