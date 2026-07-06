import React, { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertCircle,
  CheckCircle2,
  FileImage,
  FileText,
  GitBranch,
  Link2,
  ListTree,
  Loader2,
  Plus,
  Save,
  Search,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import {
  GraphNode,
  PaperArgumentGraph,
  GraphEdge,
  PaperDocumentInfo,
  PaperSummary,
  SemanticUnit,
  SourceBlock,
  deletePaper,
  documentPageImageUrl,
  fetchBlocks,
  fetchDocumentInfo,
  fetchSemanticUnits,
  fetchGraph,
  listPapers,
  patchGraph,
  uploadPaper,
} from './api';
import { GraphView, NODE_COLORS } from './GraphView';
import './styles.css';

const NODE_TYPE_OPTIONS = [
  'Contribution',
  'Motivation',
  'ResearchGap',
  'Method',
  'Module',
  'Equation',
  'Algorithm',
  'Experiment',
  'Result',
  'Figure',
  'Table',
  'Conclusion',
  'Reference',
  'TextBlock',
];

const EDGE_TYPE_OPTIONS = [
  'MOTIVATES',
  'IMPLEMENTED_BY',
  'VALIDATES',
  'SUPPORTED_BY',
  'FORMALIZES',
  'ILLUSTRATES',
  'REPORTS',
  'SUMMARIZES',
  'BUILDS_ON',
  'EXTENDS',
  'CONTRASTS_WITH',
  'DESCRIBES',
];

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const blockRefs = useRef(new Map<string, HTMLElement>());
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [graph, setGraph] = useState<PaperArgumentGraph | null>(null);
  const [blocks, setBlocks] = useState<SourceBlock[]>([]);
  const [semanticUnits, setSemanticUnits] = useState<SemanticUnit[]>([]);
  const [documentInfo, setDocumentInfo] = useState<PaperDocumentInfo | null>(null);
  const [sourceMode, setSourceMode] = useState<'pages' | 'blocks'>('blocks');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'idle' | 'uploading' | 'ready' | 'error'>('idle');
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [message, setMessage] = useState('Upload a .txt, .md, or PDF to build a Paper Argument Graph.');
  const [editTitle, setEditTitle] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [editVerified, setEditVerified] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newNodeType, setNewNodeType] = useState('Method');
  const [newNodeTitle, setNewNodeTitle] = useState('');
  const [newNodeSummary, setNewNodeSummary] = useState('');
  const [newNodeEvidenceId, setNewNodeEvidenceId] = useState('');
  const [newEdgeSourceId, setNewEdgeSourceId] = useState('');
  const [newEdgeTargetId, setNewEdgeTargetId] = useState('');
  const [newEdgeType, setNewEdgeType] = useState('SUPPORTED_BY');
  const [newEdgeEvidenceId, setNewEdgeEvidenceId] = useState('');

  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const blockById = useMemo(() => new Map(blocks.map((block) => [block.source_block_id, block])), [blocks]);
  const unitById = useMemo(
    () => new Map(semanticUnits.map((unit) => [unit.semantic_unit_id, unit])),
    [semanticUnits],
  );
  const blocksByPage = useMemo(() => {
    const grouped = new Map<number, SourceBlock[]>();
    for (const block of blocks) {
      if (!block.bbox) continue;
      grouped.set(block.page, [...(grouped.get(block.page) ?? []), block]);
    }
    return grouped;
  }, [blocks]);
  const firstNodeByEvidence = useMemo(() => {
    const byEvidence = new Map<string, string>();
    if (!graph) return byEvidence;
    for (const node of graph.nodes) {
      for (const unitId of node.semantic_unit_ids) {
        const unit = unitById.get(unitId);
        for (const sourceRange of unit?.source_ranges ?? []) {
          if (!byEvidence.has(sourceRange.source_block_id)) byEvidence.set(sourceRange.source_block_id, node.id);
        }
      }
    }
    return byEvidence;
  }, [graph, unitById]);
  const incidentEdges = useMemo(() => {
    if (!graph || !selectedNode) return [];
    return graph.edges.filter(
      (edge) => edge.source_node_id === selectedNode.id || edge.target_node_id === selectedNode.id,
    );
  }, [graph, selectedNode]);

  const evidenceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const unitId of selectedNode?.semantic_unit_ids ?? []) {
      const unit = unitById.get(unitId);
      for (const sourceRange of unit?.source_ranges ?? []) {
        ids.add(sourceRange.source_block_id);
      }
    }
    return ids;
  }, [selectedNode, unitById]);

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
    const first = selectedNode.semantic_unit_ids
      .flatMap((id) => unitById.get(id)?.source_ranges.map((range) => range.source_block_id) ?? [])
      .find((id) => blockRefs.current.has(id));
    if (first) {
      scrollToEvidence(first);
    }
  }, [selectedNodeId, graph, unitById]);

  useEffect(() => {
    const first = selectedNode?.semantic_unit_ids
      .flatMap((id) => unitById.get(id)?.source_ranges.map((range) => range.source_block_id) ?? [])
      .find((id) => blockById.has(id));
    if (first) window.requestAnimationFrame(() => scrollToEvidence(first));
  }, [sourceMode, documentInfo, selectedNodeId, unitById, blockById]);

  useEffect(() => {
    if (!graph) return;
    const source = selectedNodeId ?? graph.nodes[0]?.id ?? '';
    const target = graph.nodes.find((node) => node.id !== source)?.id ?? source;
    setNewEdgeSourceId(source);
    setNewEdgeTargetId(target);
    const evidence = selectedNode?.semantic_unit_ids.find((id) => unitById.has(id)) ?? '';
    setNewNodeEvidenceId(evidence);
    setNewEdgeEvidenceId(evidence);
  }, [graph?.paper_id, selectedNodeId, unitById]);

  async function loadPaper(paperId: string, readyMessage?: string) {
    const [nextGraph, nextBlocks, nextSemanticUnits, nextDocumentInfo] = await Promise.all([
      fetchGraph(paperId),
      fetchBlocks(paperId),
      fetchSemanticUnits(paperId),
      fetchDocumentInfo(paperId).catch(() => null),
    ]);
    setGraph(nextGraph);
    setBlocks(nextBlocks);
    setSemanticUnits(nextSemanticUnits);
    setDocumentInfo(nextDocumentInfo);
    setSourceMode(nextDocumentInfo ? 'pages' : 'blocks');
    setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
    setStatus('ready');
    setUploadProgress(null);
    setMessage(readyMessage ?? `Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
  }

  function clearPaperState(nextMessage = 'Upload a .txt, .md, or PDF to build a Paper Argument Graph.') {
    setGraph(null);
    setBlocks([]);
    setSemanticUnits([]);
    setDocumentInfo(null);
    setSelectedNodeId(null);
    setQuery('');
    setStatus('idle');
    setUploadProgress(null);
    setMessage(nextMessage);
  }

  function scrollToEvidence(contentId: string) {
    window.requestAnimationFrame(() => {
      blockRefs.current.get(contentId)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  function selectEvidenceOwner(contentId: string) {
    const nodeId = firstNodeByEvidence.get(contentId);
    if (nodeId) setSelectedNodeId(nodeId);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setStatus('uploading');
    setUploadProgress(0);
    setMessage(`Uploading ${file.name}...`);
    try {
      const nextGraph = await uploadPaper(file, {
        onUploadProgress: ({ percent }) => {
          const normalized = Math.min(100, Math.max(0, percent));
          setUploadProgress(Math.round(normalized * 0.6));
          setMessage(
            normalized >= 100
              ? `Upload complete. Waiting for server analysis: ${file.name}`
              : `Uploading ${file.name}: ${normalized}%`,
          );
        },
        onStageProgress: (progress) => {
          setUploadProgress(progress.progress);
          setMessage(progress.message);
        },
      });
      setUploadProgress(100);
      setMessage('Graph generated. Loading source evidence...');
      const [nextBlocks, nextSemanticUnits, nextDocumentInfo] = await Promise.all([
        fetchBlocks(nextGraph.paper_id),
        fetchSemanticUnits(nextGraph.paper_id),
        fetchDocumentInfo(nextGraph.paper_id).catch(() => null),
      ]);
      setGraph(nextGraph);
      setBlocks(nextBlocks);
      setSemanticUnits(nextSemanticUnits);
      setDocumentInfo(nextDocumentInfo);
      setSourceMode(nextDocumentInfo ? 'pages' : 'blocks');
      setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
      setQuery('');
      setStatus('ready');
      setUploadProgress(null);
      setMessage(`Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
      const nextPapers = await listPapers();
      setPapers(nextPapers);
    } catch (error) {
      setStatus('error');
      setUploadProgress(null);
      setMessage(error instanceof Error ? error.message : 'Upload failed.');
    } finally {
      event.target.value = '';
    }
  }

  async function deleteCurrentPaper() {
    if (!graph) return;
    const title = graph.nodes.find((node) => node.node_type === 'Paper')?.title ?? graph.paper_id;
    if (!window.confirm(`Delete “${title}” and its graph?`)) return;
    setSaving(true);
    try {
      const result = await deletePaper(graph.paper_id);
      setPapers(result.papers);
      const nextPaper = result.papers[0];
      if (nextPaper) {
        await loadPaper(nextPaper.paper_id, `Deleted “${title}”.`);
      } else {
        clearPaperState(`Deleted “${title}”.`);
      }
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to delete paper.');
    } finally {
      setSaving(false);
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

  async function addManualNode() {
    if (!graph || !newNodeTitle.trim()) return;
    const evidenceUnit = newNodeEvidenceId ? unitById.get(newNodeEvidenceId) : null;
    const evidenceBlock = evidenceUnit?.source_ranges[0]
      ? blockById.get(evidenceUnit.source_ranges[0].source_block_id)
      : null;
    const node: GraphNode = {
      id: `manual-${graph.paper_id.slice(0, 8)}-${Date.now()}`,
      paper_id: graph.paper_id,
      node_type: newNodeType,
      title: newNodeTitle.trim(),
      summary: newNodeSummary.trim(),
      confidence: 1,
      source_type: 'human_added',
      semantic_unit_ids: evidenceUnit ? [evidenceUnit.semantic_unit_id] : [],
      page_ranges: evidenceBlock ? [[evidenceBlock.page, evidenceBlock.page]] : [],
      properties: { manual: true },
      created_by: 'human',
      verified: true,
    };
    setSaving(true);
    try {
      const nextGraph = await patchGraph(graph.paper_id, [{ op: 'add_node', node }]);
      setGraph(nextGraph);
      setSelectedNodeId(node.id);
      setNewNodeTitle('');
      setNewNodeSummary('');
      setMessage(`Added ${node.title}.`);
      setStatus('ready');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to add node.');
    } finally {
      setSaving(false);
    }
  }

  async function addManualEdge() {
    if (!graph || !newEdgeSourceId || !newEdgeTargetId || newEdgeSourceId === newEdgeTargetId) return;
    const evidenceUnit = newEdgeEvidenceId ? unitById.get(newEdgeEvidenceId) : null;
    const edge: GraphEdge = {
      id: `manual-edge-${graph.paper_id.slice(0, 8)}-${Date.now()}`,
      paper_id: graph.paper_id,
      source_node_id: newEdgeSourceId,
      target_node_id: newEdgeTargetId,
      edge_type: newEdgeType,
      confidence: 1,
      semantic_unit_ids: evidenceUnit ? [evidenceUnit.semantic_unit_id] : [],
      inference_type: 'human_added',
      properties: { manual: true },
    };
    setSaving(true);
    try {
      const nextGraph = await patchGraph(graph.paper_id, [{ op: 'add_edge', edge }]);
      setGraph(nextGraph);
      setMessage(`Added ${edge.edge_type} relation.`);
      setStatus('ready');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to add relation.');
    } finally {
      setSaving(false);
    }
  }

  async function removeEdge(edgeId: string) {
    if (!graph) return;
    setSaving(true);
    try {
      const nextGraph = await patchGraph(graph.paper_id, [{ op: 'remove_edge', id: edgeId }]);
      setGraph(nextGraph);
      setMessage('Relation removed.');
      setStatus('ready');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to remove relation.');
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
          {papers.length ? (
            <select
              className="paper-select"
              value={graph?.paper_id ?? ''}
              onChange={(event) => {
                const paper = papers.find((item) => item.paper_id === event.target.value);
                loadPaper(
                  event.target.value,
                  paper ? `Loaded “${paper.title}”.` : undefined,
                ).catch((error) => {
                  setStatus('error');
                  setMessage(error instanceof Error ? error.message : 'Failed to load paper.');
                });
              }}
              aria-label="Switch paper"
            >
              {papers.map((paper) => (
                <option key={paper.paper_id} value={paper.paper_id}>{paper.title}</option>
              ))}
            </select>
          ) : null}
          <button
            className="icon-action"
            type="button"
            title="Delete current paper"
            onClick={deleteCurrentPaper}
            disabled={!graph || saving}
          >
            <Trash2 size={16} />
          </button>
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
          <button
            className="primary-action"
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={status === 'uploading'}
          >
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
          <div className="pane-heading source-heading">
            <div>
              <FileText size={20} />
              <h2>Source</h2>
            </div>
            {documentInfo ? (
              <div className="segmented-controls" aria-label="Source view">
                <button
                  type="button"
                  className={sourceMode === 'pages' ? 'active' : ''}
                  title="PDF pages"
                  onClick={() => setSourceMode('pages')}
                >
                  <FileImage size={15} />
                </button>
                <button
                  type="button"
                  className={sourceMode === 'blocks' ? 'active' : ''}
                  title="Text blocks"
                  onClick={() => setSourceMode('blocks')}
                >
                  <ListTree size={15} />
                </button>
              </div>
            ) : null}
          </div>
          <div className={`status-line ${status}`}>
            <div className="status-message">
              {status === 'uploading' ? (
                <Loader2 className="spin" size={18} />
              ) : status === 'error' ? (
                <AlertCircle size={18} />
              ) : (
                <CheckCircle2 size={18} />
              )}
              <span>{message}</span>
            </div>
            {status === 'uploading' ? (
              <div
                className="upload-progress"
                role="progressbar"
                aria-label="Upload progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={uploadProgress ?? 0}
              >
                <span style={{ width: `${uploadProgress ?? 0}%` }} />
              </div>
            ) : null}
          </div>
          {documentInfo && sourceMode === 'pages' ? (
            <div className="pdf-pages">
              {documentInfo.pages.map((page) => (
                <article
                  className="pdf-page"
                  key={page.page}
                  style={{ aspectRatio: `${page.width} / ${page.height}` }}
                >
                  <img
                    src={documentPageImageUrl(graph?.paper_id ?? '', page.page)}
                    alt={`${documentInfo.filename} page ${page.page}`}
                    loading="lazy"
                  />
                  <div className="bbox-layer">
                    {(blocksByPage.get(page.page) ?? []).map((block) => {
                      if (!block.bbox) return null;
                      const [x0, y0, x1, y1] = block.bbox;
                      const highlighted = evidenceIds.has(block.source_block_id);
                      return (
                        <button
                          key={block.source_block_id}
                          ref={(el) => {
                            if (el) blockRefs.current.set(block.source_block_id, el);
                            else blockRefs.current.delete(block.source_block_id);
                          }}
                          type="button"
                          className={`bbox-highlight ${highlighted ? 'highlighted' : ''}`}
                          style={{
                            left: `${(x0 / page.width) * 100}%`,
                            top: `${(y0 / page.height) * 100}%`,
                            width: `${((x1 - x0) / page.width) * 100}%`,
                            height: `${((y1 - y0) / page.height) * 100}%`,
                          }}
                          title={`${block.block_type}: ${block.text.slice(0, 180)}`}
                          onClick={() => selectEvidenceOwner(block.source_block_id)}
                        />
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          ) : blocks.length ? (
            <div className="block-list">
              {blocks.map((block) => (
                <div
                  key={block.source_block_id}
                  ref={(el) => {
                    if (el) blockRefs.current.set(block.source_block_id, el);
                    else blockRefs.current.delete(block.source_block_id);
                  }}
                  className={`content-block ${evidenceIds.has(block.source_block_id) ? 'highlighted' : ''}`}
                  onClick={() => selectEvidenceOwner(block.source_block_id)}
                >
                  <header>
                    <span className="role-tag">{block.block_type}</span>
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

              {selectedNode.semantic_unit_ids.length ? (
                <section className="evidence-list">
                  <h3>Semantic Units</h3>
                  <div className="evidence-chips">
                    {selectedNode.semantic_unit_ids.map((unitId) => {
                      const unit = unitById.get(unitId);
                      const firstSourceBlockId = unit?.source_ranges[0]?.source_block_id;
                      const firstBlock = firstSourceBlockId ? blockById.get(firstSourceBlockId) : null;
                      return (
                        <button
                          key={unitId}
                          type="button"
                          className="evidence-chip"
                          onClick={() =>
                            firstSourceBlockId
                              ? blockRefs.current.get(firstSourceBlockId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                              : undefined
                          }
                        >
                          {unit
                            ? `${unit.role}${firstBlock ? ` · p.${firstBlock.page}` : ''}`
                            : unitId}
                        </button>
                      );
                    })}
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

              <section className="manual-form">
                <h3>Add node</h3>
                <label>
                  Type
                  <select value={newNodeType} onChange={(event) => setNewNodeType(event.target.value)}>
                    {NODE_TYPE_OPTIONS.map((type) => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Title
                  <input value={newNodeTitle} onChange={(event) => setNewNodeTitle(event.target.value)} />
                </label>
                <label>
                  Summary
                  <textarea rows={3} value={newNodeSummary} onChange={(event) => setNewNodeSummary(event.target.value)} />
                </label>
                <label>
                  Evidence
                  <select value={newNodeEvidenceId} onChange={(event) => setNewNodeEvidenceId(event.target.value)}>
                    <option value="">No evidence</option>
                    {semanticUnits.map((unit) => (
                      <option key={unit.semantic_unit_id} value={unit.semantic_unit_id}>
                        {unit.role} · {unit.title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="primary-action"
                  type="button"
                  onClick={addManualNode}
                  disabled={saving || !newNodeTitle.trim()}
                >
                  <Plus size={16} /> Add node
                </button>
              </section>

              <section className="manual-form">
                <h3>Add relation</h3>
                <label>
                  From
                  <select value={newEdgeSourceId} onChange={(event) => setNewEdgeSourceId(event.target.value)}>
                    {(graph?.nodes ?? []).map((node) => (
                      <option key={node.id} value={node.id}>{node.title}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Relation
                  <select value={newEdgeType} onChange={(event) => setNewEdgeType(event.target.value)}>
                    {EDGE_TYPE_OPTIONS.map((type) => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                </label>
                <label>
                  To
                  <select value={newEdgeTargetId} onChange={(event) => setNewEdgeTargetId(event.target.value)}>
                    {(graph?.nodes ?? []).map((node) => (
                      <option key={node.id} value={node.id}>{node.title}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Evidence
                  <select value={newEdgeEvidenceId} onChange={(event) => setNewEdgeEvidenceId(event.target.value)}>
                    <option value="">No evidence</option>
                    {semanticUnits.map((unit) => (
                      <option key={unit.semantic_unit_id} value={unit.semantic_unit_id}>
                        {unit.role} · {unit.title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="primary-action"
                  type="button"
                  onClick={addManualEdge}
                  disabled={saving || !newEdgeSourceId || !newEdgeTargetId || newEdgeSourceId === newEdgeTargetId}
                >
                  <Link2 size={16} /> Add relation
                </button>
              </section>

              <section className="edge-list">
                <h3>Relations</h3>
                {incidentEdges.length ? incidentEdges.map((edge) => {
                  const otherId = edge.source_node_id === selectedNode.id ? edge.target_node_id : edge.source_node_id;
                  return (
                    <article className="edge-item" key={edge.id}>
                      <div className="edge-item-header">
                        <strong>{edge.edge_type}</strong>
                        <button
                          type="button"
                          className="icon-action"
                          title="Remove relation"
                          onClick={() => removeEdge(edge.id)}
                          disabled={saving}
                        >
                          <X size={14} />
                        </button>
                      </div>
                      <button type="button" className="edge-link" onClick={() => setSelectedNodeId(otherId)}>
                        {edge.source_node_id === selectedNode.id ? '→' : '←'} {nodeLabel(otherId)}
                      </button>
                      {edge.semantic_unit_ids.length ? (
                        <p>
                          {edge.semantic_unit_ids
                            .map((unitId) => unitById.get(unitId)?.text)
                            .filter(Boolean)
                            .join(' ')
                            .slice(0, 220)}
                        </p>
                      ) : null}
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
