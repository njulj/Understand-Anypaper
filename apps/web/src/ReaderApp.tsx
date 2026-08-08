import React, { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Eye,
  EyeOff,
  FileImage,
  FileText,
  FlaskConical,
  GitBranch,
  Link2,
  Loader2,
  Network,
  Plus,
  PenLine,
  Save,
  Search,
  Settings2,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import {
  AgentActivity,
  GraphNode,
  PaperArgumentGraph,
  GraphEdge,
  PageSourceLocation,
  PageSourceSegment,
  PaperDocumentInfo,
  PaperSummary,
  SemanticUnit,
  deletePaper,
  documentPageImageUrl,
  fetchDocumentInfo,
  fetchExternalContributionSubgraph,
  fetchSemanticUnits,
  fetchGraph,
  listPapers,
  patchGraph,
  streamNodeReferences,
  uploadPaper,
} from './api';
import { AgentActivityList, appendAgentActivity } from './AgentActivityList';
import { GraphView, NODE_COLORS } from './GraphView';
import { MOCK_PAPER_ID, mockGraph, mockPaper, mockSemanticUnits } from './mockPaper';
import {
  contributionEvidenceSubtitles,
  contributionGraph,
  isNavigationEdge,
  overviewGraph,
  owningContributionId,
} from './graphNavigation';

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

const DEFAULT_DESKTOP_API_CONFIG: DesktopApiConfig = {
  openaiApiKey: '',
  openaiBaseUrl: 'https://api.openai.com/v1',
  openaiModel: 'gpt-4o-mini',
  sendPromptCacheKey: true,
};

const DEFAULT_DESKTOP_SETUP: DesktopSetupInfo = {
  workspaceDir: '',
  launcherInstallDir: '',
  launcherCommandPath: '',
  launcherSourcePath: '',
  initializedAt: '',
};

const CITATION_ANALYSIS_MODE = 'citation-analysis';
const CITATION_PROGRESS_MODE = 'citation-progress';
const CITATION_ANALYSIS_CHANNEL = 'understand-anypaper-citation-analysis';
const GRAPH_NODE_URL_PREFIX = 'graph://';

function graphNodeIdFromHref(href?: string): string | null {
  if (!href?.startsWith(GRAPH_NODE_URL_PREFIX)) return null;
  return href.slice(GRAPH_NODE_URL_PREFIX.length);
}

function summaryUrlTransform(url: string): string {
  return url.startsWith(GRAPH_NODE_URL_PREFIX) ? url : defaultUrlTransform(url);
}

type AppLocation = {
  paperId: string | null;
  nodeId: string | null;
  mode: string | null;
  view?: 'graph' | null;
  sourcePaperId?: string | null;
  taskId?: string | null;
};

type CitationTaskSnapshot = {
  taskId: string;
  sourcePaperId: string;
  sourceNodeId: string;
  targetPaperId: string | null;
  targetNodeId: string | null;
  status: 'preparing' | 'analyzing' | 'complete' | 'error';
  progress: number;
  message: string;
  activities: AgentActivity[];
};

function readAppLocation(): AppLocation {
  const params = new URLSearchParams(window.location.search);
  return {
    paperId: params.get('paper'),
    nodeId: params.get('node'),
    mode: params.get('mode'),
    view: params.get('view') === 'graph' ? 'graph' : null,
    sourcePaperId: params.get('source_paper'),
    taskId: params.get('task'),
  };
}

function appUrl(
  paperId: string,
  nodeId?: string | null,
  mode?: string | null,
  options: { mock?: boolean; view?: 'graph' | null } = {},
): string {
  const url = new URL(window.location.href);
  url.search = '';
  url.searchParams.set('paper', paperId);
  if (nodeId) url.searchParams.set('node', nodeId);
  if (mode) url.searchParams.set('mode', mode);
  if (options.mock) url.searchParams.set('mock', '1');
  if (options.view) url.searchParams.set('view', options.view);
  return url.toString();
}

function replaceAppLocation(paperId: string, nodeId?: string | null, mode?: string | null) {
  const current = new URLSearchParams(window.location.search);
  window.history.replaceState({}, '', appUrl(paperId, nodeId, mode, {
    mock: current.get('mock') === '1',
    view: current.get('view') === 'graph' ? 'graph' : null,
  }));
}

function citationProgressUrl(
  task: CitationTaskSnapshot,
): string {
  const url = new URL(window.location.href);
  url.search = '';
  if (task.targetPaperId) url.searchParams.set('paper', task.targetPaperId);
  url.searchParams.set('source_paper', task.sourcePaperId);
  url.searchParams.set('node', task.sourceNodeId);
  url.searchParams.set('task', task.taskId);
  url.searchParams.set('mode', CITATION_PROGRESS_MODE);
  return url.toString();
}

function citationTaskStorageKey(taskId: string): string {
  return `understand-anypaper:citation-task:${taskId}`;
}

function saveCitationTask(task: CitationTaskSnapshot) {
  try {
    localStorage.setItem(citationTaskStorageKey(task.taskId), JSON.stringify(task));
  } catch {
    // BroadcastChannel still provides live progress when storage is unavailable.
  }
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CITATION_ANALYSIS_CHANNEL);
    channel.postMessage({ type: 'citation-task-progress', task });
    channel.close();
  }
}

function readCitationTask(taskId: string): CitationTaskSnapshot | null {
  try {
    const serialized = localStorage.getItem(citationTaskStorageKey(taskId));
    if (!serialized) return null;
    return JSON.parse(serialized) as CitationTaskSnapshot;
  } catch {
    return null;
  }
}

function announceCitationAnalysisComplete(sourcePaperId: string) {
  const payload = { type: 'citation-analysis-complete', sourcePaperId };
  window.opener?.postMessage(payload, '*');
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CITATION_ANALYSIS_CHANNEL);
    channel.postMessage(payload);
    channel.close();
  }
}

const PANE_WIDTHS_STORAGE_KEY = 'pag.workspace-pane-widths.v1';
const MIN_SOURCE_PANE_WIDTH = 260;
const MIN_GRAPH_PANE_WIDTH = 360;
const MIN_INSPECTOR_PANE_WIDTH = 280;
const SPLITTER_WIDTH = 0;

type PaneWidths = {
  source: number;
  inspector: number;
};

function initialPaneWidths(): PaneWidths {
  try {
    const stored = window.localStorage.getItem(PANE_WIDTHS_STORAGE_KEY);
    if (!stored) return { source: 320, inspector: 340 };
    const parsed = JSON.parse(stored) as Partial<PaneWidths>;
    if (
      typeof parsed.source === 'number' &&
      typeof parsed.inspector === 'number' &&
      Number.isFinite(parsed.source) &&
      Number.isFinite(parsed.inspector)
    ) {
      return {
        source: Math.max(MIN_SOURCE_PANE_WIDTH, parsed.source),
        inspector: Math.max(MIN_INSPECTOR_PANE_WIDTH, parsed.inspector),
      };
    }
  } catch {
    // A malformed preference should never prevent the workspace from opening.
  }
  return { source: 320, inspector: 340 };
}

function availableSidePaneSpace(workspace: HTMLElement): number {
  const styles = window.getComputedStyle(workspace);
  const horizontalPadding =
    (Number.parseFloat(styles.paddingLeft) || 0) + (Number.parseFloat(styles.paddingRight) || 0);
  return workspace.clientWidth - horizontalPadding - SPLITTER_WIDTH * 2 - MIN_GRAPH_PANE_WIDTH;
}

function mergeGraph(base: PaperArgumentGraph, addition: PaperArgumentGraph): PaperArgumentGraph {
  const nodes = new Map(base.nodes.map((node) => [node.id, node]));
  const edges = new Map(base.edges.map((edge) => [edge.id, edge]));
  addition.nodes.forEach((node) => nodes.set(node.id, node));
  addition.edges.forEach((edge) => edges.set(edge.id, edge));
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()] };
}

function sourceSegments(location: PageSourceLocation): PageSourceSegment[] {
  return location.segments.length ? location.segments : [location];
}

function sourcePageLabel(location: PageSourceLocation): string {
  const pages = [...new Set(sourceSegments(location).map((segment) => segment.page))].sort((a, b) => a - b);
  if (!pages.length) return 'p.?';
  if (pages.length === 1) return `p.${pages[0]}`;
  return `pp.${pages[0]}-${pages[pages.length - 1]}`;
}

function unitOwnerPriority(node: GraphNode, unitId: string): number {
  if (node.id === unitId) return 0;
  if (node.node_type === 'Contribution') return 1;
  if (!['Paper', 'Why', 'How', 'Proof'].includes(node.node_type)) return 2;
  return 3;
}

export function ReaderApp() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const blockRefs = useRef(new Map<string, HTMLElement>());
  const referenceExpansionKeys = useRef(new Set<string>());
  const externalExpansionKeys = useRef(new Set<string>());
  const initialLocation = useRef<AppLocation>(readAppLocation());
  const isMockMode = new URLSearchParams(window.location.search).get('mock') === '1';
  const isGraphWindow = initialLocation.current.view === 'graph';
  const citationAnalysisStarted = useRef(false);
  const citationProgressLoaded = useRef(false);
  const layoutRef = useRef<HTMLElement | null>(null);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [graph, setGraph] = useState<PaperArgumentGraph | null>(null);
  const [semanticUnits, setSemanticUnits] = useState<SemanticUnit[]>([]);
  const [documentInfo, setDocumentInfo] = useState<PaperDocumentInfo | null>(null);
  const [sourceMode, setSourceMode] = useState<'pages' | 'units'>('pages');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphFocusRevision, setGraphFocusRevision] = useState(0);
  const [focusedContributionId, setFocusedContributionId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'idle' | 'uploading' | 'analyzing' | 'ready' | 'error'>('idle');
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [agentActivities, setAgentActivities] = useState<AgentActivity[]>([]);
  const [citationTask, setCitationTask] = useState<CitationTaskSnapshot | null>(null);
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
  const [desktopSettingsOpen, setDesktopSettingsOpen] = useState(false);
  const [desktopSettingsSaving, setDesktopSettingsSaving] = useState(false);
  const [showDesktopApiKey, setShowDesktopApiKey] = useState(false);
  const [desktopSettings, setDesktopSettings] = useState<DesktopApiConfig>(DEFAULT_DESKTOP_API_CONFIG);
  const [desktopSettingsDraft, setDesktopSettingsDraft] = useState<DesktopApiConfig>(
    DEFAULT_DESKTOP_API_CONFIG,
  );
  const [desktopSetup, setDesktopSetup] = useState<DesktopSetupInfo>(DEFAULT_DESKTOP_SETUP);
  const [paneWidths, setPaneWidths] = useState<PaneWidths>(initialPaneWidths);

  const desktopBridge = window.pagDesktop;
  const isDesktopApp = Boolean(desktopBridge?.isDesktopApp);

  useEffect(() => {
    document.documentElement.classList.toggle('desktop-app', isDesktopApp);
    return () => document.documentElement.classList.remove('desktop-app');
  }, [isDesktopApp]);

  useEffect(() => {
    window.localStorage.setItem(PANE_WIDTHS_STORAGE_KEY, JSON.stringify(paneWidths));
  }, [paneWidths]);

  useEffect(() => {
    const fitPaneWidths = () => {
      const layout = layoutRef.current;
      if (!layout || window.matchMedia('(max-width: 980px)').matches) return;
      const availableForSidePanes = availableSidePaneSpace(layout);
      setPaneWidths((current) => {
        if (current.source + current.inspector <= availableForSidePanes) return current;
        const extraSpace = Math.max(
          0,
          availableForSidePanes - MIN_SOURCE_PANE_WIDTH - MIN_INSPECTOR_PANE_WIDTH,
        );
        const currentExtra = Math.max(
          1,
          current.source + current.inspector - MIN_SOURCE_PANE_WIDTH - MIN_INSPECTOR_PANE_WIDTH,
        );
        return {
          source: MIN_SOURCE_PANE_WIDTH + (extraSpace * (current.source - MIN_SOURCE_PANE_WIDTH)) / currentExtra,
          inspector:
            MIN_INSPECTOR_PANE_WIDTH +
            (extraSpace * (current.inspector - MIN_INSPECTOR_PANE_WIDTH)) / currentExtra,
        };
      });
    };

    fitPaneWidths();
    window.addEventListener('resize', fitPaneWidths);
    return () => window.removeEventListener('resize', fitPaneWidths);
  }, []);

  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedNodeIsExternal = Boolean(
    graph && selectedNode && selectedNode.paper_id !== graph.paper_id,
  );
  const focusedContribution = graph?.nodes.find((node) => node.id === focusedContributionId) ?? null;
  const paperTitle = graph
    ? graph.nodes.find((node) => node.node_type === 'Paper')?.title ?? graph.paper_id
    : '';
  const viewGraph = useMemo(() => {
    if (!graph) return null;
    if (focusedContributionId) {
      const subgraph = contributionGraph(graph, focusedContributionId);
      if (subgraph) return subgraph;
    }
    return overviewGraph(graph);
  }, [graph, focusedContributionId]);
  const contributionStats = useMemo(() => {
    return graph ? contributionEvidenceSubtitles(graph) : new Map<string, string>();
  }, [graph]);
  const unitById = useMemo(
    () => new Map(semanticUnits.map((unit) => [unit.semantic_unit_id, unit])),
    [semanticUnits],
  );
  const unitsByPage = useMemo(() => {
    const grouped = new Map<number, { unit: SemanticUnit; segment: PageSourceSegment; segmentIndex: number }[]>();
    for (const unit of semanticUnits) {
      sourceSegments(unit.source_location).forEach((segment, segmentIndex) => {
        const page = segment.page;
        grouped.set(page, [...(grouped.get(page) ?? []), { unit, segment, segmentIndex }]);
      });
    }
    return grouped;
  }, [semanticUnits]);
  const nodeByUnitId = useMemo(() => {
    const byUnitId = new Map<string, string>();
    if (!graph) return byUnitId;
    const localNodes = graph.nodes.filter((node) => node.paper_id === graph.paper_id);
    for (const unit of semanticUnits) {
      const unitId = unit.semantic_unit_id;
      const owner = localNodes
        .filter((node) => node.semantic_unit_ids.includes(unitId))
        .sort((left, right) => unitOwnerPriority(left, unitId) - unitOwnerPriority(right, unitId))[0];
      if (owner) byUnitId.set(unitId, owner.id);
    }
    return byUnitId;
  }, [graph, semanticUnits]);
  const incidentEdges = useMemo(() => {
    if (!graph || !selectedNode) return [];
    return graph.edges.filter(
      (edge) => edge.source_node_id === selectedNode.id || edge.target_node_id === selectedNode.id,
    );
  }, [graph, selectedNode]);
  const crossPaperEdges = useMemo(
    () => incidentEdges.filter((edge) => edge.properties.cross_paper === true),
    [incidentEdges],
  );
  const graphSubtitles = useMemo(() => {
    const subtitles = focusedContributionId ? new Map<string, string>() : new Map(contributionStats);
    if (!graph) return subtitles;
    for (const node of graph.nodes) {
      if (node.paper_id === graph.paper_id) continue;
      const relation = String(node.properties.cross_paper_relation || 'CITED');
      const targetTitle = String(node.properties.target_paper_title || 'Referenced paper');
      subtitles.set(node.id, `${relation} · ${targetTitle}`);
    }
    return subtitles;
  }, [contributionStats, focusedContributionId, graph]);

  const selectedUnitIds = useMemo(
    () => new Set(selectedNode?.semantic_unit_ids ?? []),
    [selectedNode],
  );

  useEffect(() => {
    if (isMockMode) {
      setPapers([mockPaper]);
      loadMockPaper(initialLocation.current.nodeId);
      return;
    }
    listPapers()
      .then(async (existing) => {
        setPapers(existing);
        if (
          initialLocation.current.mode === CITATION_ANALYSIS_MODE ||
          initialLocation.current.mode === CITATION_PROGRESS_MODE
        ) {
          return;
        }
        const requestedPaper = initialLocation.current.paperId
          ? existing.find((paper) => paper.paper_id === initialLocation.current.paperId)
          : null;
        const paper = requestedPaper ?? existing[0];
        if (paper) {
          await loadPaper(
            paper.paper_id,
            requestedPaper
              ? `Loaded “${paper.title}” from URL.`
              : `Restored “${paper.title}” from storage.`,
            requestedPaper ? initialLocation.current.nodeId : null,
          );
          if (!requestedPaper) {
            initialLocation.current = { paperId: paper.paper_id, nodeId: null, mode: null };
            replaceAppLocation(paper.paper_id);
          }
        }
      })
      .catch((error) => {
        setStatus('error');
        setMessage(error instanceof Error ? error.message : 'Failed to restore the requested paper.');
      });
  }, [isMockMode]);

  useEffect(() => {
    const handleCompletion = (payload: unknown) => {
      if (!payload || typeof payload !== 'object') return;
      const event = payload as { type?: string; sourcePaperId?: string };
      if (
        event.type !== 'citation-analysis-complete' ||
        !event.sourcePaperId ||
        graph?.paper_id !== event.sourcePaperId ||
        status === 'analyzing'
      ) {
        return;
      }
      void loadPaper(
        event.sourcePaperId,
        'Citation analysis completed in another tab. Refreshed cross-paper links.',
        selectedNodeId,
      );
    };
    const handleWindowMessage = (event: MessageEvent) => handleCompletion(event.data);
    window.addEventListener('message', handleWindowMessage);
    const channel =
      typeof BroadcastChannel !== 'undefined'
        ? new BroadcastChannel(CITATION_ANALYSIS_CHANNEL)
        : null;
    if (channel) channel.onmessage = (event) => handleCompletion(event.data);
    return () => {
      window.removeEventListener('message', handleWindowMessage);
      channel?.close();
    };
  }, [graph?.paper_id, selectedNodeId, status]);

  useEffect(() => {
    if (!desktopBridge?.getApiConfig) return;
    desktopBridge
      .getApiConfig()
      .then((config) => {
        setDesktopSettings(config);
        setDesktopSettingsDraft(config);
      })
      .catch(() => undefined);
  }, [desktopBridge]);

  useEffect(() => {
    if (!desktopBridge?.getSetupInfo) return;
    desktopBridge
      .getSetupInfo()
      .then((setup) => {
        setDesktopSetup(setup);
      })
      .catch(() => undefined);
  }, [desktopBridge]);

  useEffect(() => {
    if (!selectedNode) return;
    setEditTitle(selectedNode.title);
    setEditSummary(selectedNode.summary);
    setEditVerified(selectedNode.verified);
    const first = selectedNode.semantic_unit_ids.find((id) => blockRefs.current.has(id));
    if (first) {
      scrollToUnit(first);
    }
  }, [selectedNodeId, graph, unitById]);

  useEffect(() => {
    const first = selectedNode?.semantic_unit_ids.find((id) => blockRefs.current.has(id));
    if (first) window.requestAnimationFrame(() => scrollToUnit(first));
  }, [sourceMode, documentInfo, selectedNodeId, unitById]);

  useEffect(() => {
    const location = initialLocation.current;
    const sourcePaperId = location.sourcePaperId ?? location.paperId;
    if (
      location.mode !== CITATION_ANALYSIS_MODE ||
      citationAnalysisStarted.current ||
      !sourcePaperId ||
      !location.nodeId
    ) {
      return;
    }
    citationAnalysisStarted.current = true;
    void runCitationAnalysis(sourcePaperId, location.nodeId);
  }, []);

  useEffect(() => {
    const location = initialLocation.current;
    if (location.mode !== CITATION_PROGRESS_MODE || !location.taskId) return;
    const taskId = location.taskId;

    const applyTask = (task: CitationTaskSnapshot) => {
      if (task.taskId !== taskId) return;
      setCitationTask(task);
      setUploadProgress(task.progress);
      setMessage(task.message);
      setAgentActivities(task.activities);
      if (task.status === 'error') {
        setStatus('error');
        return;
      }
      if (task.status !== 'complete' || !task.targetPaperId) {
        setStatus('analyzing');
        return;
      }
      if (citationProgressLoaded.current) return;
      citationProgressLoaded.current = true;
      initialLocation.current = {
        paperId: task.targetPaperId,
        sourcePaperId: null,
        nodeId: task.targetNodeId,
        mode: null,
        taskId: null,
      };
      replaceAppLocation(task.targetPaperId, task.targetNodeId);
      void loadPaper(
        task.targetPaperId,
        'Cited-paper analysis complete. Loaded the analyzed paper.',
        task.targetNodeId,
      );
    };

    const storedTask = readCitationTask(taskId);
    if (storedTask) applyTask(storedTask);
    else {
      setStatus('analyzing');
      setMessage('Waiting for cited-paper analysis progress.');
    }

    const channel =
      typeof BroadcastChannel !== 'undefined'
        ? new BroadcastChannel(CITATION_ANALYSIS_CHANNEL)
        : null;
    if (channel) {
      channel.onmessage = (event) => {
        const payload = event.data as { type?: string; task?: CitationTaskSnapshot };
        if (payload.type === 'citation-task-progress' && payload.task) applyTask(payload.task);
      };
    }
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== citationTaskStorageKey(taskId) || !event.newValue) return;
      try {
        applyTask(JSON.parse(event.newValue) as CitationTaskSnapshot);
      } catch {
        // Ignore a partially written or malformed snapshot.
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => {
      channel?.close();
      window.removeEventListener('storage', handleStorage);
    };
  }, []);

  useEffect(() => {
    if (!graph) return;
    const editableNodes = graph.nodes.filter((node) => node.paper_id === graph.paper_id);
    const source =
      (selectedNode?.paper_id === graph.paper_id ? selectedNodeId : null) ??
      editableNodes[0]?.id ??
      '';
    const target = editableNodes.find((node) => node.id !== source)?.id ?? source;
    setNewEdgeSourceId(source);
    setNewEdgeTargetId(target);
    const evidence = selectedNode?.semantic_unit_ids.find((id) => unitById.has(id)) ?? '';
    setNewNodeEvidenceId(evidence);
    setNewEdgeEvidenceId(evidence);
  }, [graph?.paper_id, selectedNodeId, unitById]);

  async function loadPaper(
    paperId: string,
    readyMessage?: string,
    requestedNodeId?: string | null,
  ) {
    if (isMockMode || paperId === MOCK_PAPER_ID) {
      loadMockPaper(requestedNodeId);
      return;
    }
    const [nextGraph, nextSemanticUnits, nextDocumentInfo] = await Promise.all([
      fetchGraph(paperId),
      fetchSemanticUnits(paperId),
      fetchDocumentInfo(paperId).catch(() => null),
    ]);
    setGraph(nextGraph);
    setSemanticUnits(nextSemanticUnits);
    setDocumentInfo(nextDocumentInfo);
    setSourceMode(nextDocumentInfo ? 'pages' : 'units');
    const requestedNode = requestedNodeId
      ? nextGraph.nodes.find((node) => node.id === requestedNodeId)
      : null;
    setSelectedNodeId(requestedNode?.id ?? nextGraph.nodes[0]?.id ?? null);
    setFocusedContributionId(null);
    referenceExpansionKeys.current.clear();
    externalExpansionKeys.current.clear();
    setStatus('ready');
    setUploadProgress(null);
    setMessage(readyMessage ?? `Graph ready: ${nextGraph.nodes.length} nodes, ${nextGraph.edges.length} edges.`);
  }

  function loadMockPaper(requestedNodeId?: string | null) {
    const nextGraph = JSON.parse(JSON.stringify(mockGraph)) as PaperArgumentGraph;
    setGraph(nextGraph);
    setSemanticUnits(JSON.parse(JSON.stringify(mockSemanticUnits)) as SemanticUnit[]);
    setDocumentInfo(null);
    setSourceMode('units');
    setSelectedNodeId(
      requestedNodeId && nextGraph.nodes.some((node) => node.id === requestedNodeId)
        ? requestedNodeId
        : nextGraph.nodes[0]?.id ?? null,
    );
    setFocusedContributionId(null);
    setStatus('ready');
    setUploadProgress(null);
    setMessage('Mock mode — local sample data; no paper or backend is required.');
  }

  function clearPaperState(nextMessage = 'Upload a .txt, .md, or PDF to build a Paper Argument Graph.') {
    setGraph(null);
    setSemanticUnits([]);
    setDocumentInfo(null);
    setSelectedNodeId(null);
    setFocusedContributionId(null);
    referenceExpansionKeys.current.clear();
    externalExpansionKeys.current.clear();
    setQuery('');
    setStatus('idle');
    setUploadProgress(null);
    setMessage(nextMessage);
  }

  function scrollToUnit(unitId: string) {
    window.requestAnimationFrame(() => {
      blockRefs.current.get(unitId)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  function revealNode(nodeId: string) {
    if (!graph) return;
    const node = graph.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    if (node.node_type === 'Paper') {
      setFocusedContributionId(null);
    } else if (node.node_type === 'Contribution') {
      setFocusedContributionId(node.id);
      if (node.paper_id !== graph.paper_id) {
        void expandExternalContribution(node);
      }
    } else {
      const nodeIsInFocusedContribution = Boolean(
        focusedContributionId &&
          contributionGraph(graph, focusedContributionId)?.nodes.some((item) => item.id === nodeId),
      );
      const owner = nodeIsInFocusedContribution
        ? focusedContributionId
        : owningContributionId(graph, nodeId);
      if (owner) setFocusedContributionId(owner);
    }
    setSelectedNodeId(nodeId);
    if (node.paper_id === graph.paper_id && initialLocation.current.mode !== CITATION_ANALYSIS_MODE) {
      initialLocation.current = { paperId: graph.paper_id, nodeId, mode: null };
      replaceAppLocation(graph.paper_id, nodeId);
    }
  }

  function returnToPaperOverview() {
    if (!graph) return;
    const paperNode = graph.nodes.find((node) => node.node_type === 'Paper');
    setFocusedContributionId(null);
    setSelectedNodeId(paperNode?.id ?? graph.nodes[0]?.id ?? null);
  }

  function handleGraphNodeSelect(nodeId: string) {
    revealNode(nodeId);
  }

  function openCitationProgressTab(task: CitationTaskSnapshot) {
    if (!task.targetPaperId) return;
    const analysisWindow = window.open(citationProgressUrl(task), '_blank');
    if (!analysisWindow) {
      setStatus('error');
      setMessage('The browser blocked the citation-analysis tab. Allow pop-ups and try again.');
      return;
    }
  }

  async function runCitationAnalysis(paperId: string, nodeId: string) {
    if (isMockMode) {
      setMessage('Citation analysis is unavailable in mock mode; use the included reference node to develop this UI.');
      return;
    }
    const key = `${paperId}:${nodeId}`;
    if (referenceExpansionKeys.current.has(key)) return;
    referenceExpansionKeys.current.add(key);
    const taskId =
      typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let latestTask: CitationTaskSnapshot = {
      taskId,
      sourcePaperId: paperId,
      sourceNodeId: nodeId,
      targetPaperId: null,
      targetNodeId: null,
      status: 'preparing',
      progress: 0,
      message: 'Downloading and parsing cited papers.',
      activities: [],
    };

    const updateTask = (changes: Partial<CitationTaskSnapshot>) => {
      latestTask = { ...latestTask, ...changes };
      setCitationTask(latestTask);
      saveCitationTask(latestTask);
    };

    updateTask({});
    setStatus('analyzing');
    setUploadProgress(0);
    setAgentActivities([]);
    setMessage(latestTask.message);
    try {
      const expansion = await streamNodeReferences(paperId, nodeId, {
        onStageProgress: (progress) => {
          setUploadProgress(progress.progress);
          if (progress.event !== 'agent_activity') setMessage(progress.message);
          const activities = progress.activity
            ? appendAgentActivity(latestTask.activities, progress.activity)
            : latestTask.activities;
          const targetPaperId = latestTask.targetPaperId ?? progress.target_paper_id ?? null;
          updateTask({
            targetPaperId,
            status: targetPaperId ? 'analyzing' : 'preparing',
            progress: progress.progress,
            message: progress.message,
            activities,
          });
          setAgentActivities(activities);
        },
      });
      setGraph((current) =>
        current?.paper_id === expansion.paper_id ? expansion.graph : current,
      );
      const linked = expansion.results.filter((result) =>
        ['linked', 'cached_link'].includes(result.status),
      );
      if (linked.length) {
        setMessage(
          `Connected ${linked.length} citation${linked.length === 1 ? '' : 's'} to referenced contributions.`,
        );
      } else if (expansion.results.length) {
        const reason = expansion.results.map((result) => result.reason).find(Boolean);
        setMessage(reason || 'No cited contribution could be matched confidently.');
      } else {
        setMessage('This node has no citation context to analyze.');
      }
      announceCitationAnalysisComplete(paperId);
      const target = expansion.results.find((result) => result.target_paper_id);
      setPapers(await listPapers());
      const targetPaperId = target?.target_paper_id ?? latestTask.targetPaperId;
      const targetNodeId = target?.target_node_id ?? null;
      updateTask({
        targetPaperId,
        targetNodeId,
        status: 'complete',
        progress: 100,
        message: targetPaperId
          ? 'Cited-paper analysis complete. Open it in the progress tab.'
          : 'Citation analysis finished without an analyzable cited paper.',
      });
      setStatus('ready');
      setUploadProgress(100);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Failed to resolve cited contributions.';
      updateTask({ status: 'error', message: errorMessage });
      setStatus('error');
      setMessage(errorMessage);
    } finally {
      referenceExpansionKeys.current.delete(key);
    }
  }

  async function expandExternalContribution(node: GraphNode) {
    if (isMockMode) return;
    if (!graph || node.paper_id === graph.paper_id) return;
    const key = `${graph.paper_id}:${node.paper_id}:${node.id}`;
    if (externalExpansionKeys.current.has(key)) return;
    externalExpansionKeys.current.add(key);
    try {
      const subgraph = await fetchExternalContributionSubgraph(
        graph.paper_id,
        node.paper_id,
        node.id,
      );
      setGraph((current) => (current ? mergeGraph(current, subgraph) : current));
    } catch (error) {
      externalExpansionKeys.current.delete(key);
      setStatus('error');
      setMessage(
        error instanceof Error ? error.message : 'Failed to expand the referenced contribution.',
      );
    }
  }

  function selectUnitOwner(unitId: string) {
    const nodeId = nodeByUnitId.get(unitId);
    if (!nodeId) return;
    revealNode(nodeId);
    setGraphFocusRevision((current) => current + 1);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    if (isMockMode) return;
    const file = event.target.files?.[0];
    if (!file) return;
    setStatus('uploading');
    setUploadProgress(0);
    setAgentActivities([]);
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
          if (progress.event !== 'agent_activity') setMessage(progress.message);
          if (progress.activity) {
            setAgentActivities((current) => appendAgentActivity(current, progress.activity!));
          }
        },
      });
      setUploadProgress(100);
      setMessage('Graph generated. Loading source locations...');
      await loadPaper(nextGraph.paper_id);
      initialLocation.current = { paperId: nextGraph.paper_id, nodeId: null, mode: null };
      replaceAppLocation(nextGraph.paper_id);
      setQuery('');
      setPapers(await listPapers());
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
    if (isMockMode) {
      setMessage('Mock mode data is local. Leave mock mode to manage stored papers.');
      return;
    }
    const title = graph.nodes.find((node) => node.node_type === 'Paper')?.title ?? graph.paper_id;
    if (!window.confirm(`Delete “${title}” and its graph?`)) return;
    setSaving(true);
    try {
      const result = await deletePaper(graph.paper_id);
      setPapers(result.papers);
      const nextPaper = result.papers[0];
      if (nextPaper) {
        initialLocation.current = { paperId: nextPaper.paper_id, nodeId: null, mode: null };
        replaceAppLocation(nextPaper.paper_id);
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
      if (isMockMode) {
        setGraph({
          ...graph,
          nodes: graph.nodes.map((node) =>
            node.id === selectedNode.id
              ? { ...node, title: editTitle, summary: editSummary, verified: editVerified }
              : node,
          ),
        });
        setMessage(`Saved local mock changes to ${selectedNode.id}.`);
        setStatus('ready');
        return;
      }
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
      if (isMockMode) {
        const nextGraph = {
          ...graph,
          nodes: graph.nodes.filter((node) => node.id !== selectedNode.id),
          edges: graph.edges.filter(
            (edge) => edge.source_node_id !== selectedNode.id && edge.target_node_id !== selectedNode.id,
          ),
        };
        setGraph(nextGraph);
        if (selectedNode.id === focusedContributionId) setFocusedContributionId(null);
        setSelectedNodeId(nextGraph.nodes[0]?.id ?? null);
        setMessage('Removed node from local mock data.');
        setStatus('ready');
        return;
      }
      const nextGraph = await patchGraph(graph.paper_id, [{ op: 'remove_node', id: selectedNode.id }]);
      setGraph(nextGraph);
      if (selectedNode.id === focusedContributionId) setFocusedContributionId(null);
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
    const evidencePage = evidenceUnit?.source_location.page;
    const node: GraphNode = {
      id: `manual-${graph.paper_id.slice(0, 8)}-${Date.now()}`,
      paper_id: graph.paper_id,
      node_type: newNodeType,
      title: newNodeTitle.trim(),
      summary: newNodeSummary.trim(),
      confidence: 1,
      source_type: 'human_added',
      semantic_unit_ids: evidenceUnit ? [evidenceUnit.semantic_unit_id] : [],
      reference_ids: [],
      page_ranges: evidencePage ? [[evidencePage, evidencePage]] : [],
      properties: { manual: true },
      created_by: 'human',
      verified: true,
    };
    setSaving(true);
    try {
      if (isMockMode) {
        setGraph({ ...graph, nodes: [...graph.nodes, node] });
        setSelectedNodeId(node.id);
        setNewNodeTitle('');
        setNewNodeSummary('');
        setMessage(`Added ${node.title} to local mock data.`);
        setStatus('ready');
        return;
      }
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
      source_paper_id: graph.paper_id,
      source_node_id: newEdgeSourceId,
      target_paper_id: graph.paper_id,
      target_node_id: newEdgeTargetId,
      edge_type: newEdgeType,
      confidence: 1,
      semantic_unit_ids: evidenceUnit ? [evidenceUnit.semantic_unit_id] : [],
      inference_type: 'human_added',
      properties: { manual: true },
    };
    setSaving(true);
    try {
      if (isMockMode) {
        setGraph({ ...graph, edges: [...graph.edges, edge] });
        setMessage(`Added ${edge.edge_type} relation to local mock data.`);
        setStatus('ready');
        return;
      }
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
      if (isMockMode) {
        setGraph({ ...graph, edges: graph.edges.filter((edge) => edge.id !== edgeId) });
        setMessage('Removed relation from local mock data.');
        setStatus('ready');
        return;
      }
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

  function openDesktopSettings() {
    setDesktopSettingsDraft(desktopSettings);
    setShowDesktopApiKey(false);
    setDesktopSettingsOpen(true);
  }

  function closeDesktopSettings() {
    if (desktopSettingsSaving) return;
    setDesktopSettingsDraft(desktopSettings);
    setShowDesktopApiKey(false);
    setDesktopSettingsOpen(false);
  }

  async function saveDesktopSettings() {
    if (!desktopBridge?.saveApiConfig) return;
    setDesktopSettingsSaving(true);
    try {
      const savedConfig = await desktopBridge.saveApiConfig(desktopSettingsDraft);
      setDesktopSettings(savedConfig);
      setDesktopSettingsDraft(savedConfig);
      setShowDesktopApiKey(false);
      setDesktopSettingsOpen(false);
      setStatus('ready');
      setMessage('Saved desktop API settings. Future uploads will use the updated provider configuration.');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to save desktop API settings.');
    } finally {
      setDesktopSettingsSaving(false);
    }
  }

  function nodeLabel(nodeId: string): string {
    return graph?.nodes.find((node) => node.id === nodeId)?.title ?? nodeId;
  }

  function switchMockMode() {
    const url = new URL(window.location.href);
    url.search = '';
    if (!isMockMode) {
      url.searchParams.set('mock', '1');
      url.searchParams.set('paper', MOCK_PAPER_ID);
    }
    if (isGraphWindow) url.searchParams.set('view', 'graph');
    window.location.assign(url);
  }

  function openGraphWindow() {
    if (!graph) return;
    const options = {
      paperId: graph.paper_id,
      nodeId: selectedNodeId,
      mock: isMockMode,
    };
    if (desktopBridge?.openGraphWindow) {
      void desktopBridge.openGraphWindow(options).catch((error) => {
        setStatus('error');
        setMessage(error instanceof Error ? error.message : 'Failed to open the graph window.');
      });
      return;
    }
    const url = appUrl(options.paperId, options.nodeId, null, { mock: options.mock, view: 'graph' });
    const graphWindow = window.open(url, '_blank', 'popup,width=1240,height=860');
    if (!graphWindow) {
      setStatus('error');
      setMessage('The browser blocked the graph window. Allow pop-ups and try again.');
    }
  }

  function startPaneResize(
    event: React.PointerEvent<HTMLDivElement>,
    pane: keyof PaneWidths,
  ) {
    if (window.matchMedia('(max-width: 980px)').matches) return;
    const layout = layoutRef.current;
    if (!layout) return;

    event.preventDefault();
    const startX = event.clientX;
    const startWidths = paneWidths;
    const otherPane = pane === 'source' ? 'inspector' : 'source';
    const minimum = pane === 'source' ? MIN_SOURCE_PANE_WIDTH : MIN_INSPECTOR_PANE_WIDTH;
    const availableForSidePanes = availableSidePaneSpace(layout);
    const maximum = Math.max(minimum, availableForSidePanes - startWidths[otherPane]);

    const finishResize = () => {
      document.body.classList.remove('pane-resizing');
      window.removeEventListener('pointermove', resizePane);
      window.removeEventListener('pointerup', finishResize);
      window.removeEventListener('pointercancel', finishResize);
    };
    const resizePane = (moveEvent: PointerEvent) => {
      const movement = moveEvent.clientX - startX;
      // The right divider moves in the opposite direction to its panel width.
      const nextWidth = pane === 'source' ? startWidths[pane] + movement : startWidths[pane] - movement;
      setPaneWidths((current) => ({
        ...current,
        [pane]: Math.min(maximum, Math.max(minimum, nextWidth)),
      }));
    };

    document.body.classList.add('pane-resizing');
    window.addEventListener('pointermove', resizePane);
    window.addEventListener('pointerup', finishResize);
    window.addEventListener('pointercancel', finishResize);
  }

  const legendTypes = useMemo(() => {
    if (!viewGraph) return [];
    return [...new Set(viewGraph.nodes.map((node) => node.node_type))];
  }, [viewGraph]);
  const isBusy = status === 'uploading' || status === 'analyzing';
  const isCitationAnalysisTask =
    initialLocation.current.mode === CITATION_ANALYSIS_MODE ||
    initialLocation.current.mode === CITATION_PROGRESS_MODE;
  const selectedCitationTask =
    citationTask &&
    graph &&
    selectedNodeId === citationTask.sourceNodeId &&
    graph.paper_id === citationTask.sourcePaperId
      ? citationTask
      : null;
  const citationIsPreparing = Boolean(
    selectedCitationTask &&
      selectedCitationTask.status === 'preparing' &&
      !selectedCitationTask.targetPaperId,
  );

  const shellClassName = [
    'shell',
    isDesktopApp ? 'desktop-shell' : '',
    isDesktopApp ? `desktop-platform-${desktopBridge?.platform ?? 'unknown'}` : '',
    isMockMode ? 'mock-mode' : '',
    isGraphWindow ? 'graph-window' : '',
  ]
    .filter(Boolean)
    .join(' ');
  const workspaceStyle = {
    '--source-pane-width': `${paneWidths.source}px`,
    '--inspector-pane-width': `${paneWidths.inspector}px`,
  } as React.CSSProperties;

  return (
    <main className={shellClassName}>
      <section
        ref={layoutRef}
        className={`${isDesktopApp ? 'desktop-layout' : 'web-layout'}${isGraphWindow ? ' graph-window-layout' : ''}`}
        style={workspaceStyle}
      >
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
                  className={sourceMode === 'units' ? 'active' : ''}
                  title="Semantic units"
                  onClick={() => setSourceMode('units')}
                >
                  <FileText size={15} />
                </button>
              </div>
            ) : null}
          </div>
          <div className="source-content">
          <div className={`status-line ${status}`}>
            <div className="status-message">
              {isBusy ? (
                <Loader2 className="spin" size={18} />
              ) : status === 'error' ? (
                <AlertCircle size={18} />
              ) : (
                <CheckCircle2 size={18} />
              )}
              <span>{message}</span>
            </div>
            {isBusy ? (
              <div
                className="upload-progress"
                role="progressbar"
                aria-label={status === 'analyzing' ? 'Citation analysis progress' : 'Upload progress'}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={uploadProgress ?? 0}
              >
                <span style={{ width: `${uploadProgress ?? 0}%` }} />
              </div>
            ) : null}
            {isBusy && agentActivities.length ? (
              <AgentActivityList activities={agentActivities} />
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
                    {(unitsByPage.get(page.page) ?? []).map(({ unit, segment, segmentIndex }) => {
                      const unitId = unit.semantic_unit_id;
                      const { bbox, extracted_text } = segment;
                      if (bbox.length !== 4) return null;
                      const [ymin, xmin, ymax, xmax] = bbox;
                      const highlighted = selectedUnitIds.has(unitId);
                      return (
                        <button
                          key={`${unitId}:${segmentIndex}`}
                          ref={(el) => {
                            if (segmentIndex !== 0) return;
                            if (el) blockRefs.current.set(unitId, el);
                            else blockRefs.current.delete(unitId);
                          }}
                          type="button"
                          className={`bbox-highlight ${highlighted ? 'highlighted' : ''}`}
                          style={{
                            left: `${xmin * 100}%`,
                            top: `${ymin * 100}%`,
                            width: `${(xmax - xmin) * 100}%`,
                            height: `${(ymax - ymin) * 100}%`,
                          }}
                          title={`${unit.role}: ${(extracted_text || unit.text).slice(0, 180)}`}
                          onClick={() => selectUnitOwner(unitId)}
                        />
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          ) : semanticUnits.length ? (
            <div className="block-list">
              {semanticUnits.map((unit) => {
                const unitId = unit.semantic_unit_id;
                const highlighted = selectedUnitIds.has(unitId);
                return (
                  <div
                    key={unitId}
                    ref={(el) => {
                      if (el) blockRefs.current.set(unitId, el);
                      else blockRefs.current.delete(unitId);
                    }}
                    className={`content-block ${highlighted ? 'highlighted' : ''}`}
                    onClick={() => selectUnitOwner(unitId)}
                  >
                    <header>
                      <span className="role-tag">{unit.role}</span>
                      <span>{sourcePageLabel(unit.source_location)}</span>
                    </header>
                    <p>{unit.text}</p>
                  </div>
                );
              })}
            </div>
          ) : (
            <button className="upload-drop" type="button" onClick={() => fileInputRef.current?.click()}>
              <UploadCloud size={34} />
              <span>Choose a paper</span>
            </button>
          )}
          </div>
        </aside>

        <div
          className="pane-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize source panel"
          aria-valuemin={MIN_SOURCE_PANE_WIDTH}
          aria-valuenow={Math.round(paneWidths.source)}
          onPointerDown={(event) => startPaneResize(event, 'source')}
        />

        <section className="main-column">
          <header className="toolbar">
            <div className="brand"><GitBranch size={22} /> Understand Anypaper</div>
            {isDesktopApp ? (
              <div className="desktop-titlebar-title" title={paperTitle || 'Understand Anypaper'}>
                <GitBranch size={20} />
                <span>{paperTitle || 'Understand Anypaper'}</span>
              </div>
            ) : null}
            <div className="toolbar-actions">
              {papers.length && !isCitationAnalysisTask ? (
                <select
                  className="paper-select"
                  value={graph?.paper_id ?? ''}
                  disabled={isBusy}
                  onChange={(event) => {
                    const paper = papers.find((item) => item.paper_id === event.target.value);
                    initialLocation.current = {
                      paperId: event.target.value,
                      nodeId: null,
                      mode: null,
                    };
                    replaceAppLocation(event.target.value);
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
              {!isGraphWindow ? <button
                className="icon-action toolbar-icon-action desktop-optional-action"
                type="button"
                title="LaTeX writing workspace"
                aria-label="LaTeX writing workspace"
                onClick={() => {
                  const url = new URL(window.location.href);
                  if (url.protocol === 'file:') {
                    url.search = '';
                    url.hash = '/write';
                  } else {
                    url.pathname = '/write';
                    url.search = '';
                    url.hash = '';
                  }
                  window.location.assign(url);
                }}
                disabled={isBusy}
              >
                <PenLine size={16} />
              </button> : null}
              {!isGraphWindow ? <button
                className="icon-action toolbar-icon-action"
                type="button"
                title="Add paper"
                aria-label="Add paper"
                onClick={() => fileInputRef.current?.click()}
                disabled={isBusy}
              >
                {status === 'uploading' ? (
                  <Loader2 className="spin" size={16} />
                ) : (
                  <UploadCloud size={16} />
                )}
              </button> : null}
              {!isGraphWindow ? <button
                className="icon-action toolbar-icon-action danger-icon-action desktop-optional-action"
                type="button"
                title="Delete current paper"
                aria-label="Delete current paper"
                onClick={deleteCurrentPaper}
                disabled={!graph || saving || isBusy || isMockMode}
              >
                <Trash2 size={16} />
              </button> : null}
              {!isGraphWindow ? <button
                className="icon-action toolbar-icon-action desktop-optional-action"
                type="button"
                title="API settings"
                aria-label="API settings"
                onClick={openDesktopSettings}
                disabled={isBusy || saving}
              >
                <Settings2 size={16} />
              </button> : null}
              <button
                className={`icon-action toolbar-icon-action ${isMockMode ? 'active-icon-action' : ''}`}
                type="button"
                title={isMockMode ? 'Leave mock mode' : 'Open mock data mode'}
                aria-label={isMockMode ? 'Leave mock mode' : 'Open mock data mode'}
                onClick={switchMockMode}
                disabled={isBusy}
              >
                <FlaskConical size={16} />
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
        <section className="graph-pane" aria-label="Paper argument graph">
          {graph && viewGraph ? (
            <>
              <div className="graph-summary">
                <div>
                  <span className="eyebrow">{focusedContribution ? 'Contribution' : 'Paper'}</span>
                  <nav className="graph-breadcrumb" aria-label="Graph level">
                    {focusedContribution ? (
                      <>
                        <button
                          type="button"
                          className="crumb-link"
                          title="Back to paper overview"
                          onClick={returnToPaperOverview}
                        >
                          <ArrowLeft size={14} />
                          <span>{paperTitle}</span>
                        </button>
                        <span className="crumb-sep">›</span>
                        <strong>{focusedContribution.title}</strong>
                      </>
                    ) : (
                      <strong>{paperTitle}</strong>
                    )}
                  </nav>
                </div>
                <div className="graph-summary-meta">
                  <span>{viewGraph.nodes.length} nodes</span>
                  <span>{viewGraph.edges.length} edges</span>
                  {!isGraphWindow ? (
                    <button
                      className="graph-window-action"
                      type="button"
                      onClick={openGraphWindow}
                      title="Open the graph and Inspector in a dedicated window"
                    >
                      <Network size={15} />
                      Open graph window
                    </button>
                  ) : null}
                </div>
              </div>
              <div className="graph-stage">
                <GraphView
                  graph={viewGraph}
                  selectedNodeId={selectedNodeId}
                  focusRevision={graphFocusRevision}
                  query={query}
                  onSelectNode={handleGraphNodeSelect}
                  subtitles={graphSubtitles}
                />
                {!focusedContribution ? (
                  <article className="paper-summary-card" aria-labelledby="paper-summary-heading">
                    <div className="paper-summary-heading">
                      <FileText size={17} aria-hidden="true" />
                      <h2 id="paper-summary-heading">Summary</h2>
                    </div>
                    <div className="paper-summary-markdown">
                      <ReactMarkdown
                        urlTransform={summaryUrlTransform}
                        components={{
                          a: ({ href, children, node: _node, ...props }) => {
                            const nodeId = graphNodeIdFromHref(href);
                            if (nodeId !== null) {
                              const targetExists = graph.nodes.some((node) => node.id === nodeId);
                              return (
                                <a
                                  {...props}
                                  href={href}
                                  className={targetExists ? 'graph-node-link' : 'graph-node-link broken'}
                                  aria-disabled={!targetExists}
                                  onClick={(event) => {
                                    event.preventDefault();
                                    if (targetExists) {
                                      revealNode(nodeId);
                                    } else {
                                      setStatus('error');
                                      setMessage(`Summary links to an unknown graph node: ${nodeId}`);
                                    }
                                  }}
                                >
                                  {children}
                                </a>
                              );
                            }
                            const opensExternally = /^https?:\/\//i.test(href ?? '');
                            return (
                              <a
                                {...props}
                                href={href}
                                target={opensExternally ? '_blank' : undefined}
                                rel={opensExternally ? 'noreferrer' : undefined}
                              >
                                {children}
                              </a>
                            );
                          },
                        }}
                      >
                        {graph.summary || 'No paper summary is available.'}
                      </ReactMarkdown>
                    </div>
                  </article>
                ) : null}
              </div>
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

        <div
          className="pane-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize inspector panel"
          aria-valuemin={MIN_INSPECTOR_PANE_WIDTH}
          aria-valuenow={Math.round(paneWidths.inspector)}
          onPointerDown={(event) => startPaneResize(event, 'inspector')}
        />

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
                      return (
                        <button
                          key={unitId}
                          type="button"
                          className="evidence-chip"
                          onClick={() => scrollToUnit(unitId)}
                        >
                          {unit ? `${unit.role} · ${sourcePageLabel(unit.source_location)}` : unitId}
                        </button>
                      );
                    })}
                  </div>
                </section>
              ) : null}

              <section className="citation-links">
                <div className="section-heading-row">
                  <h3>{selectedNodeIsExternal ? 'Referenced paper' : 'Cited contributions'}</h3>
                  {!selectedNodeIsExternal ? (
                    <button
                      type="button"
                      className="subtle-inline-action"
                      onClick={() => {
                        if (selectedCitationTask?.targetPaperId) {
                          openCitationProgressTab(selectedCitationTask);
                        } else if (graph) {
                          void runCitationAnalysis(graph.paper_id, selectedNode.id);
                        }
                      }}
                      disabled={citationIsPreparing || (status === 'analyzing' && !selectedCitationTask)}
                    >
                      {citationIsPreparing ? (
                        <Loader2 className="spin" size={13} />
                      ) : (
                        <Link2 size={13} />
                      )}
                      {citationIsPreparing
                        ? 'Downloading cited paper…'
                        : selectedCitationTask?.targetPaperId
                          ? selectedCitationTask.status === 'complete'
                            ? 'View cited paper'
                            : 'Citation downloaded · View analysis progress'
                          : 'Analyze citations'}
                    </button>
                  ) : null}
                </div>
                {selectedNodeIsExternal ? (
                  <article className="citation-card">
                    <strong>{String(selectedNode.properties.target_paper_title || selectedNode.paper_id)}</strong>
                    <span>Contribution from a referenced paper. Its WHY / HOW / PROOF is loaded on demand.</span>
                  </article>
                ) : crossPaperEdges.length ? (
                  crossPaperEdges.map((edge) => {
                    const otherId =
                      edge.source_node_id === selectedNode.id
                        ? edge.target_node_id
                        : edge.source_node_id;
                    return (
                      <article className="citation-card" key={`citation-${edge.id}`}>
                        <div>
                          <strong>{edge.edge_type}</strong>
                          <span>{Math.round(edge.confidence * 100)}% confidence</span>
                        </div>
                        <button type="button" className="edge-link" onClick={() => revealNode(otherId)}>
                          → {nodeLabel(otherId)}
                        </button>
                        <small>{String(edge.properties.target_paper_title || '')}</small>
                        {edge.properties.citation_text ? (
                          <p>“{String(edge.properties.citation_text).slice(0, 280)}”</p>
                        ) : null}
                      </article>
                    );
                  })
                ) : (
                  <p className="muted">
                    No resolved citations for this node. Select Analyze citations to process them on demand.
                  </p>
                )}
              </section>

              {!selectedNodeIsExternal ? (
                <>
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
                    {(graph?.nodes ?? []).filter((node) => node.paper_id === graph?.paper_id).map((node) => (
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
                    {(graph?.nodes ?? []).filter((node) => node.paper_id === graph?.paper_id).map((node) => (
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
                </>
              ) : null}

              <section className="edge-list">
                <h3>Relations</h3>
                {incidentEdges.length ? incidentEdges.map((edge) => {
                  const otherId = edge.source_node_id === selectedNode.id ? edge.target_node_id : edge.source_node_id;
                  return (
                    <article className="edge-item" key={edge.id}>
                      <div className="edge-item-header">
                        <strong>{edge.edge_type}</strong>
                        {edge.source_paper_id === graph?.paper_id ? (
                          <button
                            type="button"
                            className="icon-action"
                            title="Remove relation"
                            onClick={() => removeEdge(edge.id)}
                            disabled={saving}
                          >
                            <X size={14} />
                          </button>
                        ) : null}
                      </div>
                      <button type="button" className="edge-link" onClick={() => revealNode(otherId)}>
                        {edge.source_node_id === selectedNode.id ? '→' : '←'} {nodeLabel(otherId)}
                      </button>
                      {edge.properties.cross_paper === true && edge.properties.citation_text ? (
                        <p>“{String(edge.properties.citation_text).slice(0, 280)}”</p>
                      ) : edge.semantic_unit_ids.length ? (
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
      </section>
      </section>
      {desktopSettingsOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={closeDesktopSettings}>
          <section
            className="settings-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="api-settings-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="settings-modal-header">
              <div>
                <span className="eyebrow">Desktop</span>
                <h2 id="api-settings-title">API Settings</h2>
              </div>
              <button
                type="button"
                className="icon-action"
                aria-label="Close API settings"
                onClick={closeDesktopSettings}
              >
                <X size={16} />
              </button>
            </div>
            <p className="settings-modal-copy">
              These values are stored locally for the desktop app and used for future uploads.
            </p>
            <div className="settings-form">
              <section className="desktop-setup-panel">
                <div className="desktop-setup-panel-header">
                  <div>
                    <span className="eyebrow">Workspace</span>
                    <strong>{desktopSetup.workspaceDir || 'Not initialized yet'}</strong>
                  </div>
                  <span className="type-pill">{desktopBridge?.isPackaged ? 'Packaged App' : 'Dev Mode'}</span>
                </div>
                <div className="desktop-setup-grid">
                  <div>
                    <span>Launcher command</span>
                    <strong>{desktopSetup.launcherCommandPath || 'Not installed'}</strong>
                  </div>
                  <div>
                    <span>Launcher source</span>
                    <strong>{desktopSetup.launcherSourcePath || 'Bundled with the app'}</strong>
                  </div>
                </div>
              </section>
              <label>
                API Key
                <div className="secret-input">
                  <input
                    type={showDesktopApiKey ? 'text' : 'password'}
                    value={desktopSettingsDraft.openaiApiKey}
                    onChange={(event) =>
                      setDesktopSettingsDraft((current) => ({
                        ...current,
                        openaiApiKey: event.target.value,
                      }))
                    }
                    placeholder="sk-..."
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    className="icon-action"
                    aria-label={showDesktopApiKey ? 'Hide API key' : 'Show API key'}
                    onClick={() => setShowDesktopApiKey((current) => !current)}
                  >
                    {showDesktopApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </label>
              <label>
                Base URL
                <input
                  type="url"
                  value={desktopSettingsDraft.openaiBaseUrl}
                  onChange={(event) =>
                    setDesktopSettingsDraft((current) => ({
                      ...current,
                      openaiBaseUrl: event.target.value,
                    }))
                  }
                  placeholder="https://api.openai.com/v1"
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
              <label>
                Model
                <input
                  type="text"
                  value={desktopSettingsDraft.openaiModel}
                  onChange={(event) =>
                    setDesktopSettingsDraft((current) => ({
                      ...current,
                      openaiModel: event.target.value,
                    }))
                  }
                  placeholder="gpt-4o-mini"
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
              <label className="settings-toggle-row">
                <span className="settings-toggle-copy">
                  <strong>Send prompt cache key</strong>
                  <small>
                    Enable for providers such as OpenRouter. Disable it for endpoints that reject
                    the prompt_cache_key parameter.
                  </small>
                </span>
                <input
                  type="checkbox"
                  checked={desktopSettingsDraft.sendPromptCacheKey}
                  onChange={(event) =>
                    setDesktopSettingsDraft((current) => ({
                      ...current,
                      sendPromptCacheKey: event.target.checked,
                    }))
                  }
                />
                <span className="settings-toggle-control" aria-hidden="true">
                  <span />
                </span>
              </label>
            </div>
            <div className="settings-modal-footer">
              <button type="button" className="danger-action subtle-action" onClick={closeDesktopSettings}>
                Cancel
              </button>
              <button
                type="button"
                className="primary-action"
                onClick={saveDesktopSettings}
                disabled={desktopSettingsSaving}
              >
                {desktopSettingsSaving ? <Loader2 className="spin" size={16} /> : <Save size={16} />} Save settings
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
