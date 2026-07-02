export type GraphNode = {
  id: string;
  paper_id: string;
  node_type: string;
  title: string;
  summary: string;
  confidence: number;
  source_type: string;
  evidence_ids: string[];
  page_ranges: [number, number][];
  properties: Record<string, unknown>;
  created_by: string;
  verified: boolean;
};

export type GraphEdge = {
  id: string;
  paper_id: string;
  source_node_id: string;
  target_node_id: string;
  edge_type: string;
  confidence: number;
  evidence?: {
    page?: number | null;
    block_id?: string | null;
    text?: string | null;
    bbox?: number[] | null;
  } | null;
  inference_type: string;
  properties: Record<string, unknown>;
};

export type PaperArgumentGraph = {
  paper_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type ContentBlock = {
  content_id: string;
  order: number;
  page: number;
  section?: string | null;
  bbox?: number[] | null;
  text: string;
  block_type: string;
  semantic_role: string;
  citations: string[];
};

export type PaperSummary = {
  paper_id: string;
  title: string;
  abstract: string;
};

export type PatchOperation = {
  op: 'add_node' | 'update_node' | 'remove_node' | 'add_edge' | 'update_edge' | 'remove_edge';
  id?: string;
  changes?: Record<string, unknown>;
  node?: Partial<GraphNode>;
  edge?: Partial<GraphEdge>;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function uploadPaper(file: File): Promise<PaperArgumentGraph> {
  const formData = new FormData();
  formData.append('file', file);
  return request<PaperArgumentGraph>('/api/papers', { method: 'POST', body: formData });
}

export function listPapers(): Promise<PaperSummary[]> {
  return request<PaperSummary[]>('/api/papers');
}

export function fetchGraph(paperId: string): Promise<PaperArgumentGraph> {
  return request<PaperArgumentGraph>(`/api/papers/${paperId}/graph`);
}

export function fetchBlocks(paperId: string): Promise<ContentBlock[]> {
  return request<ContentBlock[]>(`/api/papers/${paperId}/blocks`);
}

export function patchGraph(paperId: string, operations: PatchOperation[]): Promise<PaperArgumentGraph> {
  return request<PaperArgumentGraph>(`/api/papers/${paperId}/graph/patch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
  });
}
