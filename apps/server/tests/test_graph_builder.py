from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.schema import EdgeType, NodeType
from understand_anypaper.parser.models import (
    PageSourceLocation,
    PaperReference,
    ParsedPaper,
    SemanticUnit,
)


def _unit(
    unit_id: str,
    role: str,
    title: str,
    page: int,
    contribution_unit_ids: list[str] | None = None,
) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id="paper-12345678",
        role=role,
        title=title,
        text=title,
        source_locations=[PageSourceLocation(page=page, bbox=[0.1, 0.1, 0.2, 0.8], extracted_text=title)],
        confidence=0.9,
        properties={}
        if contribution_unit_ids is None
        else {"contribution_unit_ids": contribution_unit_ids},
    )


def test_builds_prd_tree_with_facets_and_evidence():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
        semantic_units=[
            _unit("unit-contribution", "contribution", "TinyLUT contribution", 1),
            _unit("unit-motivation", "motivation", "Mobile demand", 1, ["unit-contribution"]),
            _unit("unit-method", "method", "Separable mapping", 2, ["unit-contribution"]),
            _unit("unit-result", "result", "Storage improvement", 3, ["unit-contribution"]),
            _unit("unit-reference", "reference", "Prior LUT work", 1, ["unit-contribution"]),
        ],
        references=[
            PaperReference(reference_id="ref-1", marker="[1]", raw_text="[1] Prior LUT work."),
        ],
    )

    graph = PaperArgumentGraphBuilder().build(parsed)
    contribution = next(node for node in graph.nodes if node.node_type == NodeType.CONTRIBUTION)
    facet_edges = [edge for edge in graph.edges if edge.source_node_id == contribution.id]
    facet_nodes = {
        edge.target_node_id: next(node for node in graph.nodes if node.id == edge.target_node_id)
        for edge in facet_edges
    }

    assert {node.node_type for node in facet_nodes.values()} == {NodeType.WHY, NodeType.HOW, NodeType.PROOF}
    assert not any(edge.edge_type == EdgeType.NEXT for edge in graph.edges)

    why_id = next(node.id for node in facet_nodes.values() if node.node_type == NodeType.WHY)
    how_id = next(node.id for node in facet_nodes.values() if node.node_type == NodeType.HOW)
    proof_id = next(node.id for node in facet_nodes.values() if node.node_type == NodeType.PROOF)

    assert any(edge.source_node_id == why_id and edge.target_node_id == "unit-motivation" for edge in graph.edges)
    assert any(edge.source_node_id == how_id and edge.target_node_id == "unit-method" for edge in graph.edges)
    assert any(edge.source_node_id == proof_id and edge.target_node_id == "unit-result" for edge in graph.edges)
    assert any(edge.source_node_id == why_id and edge.target_node_id == "unit-reference" for edge in graph.edges)


def test_evidence_assignment_is_required():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        semantic_units=[
            _unit("unit-contribution", "contribution", "TinyLUT contribution", 1),
            _unit("unit-method", "method", "Separable mapping", 2),
        ],
    )

    try:
        PaperArgumentGraphBuilder().build(parsed)
    except Exception as exc:  # noqa: BLE001
        assert "missing LLM contribution assignment" in str(exc)
    else:
        raise AssertionError("Graph builder should reject unassigned evidence")
