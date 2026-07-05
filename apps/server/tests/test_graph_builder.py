from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.schema import EdgeType, NodeType
from understand_anypaper.parser.models import (
    CitationMention,
    PaperReference,
    ParsedPaper,
    SemanticUnit,
    SourceBlock,
    SourceRange,
)


def _unit(unit_id: str, role: str, title: str, block_id: str) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id="paper-12345678",
        role=role,
        title=title,
        text=title,
        source_ranges=[SourceRange(source_block_id=block_id)],
        confidence=0.9,
    )


def test_builds_prd_tree_with_facets_and_evidence():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
        source_blocks=[
            SourceBlock(source_block_id="paper-block1", order=1, page=1, text="We contribute TinyLUT."),
            SourceBlock(source_block_id="paper-block2", order=2, page=1, text="Mobile deployment motivates it [1]."),
            SourceBlock(source_block_id="paper-block3", order=3, page=2, text="The method uses separable mapping."),
            SourceBlock(source_block_id="paper-block4", order=4, page=3, text="Experiments improve storage."),
        ],
        semantic_units=[
            _unit("unit-contribution", "contribution", "TinyLUT contribution", "paper-block1"),
            _unit("unit-motivation", "motivation", "Mobile demand", "paper-block2"),
            _unit("unit-method", "method", "Separable mapping", "paper-block3"),
            _unit("unit-result", "result", "Storage improvement", "paper-block4"),
        ],
        references=[
            PaperReference(reference_id="ref-1", marker="[1]", raw_text="[1] Prior LUT work."),
        ],
        mentions=[
            CitationMention(
                mention_id="mention-1",
                reference_id="ref-1",
                source_block_id="paper-block2",
                sentence="Mobile deployment motivates it [1].",
                intent="BACKGROUND",
            )
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
    assert any(edge.source_node_id == why_id and edge.target_node_id == "ref-1" for edge in graph.edges)
