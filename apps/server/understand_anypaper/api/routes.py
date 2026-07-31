import asyncio
import json
import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4

import fitz
import httpx
from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from understand_anypaper.analyzers.citation_contribution_matcher import (
    CitationContributionMatcher,
)
from understand_anypaper.analyzers.paper_graph_agent import PaperGraphAgent
from understand_anypaper.config import apply_desktop_api_overrides, settings
from understand_anypaper.graph.graph_validator import GraphValidator
from understand_anypaper.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import PaperReference, ParsedPaper, SemanticUnit
from understand_anypaper.parser.pdf_parser import PdfParser
from understand_anypaper.recursive.traversal_policy import TraversalPolicy
from understand_anypaper.storage import GraphStore, create_graph_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_store: GraphStore | None = None


def get_store() -> GraphStore:
    global _store
    if _store is None:
        _store = create_graph_store()
    return _store


class ReferenceAnalyzeRequest(BaseModel):
    depth: int = 1
    focus: str = "current_citation_context"
    expand: bool = False


class NodeReferenceExpansionRequest(BaseModel):
    depth: int = Field(default=1, ge=1, le=2)


class GraphSearchRequest(BaseModel):
    query: str
    paper_id: str
    node_types: list[str] = []
    expand_depth: int = Field(default=0, ge=0, le=3)


class DocumentPageInfo(BaseModel):
    page: int
    width: float
    height: float


class PaperDocumentInfo(BaseModel):
    filename: str
    media_type: str
    pages: list[DocumentPageInfo]


class PatchOperation(BaseModel):
    op: Literal["add_node", "update_node", "remove_node", "add_edge", "update_edge", "remove_edge"]
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    id: str | None = None
    changes: dict = Field(default_factory=dict)


class GraphPatchRequest(BaseModel):
    operations: list[PatchOperation]


async def _analyze_and_build_graph(parsed: ParsedPaper) -> PaperArgumentGraph:
    return await PaperGraphAgent().build(parsed)


def _upload_progress_line(event: str, progress: int, message: str, **payload: object) -> str:
    logger.info("Processing uploaded paper, msg=%s", message)
    return json.dumps(
        {
            "event": event,
            "progress": progress,
            "message": message,
            **payload,
        },
        ensure_ascii=False,
    ) + "\n"


@router.post("/papers")
async def upload_paper(file: Annotated[UploadFile, File(...)]) -> StreamingResponse:
    apply_desktop_api_overrides(settings)
    suffix = Path(file.filename or "paper.pdf").suffix
    media_type = "application/pdf" if suffix.lower() == ".pdf" else (file.content_type or "application/octet-stream")
    data = await file.read()
    filename = file.filename or f"paper{suffix}"

    async def progress_stream():
        try:
            yield _upload_progress_line(
                "upload_received", 60, "Upload received. Rendering document pages."
            )
            with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)

            try:
                parsed = await asyncio.to_thread(PdfParser().parse, tmp_path)
            except Exception as exc:  # noqa: BLE001 - surface parse failures in the progress stream
                yield _upload_progress_line("error", 100, f"Failed to parse document: {exc}")
                return
            finally:
                tmp_path.unlink(missing_ok=True)

            parsed.metadata.update(
                {
                    "source_filename": filename,
                    "source_media_type": media_type,
                }
            )
            yield _upload_progress_line(
                "rendered_pages",
                68,
                "Rendered document pages.",
                page_count=len(parsed.pages),
            )

            yield _upload_progress_line(
                "started_graph_agent",
                74,
                "Started the graph authoring agent.",
            )
            graph = await _analyze_and_build_graph(parsed)
            yield _upload_progress_line(
                "built_argument_graph",
                94,
                "Built the argument graph.",
                node_count=len(graph.nodes),
                edge_count=len(graph.edges),
                semantic_unit_count=len(parsed.semantic_units),
            )

            store = get_store()
            await asyncio.to_thread(store.save_paper, parsed, graph)
            if suffix.lower() == ".pdf":
                await asyncio.to_thread(
                    store.save_source_document,
                    parsed.paper_id,
                    filename,
                    media_type,
                    data,
                )
            yield _upload_progress_line("saved_graph", 98, "Saved graph and source document.")
            yield _upload_progress_line(
                "complete",
                100,
                "Graph ready.",
                graph=graph.model_dump(mode="json"),
            )
        except Exception as exc:  # noqa: BLE001 - preserve the progress stream contract for unexpected failures
            logger.exception("Unexpected paper upload failure")
            yield _upload_progress_line("error", 100, f"Upload failed: {exc}")

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")


@router.get("/papers")
def list_papers() -> list[dict]:
    return get_store().list_papers()


@router.delete("/papers/{paper_id}")
def delete_paper(paper_id: str) -> dict:
    store = get_store()
    if not store.delete_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    return {"deleted": paper_id, "papers": store.list_papers()}


@router.get("/papers/{paper_id}/graph", response_model=PaperArgumentGraph)
def get_graph(paper_id: str) -> PaperArgumentGraph:
    return _materialize_cross_paper_contributions(_get_graph(paper_id), get_store())


@router.get("/papers/{paper_id}/semantic-units", response_model=list[SemanticUnit])
def get_semantic_units(paper_id: str) -> list[SemanticUnit]:
    _get_graph(paper_id)
    return get_store().get_semantic_units(paper_id)


@router.get("/papers/{paper_id}/document", response_model=PaperDocumentInfo)
def get_document_info(paper_id: str) -> PaperDocumentInfo:
    _get_graph(paper_id)
    document = get_store().get_source_document(paper_id)
    if document is None or not _is_pdf_media_type(document.media_type):
        raise HTTPException(status_code=404, detail="PDF source document not available")
    try:
        pdf = fitz.open(stream=document.data, filetype="pdf")
        try:
            pages = [
                DocumentPageInfo(page=index + 1, width=page.rect.width, height=page.rect.height)
                for index, page in enumerate(pdf)
            ]
        finally:
            pdf.close()
    except Exception as exc:  # noqa: BLE001 - corrupt stored PDFs should be reported as bad source data
        raise HTTPException(status_code=422, detail=f"Failed to inspect PDF source: {exc}") from exc
    return PaperDocumentInfo(filename=document.filename, media_type=document.media_type, pages=pages)


@router.get("/papers/{paper_id}/document/pages/{page_number}.png")
def render_document_page(paper_id: str, page_number: int, scale: float = 1.6) -> Response:
    _get_graph(paper_id)
    document = get_store().get_source_document(paper_id)
    if document is None or not _is_pdf_media_type(document.media_type):
        raise HTTPException(status_code=404, detail="PDF source document not available")
    if page_number < 1:
        raise HTTPException(status_code=404, detail="Page not found")
    scale = min(max(scale, 0.8), 3.0)
    try:
        pdf = fitz.open(stream=document.data, filetype="pdf")
        try:
            if page_number > pdf.page_count:
                raise HTTPException(status_code=404, detail="Page not found")
            page = pdf.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return Response(content=pixmap.tobytes("png"), media_type="image/png")
        finally:
            pdf.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface render failures as HTTP 422
        raise HTTPException(status_code=422, detail=f"Failed to render PDF page: {exc}") from exc


@router.get("/papers/{paper_id}/references", response_model=list[PaperReference])
def get_references(paper_id: str) -> list[PaperReference]:
    _get_graph(paper_id)
    return get_store().get_references(paper_id)


@router.get("/papers/{paper_id}/graph/subgraph", response_model=PaperArgumentGraph)
def get_subgraph(paper_id: str, node_id: str, depth: int = 2) -> PaperArgumentGraph:
    graph = _get_graph(paper_id)
    selected = {node_id}
    for _ in range(depth):
        for edge in graph.edges:
            if edge.source_node_id in selected or edge.target_node_id in selected:
                selected.update([edge.source_node_id, edge.target_node_id])
    return PaperArgumentGraph(
        paper_id=paper_id,
        nodes=[node for node in graph.nodes if node.id in selected],
        edges=[edge for edge in graph.edges if edge.source_node_id in selected and edge.target_node_id in selected],
    )


@router.get("/nodes/{node_id}/evidence")
def get_node_evidence(node_id: str, paper_id: str | None = None) -> dict:
    store = get_store()
    paper_ids = [paper_id] if paper_id else [paper["paper_id"] for paper in store.list_papers()]
    for pid in paper_ids:
        graph = store.get_graph(pid)
        if graph is None:
            continue
        node = next((n for n in graph.nodes if n.id == node_id), None)
        if node is None:
            continue
        units_by_id = {unit.semantic_unit_id: unit for unit in store.get_semantic_units(pid)}
        evidence = []
        for semantic_unit_id in node.semantic_unit_ids:
            unit = units_by_id.get(semantic_unit_id)
            evidence.append(
                {
                    "semantic_unit_id": semantic_unit_id,
                    "role": unit.role if unit else None,
                    "title": unit.title if unit else None,
                    "text": unit.text if unit else None,
                    "source_location": unit.source_location.model_dump() if unit else None,
                }
            )
        return {
            "node_id": node_id,
            "paper_id": pid,
            "semantic_unit_ids": node.semantic_unit_ids,
            "page_ranges": node.page_ranges,
            "evidence": evidence,
        }
    raise HTTPException(status_code=404, detail="Node not found")


@router.post("/references/{reference_id}/resolve", response_model=PaperReference)
def resolve_reference(reference_id: str) -> PaperReference:
    store = get_store()
    reference = store.find_reference(reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference not found")
    enriched = _crossref_enrich(reference) or reference
    enriched = _semantic_scholar_enrich(enriched) or enriched
    if enriched != reference:
        store.update_reference(enriched)
    return enriched


@router.post("/references/{reference_id}/analyze")
async def analyze_reference(reference_id: str, request: ReferenceAnalyzeRequest) -> dict:
    store = get_store()
    reference = await asyncio.to_thread(store.find_reference, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference not found")
    policy = TraversalPolicy(max_depth=min(request.depth, settings.recursion_max_depth), max_papers=settings.recursion_max_papers)
    can_expand = policy.can_expand(reference_id, request.depth)
    expansion = await _expand_reference(reference, store) if request.expand and can_expand else None
    return {
        "reference_id": reference_id,
        "reference": reference.model_dump(),
        "focus": request.focus,
        "can_expand": can_expand,
        "expansion": expansion,
        "expand_hint": "Upload the referenced paper to build its own argument graph."
        if can_expand
        else "Traversal policy limit reached.",
    }


@router.post("/papers/{paper_id}/nodes/{node_id}/references/expand")
async def expand_node_references(
    paper_id: str,
    node_id: str,
    request: NodeReferenceExpansionRequest,
) -> dict:
    """Resolve a node's citations directly to contributions in referenced papers."""
    store = get_store()
    graph = await asyncio.to_thread(_get_graph, paper_id)
    source_node = next((node for node in graph.nodes if node.id == node_id), None)
    if source_node is None:
        raise HTTPException(status_code=404, detail="Node not found in the current paper")

    contexts = await asyncio.to_thread(_citation_contexts_for_node, graph, source_node, store)
    policy = TraversalPolicy(
        max_depth=min(request.depth, settings.recursion_max_depth),
        max_papers=settings.recursion_max_papers,
    )
    matcher = CitationContributionMatcher()
    results: list[dict] = []
    graph_changed = False

    for context in contexts[: settings.recursion_max_papers]:
        reference: PaperReference = context["reference"]
        existing = next(
            (
                edge
                for edge in graph.edges
                if edge.source_node_id == source_node.id
                and edge.properties.get("cross_paper") is True
                and edge.properties.get("reference_id") == reference.reference_id
            ),
            None,
        )
        if existing is not None:
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": "cached_link",
                    "target_paper_id": existing.properties.get("target_paper_id"),
                    "target_node_id": existing.target_node_id,
                    "relation_type": str(existing.edge_type),
                    "confidence": existing.confidence,
                }
            )
            continue

        cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)
        if cached is None:
            enriched = await asyncio.to_thread(_resolve_reference_metadata, reference)
            if enriched != reference:
                await asyncio.to_thread(store.update_reference, enriched)
                reference = enriched
            cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)

        expansion: dict
        if cached is not None:
            expansion = {
                "status": "cached",
                "paper_id": cached["paper_id"],
                "title": cached["title"],
            }
        elif policy.can_expand(reference.reference_id, request.depth):
            policy.visited_paper_ids.add(reference.reference_id)
            expansion = await _expand_reference(reference, store)
        else:
            expansion = {"status": "unavailable", "reason": "Traversal policy limit reached."}

        target_paper_id = expansion.get("paper_id")
        if not isinstance(target_paper_id, str):
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": expansion.get("status", "unavailable"),
                    "reason": expansion.get("reason", "Referenced paper could not be analyzed."),
                }
            )
            continue

        target_graph = await asyncio.to_thread(store.get_graph, target_paper_id)
        if target_graph is None:
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": "failed",
                    "reason": "Referenced paper graph was not found after analysis.",
                }
            )
            continue
        target_contributions = [
            node for node in target_graph.nodes if node.node_type == NodeType.CONTRIBUTION
        ]
        target_title = str(expansion.get("title") or _paper_title(target_paper_id, store))
        match = await matcher.match(
            source_node=source_node,
            citation_context=context["citation_text"],
            reference=reference,
            target_paper_title=target_title,
            candidate_contributions=target_contributions,
        )
        target_node = next(
            (
                node
                for node in target_contributions
                if node.id == match.target_contribution_node_id
            ),
            None,
        )
        if not match.matched or target_node is None or match.confidence < 0.45:
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": "unmatched",
                    "reason": match.rationale,
                    "confidence": match.confidence,
                }
            )
            continue

        edge = GraphEdge(
            id=f"edge-cross-paper-{uuid4()}",
            paper_id=paper_id,
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            edge_type=EdgeType(match.relation_type),
            confidence=match.confidence,
            semantic_unit_ids=context["semantic_unit_ids"],
            inference_type="llm_citation_contribution_match",
            properties={
                "cross_paper": True,
                "citation_text": context["citation_text"],
                "reference_id": reference.reference_id,
                "reference_marker": reference.marker,
                "reference_raw_text": reference.raw_text,
                "source_paper_id": paper_id,
                "target_paper_id": target_paper_id,
                "target_node_id": target_node.id,
                "target_paper_title": target_title,
                "target_contribution_title": target_node.title,
                "match_rationale": match.rationale,
            },
        )
        graph.edges.append(edge)
        graph_changed = True
        results.append(
            {
                "reference_id": reference.reference_id,
                "status": "linked",
                "target_paper_id": target_paper_id,
                "target_node_id": target_node.id,
                "target_contribution_title": target_node.title,
                "relation_type": match.relation_type,
                "confidence": match.confidence,
                "rationale": match.rationale,
            }
        )

    if graph_changed:
        await asyncio.to_thread(store.replace_graph, paper_id, graph)
    view_graph = await asyncio.to_thread(_materialize_cross_paper_contributions, graph, store)
    return {
        "paper_id": paper_id,
        "node_id": node_id,
        "results": results,
        "graph": view_graph.model_dump(mode="json"),
    }


@router.get(
    "/papers/{paper_id}/external-contributions/{target_paper_id}/{contribution_node_id}",
    response_model=PaperArgumentGraph,
)
def get_external_contribution_subgraph(
    paper_id: str,
    target_paper_id: str,
    contribution_node_id: str,
) -> PaperArgumentGraph:
    current_graph = _get_graph(paper_id)
    allowed = any(
        edge.target_node_id == contribution_node_id
        and edge.properties.get("cross_paper") is True
        and edge.properties.get("target_paper_id") == target_paper_id
        for edge in current_graph.edges
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Cross-paper contribution link not found")

    store = get_store()
    target_graph = store.get_graph(target_paper_id)
    if target_graph is None:
        raise HTTPException(status_code=404, detail="Referenced paper graph not found")
    selected = {contribution_node_id}
    frontier = {contribution_node_id}
    for _ in range(2):
        next_frontier: set[str] = set()
        for edge in target_graph.edges:
            if edge.edge_type in {EdgeType.NEXT, EdgeType.PREVIOUS}:
                continue
            if edge.source_node_id in frontier and edge.target_node_id not in selected:
                selected.add(edge.target_node_id)
                next_frontier.add(edge.target_node_id)
        frontier = next_frontier

    target_title = _paper_title(target_paper_id, store)
    nodes = [
        _externalize_node(node, target_title)
        for node in target_graph.nodes
        if node.id in selected and node.node_type != NodeType.PAPER
    ]
    edges = [
        edge.model_copy(
            update={
                "semantic_unit_ids": [],
                "properties": {
                    **edge.properties,
                    "external_subgraph": True,
                    "target_paper_id": target_paper_id,
                    "target_paper_title": target_title,
                },
            }
        )
        for edge in target_graph.edges
        if edge.source_node_id in selected and edge.target_node_id in selected
    ]
    return PaperArgumentGraph(paper_id=target_paper_id, nodes=nodes, edges=edges)


@router.post("/graph/search")
def search_graph(request: GraphSearchRequest) -> dict:
    graph = _get_graph(request.paper_id)
    query = request.query.lower()
    scored: dict[str, dict] = {}
    for node in graph.nodes:
        if request.node_types and node.node_type not in request.node_types:
            continue
        haystack = f"{node.title} {node.summary}".lower()
        if query in haystack:
            scored[node.id] = {"node": node, "score": 1.0 + haystack.count(query) * 0.1, "source": "lexical"}

    matches = sorted(scored.values(), key=lambda item: item["score"], reverse=True)
    selected_ids = {item["node"].id for item in matches}
    for _ in range(request.expand_depth):
        next_ids = set(selected_ids)
        for edge in graph.edges:
            if edge.source_node_id in selected_ids or edge.target_node_id in selected_ids:
                next_ids.update([edge.source_node_id, edge.target_node_id])
        selected_ids = next_ids
    expanded_nodes = [node for node in graph.nodes if node.id in selected_ids]
    expanded_edges = [
        edge
        for edge in graph.edges
        if edge.source_node_id in selected_ids and edge.target_node_id in selected_ids
    ]
    return {
        "query": request.query,
        "matches": [
            {"node": item["node"], "score": round(item["score"], 4), "source": item["source"]}
            for item in matches
        ],
        "expanded_subgraph": {
            "nodes": expanded_nodes,
            "edges": expanded_edges,
            "depth": request.expand_depth,
        },
    }


@router.get("/papers/{paper_id}/completeness")
def completeness(paper_id: str) -> dict:
    return {"scores": GraphValidator().score_completeness(_get_graph(paper_id))}


@router.post("/papers/{paper_id}/graph/patch", response_model=PaperArgumentGraph)
def patch_graph(paper_id: str, request: GraphPatchRequest) -> PaperArgumentGraph:
    graph = _get_graph(paper_id)
    for operation in request.operations:
        _apply_patch_operation(graph, operation)
    store = get_store()
    store.replace_graph(paper_id, graph)
    store.record_patch(paper_id, [op.model_dump(mode="json") for op in request.operations])
    return _materialize_cross_paper_contributions(graph, store)


_MUTABLE_NODE_FIELDS = {"title", "summary", "node_type", "confidence", "verified", "properties", "semantic_unit_ids"}
_MUTABLE_EDGE_FIELDS = {"edge_type", "confidence", "inference_type", "properties", "semantic_unit_ids"}


def _apply_patch_operation(graph: PaperArgumentGraph, operation: PatchOperation) -> None:
    if operation.op == "add_node":
        if operation.node is None:
            raise HTTPException(status_code=422, detail="add_node requires a node")
        if any(node.id == operation.node.id for node in graph.nodes):
            raise HTTPException(status_code=409, detail=f"Node {operation.node.id} already exists")
        operation.node.paper_id = graph.paper_id
        graph.nodes.append(operation.node)
    elif operation.op == "update_node":
        node = next((n for n in graph.nodes if n.id == operation.id), None)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {operation.id} not found")
        for field, value in operation.changes.items():
            if field in _MUTABLE_NODE_FIELDS:
                setattr(node, field, value)
    elif operation.op == "remove_node":
        if not any(node.id == operation.id for node in graph.nodes):
            raise HTTPException(status_code=404, detail=f"Node {operation.id} not found")
        graph.nodes = [node for node in graph.nodes if node.id != operation.id]
        graph.edges = [
            edge for edge in graph.edges
            if edge.source_node_id != operation.id and edge.target_node_id != operation.id
        ]
    elif operation.op == "add_edge":
        if operation.edge is None:
            raise HTTPException(status_code=422, detail="add_edge requires an edge")
        node_ids = {node.id for node in graph.nodes}
        if operation.edge.source_node_id not in node_ids or operation.edge.target_node_id not in node_ids:
            raise HTTPException(status_code=422, detail="Edge endpoints must exist in the graph")
        operation.edge.paper_id = graph.paper_id
        graph.edges.append(operation.edge)
    elif operation.op == "update_edge":
        edge = next((e for e in graph.edges if e.id == operation.id), None)
        if edge is None:
            raise HTTPException(status_code=404, detail=f"Edge {operation.id} not found")
        for field, value in operation.changes.items():
            if field in _MUTABLE_EDGE_FIELDS:
                setattr(edge, field, value)
    elif operation.op == "remove_edge":
        if not any(edge.id == operation.id for edge in graph.edges):
            raise HTTPException(status_code=404, detail=f"Edge {operation.id} not found")
        graph.edges = [edge for edge in graph.edges if edge.id != operation.id]


def _citation_contexts_for_node(
    graph: PaperArgumentGraph,
    node: GraphNode,
    store: GraphStore,
) -> list[dict]:
    units_by_id = {
        unit.semantic_unit_id: unit for unit in store.get_semantic_units(graph.paper_id)
    }
    units = [units_by_id[unit_id] for unit_id in node.semantic_unit_ids if unit_id in units_by_id]
    references = store.get_references(graph.paper_id)
    contexts: dict[str, dict] = {}

    for unit in units:
        property_markers = unit.properties.get("citation_markers")
        marker_strings = (
            [marker for marker in property_markers if isinstance(marker, str)]
            if isinstance(property_markers, list)
            else []
        )
        citation_text = unit.properties.get("citation_text")
        exact_text = (
            citation_text.strip()
            if isinstance(citation_text, str) and citation_text.strip()
            else unit.source_location.extracted_text.strip() or unit.text.strip()
        )
        searchable = " ".join([*marker_strings, exact_text, unit.text])
        cited_numbers = _numeric_citation_numbers(searchable)
        normalized_markers = {_normalize_citation_marker(marker) for marker in marker_strings}

        for reference in references:
            marker_matches = False
            if reference.marker:
                marker_number = _single_marker_number(reference.marker)
                marker_matches = (
                    (marker_number is not None and marker_number in cited_numbers)
                    or _normalize_citation_marker(reference.marker) in normalized_markers
                    or reference.marker in exact_text
                )
            elif _author_year_reference_matches(reference, searchable):
                marker_matches = True
            if not marker_matches:
                continue

            context = contexts.setdefault(
                reference.reference_id,
                {
                    "reference": reference,
                    "semantic_unit_ids": [],
                    "citation_texts": [],
                },
            )
            if unit.semantic_unit_id not in context["semantic_unit_ids"]:
                context["semantic_unit_ids"].append(unit.semantic_unit_id)
            if exact_text and exact_text not in context["citation_texts"]:
                context["citation_texts"].append(exact_text)

    return [
        {
            "reference": context["reference"],
            "semantic_unit_ids": context["semantic_unit_ids"],
            "citation_text": "\n".join(context["citation_texts"])[:4000],
        }
        for context in contexts.values()
    ]


def _numeric_citation_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", text):
        for part in re.split(r"[,;]", bracketed):
            token = part.strip()
            range_match = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", token)
            if range_match:
                start, end = (int(value) for value in range_match.groups())
                if start <= end and end - start <= 25:
                    numbers.update(range(start, end + 1))
                continue
            if token.isdigit():
                numbers.add(int(token))
    return numbers


def _single_marker_number(marker: str) -> int | None:
    match = re.fullmatch(r"\s*\[(\d+)\]\s*", marker)
    return int(match.group(1)) if match else None


def _normalize_citation_marker(marker: str) -> str:
    return re.sub(r"\s+", " ", marker).strip().casefold()


def _author_year_reference_matches(reference: PaperReference, text: str) -> bool:
    if reference.year is None or str(reference.year) not in text:
        return False
    prefix = reference.raw_text.split(str(reference.year), 1)[0]
    author_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", prefix)
        if len(token) >= 4
    }
    searchable = text.casefold()
    return any(token in searchable for token in author_tokens)


def _resolve_reference_metadata(reference: PaperReference) -> PaperReference:
    enriched = _crossref_enrich(reference) or reference
    return _semantic_scholar_enrich(enriched) or enriched


def _paper_title(paper_id: str, store: GraphStore) -> str:
    paper = next((item for item in store.list_papers() if item["paper_id"] == paper_id), None)
    return str(paper.get("title") if paper else paper_id)


def _externalize_node(node: GraphNode, target_paper_title: str, relation: str = "") -> GraphNode:
    return node.model_copy(
        update={
            "semantic_unit_ids": [],
            "page_ranges": [],
            "source_type": "cross_paper_reference",
            "properties": {
                **node.properties,
                "cross_paper": True,
                "target_paper_id": node.paper_id,
                "target_paper_title": target_paper_title,
                "cross_paper_relation": relation,
            },
        }
    )


def _materialize_cross_paper_contributions(
    graph: PaperArgumentGraph,
    store: GraphStore,
) -> PaperArgumentGraph:
    nodes = list(graph.nodes)
    known_node_ids = {node.id for node in nodes}
    for edge in graph.edges:
        if edge.properties.get("cross_paper") is not True or edge.target_node_id in known_node_ids:
            continue
        target_paper_id = edge.properties.get("target_paper_id")
        if not isinstance(target_paper_id, str):
            continue
        target_graph = store.get_graph(target_paper_id)
        if target_graph is None:
            continue
        target_node = next(
            (
                node
                for node in target_graph.nodes
                if node.id == edge.target_node_id and node.node_type == NodeType.CONTRIBUTION
            ),
            None,
        )
        if target_node is None:
            continue
        target_title = str(
            edge.properties.get("target_paper_title") or _paper_title(target_paper_id, store)
        )
        nodes.append(_externalize_node(target_node, target_title, str(edge.edge_type)))
        known_node_ids.add(target_node.id)
    return graph.model_copy(update={"nodes": nodes})


def _crossref_enrich(reference: PaperReference) -> PaperReference | None:
    """Best-effort metadata enrichment via Crossref. Returns None when unavailable."""
    try:
        if reference.doi:
            response = httpx.get(f"https://api.crossref.org/works/{reference.doi}", timeout=8)
        else:
            query = reference.title or re.sub(r"\[\d+\]", "", reference.raw_text)[:200]
            response = httpx.get(
                "https://api.crossref.org/works",
                params={"query.bibliographic": query, "rows": 1},
                timeout=8,
            )
        response.raise_for_status()
        payload = response.json()["message"]
        item = payload["items"][0] if "items" in payload else payload
        if not item:
            return None
        updated = reference.model_copy()
        titles = item.get("title") or []
        if titles:
            updated.title = titles[0]
        updated.doi = item.get("DOI", updated.doi)
        issued = (item.get("issued", {}).get("date-parts") or [[None]])[0][0]
        if issued:
            updated.year = int(issued)
        authors = [
            " ".join(part for part in (author.get("given"), author.get("family")) if part)
            for author in item.get("author", [])
        ]
        if authors:
            updated.authors = authors[:12]
        return updated
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("Crossref resolution failed for %s: %s", reference.reference_id, exc)
        return None


def _semantic_scholar_enrich(reference: PaperReference) -> PaperReference | None:
    try:
        fields = "title,year,authors,externalIds"
        if reference.arxiv_id:
            url = f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{quote(reference.arxiv_id)}"
            response = httpx.get(url, params={"fields": fields}, timeout=8)
            payload = response.json()
        elif reference.doi:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(reference.doi, safe='')}"
            response = httpx.get(url, params={"fields": fields}, timeout=8)
            payload = response.json()
        else:
            query = reference.title or re.sub(r"\[\d+\]", "", reference.raw_text)[:200]
            response = httpx.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "limit": 1, "fields": fields},
                timeout=8,
            )
            payload = (response.json().get("data") or [None])[0]
        response.raise_for_status()
        if not payload:
            return None
        updated = reference.model_copy()
        if payload.get("title"):
            updated.title = payload["title"]
        if payload.get("year"):
            updated.year = int(payload["year"])
        authors = [author.get("name") for author in payload.get("authors", []) if author.get("name")]
        if authors:
            updated.authors = authors[:12]
        external = payload.get("externalIds") or {}
        updated.doi = external.get("DOI") or updated.doi
        updated.arxiv_id = external.get("ArXiv") or updated.arxiv_id
        return updated
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
        logger.warning("Semantic Scholar resolution failed for %s: %s", reference.reference_id, exc)
        return None


async def _expand_reference(reference: PaperReference, store: GraphStore) -> dict:
    cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)
    if cached:
        return {"status": "cached", "paper_id": cached["paper_id"], "title": cached["title"]}
    if not reference.arxiv_id:
        return {"status": "unavailable", "reason": "No arXiv identifier or downloadable PDF is known."}

    url = f"https://arxiv.org/pdf/{reference.arxiv_id}.pdf"
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
            response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("arXiv PDF download failed for %s: %s", reference.reference_id, exc)
        return {"status": "unavailable", "reason": "arXiv PDF download failed."}
    data = response.content
    if not data.startswith(b"%PDF"):
        return {"status": "unavailable", "reason": "Downloaded arXiv response was not a PDF."}

    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        parsed = await asyncio.to_thread(PdfParser().parse, tmp_path)
    except Exception as exc:  # noqa: BLE001 - reference expansion should not break citation analysis
        logger.warning("Recursive reference parse failed for %s: %s", reference.reference_id, exc)
        return {"status": "failed", "reason": f"Failed to parse referenced PDF: {exc}"}
    finally:
        tmp_path.unlink(missing_ok=True)

    parsed.metadata.update(
        {
            "source_reference_id": reference.reference_id,
            "source_arxiv_id": reference.arxiv_id,
            "source_filename": f"{reference.arxiv_id}.pdf",
            "source_media_type": "application/pdf",
        }
    )
    try:
        graph = await _analyze_and_build_graph(parsed)
    except Exception as exc:  # noqa: BLE001 - reference expansion reports failures per reference
        return {"status": "failed", "reason": str(exc)}
    await asyncio.to_thread(store.save_paper, parsed, graph)
    await asyncio.to_thread(
        store.save_source_document, parsed.paper_id, f"{reference.arxiv_id}.pdf", "application/pdf", data
    )
    return {
        "status": "expanded",
        "paper_id": parsed.paper_id,
        "title": parsed.title,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
    }


def _find_cached_reference_graph(reference: PaperReference, store: GraphStore) -> dict | None:
    reference_title = _normalize_title(reference.title or "")
    for paper in store.list_papers():
        metadata = paper.get("metadata") or {}
        if metadata.get("source_reference_id") == reference.reference_id:
            return paper
        if reference.arxiv_id and metadata.get("source_arxiv_id") == reference.arxiv_id:
            return paper
        paper_title = _normalize_title(paper.get("title") or "")
        if reference_title and (reference_title == paper_title or reference_title in paper_title):
            return paper
    return None


def _normalize_title(title: str) -> str:
    return re.sub(r"\W+", " ", title).strip().casefold()


def _get_graph(paper_id: str) -> PaperArgumentGraph:
    graph = get_store().get_graph(paper_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return graph


def _is_pdf_media_type(media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() == "application/pdf"
