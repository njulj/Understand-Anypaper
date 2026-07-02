import React, { useEffect, useMemo, useRef, useState } from 'react';
import { GraphEdge, GraphNode, PaperArgumentGraph } from './api';

type Point = { x: number; y: number };
type ViewBox = { x: number; y: number; w: number; h: number };

export const NODE_COLORS: Record<string, string> = {
  Paper: '#4f8cff',
  Contribution: '#58c783',
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

const LAYOUT_SIZE = 900;

function nodeRadius(node: GraphNode): number {
  if (node.node_type === 'Paper') return 26;
  if (node.node_type === 'Contribution') return 19;
  if (node.node_type === 'Reference') return 11;
  return 13;
}

function computeLayout(nodes: GraphNode[], edges: GraphEdge[]): Map<string, Point> {
  const positions = new Map<string, Point>();
  const center = LAYOUT_SIZE / 2;
  const contributions = nodes.filter((n) => n.node_type === 'Contribution');
  const others = nodes.filter((n) => n.node_type !== 'Contribution' && n.node_type !== 'Paper');

  nodes.forEach((node) => {
    if (node.node_type === 'Paper') {
      positions.set(node.id, { x: center, y: center });
    }
  });
  contributions.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(contributions.length, 1);
    positions.set(node.id, { x: center + 170 * Math.cos(angle), y: center + 170 * Math.sin(angle) });
  });
  others.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(others.length, 1) + 0.4;
    positions.set(node.id, { x: center + 330 * Math.cos(angle), y: center + 330 * Math.sin(angle) });
  });

  const ids = nodes.map((n) => n.id);
  const links = edges
    .filter((e) => positions.has(e.source_node_id) && positions.has(e.target_node_id))
    .map((e) => [e.source_node_id, e.target_node_id] as const);

  for (let iter = 0; iter < 260; iter += 1) {
    const forces = new Map<string, Point>(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = positions.get(ids[i])!;
        const b = positions.get(ids[j])!;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const distSq = Math.max(dx * dx + dy * dy, 64);
        const force = 26000 / distSq;
        const dist = Math.sqrt(distSq);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        const fa = forces.get(ids[i])!;
        const fb = forces.get(ids[j])!;
        fa.x += fx; fa.y += fy;
        fb.x -= fx; fb.y -= fy;
      }
    }
    for (const [source, target] of links) {
      const a = positions.get(source)!;
      const b = positions.get(target)!;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const stretch = (dist - 150) * 0.02;
      const fx = (dx / dist) * stretch;
      const fy = (dy / dist) * stretch;
      const fa = forces.get(source)!;
      const fb = forces.get(target)!;
      fa.x += fx; fa.y += fy;
      fb.x -= fx; fb.y -= fy;
    }
    const cooling = 1 - iter / 260;
    for (const id of ids) {
      const pos = positions.get(id)!;
      const force = forces.get(id)!;
      force.x += (center - pos.x) * 0.012;
      force.y += (center - pos.y) * 0.012;
      const magnitude = Math.sqrt(force.x * force.x + force.y * force.y) || 1;
      const step = Math.min(magnitude, 24 * cooling + 2);
      pos.x += (force.x / magnitude) * step;
      pos.y += (force.y / magnitude) * step;
    }
  }
  return positions;
}

type GraphViewProps = {
  graph: PaperArgumentGraph;
  selectedNodeId: string | null;
  query: string;
  onSelectNode: (id: string) => void;
};

export function GraphView({ graph, selectedNodeId, query, onSelectNode }: GraphViewProps) {
  const [positions, setPositions] = useState<Map<string, Point>>(new Map());
  const [viewBox, setViewBox] = useState<ViewBox>({ x: 0, y: 0, w: LAYOUT_SIZE, h: LAYOUT_SIZE });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragState = useRef<
    | { kind: 'node'; id: string }
    | { kind: 'pan'; startX: number; startY: number; box: ViewBox }
    | null
  >(null);

  useEffect(() => {
    setPositions(computeLayout(graph.nodes, graph.edges));
    setViewBox({ x: 0, y: 0, w: LAYOUT_SIZE, h: LAYOUT_SIZE });
  }, [graph]);

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
      const w = Math.min(Math.max(box.w * scale, 160), LAYOUT_SIZE * 4);
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
      setPositions((prev) => {
        const next = new Map(prev);
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
      {graph.edges.map((edge) => {
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
            <title>{`${edge.edge_type} (${Math.round(edge.confidence * 100)}%)${edge.evidence?.text ? `\n${edge.evidence.text.slice(0, 160)}` : ''}`}</title>
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
            <text y={radius + 13}>{node.title.length > 26 ? `${node.title.slice(0, 24)}…` : node.title}</text>
            <title>{`${node.node_type}: ${node.title}\n${node.summary.slice(0, 200)}`}</title>
          </g>
        );
      })}
    </svg>
  );
}
