from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.schema import NodeType
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
