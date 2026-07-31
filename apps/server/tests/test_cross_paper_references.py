from fastapi.testclient import TestClient

from understand_anypaper.analyzers.citation_contribution_matcher import (
    CitationContributionMatchOutput,
)
from understand_anypaper.api import routes
from understand_anypaper.graph.schema import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
    PaperArgumentGraph,
)
from understand_anypaper.main import app
from understand_anypaper.parser.models import (
    PageSourceLocation,
    PaperReference,
    ParsedPaper,
    SemanticUnit,
)
from understand_anypaper.storage import InMemoryGraphStore


def _unit(
    paper_id: str,
    unit_id: str,
    role: str,
    title: str,
    *,
    contribution_id: str | None = None,
    properties: dict | None = None,
) -> SemanticUnit:
    merged_properties = dict(properties or {})
    if contribution_id is not None:
        merged_properties["contribution_unit_ids"] = [contribution_id]
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id=paper_id,
        role=role,
        title=title,
        text=title,
        source_location=PageSourceLocation(
            page=1,
            bbox=[0.1, 0.1, 0.2, 0.8],
            extracted_text=merged_properties.get("citation_text", title),
        ),
        confidence=0.9,
        properties=merged_properties,
    )


def _paper_pair() -> tuple[ParsedPaper, ParsedPaper]:
    target_id = "22222222-target-paper"
    target_contribution_id = "target-contribution-unit"
    target = ParsedPaper(
        paper_id=target_id,
        title="Foundational Gating",
        abstract="A paper about a gated attention mechanism.",
        semantic_units=[
            _unit(
                target_id,
                target_contribution_id,
                "contribution",
                "Learned gates control attention routing",
            ),
            _unit(
                target_id,
                "target-method-unit",
                "method_component",
                "A learned sigmoid gate routes each token",
                contribution_id=target_contribution_id,
            ),
        ],
    )

    current_id = "11111111-current-paper"
    current_contribution_id = "current-contribution-unit"
    citation_text = "Our residual router extends the learned gating mechanism of [2]."
    current = ParsedPaper(
        paper_id=current_id,
        title="Residual Routing",
        abstract="A paper that extends gated attention.",
        semantic_units=[
            _unit(
                current_id,
                current_contribution_id,
                "contribution",
                "Residual routing stabilizes gated attention",
            ),
            _unit(
                current_id,
                "current-method-unit",
                "method_component",
                "Residual routing extends learned gating",
                contribution_id=current_contribution_id,
                properties={
                    "citation_markers": ["[2]"],
                    "citation_text": citation_text,
                },
            ),
        ],
        references=[
            PaperReference(
                reference_id="ref-current-2",
                marker="[2]",
                raw_text="A. Author. Foundational Gating. 2022.",
                title="Foundational Gating",
                year=2022,
            )
        ],
    )
    return current, target


def _graph_for(parsed: ParsedPaper) -> PaperArgumentGraph:
    paper_node_id = f"paper-{parsed.paper_id}"
    contribution = next(unit for unit in parsed.semantic_units if unit.role == "contribution")
    facet_ids = {
        NodeType.WHY: f"{contribution.semantic_unit_id}-why",
        NodeType.HOW: f"{contribution.semantic_unit_id}-how",
        NodeType.PROOF: f"{contribution.semantic_unit_id}-proof",
    }
    nodes = [
        GraphNode(
            id=paper_node_id,
            paper_id=parsed.paper_id,
            node_type=NodeType.PAPER,
            title=parsed.title,
        ),
        GraphNode(
            id=contribution.semantic_unit_id,
            paper_id=parsed.paper_id,
            node_type=NodeType.CONTRIBUTION,
            title=contribution.title,
            semantic_unit_ids=[contribution.semantic_unit_id],
        ),
        *[
            GraphNode(
                id=facet_id,
                paper_id=parsed.paper_id,
                node_type=facet_type,
                title=facet_type.value,
            )
            for facet_type, facet_id in facet_ids.items()
        ],
    ]
    edges = [
        GraphEdge(
            id=f"{paper_node_id}-contribution",
            paper_id=parsed.paper_id,
            source_node_id=paper_node_id,
            target_node_id=contribution.semantic_unit_id,
            edge_type=EdgeType.HAS_CONTRIBUTION,
        ),
        *[
            GraphEdge(
                id=f"{contribution.semantic_unit_id}-{facet_type.value.casefold()}",
                paper_id=parsed.paper_id,
                source_node_id=contribution.semantic_unit_id,
                target_node_id=facet_id,
                edge_type=EdgeType.CONTAINS,
            )
            for facet_type, facet_id in facet_ids.items()
        ],
    ]
    for unit in parsed.semantic_units:
        if unit.role == "contribution":
            continue
        nodes.append(
            GraphNode(
                id=unit.semantic_unit_id,
                paper_id=parsed.paper_id,
                node_type=NodeType.MODULE,
                title=unit.title,
                semantic_unit_ids=[unit.semantic_unit_id],
            )
        )
        edges.append(
            GraphEdge(
                id=f"{facet_ids[NodeType.HOW]}-{unit.semantic_unit_id}",
                paper_id=parsed.paper_id,
                source_node_id=facet_ids[NodeType.HOW],
                target_node_id=unit.semantic_unit_id,
                edge_type=EdgeType.CONTAINS,
                semantic_unit_ids=[unit.semantic_unit_id],
            )
        )
    return PaperArgumentGraph(paper_id=parsed.paper_id, nodes=nodes, edges=edges)


def test_node_reference_expansion_links_directly_to_external_contribution(monkeypatch):
    current, target = _paper_pair()
    target_graph = _graph_for(target)
    current_graph = _graph_for(current)
    target_contribution = next(
        node for node in target_graph.nodes if str(node.node_type) == "Contribution"
    )
    store = InMemoryGraphStore()
    store.save_paper(target, target_graph)
    store.save_paper(current, current_graph)
    monkeypatch.setattr(routes, "_store", store)

    class FakeMatcher:
        calls = 0

        async def match(self, **kwargs):
            FakeMatcher.calls += 1
            return CitationContributionMatchOutput(
                matched=True,
                target_contribution_node_id=target_contribution.id,
                relation_type="BUILDS_ON",
                rationale="The current method explicitly extends the cited gate.",
                confidence=0.94,
            )

    monkeypatch.setattr(routes, "CitationContributionMatcher", FakeMatcher)

    with TestClient(app) as client:
        response = client.post(
            f"/api/papers/{current.paper_id}/nodes/current-method-unit/references/expand",
            json={"depth": 1},
        )
        repeated = client.post(
            f"/api/papers/{current.paper_id}/nodes/current-method-unit/references/expand",
            json={"depth": 1},
        )
        subgraph = client.get(
            f"/api/papers/{current.paper_id}/external-contributions/"
            f"{target.paper_id}/{target_contribution.id}"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "linked"
    assert payload["results"][0]["relation_type"] == "BUILDS_ON"
    assert any(node["id"] == target_contribution.id for node in payload["graph"]["nodes"])
    assert not any(
        node["paper_id"] == target.paper_id and node["node_type"] == "Paper"
        for node in payload["graph"]["nodes"]
    )

    stored = store.get_graph(current.paper_id)
    assert stored is not None
    cross_edge = next(edge for edge in stored.edges if edge.properties.get("cross_paper"))
    assert cross_edge.source_node_id == "current-method-unit"
    assert cross_edge.target_node_id == target_contribution.id
    assert cross_edge.properties["reference_id"] == "ref-current-2"
    assert cross_edge.properties["target_paper_id"] == target.paper_id
    assert "[2]" in cross_edge.properties["citation_text"]
    assert FakeMatcher.calls == 1
    assert repeated.json()["results"][0]["status"] == "cached_link"

    assert subgraph.status_code == 200
    external = subgraph.json()
    assert any(node["id"] == target_contribution.id for node in external["nodes"])
    assert not any(node["node_type"] == "Paper" for node in external["nodes"])
    assert {"Why", "How", "Proof"}.issubset(
        {node["node_type"] for node in external["nodes"]}
    )


def test_numeric_citation_parser_expands_grouped_ranges():
    assert routes._numeric_citation_numbers("Prior work [1, 3-5; 8] is extended.") == {
        1,
        3,
        4,
        5,
        8,
    }


def test_node_reference_binding_is_preferred_over_marker_heuristics():
    current, _ = _paper_pair()
    graph = _graph_for(current)
    source_node = next(node for node in graph.nodes if node.id == "current-method-unit")
    source_node.reference_ids = ["ref-current-2"]
    unit = next(
        unit for unit in current.semantic_units if unit.semantic_unit_id == "current-method-unit"
    )
    unit.properties.pop("citation_markers")
    unit.properties.pop("citation_text")
    store = InMemoryGraphStore()
    store.save_paper(current, graph)

    contexts = routes._citation_contexts_for_node(graph, source_node, store)

    assert len(contexts) == 1
    assert contexts[0]["reference"].reference_id == "ref-current-2"
