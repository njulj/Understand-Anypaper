from understand_anypaper.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import PageSourceLocation, ParsedPaper, SemanticUnit
from understand_anypaper.storage.graph_store import SQLiteGraphStore


def test_sqlite_round_trips_node_reference_ids(tmp_path):
    paper_id = "paper-storage-test"
    parsed = ParsedPaper(paper_id=paper_id, title="Storage Test")
    graph = PaperArgumentGraph(
        paper_id=paper_id,
        nodes=[
            GraphNode(
                id="prior-work",
                paper_id=paper_id,
                node_type=NodeType.PRIOR_WORK,
                title="A cited method",
                reference_ids=["ref-storage-12"],
            )
        ],
    )
    store = SQLiteGraphStore(f"sqlite:///{tmp_path / 'graph.db'}")

    store.save_paper(parsed, graph)
    stored = store.get_graph(paper_id)

    assert stored is not None
    assert stored.nodes[0].reference_ids == ["ref-storage-12"]


def _paper_with_reused_graph_ids(paper_id: str) -> tuple[ParsedPaper, PaperArgumentGraph]:
    unit = SemanticUnit(
        semantic_unit_id="c1",
        paper_id=paper_id,
        role="contribution",
        title=f"Contribution in {paper_id}",
        text="Shared local identifier, distinct paper.",
        source_location=PageSourceLocation(
            page=1,
            bbox=[0.1, 0.1, 0.2, 0.2],
            extracted_text="Shared local identifier, distinct paper.",
        ),
    )
    parsed = ParsedPaper(paper_id=paper_id, title=paper_id, semantic_units=[unit])
    graph = PaperArgumentGraph(
        paper_id=paper_id,
        nodes=[
            GraphNode(
                id="c1",
                paper_id=paper_id,
                node_type=NodeType.CONTRIBUTION,
                title=unit.title,
                semantic_unit_ids=[unit.semantic_unit_id],
            )
        ],
        edges=[
            GraphEdge(
                id="e1",
                source_paper_id=paper_id,
                source_node_id="c1",
                target_paper_id=paper_id,
                target_node_id="c1",
                edge_type=EdgeType.SUMMARIZES,
            )
        ],
    )
    return parsed, graph


def test_sqlite_scopes_graph_ids_by_paper(tmp_path):
    store = SQLiteGraphStore(f"sqlite:///{tmp_path / 'graph.db'}")
    first = _paper_with_reused_graph_ids("paper-a")
    second = _paper_with_reused_graph_ids("paper-b")

    store.save_paper(*first)
    store.save_paper(*second)

    for paper_id in ("paper-a", "paper-b"):
        stored = store.get_graph(paper_id)
        assert stored is not None
        assert [node.id for node in stored.nodes] == ["c1"]
        assert [edge.id for edge in stored.edges] == ["e1"]
        assert stored.edges[0].source_paper_id == paper_id
        assert stored.edges[0].target_paper_id == paper_id
        assert [unit.semantic_unit_id for unit in store.get_semantic_units(paper_id)] == ["c1"]

    first_graph = first[1].model_copy(
        update={
            "edges": [
                first[1].edges[0].model_copy(update={"target_paper_id": "paper-b"})
            ]
        }
    )
    store.replace_graph("paper-a", first_graph)

    cross_paper_edge = store.get_graph("paper-a").edges[0]  # type: ignore[union-attr]
    assert (cross_paper_edge.source_paper_id, cross_paper_edge.source_node_id) == (
        "paper-a",
        "c1",
    )
    assert (cross_paper_edge.target_paper_id, cross_paper_edge.target_node_id) == (
        "paper-b",
        "c1",
    )
