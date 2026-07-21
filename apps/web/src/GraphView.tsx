import React, { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import jsMind from 'jsmind';
import type { JsMindOptions, Node as JsMindNode, NodeTreeData, NodeTreeFormat } from 'jsmind';
import 'jsmind/style/jsmind.css';
import { GraphEdge, GraphNode, PaperArgumentGraph } from './api';

export const NODE_COLORS: Record<string, string> = {
  Paper: '#4f8cff',
  Contribution: '#58c783',
  Why: '#e5a34f',
  How: '#c9a227',
  Proof: '#ff7ba9',
  Problem: '#e5a34f',
  ResearchGap: '#e5734f',
  Motivation: '#e5a34f',
  PriorWork: '#9aa8b5',
  Definition: '#7fb0ff',
  Observation: '#e5a34f',
  DesignRationale: '#d39a3b',
  Claim: '#ff8a80',
  Method: '#c9a227',
  Module: '#c9a227',
  Equation: '#9d7bff',
  Algorithm: '#9d7bff',
  Implementation: '#b79b38',
  Training: '#b79b38',
  Inference: '#b79b38',
  Dataset: '#57b7a3',
  Metric: '#57b7a3',
  Baseline: '#8fa0b3',
  Experiment: '#ff7ba9',
  Ablation: '#ff7ba9',
  Result: '#ff9db9',
  QualitativeResult: '#ff9db9',
  Efficiency: '#57b7a3',
  Extension: '#c9a227',
  Figure: '#5fc9d8',
  Table: '#5fc9d8',
  Conclusion: '#8fd0ff',
  Reference: '#9aa8b5',
  TextBlock: '#67788a',
};

type NodeTreeDataWithStyle = NodeTreeData & {
  'background-color'?: string;
  'foreground-color'?: string;
  'leading-line-color'?: string;
  nodeType?: string;
  subtitle?: string;
};

type RenderedJsMindNode = JsMindNode & {
  _data: {
    view?: {
      element?: HTMLElement;
    };
  };
};

function isStructuralEdge(edge: GraphEdge): boolean {
  return edge.edge_type !== 'NEXT' && edge.edge_type !== 'PREVIOUS';
}

function outgoing(edges: GraphEdge[], nodeId: string): GraphEdge[] {
  return edges.filter((edge) => edge.source_node_id === nodeId);
}

function displayTitle(node: GraphNode): string {
  return node.title.length > 42 ? `${node.title.slice(0, 40)}...` : node.title;
}

function nodeSubtitle(node: GraphNode, subtitles?: Map<string, string>): string {
  return subtitles?.get(node.id) ?? node.node_type;
}

function uniqueChildNodes(
  parentId: string,
  edges: GraphEdge[],
  nodesById: Map<string, GraphNode>,
  assigned: Set<string>,
): GraphNode[] {
  const seen = new Set<string>();
  return outgoing(edges, parentId).flatMap((edge) => {
    if (seen.has(edge.target_node_id) || assigned.has(edge.target_node_id)) return [];
    const child = nodesById.get(edge.target_node_id);
    if (!child) return [];
    seen.add(child.id);
    assigned.add(child.id);
    return [child];
  });
}

function sortNodes(nodes: GraphNode[]): GraphNode[] {
  const order = [
    'Paper',
    'Contribution',
    'Why',
    'How',
    'Proof',
    'Motivation',
    'Problem',
    'ResearchGap',
    'PriorWork',
    'Definition',
    'Observation',
    'DesignRationale',
    'Claim',
    'Method',
    'Module',
    'Equation',
    'Algorithm',
    'Implementation',
    'Training',
    'Inference',
    'Extension',
    'Dataset',
    'Metric',
    'Baseline',
    'Experiment',
    'Ablation',
    'Result',
    'QualitativeResult',
    'Efficiency',
    'Figure',
    'Table',
    'Conclusion',
    'Reference',
    'TextBlock',
  ];
  return [...nodes].sort((a, b) => {
    const aIndex = order.indexOf(a.node_type);
    const bIndex = order.indexOf(b.node_type);
    return (aIndex === -1 ? order.length : aIndex) - (bIndex === -1 ? order.length : bIndex);
  });
}

function toMindNode(
  node: GraphNode,
  edges: GraphEdge[],
  nodesById: Map<string, GraphNode>,
  assigned: Set<string>,
  subtitles?: Map<string, string>,
  direction?: 'left' | 'right',
): NodeTreeDataWithStyle {
  const children = sortNodes(uniqueChildNodes(node.id, edges, nodesById, assigned)).map((child) =>
    toMindNode(child, edges, nodesById, assigned, subtitles),
  );

  return {
    id: node.id,
    topic: displayTitle(node),
    direction,
    expanded: true,
    nodeType: node.node_type,
    subtitle: nodeSubtitle(node, subtitles),
    'background-color': NODE_COLORS[node.node_type] ?? '#67788a',
    'foreground-color': '#ffffff',
    'leading-line-color': NODE_COLORS[node.node_type] ?? '#8fa0b3',
    children,
  };
}

function chooseRoot(graph: PaperArgumentGraph): GraphNode | null {
  return (
    graph.nodes.find((node) => node.node_type === 'Paper') ??
    graph.nodes.find((node) => node.node_type === 'Contribution') ??
    graph.nodes[0] ??
    null
  );
}

function graphToMind(graph: PaperArgumentGraph, subtitles?: Map<string, string>): NodeTreeFormat {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edges = graph.edges.filter(isStructuralEdge);
  const root = chooseRoot(graph);

  if (!root) {
    return {
      meta: { name: graph.paper_id, author: 'Understand AnyPaper', version: '1.0' },
      format: 'node_tree',
      data: { id: 'empty', topic: 'No graph data' },
    };
  }

  const assigned = new Set<string>([root.id]);
  const rootChildren = sortNodes(uniqueChildNodes(root.id, edges, nodesById, assigned));
  const midpoint = Math.ceil(rootChildren.length / 2);
  const children = rootChildren.map((child, index) =>
    toMindNode(child, edges, nodesById, assigned, subtitles, index < midpoint ? 'left' : 'right'),
  );

  return {
    meta: { name: graph.paper_id, author: 'Understand AnyPaper', version: '1.0' },
    format: 'node_tree',
    data: {
      id: root.id,
      topic: displayTitle(root),
      expanded: true,
      nodeType: root.node_type,
      subtitle: nodeSubtitle(root, subtitles),
      'background-color': NODE_COLORS[root.node_type] ?? '#67788a',
      'foreground-color': '#ffffff',
      children,
    } as NodeTreeDataWithStyle,
  };
}

function applyNodeRender(_jm: jsMind, element: HTMLElement, node: JsMindNode): boolean {
  const nodeData = node.data as NodeTreeDataWithStyle;
  element.textContent = '';
  element.classList.add('pag-mind-node', `pag-mind-node-${String(nodeData.nodeType ?? 'unknown').toLowerCase()}`);
  element.dataset.nodeId = node.id;

  const title = document.createElement('span');
  title.className = 'pag-mind-node-title';
  title.textContent = node.topic;
  element.appendChild(title);

  if (nodeData.subtitle) {
    const subtitle = document.createElement('span');
    subtitle.className = 'pag-mind-node-subtitle';
    subtitle.textContent = nodeData.subtitle;
    element.appendChild(subtitle);
  }

  return true;
}

function setMindNodeClasses(
  jm: jsMind,
  graph: PaperArgumentGraph,
  selectedNodeId: string | null,
  matchedIds: Set<string> | null,
) {
  for (const node of graph.nodes) {
    const mindNode = jm.get_node(node.id) as RenderedJsMindNode | null;
    const element = mindNode?._data.view?.element;
    if (!element) continue;
    element.classList.toggle('selected', node.id === selectedNodeId);
    element.classList.toggle('dimmed', Boolean(matchedIds && !matchedIds.has(node.id)));
  }
}

type GraphViewProps = {
  graph: PaperArgumentGraph;
  selectedNodeId: string | null;
  focusRevision: number;
  query: string;
  onSelectNode: (id: string) => void;
  subtitles?: Map<string, string>;
};

export function GraphView({
  graph,
  selectedNodeId,
  focusRevision,
  query,
  onSelectNode,
  subtitles,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mindRef = useRef<jsMind | null>(null);
  const onSelectNodeRef = useRef(onSelectNode);
  const syncingSelectionRef = useRef(false);
  const mind = useMemo(() => graphToMind(graph, subtitles), [graph, subtitles]);

  const matchedIds = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return null;
    return new Set(
      graph.nodes
        .filter((node) => `${node.title} ${node.summary} ${node.node_type}`.toLowerCase().includes(normalized))
        .map((node) => node.id),
    );
  }, [graph, query]);

  useEffect(() => {
    onSelectNodeRef.current = onSelectNode;
  }, [onSelectNode]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    container.innerHTML = '';
    const options: JsMindOptions = {
      container,
      editable: false,
      theme: 'pag',
      mode: 'full',
      support_html: false,
      log_level: 'error',
      view: {
        engine: 'svg',
        draggable: true,
        hide_scrollbars_when_draggable: true,
        hmargin: 70,
        vmargin: 42,
        line_width: 2,
        line_color: '#8fa0b3',
        line_style: 'curved',
        node_overflow: 'wrap',
        custom_node_render: applyNodeRender,
        expander_style: 'number',
        zoom: { min: 0.55, max: 2.2, step: 0.12 },
      },
      layout: {
        hspace: 86,
        vspace: 18,
        pspace: 18,
        cousin_space: 16,
      },
      shortcut: { enable: false },
    };

    const jm = new jsMind(options);
    mindRef.current = jm;
    jm.show(mind);
    jm.expand_all();
    jm.add_event_listener((type, data) => {
      if (type === jsMind.event_type.select && data.node && !syncingSelectionRef.current) {
        onSelectNodeRef.current(data.node);
      }
    });

    return () => {
      jm.clear_event_listener();
      container.innerHTML = '';
      if (mindRef.current === jm) mindRef.current = null;
    };
  }, [mind]);

  useLayoutEffect(() => {
    const jm = mindRef.current;
    if (!jm) return;
    syncingSelectionRef.current = true;
    try {
      if (selectedNodeId && jm.get_node(selectedNodeId)) {
        jm.select_node(selectedNodeId);
        jm.scroll_node_to_center(selectedNodeId);
      } else {
        jm.select_clear();
      }
    } finally {
      window.setTimeout(() => {
        syncingSelectionRef.current = false;
      }, 0);
    }
    setMindNodeClasses(jm, graph, selectedNodeId, matchedIds);

    if (!selectedNodeId || !jm.get_node(selectedNodeId)) return;
    const frame = window.requestAnimationFrame(() => {
      if (mindRef.current === jm && jm.get_node(selectedNodeId)) {
        jm.scroll_node_to_center(selectedNodeId);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusRevision, graph, matchedIds, selectedNodeId]);

  return <div ref={containerRef} className="graph-canvas graph-mindmap" />;
}
