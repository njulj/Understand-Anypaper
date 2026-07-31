from understand_anypaper.graph.schema import GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper
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
