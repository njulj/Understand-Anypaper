package render

import (
	"strings"
	"testing"

	"github.com/njulj/Understand-Anypaper/internal/api"
)

func TestGraphTreeUsesPaperRootAndMarksRevisit(t *testing.T) {
	graph := api.PaperArgumentGraph{
		PaperID: "paper-1",
		Nodes: []api.GraphNode{
			{ID: "paper-1", NodeType: "Paper", Title: "Paper"},
			{ID: "contribution-1", NodeType: "Contribution", Title: "Contribution A"},
			{ID: "why-1", NodeType: "Why", Title: "Why"},
		},
		Edges: []api.GraphEdge{
			{SourceNodeID: "paper-1", TargetNodeID: "contribution-1", EdgeType: "HAS_CONTRIBUTION"},
			{SourceNodeID: "contribution-1", TargetNodeID: "why-1", EdgeType: "MOTIVATES"},
			{SourceNodeID: "paper-1", TargetNodeID: "why-1", EdgeType: "SUPPORTED_BY"},
		},
	}

	got := GraphTree(graph, "", 3)

	if !strings.Contains(got, "Paper: Paper [paper-1]") {
		t.Fatalf("expected paper root in output, got:\n%s", got)
	}
	if !strings.Contains(got, "(already shown)") {
		t.Fatalf("expected revisited node marker, got:\n%s", got)
	}
}
