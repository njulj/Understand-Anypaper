import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Simulation,
  SimulationNodeDatum,
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  forceX,
  forceY,
} from 'd3-force';
import { GraphEdge, GraphNode, PaperArgumentGraph } from './api';

type Point = { x: number; y: number };
type ViewBox = { x: number; y: number; w: number; h: number };

type SimNode = SimulationNodeDatum & { id: string; nodeType: string };

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
const CENTER = LAYOUT_SIZE / 2;

function nodeRadius(node: Pick<GraphNode, 'node_type'>): number {
  if (node.node_type === 'Paper') return 26;
  if (node.node_type === 'Contribution') return 19;
  if (node.node_type === 'Reference') return 11;
  return 13;
}

function linkDistance(edge: GraphEdge, typeById: Map<string, string>): number {
  const source = typeById.get(edge.source_node_id);
  const target = typeById.get(edge.target_node_id);
  if (source === 'Paper' || target === 'Paper') return 230;
  if (source === 'Reference' || target === 'Reference') return 120;
  return 150;
}

function seedPosition(node: GraphNode, index: number, total: number): Point {
  if (node.node_type === 'Paper') return { x: CENTER, y: CENTER };
  const ring = node.node_type === 'Contribution' ? 170 : 320;
  const angle = (2 * Math.PI * index) / Math.max(total, 1) + (node.node_type === 'Contribution' ? 0 : 0.4);
  return { x: CENTER + ring * Math.cos(angle), y: CENTER + ring * Math.sin(angle) };
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
  const simulationRef = useRef<Simulation<SimNode, undefined> | null>(null);
  const simNodesRef = useRef<Map<string, SimNode>>(new Map());
  const dragState = useRef<
    | { kind: 'node'; id: string }
    | { kind: 'pan'; startX: number; startY: number; box: ViewBox }
    | null
  >(null);
  const userAdjustedView = useRef(false);

  useEffect(() => {
    const typeById = new Map(graph.nodes.map((node) => [node.id, node.node_type]));
    const grouped = new Map<string, number>();
    const simNodes: SimNode[] = graph.nodes.map((node) => {
      const siblings = graph.nodes.filter((n) => n.node_type === node.node_type).length;
      const index = grouped.get(node.node_type) ?? 0;
      grouped.set(node.node_type, index + 1);
      const seed = seedPosition(node, index, siblings);
      return { id: node.id, nodeType: node.node_type, x: seed.x, y: seed.y };
    });
    simNodesRef.current = new Map(simNodes.map((n) => [n.id, n]));

    const links = graph.edges
      .filter((e) => typeById.has(e.source_node_id) && typeById.has(e.target_node_id))
      .map((e) => ({ source: e.source_node_id, target: e.target_node_id, distance: linkDistance(e, typeById) }));

    const simulation = forceSimulation<SimNode>(simNodes)
      .force(
        'link',
        forceLink<SimNode, { source: string; target: string; distance: number }>(links)
          .id((n) => n.id)
          .distance((l) => l.distance)
          .strength(0.6),
      )
      .force('charge', forceManyBody<SimNode>().strength(-560).distanceMax(560))
      .force(
        'collide',
        forceCollide<SimNode>()
          .radius((n) => nodeRadius({ node_type: n.nodeType }) + 28)
          .strength(0.95),
      )
      .force('x', forceX<SimNode>(CENTER).strength(0.03))
      .force('y', forceY<SimNode>(CENTER).strength(0.03))
      .force(
        'radial',
        forceRadial<SimNode>(380, CENTER, CENTER).strength((n) => (n.nodeType === 'Reference' ? 0.12 : 0)),
      )
      .alpha(1)
      .alphaDecay(0.028)
      .on('tick', () => {
        setPositions(new Map(simNodes.map((n) => [n.id, { x: n.x ?? CENTER, y: n.y ?? CENTER }])));
      })
      .on('end', () => {
        if (!simNodes.length || userAdjustedView.current) return;
        const xs = simNodes.map((n) => n.x ?? CENTER);
        const ys = simNodes.map((n) => n.y ?? CENTER);
        const pad = 90;
        const minX = Math.min(...xs) - pad;
        const maxX = Math.max(...xs) + pad;
        const minY = Math.min(...ys) - pad;
        const maxY = Math.max(...ys) + pad;
        const size = Math.max(maxX - minX, maxY - minY, 320);
        setViewBox({
          x: (minX + maxX) / 2 - size / 2,
          y: (minY + maxY) / 2 - size / 2,
          w: size,
          h: size,
        });
      });

    simulationRef.current = simulation;
    userAdjustedView.current = false;
    setPositions(new Map(simNodes.map((n) => [n.id, { x: n.x ?? CENTER, y: n.y ?? CENTER }])));
    setViewBox({ x: 0, y: 0, w: LAYOUT_SIZE, h: LAYOUT_SIZE });
    return () => {
      simulation.stop();
      simulationRef.current = null;
    };
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
    userAdjustedView.current = true;
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
      userAdjustedView.current = true;
      const rect = svgRef.current!.getBoundingClientRect();
      const dx = ((event.clientX - state.startX) / rect.width) * state.box.w;
      const dy = ((event.clientY - state.startY) / rect.height) * state.box.h;
      setViewBox({ ...state.box, x: state.box.x - dx, y: state.box.y - dy });
    } else {
      const point = toGraphCoords(event);
      const simNode = simNodesRef.current.get(state.id);
      if (simNode) {
        simNode.fx = point.x;
        simNode.fy = point.y;
      }
    }
  }

  function handlePointerUp() {
    const state = dragState.current;
    if (state?.kind === 'node') {
      const simNode = simNodesRef.current.get(state.id);
      if (simNode) {
        simNode.fx = null;
        simNode.fy = null;
      }
      simulationRef.current?.alphaTarget(0);
    }
    dragState.current = null;
  }

  function startNodeDrag(event: React.PointerEvent, nodeId: string) {
    event.stopPropagation();
    dragState.current = { kind: 'node', id: nodeId };
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    const simNode = simNodesRef.current.get(nodeId);
    if (simNode) {
      simNode.fx = simNode.x;
      simNode.fy = simNode.y;
    }
    simulationRef.current?.alphaTarget(0.3).restart();
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
            <text y={radius + 13}>{node.title.length > 26 ? `${node.title.slice(0, 24)}…` : node.title}</text>
            <title>{`${node.node_type}: ${node.title}\n${node.summary.slice(0, 200)}`}</title>
          </g>
        );
      })}
    </svg>
  );
}
