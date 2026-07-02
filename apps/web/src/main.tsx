import React, { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  GitBranch,
  Loader2,
  Save,
  Search,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import {
  ContentBlock,
  GraphNode,
  PaperArgumentGraph,
  PaperSummary,
  fetchBlocks,
  fetchGraph,
  listPapers,
  patchGraph,
  uploadPaper,
} from './api';
import { GraphView, NODE_COLORS } from './GraphView';
import './styles.css';

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const blockRefs = useRef(new Map<string, HTMLDivElement>());
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [graph, setGraph] = useState<PaperArgumentGraph | null>(null);
  const [blocks, setBlocks] = useState<ContentBlock[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'idle' | 'uploading' | 'ready' | 'error'>('idle');
  const [message, setMessage] = useState('Upload a .txt, .md, or PDF to build a Paper Argument Graph.');
  const [editTitle, setEditTitle] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [editVerified, setEditVerified] = useState(false);
  const [saving, setSaving] = useState(false);

  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const incidentEdges = useMemo(() => {
    if (!graph || !selectedNode) return [];
    return graph.edges.filter(
      (edge) => edge.source_node_id === selectedNode.id || edge.target_node_id === selectedNode.id,
    );
  }, [graph, selectedNode]);

  const evidenceIds = useMemo(() => new Set(selectedNode?.evidence_ids ?? []), [selectedNode]);

  useEffect(() => {
    listPapers()
      .then(async (existing) => {
        setPapers(existing);
        if (existing.length) {
          await loadPaper(existing[0].paper_id, `Restored “${existing[0].title}” from storage.`);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedNode) return;
    setEditTitle(selectedNode.title);
    setEditSummary(selectedNode.summary);
    setEditVerified(selectedNode.verified);
    const first = selectedNode.evidence_ids.find((id) => blockRefs.current.has(id));
    if (first) {
      blockRefs.current.get(first)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [selectedNodeId, graph]);

  async function loadPaper(paperId: string, readyMessage?: string) {
    const [nextGraph, nextBlocks] = await Promise.all([fetchGraph(paperId), fetchBlocks(paperId)]);
    setGraph(nextGraph);
    setBlocks(nextBlocks);
    setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
    setStatus('ready');
    setMessage(readyMessage ?? `Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setStatus('uploading');
    setMessage(`Uploading ${file.name}...`);
    try {
      const nextGraph = await uploadPaper(file);
      const nextBlocks = await fetchBlocks(nextGraph.paper_id);
      setGraph(nextGraph);
      setBlocks(nextBlocks);
      setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
      setQuery('');
      setStatus('ready');
      setMessage(`Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
      listPapers().then(setPapers).catch(() => undefined);
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Upload failed.');
    } finally {
      event.target.value = '';
    }
  }

  async function saveNodeEdits() {
    if (!graph || !selectedNode) return;
    setSaving(true);
    try {
      const nextGraph = await patchGraph(graph.paper_id, [
        {
          op: 'update_node',
          id: selectedNode.id,
          changes: { title: editTitle, summary: editSummary, verified: editVerified },
        },
      ]);
      setGraph(nextGraph);
      setMessage(`Saved changes to ${selectedNode.id}.`);
      setStatus('ready');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to save node.');
    } finally {
      setSaving(false);
    }
  }

  async function deleteNode() {
    if (!graph || !selectedNode) return;
    if (!window.confirm(`Remove node “${selectedNode.title}” and its relations?`)) return;
    setSaving(true);
    try {
      const nextGraph = await patchGraph(graph.paper_id, [{ op: 'remove_node', id: selectedNode.id }]);
      setGraph(nextGraph);
      setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
      setMessage('Node removed.');
      setStatus('ready');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to remove node.');
    } finally {
      setSaving(false);
    }
  }

  function nodeLabel(nodeId: string): string {
    return graph?.nodes.find((node) => node.id === nodeId)?.title ?? nodeId;
  }

  const legendTypes = useMemo(() => {
    if (!graph) return [];
    return [...new Set(graph.nodes.map((node) => node.node_type))];
  }, [graph]);

  return (
    <main className="shell">
      <header className="toolbar">
        <div className="brand"><GitBranch size={22} /> Understand Anypaper</div>
        <div className="toolbar-actions">
          {papers.length > 1 ? (
            <select
              className="paper-select"
              value={graph?.paper_id ?? ''}
              onChange={(event) => loadPaper(event.target.value).catch(() => undefined)}
              aria-label="Switch paper"
            >
              {papers.map((paper) => (
                <option key={paper.paper_id} value={paper.paper_id}>{paper.title}</option>
              ))}
            </select>
          ) : null}
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
            onChange={handleUpload}
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
          {blocks.length ? (
            <div className="block-list">
              {blocks.map((block) => (
                <div
                  key={block.content_id}
                  ref={(el) => {
                    if (el) blockRefs.current.set(block.content_id, el);
                    else blockRefs.current.delete(block.content_id);
                  }}
                  className={`content-block ${evidenceIds.has(block.content_id) ? 'highlighted' : ''}`}
                >
                  <header>
                    <span className="role-tag">{block.semantic_role}</span>
                    <span>p.{block.page}{block.section ? ` · ${block.section}` : ''}</span>
                  </header>
                  <p>{block.text}</p>
                </div>
              ))}
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
                  <span className="eyebrow">Paper</span>
                  <strong>{graph.nodes.find((node) => node.node_type === 'Paper')?.title ?? graph.paper_id}</strong>
                </div>
                <div>
                  <span>{graph.nodes.length} nodes</span>
                  <span>{graph.edges.length} edges</span>
                </div>
              </div>
              <GraphView
                graph={graph}
                selectedNodeId={selectedNodeId}
                query={query}
                onSelectNode={setSelectedNodeId}
              />
              <div className="graph-legend">
                {legendTypes.map((nodeType) => (
                  <span key={nodeType}>
                    <i style={{ background: NODE_COLORS[nodeType] ?? '#67788a' }} />
                    {nodeType}
                  </span>
                ))}
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
                <span className="type-pill" style={{ borderColor: NODE_COLORS[selectedNode.node_type] }}>
                  {selectedNode.node_type}
                </span>
                <h3>{selectedNode.title}</h3>
                <p>{selectedNode.summary || 'No summary is available for this node.'}</p>
              </div>
              <div className="meta-grid">
                <span>Confidence</span><strong>{Math.round(selectedNode.confidence * 100)}%</strong>
                <span>Source</span><strong>{selectedNode.source_type}</strong>
                <span>Created by</span><strong>{selectedNode.created_by}</strong>
                <span>Verified</span><strong>{selectedNode.verified ? 'Yes' : 'No'}</strong>
              </div>

              {selectedNode.evidence_ids.length ? (
                <section className="evidence-list">
                  <h3>Evidence</h3>
                  <div className="evidence-chips">
                    {selectedNode.evidence_ids.map((contentId) => (
                      <button
                        key={contentId}
                        type="button"
                        className="evidence-chip"
                        onClick={() =>
                          blockRefs.current.get(contentId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                        }
                      >
                        {blocks.find((block) => block.content_id === contentId)
                          ? `p.${blocks.find((block) => block.content_id === contentId)!.page} ${contentId.split('-').slice(-1)[0]}`
                          : contentId}
                      </button>
                    ))}
                  </div>
                </section>
              ) : null}

              <section className="edit-form">
                <h3>Correct this node</h3>
                <label>
                  Title
                  <input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} />
                </label>
                <label>
                  Summary
                  <textarea rows={4} value={editSummary} onChange={(event) => setEditSummary(event.target.value)} />
                </label>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={editVerified}
                    onChange={(event) => setEditVerified(event.target.checked)}
                  />
                  Mark as human-verified
                </label>
                <div className="edit-actions">
                  <button className="primary-action" type="button" onClick={saveNodeEdits} disabled={saving}>
                    {saving ? <Loader2 className="spin" size={16} /> : <Save size={16} />} Save
                  </button>
                  <button className="danger-action" type="button" onClick={deleteNode} disabled={saving}>
                    <Trash2 size={16} /> Remove
                  </button>
                </div>
              </section>

              <section className="edge-list">
                <h3>Relations</h3>
                {incidentEdges.length ? incidentEdges.map((edge) => {
                  const otherId = edge.source_node_id === selectedNode.id ? edge.target_node_id : edge.source_node_id;
                  return (
                    <article className="edge-item" key={edge.id}>
                      <strong>{edge.edge_type}</strong>
                      <button type="button" className="edge-link" onClick={() => setSelectedNodeId(otherId)}>
                        {edge.source_node_id === selectedNode.id ? '→' : '←'} {nodeLabel(otherId)}
                      </button>
                      {edge.evidence?.text ? <p>{edge.evidence.text.slice(0, 220)}</p> : null}
                    </article>
                  );
                }) : <p className="muted">No relations for this node.</p>}
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

createRoot(document.getElementById('root')!).render(<App />);
