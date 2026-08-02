import React, { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  FileArchive,
  FileImage,
  FolderOpen,
  GitBranch,
  Loader2,
  Network,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  AgentActivity,
  LatexProject,
  PaperArgumentGraph,
  PaperDocumentInfo,
  documentPageImageUrl,
  fetchDocumentInfo,
  fetchGraph,
  fetchLatexProject,
  forgetLatexProject,
  importLatexProject,
  listLatexProjects,
  openLatexFolder,
  setLatexMainTex,
  streamLatexGraphUpdate,
} from './api';
import { AgentActivityList, appendAgentActivity } from './AgentActivityList';
import { GraphView } from './GraphView';
import {
  contributionEvidenceSubtitles,
  contributionGraph,
  overviewGraph,
} from './graphNavigation';


function writingUrl(projectId?: string): string {
  const url = new URL(window.location.href);
  const route = `/write${projectId ? `/${projectId}` : ''}`;
  if (url.protocol === 'file:') {
    url.hash = route;
    url.search = '';
  } else {
    url.pathname = route;
    url.search = '';
    url.hash = '';
  }
  return url.toString();
}

function readerUrl(): string {
  const url = new URL(window.location.href);
  if (url.protocol === 'file:') {
    url.hash = '';
    url.search = '';
  } else {
    url.pathname = '/';
    url.search = '';
    url.hash = '';
  }
  return url.toString();
}

async function editorUrl(project: LatexProject): Promise<string> {
  if (window.pagDesktop?.openVSCodeUrlForFolder) {
    return window.pagDesktop.openVSCodeUrlForFolder(project.root_path);
  }
  const base = import.meta.env.VITE_OPENVSCODE_URL || 'http://127.0.0.1:3001';
  const url = new URL(base);
  url.searchParams.set('folder', project.root_path);
  return url.toString();
}

type WritingAppProps = {
  projectId: string | null;
};

const WRITING_PREVIEW_WIDTH_STORAGE_KEY = 'pag.writing-preview-width.v1';
const MIN_WRITING_PREVIEW_WIDTH = 320;
const MIN_WRITING_EDITOR_WIDTH = 480;
const WRITING_SPLITTER_WIDTH = 10;

function initialWritingPreviewWidth(): number {
  const fallback = Math.max(MIN_WRITING_PREVIEW_WIDTH, window.innerWidth * 0.38);
  try {
    const stored = Number.parseFloat(
      window.localStorage.getItem(WRITING_PREVIEW_WIDTH_STORAGE_KEY) ?? '',
    );
    return Number.isFinite(stored) ? Math.max(MIN_WRITING_PREVIEW_WIDTH, stored) : fallback;
  } catch {
    return fallback;
  }
}

function maximumWritingPreviewWidth(workspace: HTMLElement): number {
  return Math.max(
    MIN_WRITING_PREVIEW_WIDTH,
    workspace.clientWidth - WRITING_SPLITTER_WIDTH - MIN_WRITING_EDITOR_WIDTH,
  );
}

function EditorSurface({ src }: { src: string }) {
  if (window.pagDesktop) {
    return React.createElement('webview', {
      src,
      title: 'OpenVSCode LaTeX editor',
      partition: 'persist:openvscode',
    });
  }
  return <iframe src={src} title="OpenVSCode LaTeX editor" allow="clipboard-read; clipboard-write" />;
}

export function WritingApp({ projectId }: WritingAppProps) {
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const [projects, setProjects] = useState<LatexProject[]>([]);
  const [project, setProject] = useState<LatexProject | null>(null);
  const [graph, setGraph] = useState<PaperArgumentGraph | null>(null);
  const [documentInfo, setDocumentInfo] = useState<PaperDocumentInfo | null>(null);
  const [editorSrc, setEditorSrc] = useState('');
  const [editorError, setEditorError] = useState('');
  const [leftMode, setLeftMode] = useState<'pdf' | 'graph'>('pdf');
  const [message, setMessage] = useState('Choose or import a LaTeX project.');
  const [updateError, setUpdateError] = useState('');
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [activities, setActivities] = useState<AgentActivity[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [focusedContributionId, setFocusedContributionId] = useState<string | null>(null);
  const [previewWidth, setPreviewWidth] = useState(initialWritingPreviewWidth);

  useEffect(() => {
    document.documentElement.classList.toggle('desktop-app', Boolean(window.pagDesktop));
    listLatexProjects()
      .then(setProjects)
      .catch((error) => setMessage(error instanceof Error ? error.message : 'Failed to list projects.'));
  }, []);

  useEffect(() => {
    window.localStorage.setItem(WRITING_PREVIEW_WIDTH_STORAGE_KEY, String(previewWidth));
  }, [previewWidth]);

  useEffect(() => {
    const fitPreviewWidth = () => {
      const workspace = workspaceRef.current;
      if (!workspace || window.matchMedia('(max-width: 980px)').matches) return;
      setPreviewWidth((current) => Math.min(current, maximumWritingPreviewWidth(workspace)));
    };
    fitPreviewWidth();
    window.addEventListener('resize', fitPreviewWidth);
    return () => window.removeEventListener('resize', fitPreviewWidth);
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let canceled = false;
    fetchLatexProject(projectId)
      .then(async (loadedProject) => {
        const [loadedGraph, loadedDocument] = loadedProject.graph_ready
          ? await Promise.all([
              fetchGraph(loadedProject.paper_id),
              fetchDocumentInfo(loadedProject.paper_id),
            ])
          : [null, null];
        return [loadedProject, loadedGraph, loadedDocument] as const;
      })
      .then(async ([loadedProject, loadedGraph, loadedDocument]) => {
        if (canceled) return;
        setProject(loadedProject);
        setGraph(loadedGraph);
        setDocumentInfo(loadedDocument);
        setMessage(loadedGraph ? 'Project and graph ready.' : 'Edit the paper, then update its graph.');
        const src = await editorUrl(loadedProject);
        if (!canceled) setEditorSrc(src);
      })
      .catch((error) => {
        if (!canceled) {
          const detail = error instanceof Error ? error.message : 'Failed to open project.';
          setEditorError(detail);
          setMessage(detail);
        }
      });
    return () => {
      canceled = true;
    };
  }, [projectId]);

  const focusedContribution = graph?.nodes.find(
    (node) => node.id === focusedContributionId,
  ) ?? null;
  const viewGraph = useMemo(() => {
    if (!graph) return null;
    if (focusedContributionId) {
      const subgraph = contributionGraph(graph, focusedContributionId);
      if (subgraph) return subgraph;
    }
    return overviewGraph(graph);
  }, [graph, focusedContributionId]);
  const graphSubtitles = useMemo(
    () => graph && !focusedContributionId ? contributionEvidenceSubtitles(graph) : undefined,
    [focusedContributionId, graph],
  );

  async function importZip(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    setMessage('Importing LaTeX project…');
    try {
      const created = await importLatexProject(file);
      window.location.assign(writingUrl(created.project_id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to import project.');
      setBusy(false);
    }
  }

  async function openFolder() {
    const folder = await window.pagDesktop?.chooseLatexFolder?.();
    if (!folder) return;
    setBusy(true);
    setMessage('Opening LaTeX folder…');
    try {
      const opened = await openLatexFolder(folder);
      window.location.assign(writingUrl(opened.project_id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to open folder.');
      setBusy(false);
    }
  }

  async function chooseMainTex(mainTex: string) {
    if (!project) return;
    try {
      const updated = await setLatexMainTex(project.project_id, mainTex);
      setProject(updated);
      setMessage(`Using ${mainTex} as the root document.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to select the root document.');
    }
  }

  async function updateGraph() {
    if (!project) return;
    setBusy(true);
    setActivities([]);
    setProgress(0);
    setUpdateError('');
    try {
      setMessage('Saving editor changes…');
      await new Promise((resolve) => window.setTimeout(resolve, 650));
      const result = await streamLatexGraphUpdate(project.project_id, (event) => {
        setMessage(event.message);
        setProgress(event.progress);
        if (event.activity) {
          setActivities((current) => appendAgentActivity(current, event.activity!));
        }
      });
      setProject(result.project);
      setGraph(result.graph);
      setFocusedContributionId(null);
      setSelectedNodeId(null);
      setDocumentInfo(await fetchDocumentInfo(result.project.paper_id));
      setMessage('Paper graph updated.');
      setProgress(100);
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Graph update failed.';
      setMessage(detail);
      setUpdateError(detail);
    } finally {
      setBusy(false);
    }
  }

  async function forgetProject() {
    if (!project) return;
    const removesManagedSources = project.source_kind === 'managed';
    const confirmed = window.confirm(
      removesManagedSources
        ? 'Forget this project and delete its imported source copy?'
        : 'Forget this project? The original folder will not be changed.',
    );
    if (!confirmed) return;
    await forgetLatexProject(project.project_id);
    window.location.assign(writingUrl());
  }

  function selectGraphNode(nodeId: string) {
    const node = graph?.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    if (node.node_type === 'Paper') setFocusedContributionId(null);
    if (node.node_type === 'Contribution') setFocusedContributionId(node.id);
    setSelectedNodeId(node.id);
  }

  function returnToGraphOverview() {
    if (!graph) return;
    const paperNode = graph.nodes.find((node) => node.node_type === 'Paper');
    setFocusedContributionId(null);
    setSelectedNodeId(paperNode?.id ?? null);
  }

  function resizePreviewBy(nextWidth: number) {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    setPreviewWidth(
      Math.min(
        maximumWritingPreviewWidth(workspace),
        Math.max(MIN_WRITING_PREVIEW_WIDTH, nextWidth),
      ),
    );
  }

  function startPreviewResize(event: React.PointerEvent<HTMLDivElement>) {
    if (window.matchMedia('(max-width: 980px)').matches) return;
    const splitter = event.currentTarget;
    const startX = event.clientX;
    const startWidth = previewWidth;
    event.preventDefault();
    splitter.setPointerCapture(event.pointerId);
    document.body.classList.add('pane-resizing');

    const resize = (moveEvent: PointerEvent) => {
      resizePreviewBy(startWidth + moveEvent.clientX - startX);
    };
    const finish = () => {
      document.body.classList.remove('pane-resizing');
      splitter.removeEventListener('pointermove', resize);
      splitter.removeEventListener('pointerup', finish);
      splitter.removeEventListener('pointercancel', finish);
    };
    splitter.addEventListener('pointermove', resize);
    splitter.addEventListener('pointerup', finish);
    splitter.addEventListener('pointercancel', finish);
  }

  function handlePreviewSplitterKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      resizePreviewBy(previewWidth + (event.key === 'ArrowLeft' ? -24 : 24));
    } else if (event.key === 'Home') {
      event.preventDefault();
      resizePreviewBy(MIN_WRITING_PREVIEW_WIDTH);
    } else if (event.key === 'End' && workspaceRef.current) {
      event.preventDefault();
      resizePreviewBy(maximumWritingPreviewWidth(workspaceRef.current));
    }
  }

  if (!projectId) {
    return (
      <main className="writing-home">
        <header className="writing-home-header">
          <a className="subtle-action writing-link-button" href={readerUrl()}><ArrowLeft size={16} /> Reader</a>
          <div className="brand"><GitBranch size={22} /> Understand Anypaper</div>
        </header>
        <section className="writing-home-card">
          <Network size={36} />
          <h1>Write with your paper graph</h1>
          <p>Import a LaTeX ZIP or open an existing local folder.</p>
          <div className="writing-home-actions">
            <button className="primary-action" type="button" onClick={() => uploadRef.current?.click()} disabled={busy}>
              <FileArchive size={17} /> Import ZIP
            </button>
            {window.pagDesktop ? (
              <button className="subtle-action writing-link-button" type="button" onClick={openFolder} disabled={busy}>
                <FolderOpen size={17} /> Open Folder
              </button>
            ) : null}
          </div>
          <input ref={uploadRef} type="file" accept=".zip,application/zip" hidden onChange={importZip} />
          <p className="writing-status">{busy ? <Loader2 className="spin" size={16} /> : null}{message}</p>
          {projects.length ? (
            <div className="writing-project-list">
              {projects.map((item) => (
                <a key={item.project_id} href={writingUrl(item.project_id)}>
                  <strong>{item.name}</strong>
                  <span>{item.main_tex || 'Choose main .tex'} · {item.source_kind}</span>
                </a>
              ))}
            </div>
          ) : null}
        </section>
      </main>
    );
  }

  return (
    <main className="writing-shell">
      <header className="writing-toolbar">
        <a className="icon-action writing-back" href={writingUrl()} title="All writing projects"><ArrowLeft size={18} /></a>
        <div className="writing-project-title">
          <strong>{project?.name || 'Loading project…'}</strong>
          <span>{project?.root_path}</span>
        </div>
        {project && project.tex_files.length > 1 ? (
          <select
            value={project.main_tex || ''}
            onChange={(event) => chooseMainTex(event.target.value)}
            aria-label="Main TeX file"
            disabled={busy}
          >
            <option value="" disabled>Select main .tex…</option>
            {project.tex_files.map((file) => <option key={file} value={file}>{file}</option>)}
          </select>
        ) : null}
        <div className="writing-toolbar-actions">
          <button className="primary-action" type="button" onClick={updateGraph} disabled={!project?.main_tex || busy}>
            {busy ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />} Update graph
          </button>
          <button className="icon-action danger-icon-action" type="button" title="Forget project" onClick={forgetProject} disabled={busy}>
            <Trash2 size={16} />
          </button>
        </div>
      </header>
      <section
        ref={workspaceRef}
        className="writing-workspace"
        style={{ '--writing-preview-width': `${previewWidth}px` } as React.CSSProperties}
      >
        <aside className="writing-preview">
          <div className="writing-preview-controls">
            {busy ? (
              <section className="writing-agent-progress" aria-live="polite">
                <div className="writing-agent-progress-heading">
                  <Loader2 className="spin" size={16} />
                  <span>{message}</span>
                  <strong>{progress ?? 0}%</strong>
                </div>
                <progress max={100} value={progress ?? 0} />
                {activities.length ? <AgentActivityList activities={activities} /> : null}
              </section>
            ) : null}
            {!busy && updateError ? (
              <button
                className="writing-update-error"
                type="button"
                title="Dismiss"
                onClick={() => setUpdateError('')}
              >
                <AlertCircle size={15} />
                <span>{updateError}</span>
              </button>
            ) : null}
            <div className="writing-preview-tabs">
              <button className={leftMode === 'pdf' ? 'active' : ''} type="button" onClick={() => setLeftMode('pdf')}><FileImage size={15} /> PDF</button>
              <button className={leftMode === 'graph' ? 'active' : ''} type="button" onClick={() => setLeftMode('graph')}><Network size={15} /> Graph</button>
            </div>
            {leftMode === 'graph' && focusedContribution ? (
              <button
                className="writing-graph-back"
                type="button"
                onClick={returnToGraphOverview}
                title="Back to paper overview"
              >
                <ArrowLeft size={14} />
                <span>{focusedContribution.title}</span>
              </button>
            ) : null}
          </div>
          {leftMode === 'pdf' && documentInfo ? (
            <div className="writing-pdf-pages">
              {documentInfo.pages.map((page) => (
                <img
                  key={page.page}
                  src={`${documentPageImageUrl(project?.paper_id || '', page.page)}&revision=${encodeURIComponent(project?.current_graph_revision || '')}`}
                  alt={`Page ${page.page}`}
                />
              ))}
            </div>
          ) : leftMode === 'graph' && viewGraph ? (
            <GraphView graph={viewGraph} selectedNodeId={selectedNodeId} focusRevision={0} query="" onSelectNode={selectGraphNode} subtitles={graphSubtitles} />
          ) : (
            <div className="writing-empty-preview">Update the graph to generate the compiled PDF and argument graph.</div>
          )}
        </aside>
        <div
          className="pane-splitter writing-pane-splitter"
          role="separator"
          tabIndex={0}
          aria-label="Resize PDF and graph panel"
          aria-orientation="vertical"
          aria-valuemin={MIN_WRITING_PREVIEW_WIDTH}
          aria-valuenow={Math.round(previewWidth)}
          onPointerDown={startPreviewResize}
          onKeyDown={handlePreviewSplitterKeyDown}
        />
        <section className="writing-editor">
          {editorSrc ? (
            <EditorSurface src={editorSrc} />
          ) : editorError ? (
            <div className="writing-editor-loading"><AlertCircle size={24} /> {editorError}</div>
          ) : (
            <div className="writing-editor-loading"><Loader2 className="spin" size={24} /> Starting OpenVSCode Server…</div>
          )}
        </section>
      </section>
    </main>
  );
}
