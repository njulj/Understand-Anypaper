import React, { useEffect, useMemo, useRef, useState } from 'react';
import { GraphEdge, GraphNode, PaperArgumentGraph } from './api';

type Point = { x: number; y: number };
type ViewBox = { x: number; y: number; w: number; h: number };

export const NODE_COLORS: Record<string, string> = {
  Paper: '#4f8cff',
  Contribution: '#58c783',
  Why: '#e5a34f',
  How: '#c9a227',
  Proof: '#ff7ba9',
  ResearchGap: '#e5734f',
  Motivation: '#e5a34f',
  Method: '#c9a227',
  Module: '#c9a227',
  Equation: '#9d7bff',
  Algorithm: '#9d7bff',
  Experiment: '#ff7ba9',
  Result: '#ff9db9',
  Figure: '#5fc9d8',
  Table: '#5fc9d8',
  Conclusion: '#8fd0ff',
  Reference: '#9aa8b5',
  TextBlock: '#67788a',
};

const MIN_WIDTH = 900;
const TOP_PADDING = 80;
const CONTRIBUTION_Y = 230;
const FACET_Y = 390;
const EVIDENCE_Y = 545;
const EVIDENCE_ROW_GAP = 96;
const COLUMN_WIDTH = 430;

function nodeRadius(node: Pick<GraphNode, 'node_type'>): number {
  if (node.node_type === 'Paper') return 26;
  if (node.node_type === 'Contribution') return 19;
  if (node.node_type === 'Why' || node.node_type === 'How' || node.node_type === 'Proof') return 15;
  if (node.node_type === 'Reference') return 11;
  return 13;
}

function isStructuralEdge(edge: GraphEdge): boolean {
  return edge.edge_type !== 'NEXT' && edge.edge_type !== 'PREVIOUS';
}

function outgoing(edges: GraphEdge[], nodeId: string): GraphEdge[] {
  return edges.filter((edge) => edge.source_node_id === nodeId);
}

function uniqueNodes(ids: string[], nodesById: Map<string, GraphNode>): GraphNode[] {
  const seen = new Set<string>();
  return ids.flatMap((id) => {
    if (seen.has(id)) return [];
    const node = nodesById.get(id);
    if (!node) return [];
    seen.add(id);
    return [node];
  });
}

function layoutGraph(graph: PaperArgumentGraph): { positions: Map<string, Point>; viewBox: ViewBox } {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edges = graph.edges.filter(isStructuralEdge);
  const paper = graph.nodes.find((node) => node.node_type === 'Paper') ?? graph.nodes[0];
  const positions = new Map<string, Point>();
  if (!paper) return { positions, viewBox: { x: 0, y: 0, w: MIN_WIDTH, h: MIN_WIDTH } };

  const contributionIds = outgoing(edges, paper.id)
    .filter((edge) => edge.edge_type === 'HAS_CONTRIBUTION')
    .map((edge) => edge.target_node_id);
  const contributions = uniqueNodes(contributionIds, nodesById);
  const orderedContributions =
    contributions.length > 0
      ? contributions
      : graph.nodes.filter((node) => node.node_type === 'Contribution');

  const columnCount = Math.max(orderedContributions.length, 1);
  const width = Math.max(MIN_WIDTH, columnCount * COLUMN_WIDTH);
  const centerX = width / 2;
  positions.set(paper.id, { x: centerX, y: TOP_PADDING });

  let maxEvidenceRows = 1;
  orderedContributions.forEach((contribution, index) => {
    const columnCenter = ((index + 0.5) * width) / columnCount;
    positions.set(contribution.id, { x: columnCenter, y: CONTRIBUTION_Y });

    const facetIds = outgoing(edges, contribution.id)
      .filter((edge) => edge.edge_type === 'CONTAINS')
      .map((edge) => edge.target_node_id);
    const facets = uniqueNodes(facetIds, nodesById).sort((a, b) => {
      const order = ['Why', 'How', 'Proof'];
      return order.indexOf(a.node_type) - order.indexOf(b.node_type);
    });
    const facetSlots = facets.length || 1;

    facets.forEach((facet, facetIndex) => {
      const spread = columnCount === 1 ? Math.min(width * 0.72, 640) : Math.min(COLUMN_WIDTH * 0.68, 300);
      const offset = facetSlots === 1 ? 0 : -spread / 2 + (spread * facetIndex) / (facetSlots - 1);
      const facetX = columnCenter + offset;
      positions.set(facet.id, { x: facetX, y: FACET_Y });

      const childIds = outgoing(edges, facet.id).map((edge) => edge.target_node_id);
      const children = uniqueNodes(childIds, nodesById);
      maxEvidenceRows = Math.max(maxEvidenceRows, children.length);
      children.forEach((child, childIndex) => {
        positions.set(child.id, {
          x: facetX,
          y: EVIDENCE_Y + childIndex * EVIDENCE_ROW_GAP,
        });
      });
    });
  });

  const positioned = new Set(positions.keys());
  const leftovers = graph.nodes.filter((node) => !positioned.has(node.id));
  leftovers.forEach((node, index) => {
    const columns = Math.max(Math.floor(width / 180), 1);
    positions.set(node.id, {
      x: 90 + (index % columns) * 180,
      y: EVIDENCE_Y + (maxEvidenceRows + 1 + Math.floor(index / columns)) * EVIDENCE_ROW_GAP,
    });
  });

  const xs = [...positions.values()].map((point) => point.x);
  const ys = [...positions.values()].map((point) => point.y);
  const pad = 90;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  return {
    positions,
    viewBox: { x: minX, y: minY, w: Math.max(maxX - minX, MIN_WIDTH), h: Math.max(maxY - minY, 520) },
  };
}

type GraphViewProps = {
  graph: PaperArgumentGraph;
  selectedNodeId: string | null;
  query: string;
  onSelectNode: (id: string) => void;
  subtitles?: Map<string, string>;
};

export function GraphView({ graph, selectedNodeId, query, onSelectNode, subtitles }: GraphViewProps) {
  const initialLayout = useMemo(() => layoutGraph(graph), [graph]);
  const [positions, setPositions] = useState<Map<string, Point>>(initialLayout.positions);
  const [viewBox, setViewBox] = useState<ViewBox>(initialLayout.viewBox);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragState = useRef<
    | { kind: 'node'; id: string }
    | { kind: 'pan'; startX: number; startY: number; box: ViewBox }
    | null
  >(null);

  useEffect(() => {
    const nextLayout = layoutGraph(graph);
    setPositions(nextLayout.positions);
    setViewBox(nextLayout.viewBox);
  }, [graph]);

  const visibleEdges = useMemo(() => graph.edges.filter(isStructuralEdge), [graph]);
  const matchedIds = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return null;
    return new Set(
      graph.nodes
        .filter((node) => `${node.title} ${node.summary} ${node.node_type}`.toLowerCase().includes(normalized))
        .map((node) => node.id),
    );
  }, [graph, query]);

  const nodesById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph]);

  function toGraphCoords(event: React.PointerEvent | React.WheelEvent): Point {
    const svg = svgRef.current!;
    const rect = svg.getBoundingClientRect();
    return {
      x: viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.w,
      y: viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.h,
    };
  }

  function handleWheel(event: React.WheelEvent<SVGSVGElement>) {
    const scale = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    const focus = toGraphCoords(event);
    setViewBox((box) => {
      const w = Math.min(Math.max(box.w * scale, 180), MIN_WIDTH * 4);
      const h = (w / box.w) * box.h;
      return {
        x: focus.x - ((focus.x - box.x) / box.w) * w,
        y: focus.y - ((focus.y - box.y) / box.h) * h,
        w,
        h,
      };
    });
  }

  function handlePointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (dragState.current) return;
    dragState.current = { kind: 'pan', startX: event.clientX, startY: event.clientY, box: viewBox };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<SVGSVGElement>) {
    const state = dragState.current;
    if (!state) return;
    if (state.kind === 'pan') {
      const rect = svgRef.current!.getBoundingClientRect();
      const dx = ((event.clientX - state.startX) / rect.width) * state.box.w;
      const dy = ((event.clientY - state.startY) / rect.height) * state.box.h;
      setViewBox({ ...state.box, x: state.box.x - dx, y: state.box.y - dy });
    } else {
      const point = toGraphCoords(event);
      setPositions((current) => {
        const next = new Map(current);
        next.set(state.id, point);
        return next;
      });
    }
  }

  function handlePointerUp() {
    dragState.current = null;
  }

  function startNodeDrag(event: React.PointerEvent, nodeId: string) {
    event.stopPropagation();
    dragState.current = { kind: 'node', id: nodeId };
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
  }

  return (
    <svg
      ref={svgRef}
      className="graph-canvas"
      viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerUp}
    >
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#5b6b7d" />
        </marker>
      </defs>
      {visibleEdges.map((edge) => {
        const source = positions.get(edge.source_node_id);
        const target = positions.get(edge.target_node_id);
        const targetNode = nodesById.get(edge.target_node_id);
        if (!source || !target || !targetNode) return null;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const trim = nodeRadius(targetNode) + 4;
        const endX = target.x - (dx / dist) * trim;
        const endY = target.y - (dy / dist) * trim;
        const dimmed = matchedIds && !(matchedIds.has(edge.source_node_id) && matchedIds.has(edge.target_node_id));
        return (
          <g key={edge.id} className={`graph-edge ${dimmed ? 'dimmed' : ''}`}>
            <line x1={source.x} y1={source.y} x2={endX} y2={endY} markerEnd="url(#arrow)" />
            <title>{`${edge.edge_type} (${Math.round(edge.confidence * 100)}%)`}</title>
          </g>
        );
      })}
      {graph.nodes.map((node) => {
        const position = positions.get(node.id);
        if (!position) return null;
        const radius = nodeRadius(node);
        const dimmed = matchedIds && !matchedIds.has(node.id);
        const selected = node.id === selectedNodeId;
        return (
          <g
            key={node.id}
            className={`graph-node ${dimmed ? 'dimmed' : ''} ${selected ? 'selected' : ''}`}
            transform={`translate(${position.x}, ${position.y})`}
            onPointerDown={(event) => startNodeDrag(event, node.id)}
            onClick={(event) => {
              event.stopPropagation();
              onSelectNode(node.id);
            }}
          >
            <circle r={radius} fill={NODE_COLORS[node.node_type] ?? '#67788a'} />
            {node.verified ? <circle r={radius + 3.5} className="verified-ring" /> : null}
            <text y={radius + 13}>{node.title.length > 26 ? `${node.title.slice(0, 24)}...` : node.title}</text>
            {subtitles?.has(node.id) ? (
              <text y={radius + 27} className="node-subtitle">{subtitles.get(node.id)}</text>
            ) : null}
            <title>{`${node.node_type}: ${node.title}\n${node.summary.slice(0, 200)}`}</title>
          </g>
        );
      })}
    </svg>
  );
}
