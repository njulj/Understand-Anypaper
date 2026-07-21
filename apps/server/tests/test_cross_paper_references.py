from fastapi.testclient import TestClient

from understand_anypaper.analyzers.citation_contribution_matcher import (
    CitationContributionMatchOutput,
)
from understand_anypaper.api import routes
from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
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


def test_node_reference_expansion_links_directly_to_external_contribution(monkeypatch):
    current, target = _paper_pair()
    target_graph = PaperArgumentGraphBuilder().build(target)
    current_graph = PaperArgumentGraphBuilder().build(current)
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
