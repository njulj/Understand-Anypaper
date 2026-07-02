import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from understand_anypaper.config import settings
from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.graph_validator import GraphValidator
from understand_anypaper.graph.schema import GraphEdge, GraphNode, PaperArgumentGraph
from understand_anypaper.parser.models import ContentBlock, PaperReference
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


class GraphSearchRequest(BaseModel):
    query: str
    paper_id: str
    node_types: list[str] = []


class PatchOperation(BaseModel):
    op: Literal["add_node", "update_node", "remove_node", "add_edge", "update_edge", "remove_edge"]
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    id: str | None = None
    changes: dict = Field(default_factory=dict)


class GraphPatchRequest(BaseModel):
    operations: list[PatchOperation]


@router.post("/papers", response_model=PaperArgumentGraph)
async def upload_paper(file: Annotated[UploadFile, File(...)]) -> PaperArgumentGraph:
    suffix = Path(file.filename or "paper.pdf").suffix
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        parsed = PdfParser().parse(tmp_path)
    except Exception as exc:  # noqa: BLE001 - surface parse failures as HTTP 422
        raise HTTPException(status_code=422, detail=f"Failed to parse document: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    graph = PaperArgumentGraphBuilder().build(parsed)
    get_store().save_paper(parsed, graph)
    return graph


@router.get("/papers")
def list_papers() -> list[dict]:
    return get_store().list_papers()


@router.get("/papers/{paper_id}/graph", response_model=PaperArgumentGraph)
def get_graph(paper_id: str) -> PaperArgumentGraph:
    return _get_graph(paper_id)


@router.get("/papers/{paper_id}/blocks", response_model=list[ContentBlock])
def get_blocks(paper_id: str) -> list[ContentBlock]:
    _get_graph(paper_id)
    return get_store().get_blocks(paper_id)


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
        blocks = {block.content_id: block for block in store.get_blocks(pid)}
        evidence = [
            {
                "content_id": content_id,
                "page": blocks[content_id].page if content_id in blocks else None,
                "text": blocks[content_id].text if content_id in blocks else None,
                "bbox": blocks[content_id].bbox if content_id in blocks else None,
            }
            for content_id in node.evidence_ids
        ]
        return {
            "node_id": node_id,
            "paper_id": pid,
            "evidence_ids": node.evidence_ids,
            "page_ranges": node.page_ranges,
            "evidence": evidence,
        }
    raise HTTPException(status_code=404, detail="Node not found")


@router.get("/content/{content_id}/assignments")
def get_content_assignments(content_id: str) -> dict:
    store = get_store()
    assignments = []
    for paper in store.list_papers():
        graph = store.get_graph(paper["paper_id"])
        if graph is None:
            continue
        assignments.extend(
            {
                "paper_id": graph.paper_id,
                "contribution_id": edge.target_node_id,
                "relation": edge.edge_type,
                "confidence": edge.confidence,
                "explanation": edge.inference_type,
            }
            for edge in graph.edges
            if edge.source_node_id == content_id and edge.target_node_id.startswith("contribution-")
        )
    return {"content_id": content_id, "assignments": assignments}


@router.post("/references/{reference_id}/resolve", response_model=PaperReference)
def resolve_reference(reference_id: str) -> PaperReference:
    store = get_store()
    reference = store.find_reference(reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference not found")
    enriched = _crossref_enrich(reference)
    if enriched is not None:
        store.update_reference(enriched)
        return enriched
    return reference


@router.post("/references/{reference_id}/analyze")
def analyze_reference(reference_id: str, request: ReferenceAnalyzeRequest) -> dict:
    store = get_store()
    reference = store.find_reference(reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference not found")
    policy = TraversalPolicy(max_depth=min(request.depth, settings.recursion_max_depth), max_papers=settings.recursion_max_papers)
    mentions = store.get_mentions(reference_id)
    intent_counts: dict[str, int] = {}
    for mention in mentions:
        intent_counts[mention.intent] = intent_counts.get(mention.intent, 0) + 1
    return {
        "reference_id": reference_id,
        "reference": reference.model_dump(),
        "focus": request.focus,
        "mentions": [mention.model_dump() for mention in mentions],
        "intent_summary": intent_counts,
        "can_expand": policy.can_expand(reference_id, request.depth),
        "expand_hint": "Upload the referenced paper to build its own argument graph."
        if policy.can_expand(reference_id, request.depth)
        else "Traversal policy limit reached.",
    }


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

    nodes_by_id = {node.id: node for node in graph.nodes}
    for node_id, similarity in get_store().vector_search(request.paper_id, request.query):
        node = nodes_by_id.get(node_id)
        if node is None or (request.node_types and node.node_type not in request.node_types):
            continue
        if node_id in scored:
            scored[node_id]["score"] += similarity
            scored[node_id]["source"] = "hybrid"
        elif similarity > 0.35:
            scored[node_id] = {"node": node, "score": similarity, "source": "vector"}

    matches = sorted(scored.values(), key=lambda item: item["score"], reverse=True)
    return {
        "query": request.query,
        "matches": [
            {"node": item["node"], "score": round(item["score"], 4), "source": item["source"]}
            for item in matches
        ],
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
    return graph


_MUTABLE_NODE_FIELDS = {"title", "summary", "node_type", "confidence", "verified", "properties"}
_MUTABLE_EDGE_FIELDS = {"edge_type", "confidence", "inference_type", "properties"}


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


def _get_graph(paper_id: str) -> PaperArgumentGraph:
    graph = get_store().get_graph(paper_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return graph
