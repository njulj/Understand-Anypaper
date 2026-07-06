export type GraphNode = {
  id: string;
  paper_id: string;
  node_type: string;
  title: string;
  summary: string;
  confidence: number;
  source_type: string;
  semantic_unit_ids: string[];
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
  semantic_unit_ids: string[];
  inference_type: string;
  properties: Record<string, unknown>;
};

export type PaperArgumentGraph = {
  paper_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type PageSourceLocation = {
  page: number;
  bbox: number[];
  extracted_text: string;
  start_text: string;
  end_text: string;
  extraction_method: string;
};

export type SemanticUnit = {
  semantic_unit_id: string;
  paper_id: string;
  role: string;
  title: string;
  text: string;
  source_location: PageSourceLocation;
  confidence: number;
  created_by: string;
  properties: Record<string, unknown>;
};

export type PaperSummary = {
  paper_id: string;
  title: string;
  abstract: string;
  metadata?: Record<string, unknown>;
};

export type DocumentPageInfo = {
  page: number;
  width: number;
  height: number;
};

export type PaperDocumentInfo = {
  filename: string;
  media_type: string;
  pages: DocumentPageInfo[];
};

export type PatchOperation = {
  op: 'add_node' | 'update_node' | 'remove_node' | 'add_edge' | 'update_edge' | 'remove_edge';
  id?: string;
  changes?: Record<string, unknown>;
  node?: Partial<GraphNode>;
  edge?: Partial<GraphEdge>;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export type UploadProgress = {
  loaded: number;
  total: number;
  percent: number;
};

export type UploadStageProgress = {
  event: string;
  progress: number;
  message: string;
  graph?: PaperArgumentGraph;
  page_count?: number;
  semantic_unit_count?: number;
  node_count?: number;
  edge_count?: number;
};

export type UploadPaperOptions = {
  onUploadProgress?: (progress: UploadProgress) => void;
  onStageProgress?: (progress: UploadStageProgress) => void;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function uploadPaper(file: File, options?: UploadPaperOptions): Promise<PaperArgumentGraph> {
  const formData = new FormData();
  formData.append('file', file);
  const { onUploadProgress, onStageProgress } = options ?? {};

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let processedLength = 0;
    let settled = false;

    function settleWithGraph(graph: PaperArgumentGraph) {
      if (settled) return;
      settled = true;
      resolve(graph);
    }

    function settleWithError(error: Error) {
      if (settled) return;
      settled = true;
      reject(error);
    }

    function handleProgressLine(line: string) {
      if (!line.trim()) return;
      let progress: UploadStageProgress;
      try {
        progress = JSON.parse(line) as UploadStageProgress;
      } catch {
        settleWithError(new Error(`Invalid progress event: ${line}`));
        return;
      }

      onStageProgress?.(progress);
      if (progress.event === 'error') {
        settleWithError(new Error(progress.message || 'Upload failed.'));
        xhr.abort();
        return;
      }
      if (progress.event === 'complete' && progress.graph) {
        settleWithGraph(progress.graph);
      }
    }

    function processProgressLines(final = false) {
      const text = xhr.responseText.slice(processedLength);
      const lines = text.split('\n');
      const completeLines = final ? lines : lines.slice(0, -1);
      processedLength = final ? xhr.responseText.length : xhr.responseText.length - lines[lines.length - 1].length;
      completeLines.forEach(handleProgressLine);
    }

    xhr.open('POST', `${API_BASE_URL}/api/papers`);

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !onUploadProgress) return;
      onUploadProgress({
        loaded: event.loaded,
        total: event.total,
        percent: Math.round((event.loaded / event.total) * 100),
      });
    };

    xhr.onprogress = () => processProgressLines();

    xhr.onload = () => {
      processProgressLines(true);
      if (settled) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        settleWithError(new Error('Upload finished without a graph result.'));
        return;
      }
      settleWithError(new Error(xhr.responseText || xhr.statusText || `Request failed with HTTP ${xhr.status}`));
    };

    xhr.onerror = () => settleWithError(new Error('Upload failed.'));
    xhr.onabort = () => {
      if (!settled) settleWithError(new Error('Upload canceled.'));
    };
    xhr.send(formData);
  });
}

export function listPapers(): Promise<PaperSummary[]> {
  return request<PaperSummary[]>('/api/papers');
}

export function deletePaper(paperId: string): Promise<{ deleted: string; papers: PaperSummary[] }> {
  return request<{ deleted: string; papers: PaperSummary[] }>(`/api/papers/${paperId}`, {
    method: 'DELETE',
  });
}

export function fetchGraph(paperId: string): Promise<PaperArgumentGraph> {
  return request<PaperArgumentGraph>(`/api/papers/${paperId}/graph`);
}

export function fetchSemanticUnits(paperId: string): Promise<SemanticUnit[]> {
  return request<SemanticUnit[]>(`/api/papers/${paperId}/semantic-units`);
}

export function fetchDocumentInfo(paperId: string): Promise<PaperDocumentInfo> {
  return request<PaperDocumentInfo>(`/api/papers/${paperId}/document`);
}

export function documentPageImageUrl(paperId: string, page: number, scale = 1.8): string {
  return `${API_BASE_URL}/api/papers/${paperId}/document/pages/${page}.png?scale=${scale}`;
}

export function patchGraph(paperId: string, operations: PatchOperation[]): Promise<PaperArgumentGraph> {
  return request<PaperArgumentGraph>(`/api/papers/${paperId}/graph/patch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
  });
}
