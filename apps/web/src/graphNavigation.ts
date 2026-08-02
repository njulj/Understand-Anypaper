import { GraphEdge, PaperArgumentGraph } from './api';

export function isNavigationEdge(edge: GraphEdge): boolean {
  return edge.edge_type !== 'NEXT' && edge.edge_type !== 'PREVIOUS';
}

function subgraphOf(graph: PaperArgumentGraph, keep: Set<string>): PaperArgumentGraph {
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    edges: graph.edges.filter((edge) => keep.has(edge.source_node_id) && keep.has(edge.target_node_id)),
  };
}

export function overviewGraph(graph: PaperArgumentGraph): PaperArgumentGraph {
  const keep = new Set(
    graph.nodes
      .filter(
        (node) =>
          node.paper_id === graph.paper_id &&
          (node.node_type === 'Paper' || node.node_type === 'Contribution'),
      )
      .map((node) => node.id),
  );
  return subgraphOf(graph, keep);
}

export function contributionGraph(
  graph: PaperArgumentGraph,
  contributionId: string,
): PaperArgumentGraph | null {
  if (!graph.nodes.some((node) => node.id === contributionId)) return null;
  const keep = new Set([contributionId]);
  let frontier = new Set([contributionId]);
  // Contribution → facet (Why/How/Proof) → evidence: two hops of outgoing edges.
  for (let hop = 0; hop < 2; hop += 1) {
    const next = new Set<string>();
    for (const edge of graph.edges) {
      if (!isNavigationEdge(edge)) continue;
      if (frontier.has(edge.source_node_id) && !keep.has(edge.target_node_id)) {
        keep.add(edge.target_node_id);
        next.add(edge.target_node_id);
      }
    }
    frontier = next;
  }
  // Resolved references remain one more hop away.
  for (const edge of graph.edges) {
    if (edge.properties.cross_paper === true && keep.has(edge.source_node_id)) {
      keep.add(edge.target_node_id);
    }
  }
  return subgraphOf(graph, keep);
}

export function owningContributionId(
  graph: PaperArgumentGraph,
  nodeId: string,
): string | null {
  const typeById = new Map(graph.nodes.map((node) => [node.id, node.node_type]));
  if (typeById.get(nodeId) === 'Contribution') return nodeId;
  const visited = new Set([nodeId]);
  let frontier = new Set([nodeId]);
  for (let hop = 0; hop < 3; hop += 1) {
    const next = new Set<string>();
    for (const edge of graph.edges) {
      if (!isNavigationEdge(edge)) continue;
      if (!frontier.has(edge.target_node_id) || visited.has(edge.source_node_id)) continue;
      if (typeById.get(edge.source_node_id) === 'Contribution') return edge.source_node_id;
      visited.add(edge.source_node_id);
      next.add(edge.source_node_id);
    }
    frontier = next;
  }
  return null;
}

export function contributionEvidenceSubtitles(
  graph: PaperArgumentGraph,
): Map<string, string> {
  const subtitles = new Map<string, string>();
  for (const contribution of graph.nodes) {
    if (contribution.node_type !== 'Contribution') continue;
    const facetIds = new Set(
      graph.edges
        .filter((edge) => edge.source_node_id === contribution.id && edge.edge_type === 'CONTAINS')
        .map((edge) => edge.target_node_id),
    );
    const evidenceNodeIds = new Set(
      graph.edges
        .filter((edge) => isNavigationEdge(edge) && facetIds.has(edge.source_node_id))
        .map((edge) => edge.target_node_id),
    );
    subtitles.set(contribution.id, `${evidenceNodeIds.size} evidence`);
  }
  return subtitles;
}
