import React, { ChangeEvent, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  GitBranch,
  Loader2,
  Search,
  UploadCloud,
} from 'lucide-react';
import './styles.css';

type GraphNode = {
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

type GraphEdge = {
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

type PaperArgumentGraph = {
  paper_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

const TYPE_ORDER = [
  'Paper',
  'Contribution',
  'ResearchGap',
  'Motivation',
  'Method',
  'Module',
  'Equation',
  'Experiment',
  'Result',
  'Conclusion',
  'Reference',
  'TextBlock',
];

const TYPE_LABELS: Record<string, string> = {
  Paper: 'Paper',
  Contribution: 'Contributions',
  ResearchGap: 'Gaps',
  Motivation: 'Motivation',
  Method: 'Methods',
  Module: 'Modules',
  Equation: 'Equations',
  Experiment: 'Experiments',
  Result: 'Results',
  Conclusion: 'Conclusions',
  Reference: 'References',
  TextBlock: 'Evidence',
};

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [graph, setGraph] = useState<PaperArgumentGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'idle' | 'uploading' | 'ready' | 'error'>('idle');
  const [message, setMessage] = useState('Upload a .txt, .md, or PDF to build a Paper Argument Graph.');

  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) ?? graph?.nodes[0] ?? null;
  const incidentEdges = useMemo(() => {
    if (!graph || !selectedNode) return [];
    return graph.edges.filter(
      (edge) => edge.source_node_id === selectedNode.id || edge.target_node_id === selectedNode.id,
    );
  }, [graph, selectedNode]);

  const filteredNodes = useMemo(() => {
    if (!graph) return [];
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return graph.nodes;
    return graph.nodes.filter((node) =>
      `${node.title} ${node.summary} ${node.node_type}`.toLowerCase().includes(normalizedQuery),
    );
  }, [graph, query]);

  const groupedNodes = useMemo(() => {
    const groups = new Map<string, GraphNode[]>();
    for (const node of filteredNodes) {
      const nodes = groups.get(node.node_type) ?? [];
      groups.set(node.node_type, [...nodes, node]);
    }
    return [...groups.entries()].sort(([left], [right]) => {
      const leftIndex = TYPE_ORDER.indexOf(left);
      const rightIndex = TYPE_ORDER.indexOf(right);
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    });
  }, [filteredNodes]);

  async function uploadPaper(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    setStatus('uploading');
    setMessage(`Uploading ${file.name}...`);

    try {
      const response = await fetch(`${API_BASE_URL}/api/papers`, {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Upload failed with HTTP ${response.status}`);
      }

      const nextGraph = (await response.json()) as PaperArgumentGraph;
      setGraph(nextGraph);
      setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
      setQuery('');
      setStatus('ready');
      setMessage(`Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Upload failed.');
    } finally {
      event.target.value = '';
    }
  }

  return (
    <main className="shell">
      <header className="toolbar">
        <div className="brand"><GitBranch size={22} /> Understand Anypaper</div>
        <div className="toolbar-actions">
          <label className="search-box" aria-label="Search graph nodes">
            <Search size={16} />
            <input
              type="search"
              placeholder="Search graph"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={!graph}
            />
          </label>
          <button className="primary-action" type="button" onClick={() => fileInputRef.current?.click()}>
            {status === 'uploading' ? <Loader2 className="spin" size={18} /> : <UploadCloud size={18} />}
            Upload
          </button>
          <input
            ref={fileInputRef}
            className="sr-only"
            type="file"
            accept=".pdf,.txt,.md,text/plain,text/markdown,application/pdf"
            onChange={uploadPaper}
          />
        </div>
      </header>

      <section className="workspace">
        <aside className="pdf-pane">
          <div className="pane-heading">
            <FileText size={20} />
            <h2>Source</h2>
          </div>
          <div className={`status-line ${status}`}>
            {status === 'error' ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
            <span>{message}</span>
          </div>
          {selectedNode ? (
            <div className="source-preview">
              <span className="eyebrow">Selected Evidence</span>
              <h3>{selectedNode.title}</h3>
              <p>{selectedNode.summary || 'No summary is available for this node.'}</p>
              <div className="meta-grid">
                <span>Type</span><strong>{selectedNode.node_type}</strong>
                <span>Evidence</span><strong>{selectedNode.evidence_ids.length || 0}</strong>
                <span>Pages</span><strong>{formatPages(selectedNode.page_ranges)}</strong>
              </div>
            </div>
          ) : (
            <button className="upload-drop" type="button" onClick={() => fileInputRef.current?.click()}>
              <UploadCloud size={34} />
              <span>Choose a paper</span>
            </button>
          )}
        </aside>

        <section className="graph-pane" aria-label="Paper argument graph">
          {graph ? (
            <>
              <div className="graph-summary">
                <div>
                  <span className="eyebrow">Paper ID</span>
                  <strong>{graph.paper_id}</strong>
                </div>
                <div>
                  <span>{filteredNodes.length} visible nodes</span>
                  <span>{graph.edges.length} edges</span>
                </div>
              </div>
              <div className="graph-groups">
                {groupedNodes.length ? groupedNodes.map(([nodeType, nodes]) => (
                  <section className="node-group" key={nodeType}>
                    <h3>{TYPE_LABELS[nodeType] ?? nodeType}</h3>
                    <div className="node-list">
                      {nodes.map((node) => (
                        <button
                          className={`node ${node.node_type} ${selectedNode?.id === node.id ? 'selected' : ''}`}
                          type="button"
                          key={node.id}
                          onClick={() => setSelectedNodeId(node.id)}
                        >
                          <span>{node.title}</span>
                          <small>{Math.round(node.confidence * 100)}%</small>
                        </button>
                      ))}
                    </div>
                  </section>
                )) : (
                  <div className="empty-state">No nodes match the current search.</div>
                )}
              </div>
            </>
          ) : (
            <div className="empty-state">
              <GitBranch size={38} />
              <h2>No graph yet</h2>
              <p>Upload a paper to see contribution, evidence, and support nodes here.</p>
            </div>
          )}
        </section>

        <aside className="inspector">
          <div className="pane-heading">
            <GitBranch size={20} />
            <h2>Inspector</h2>
          </div>
          {selectedNode ? (
            <>
              <div className="inspector-title">
                <span className="type-pill">{selectedNode.node_type}</span>
                <h3>{selectedNode.title}</h3>
                <p>{selectedNode.summary || 'No summary is available for this node.'}</p>
              </div>
              <div className="meta-grid">
                <span>Confidence</span><strong>{Math.round(selectedNode.confidence * 100)}%</strong>
                <span>Source</span><strong>{selectedNode.source_type}</strong>
                <span>Created by</span><strong>{selectedNode.created_by}</strong>
                <span>Verified</span><strong>{selectedNode.verified ? 'Yes' : 'No'}</strong>
              </div>
              <section className="edge-list">
                <h3>Relations</h3>
                {incidentEdges.length ? incidentEdges.map((edge) => (
                  <article className="edge-item" key={edge.id}>
                    <strong>{edge.edge_type}</strong>
                    <span>{`${edge.source_node_id} -> ${edge.target_node_id}`}</span>
                    {edge.evidence?.text ? <p>{edge.evidence.text}</p> : null}
                  </article>
                )) : <p className="muted">No relations for this node.</p>}
              </section>
            </>
          ) : (
            <p className="muted">Select a graph node to inspect its evidence and relations.</p>
          )}
        </aside>
      </section>
    </main>
  );
}

function formatPages(pageRanges: [number, number][]) {
  if (!pageRanges.length) return 'None';
  return pageRanges.map(([start, end]) => start === end ? `${start}` : `${start}-${end}`).join(', ');
}

createRoot(document.getElementById('root')!).render(<App />);
