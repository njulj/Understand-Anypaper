from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.schema import EdgeType, NodeType
from understand_anypaper.parser.models import ContentBlock, ParsedPaper


def test_builder_creates_traceable_contribution_graph():
    parsed = ParsedPaper(
        paper_id="paper-1",
        title="ExampleNet",
        blocks=[
            ContentBlock(content_id="p1", order=1, page=1, text="Existing methods have a gap.", semantic_role="gap"),
            ContentBlock(content_id="p2", order=2, page=1, text="Our contribution is a cross-layer fusion module.", semantic_role="contribution"),
            ContentBlock(content_id="p3", order=3, page=2, text="The method uses a fusion module.", semantic_role="method"),
        ],
    )

    graph = PaperArgumentGraphBuilder().build(parsed)

    assert any(node.node_type == NodeType.CONTRIBUTION for node in graph.nodes)
    assert all(node.evidence_ids for node in graph.nodes)
    assert all(edge.evidence is not None for edge in graph.edges)


def test_builder_links_distant_why_how_proof_evidence():
    parsed = ParsedPaper(
        paper_id="paper-2",
        title="FusionNet",
        blocks=[
            ContentBlock(content_id="p1", order=1, page=1, text="Long context has a major gap.", semantic_role="gap"),
            ContentBlock(content_id="p2", order=2, page=1, text="Our contribution is a fusion module for long context.", semantic_role="contribution"),
            ContentBlock(content_id="p3", order=8, page=3, text="The fusion module method combines local and global states.", semantic_role="method"),
            ContentBlock(content_id="p4", order=18, page=7, text="Experiments show the fusion module improves accuracy.", semantic_role="result"),
        ],
    )
    for index, block in enumerate(parsed.blocks):
        if index > 0:
            block.neighbor_ids.append(parsed.blocks[index - 1].content_id)
        if index + 1 < len(parsed.blocks):
            block.neighbor_ids.append(parsed.blocks[index + 1].content_id)

    graph = PaperArgumentGraphBuilder().build(parsed)
    contribution = next(node for node in graph.nodes if node.node_type == NodeType.CONTRIBUTION)
    linked_sources = {
        edge.source_node_id
        for edge in graph.edges
        if edge.target_node_id == contribution.id and edge.edge_type != EdgeType.HAS_CONTRIBUTION
    }

    assert {"p1", "p3", "p4"}.issubset(linked_sources)
    assert any(edge.properties.get("argument_facet") == "how" for edge in graph.edges)
    assert any(edge.properties.get("argument_facet") == "proof" for edge in graph.edges)
