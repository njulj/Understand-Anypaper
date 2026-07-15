package render

import (
	"fmt"
	"sort"
	"strings"

	"github.com/njulj/Understand-Anypaper/internal/api"
)

func GraphTree(graph api.PaperArgumentGraph, rootID string, maxDepth int) string {
	if maxDepth < 0 {
		maxDepth = 0
	}
	nodesByID := make(map[string]api.GraphNode, len(graph.Nodes))
	outgoing := make(map[string][]api.GraphEdge, len(graph.Nodes))
	for _, node := range graph.Nodes {
		nodesByID[node.ID] = node
	}
	for _, edge := range graph.Edges {
		outgoing[edge.SourceNodeID] = append(outgoing[edge.SourceNodeID], edge)
	}
	for nodeID := range outgoing {
		sort.Slice(outgoing[nodeID], func(i, j int) bool {
			left := nodesByID[outgoing[nodeID][i].TargetNodeID]
			right := nodesByID[outgoing[nodeID][j].TargetNodeID]
			if outgoing[nodeID][i].EdgeType != outgoing[nodeID][j].EdgeType {
				return outgoing[nodeID][i].EdgeType < outgoing[nodeID][j].EdgeType
			}
			if left.NodeType != right.NodeType {
				return left.NodeType < right.NodeType
			}
			return left.Title < right.Title
		})
	}

	root := pickRootNode(graph, rootID)
	if root.ID == "" {
		return "(graph is empty)"
	}

	lines := make([]string, 0, len(graph.Nodes))
	visited := map[string]bool{}
	var walk func(nodeID string, depth int, incoming *api.GraphEdge)
	walk = func(nodeID string, depth int, incoming *api.GraphEdge) {
		node, ok := nodesByID[nodeID]
		if !ok {
			return
		}
		indent := strings.Repeat("  ", depth)
		prefix := ""
		if incoming != nil {
			prefix = incoming.EdgeType + " -> "
		}
		line := fmt.Sprintf("%s%s%s: %s [%s]", indent, prefix, node.NodeType, node.Title, node.ID)
		if visited[nodeID] {
			lines = append(lines, line+" (already shown)")
			return
		}
		lines = append(lines, line)
		visited[nodeID] = true
		if depth >= maxDepth {
			return
		}
		for _, edge := range outgoing[nodeID] {
			walk(edge.TargetNodeID, depth+1, &edge)
		}
	}

	walk(root.ID, 0, nil)
	return strings.Join(lines, "\n")
}

func pickRootNode(graph api.PaperArgumentGraph, preferredID string) api.GraphNode {
	if preferredID != "" {
		for _, node := range graph.Nodes {
			if node.ID == preferredID {
				return node
			}
		}
	}
	for _, node := range graph.Nodes {
		if node.NodeType == "Paper" {
			return node
		}
	}
	for _, node := range graph.Nodes {
		if node.NodeType == "Contribution" {
			return node
		}
	}
	if len(graph.Nodes) > 0 {
		return graph.Nodes[0]
	}
	return api.GraphNode{}
}
