import asyncio
import json

import httpx
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
            source_paper_id=parsed.paper_id,
            source_node_id=paper_node_id,
            target_paper_id=parsed.paper_id,
            target_node_id=contribution.semantic_unit_id,
            edge_type=EdgeType.HAS_CONTRIBUTION,
        ),
        *[
            GraphEdge(
                id=f"{contribution.semantic_unit_id}-{facet_type.value.casefold()}",
                source_paper_id=parsed.paper_id,
                source_node_id=contribution.semantic_unit_id,
                target_paper_id=parsed.paper_id,
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
                source_paper_id=parsed.paper_id,
                source_node_id=facet_ids[NodeType.HOW],
                target_paper_id=parsed.paper_id,
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
    assert cross_edge.target_paper_id == target.paper_id
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


def test_reference_metadata_resolution_skips_remote_lookups_when_arxiv_id_is_known(
    monkeypatch,
):
    reference = PaperReference(
        reference_id="ref-arxiv",
        marker="[2]",
        raw_text="A. Author. A known arXiv paper. 2018.",
        arxiv_id="1809.00219",
    )

    def unexpected_lookup(_reference):
        raise AssertionError("metadata lookup should not run for a known arXiv identifier")

    monkeypatch.setattr(routes, "_crossref_enrich", unexpected_lookup)
    monkeypatch.setattr(routes, "_semantic_scholar_enrich", unexpected_lookup)

    assert routes._resolve_reference_metadata(reference) is reference


def test_reference_metadata_resolution_skips_semantic_scholar_when_doi_is_known(monkeypatch):
    reference = PaperReference(
        reference_id="ref-doi",
        marker="[3]",
        raw_text="A. Author. An open-access paper. 2020.",
    )
    enriched = reference.model_copy(update={"doi": "10.1234/example"})

    monkeypatch.setattr(routes, "_crossref_enrich", lambda _reference: enriched)

    def unexpected_lookup(_reference):
        raise AssertionError("Semantic Scholar should not be needed after DOI resolution")

    monkeypatch.setattr(routes, "_semantic_scholar_enrich", unexpected_lookup)

    assert routes._resolve_reference_metadata(reference) == enriched


def test_reference_metadata_resolution_skips_semantic_scholar_when_title_is_known(monkeypatch):
    reference = PaperReference(
        reference_id="ref-title",
        marker="[4]",
        raw_text="A. Author. A PMLR paper. 2020.",
        title="A PMLR paper",
    )
    monkeypatch.setattr(routes, "_crossref_enrich", lambda _reference: None)

    def unexpected_lookup(_reference):
        raise AssertionError("DBLP venue lookup only needs the known title")

    monkeypatch.setattr(routes, "_semantic_scholar_enrich", unexpected_lookup)

    assert routes._resolve_reference_metadata(reference) is reference


def test_crossref_enrichment_rejects_a_wrong_top_search_result(monkeypatch):
    reference = PaperReference(
        reference_id="ref-crossref-wrong",
        raw_text="Learning a deep convolutional network for image super-resolution. 2014.",
        title="Learning a deep convolutional network for image super-resolution",
        year=2014,
    )

    def fake_get(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "message": {
                    "items": [
                        {
                            "title": [
                                "Deep iterative convolutional neural network for face image "
                                "super-resolution"
                            ],
                            "DOI": "10.58837/wrong-paper",
                            "issued": {"date-parts": [[2022]]},
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(routes.httpx, "get", fake_get)

    assert routes._crossref_enrich(reference) is None


def test_crossref_enrichment_accepts_dehyphenated_title_with_venue_suffix(monkeypatch):
    reference = PaperReference(
        reference_id="ref-crossref-correct",
        raw_text="AdderNet. CVPR 2020.",
        title=(
            "Addernet: Do we really need multiplications in deep learning? In: IEEE "
            "Conference on Computer Vision and Pattern Recognition"
        ),
        year=2020,
    )

    def fake_get(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "message": {
                    "items": [
                        {
                            "title": ["AdderNet: Do We Really Need Multiplications in Deep Learning?"],
                            "DOI": "10.1109/CVPR42600.2020.00154",
                            "issued": {"date-parts": [[2020]]},
                            "author": [{"given": "Hanting", "family": "Chen"}],
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(routes.httpx, "get", fake_get)

    enriched = routes._crossref_enrich(reference)

    assert enriched is not None
    assert enriched.doi == "10.1109/CVPR42600.2020.00154"
    assert enriched.authors == ["Hanting Chen"]


def test_openalex_pdf_urls_prefers_open_access_locations_and_normalizes_arxiv(monkeypatch):
    reference = PaperReference(
        reference_id="ref-openalex",
        marker="[4]",
        raw_text="A. Author. Open paper. 2021.",
        doi="https://doi.org/10.1234/example(test)",
    )
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "best_oa_location": {
                    "is_oa": True,
                    "pdf_url": "https://export.arxiv.org/abs/2101.01234",
                },
                "locations": [
                    {
                        "is_oa": True,
                        "pdf_url": "https://repository.example/paper.pdf",
                    },
                    {
                        "is_oa": False,
                        "pdf_url": "https://publisher.example/paywalled.pdf",
                    },
                ],
                "primary_location": {
                    "is_oa": True,
                    "pdf_url": "https://repository.example/paper.pdf",
                },
                "open_access": {
                    "is_oa": True,
                    "oa_url": "https://repository.example/landing",
                },
            },
        )

    monkeypatch.setattr(routes.httpx, "get", fake_get)
    monkeypatch.setattr(routes.settings, "openalex_api_key", "test-openalex-key")

    assert routes._openalex_pdf_urls(reference) == [
        "https://arxiv.org/pdf/2101.01234.pdf",
        "https://repository.example/paper.pdf",
        "https://repository.example/landing",
    ]
    assert seen["url"].endswith("/works/doi:10.1234/example%28test%29")
    assert seen["params"]["api_key"] == "test-openalex-key"


def test_official_venue_pdf_candidates_cover_acl_cvf_pmlr_and_openreview():
    acl_reference = PaperReference(
        reference_id="ref-acl",
        raw_text="BERT. NAACL 2019.",
        title="BERT",
        doi="10.18653/v1/N19-1423",
    )
    assert routes._official_venue_pdf_candidates(acl_reference) == [
        ("ACL Anthology", "https://aclanthology.org/N19-1423.pdf")
    ]


def test_cvf_index_pdf_urls_cover_main_conference_and_workshop(monkeypatch):
    main = PaperReference(
        reference_id="ref-cvf-main",
        raw_text="A. Author. Residual Dense Network. In: CVPR. (2098)",
        title="Residual Dense Network for Image Super-Resolution",
        year=2098,
    )
    workshop = PaperReference(
        reference_id="ref-cvf-workshop",
        raw_text="A. Author. NTIRE Challenge. In: CVPRW. (2097)",
        title="NTIRE Challenge on Single Image Super-Resolution",
        year=2097,
    )

    def fake_get(url, **_kwargs):
        if url.endswith("CVPR2098?day=all"):
            document = (
                '<a href="content_cvpr_2098/html/Zhang_RDN_CVPR_2098_paper.html">'
                "Residual Dense Network for Image Super-Resolution</a>"
            )
        elif url.endswith("CVPR2097_workshops/menu"):
            document = '<a href="CVPR2097_W12.py">NTIRE workshop and challenge</a>'
        elif url.endswith("CVPR2097_workshops/CVPR2097_W12.py"):
            document = (
                '<a href="content_cvpr_2097_workshops/w12/html/'
                'Agustsson_NTIRE_CVPR_2097_paper.html">'
                "NTIRE Challenge on Single Image Super-Resolution</a>"
            )
        else:
            raise AssertionError(f"unexpected CVF index URL: {url}")
        return httpx.Response(
            200,
            text=document,
            request=httpx.Request("GET", url),
        )

    routes._cvf_index_anchors.cache_clear()
    monkeypatch.setattr(routes, "_http_get_with_retry", fake_get)

    assert routes._cvf_index_pdf_urls(main) == [
        "https://openaccess.thecvf.com/content_cvpr_2098/papers/"
        "Zhang_RDN_CVPR_2098_paper.pdf"
    ]
    assert routes._cvf_index_pdf_urls(workshop) == [
        "https://openaccess.thecvf.com/content_cvpr_2097_workshops/w12/papers/"
        "Agustsson_NTIRE_CVPR_2097_paper.pdf"
    ]

    reference = PaperReference(
        reference_id="ref-venues",
        raw_text="A cited conference paper.",
        title="A cited conference paper",
    )
    publication_urls = [
        (
            "https://openaccess.thecvf.com/content/CVPR2021/html/"
            "Jo_Practical_Single-Image_Super-Resolution_Using_Look-Up_Table_"
            "CVPR_2021_paper.html"
        ),
        "http://proceedings.mlr.press/v54/mcmahan17a.html",
        "https://openreview.net/forum?id=YicbFdNTTy",
    ]

    assert routes._official_venue_pdf_candidates(reference, publication_urls) == [
        (
            "CVF Open Access",
            "https://openaccess.thecvf.com/content/CVPR2021/papers/"
            "Jo_Practical_Single-Image_Super-Resolution_Using_Look-Up_Table_"
            "CVPR_2021_paper.pdf",
        ),
        ("PMLR", "https://proceedings.mlr.press/v54/mcmahan17a/mcmahan17a.pdf"),
        ("OpenReview", "https://openreview.net/pdf?id=YicbFdNTTy"),
    ]


def test_dblp_publication_urls_requires_an_exact_title_match(monkeypatch):
    reference = PaperReference(
        reference_id="ref-dblp",
        raw_text="Practical Single-Image Super-Resolution Using Look-Up Table. CVPR 2021.",
        title="Practical Single-Image Super-Resolution Using Look-Up Table",
        year=2021,
    )
    calls = 0

    def fake_get(url, **kwargs):
        nonlocal calls
        calls += 1
        assert url == "https://dblp.org/search/publ/api"
        assert kwargs["params"]["format"] == "json"
        if calls == 1:
            return httpx.Response(
                500,
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "result": {
                    "hits": {
                        "hit": [
                            {
                                "info": {
                                    "title": "A loosely related super-resolution paper.",
                                    "year": "2021",
                                    "ee": "https://publisher.example/wrong",
                                }
                            },
                            {
                                "info": {
                                    "title": (
                                        "Practical Single-Image Super-Resolution Using "
                                        "Look-Up Table."
                                    ),
                                    "year": "2021",
                                    "doi": "10.1109/CVPR46437.2021.00075",
                                    "ee": "https://openaccess.thecvf.com/paper.html",
                                }
                            },
                        ]
                    }
                }
            },
        )

    monkeypatch.setattr(routes.httpx, "get", fake_get)

    assert routes._dblp_publication_urls(reference) == [
        "https://openaccess.thecvf.com/paper.html",
        "https://doi.org/10.1109/CVPR46437.2021.00075",
    ]
    assert calls == 2


def test_download_reference_pdf_uses_title_lookup_without_a_doi(monkeypatch):
    reference = PaperReference(
        reference_id="ref-title-only",
        raw_text="A. Author. An OpenReview paper. ICLR 2021.",
        title="An OpenReview paper",
        year=2021,
    )
    monkeypatch.setattr(
        routes,
        "_dblp_publication_urls",
        lambda _reference: ["https://openreview.net/forum?id=test-note"],
    )

    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(routes.asyncio, "to_thread", run_inline)

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url == "https://openreview.net/pdf?id=test-note"
            return httpx.Response(
                200,
                content=b"%PDF-1.7\nmock paper",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeAsyncClient)

    downloaded = asyncio.run(
        routes._download_reference_pdf(
            reference,
            trace_id="test-trace",
            on_progress=None,
            progress=20,
        )
    )

    assert downloaded == (b"%PDF-1.7\nmock paper", "https://openreview.net/pdf?id=test-note")


def test_download_reference_pdf_falls_back_from_openreview_to_arxiv(monkeypatch):
    reference = PaperReference(
        reference_id="ref-openreview-fallback",
        raw_text="A. Author. An OpenReview paper. ICLR 2021.",
        title="An OpenReview paper",
        year=2021,
    )
    monkeypatch.setattr(
        routes,
        "_dblp_publication_urls",
        lambda _reference: [
            "https://openreview.net/forum?id=test-note",
            "https://arxiv.org/abs/2010.11929",
        ],
    )

    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(routes.asyncio, "to_thread", run_inline)

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            if "openreview.net" in url:
                return httpx.Response(
                    403,
                    request=httpx.Request("GET", url),
                )
            assert url == "https://arxiv.org/pdf/2010.11929.pdf"
            return httpx.Response(
                200,
                content=b"%PDF-1.7\narxiv mirror",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeAsyncClient)

    downloaded = asyncio.run(
        routes._download_reference_pdf(
            reference,
            trace_id="test-trace",
            on_progress=None,
            progress=20,
        )
    )

    assert downloaded == (
        b"%PDF-1.7\narxiv mirror",
        "https://arxiv.org/pdf/2010.11929.pdf",
    )


def test_cross_paper_materialization_distinguishes_reused_node_ids():
    source_paper_id = "source-paper"
    target_paper_id = "target-paper"
    shared_node_id = "c1"
    source_graph = PaperArgumentGraph(
        paper_id=source_paper_id,
        nodes=[
            GraphNode(
                id=shared_node_id,
                paper_id=source_paper_id,
                node_type=NodeType.CONTRIBUTION,
                title="Source contribution",
            )
        ],
        edges=[
            GraphEdge(
                id="cross-e1",
                source_paper_id=source_paper_id,
                source_node_id=shared_node_id,
                target_paper_id=target_paper_id,
                target_node_id=shared_node_id,
                edge_type=EdgeType.BUILDS_ON,
                properties={"cross_paper": True},
            )
        ],
    )
    target_graph = PaperArgumentGraph(
        paper_id=target_paper_id,
        nodes=[
            GraphNode(
                id=shared_node_id,
                paper_id=target_paper_id,
                node_type=NodeType.CONTRIBUTION,
                title="Target contribution",
            )
        ],
    )
    store = InMemoryGraphStore()
    store.save_paper(
        ParsedPaper(paper_id=source_paper_id, title="Source"),
        source_graph,
    )
    store.save_paper(
        ParsedPaper(paper_id=target_paper_id, title="Target"),
        target_graph,
    )

    materialized = routes._materialize_cross_paper_contributions(source_graph, store)

    assert {(node.paper_id, node.id) for node in materialized.nodes} == {
        (source_paper_id, shared_node_id),
        (target_paper_id, shared_node_id),
    }


def test_node_reference_expansion_streams_agent_activity_and_result(monkeypatch):
    paper_id = "paper-stream"
    node_id = "node-stream"
    graph = PaperArgumentGraph(paper_id=paper_id)

    async def fake_expand(source_paper_id, source_node_id, request, *, on_progress=None):
        assert source_paper_id == paper_id
        assert source_node_id == node_id
        assert on_progress is not None
        await on_progress(
            {
                "event": "agent_activity",
                "progress": 55,
                "message": "Read cited paper text",
                "activity": {
                    "id": "ref-1:read-1",
                    "kind": "read",
                    "label": "Read cited paper text",
                },
            }
        )
        return {
            "paper_id": paper_id,
            "node_id": node_id,
            "results": [],
            "graph": graph.model_dump(mode="json"),
        }

    monkeypatch.setattr(routes, "_expand_node_references_impl", fake_expand)

    async def consume_stream():
        response = await routes.stream_node_references(
            paper_id,
            node_id,
            routes.NodeReferenceExpansionRequest(depth=1),
        )
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(consume_stream())
    body = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks
    )
    events = [json.loads(line) for line in body.strip().splitlines()]
    assert [event["event"] for event in events] == ["agent_activity", "complete"]
    assert events[0]["activity"]["kind"] == "read"
    assert events[-1]["expansion"]["paper_id"] == paper_id
