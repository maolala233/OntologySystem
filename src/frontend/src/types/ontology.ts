import { Node, Edge } from 'reactflow';
import { KnowledgeDomain } from '../api/domains';

// 合并两个 OntologyNodeData 接口定义
export interface OntologyNodeData {
    label: string;
    labelEn?: string; // 支持英文
    type: string; // 'owl:Class' | 'owl:NamedIndividual' 等
    properties?: Record<string, any>;
    currentLang?: 'zh' | 'en';
}

export type OntologyNode = Node<OntologyNodeData>;

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
  domain_id?: number;
  domain?: KnowledgeDomain;
  created_at?: string;
  updated_at?: string;
}
