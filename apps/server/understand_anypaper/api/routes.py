import asyncio
import json
import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote

import fitz
import httpx
from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from understand_anypaper.analyzers.contribution_evidence_assigner import (
    ContributionEvidenceAssigner,
    ContributionEvidenceAssignmentError,
)
from understand_anypaper.analyzers.semantic_unit_slicer import SemanticUnitSlicer
from understand_anypaper.config import settings
from understand_anypaper.graph.graph_builder import GraphBuildError, PaperArgumentGraphBuilder
from understand_anypaper.graph.graph_validator import GraphValidator
from understand_anypaper.graph.schema import GraphEdge, GraphNode, PaperArgumentGraph
from understand_anypaper.parser.models import PaperReference, ParsedPaper, SemanticUnit, SourceBlock
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


def _analyze_and_build_graph(parsed: ParsedPaper) -> PaperArgumentGraph:
    units = SemanticUnitSlicer().slice_semantic_units(parsed)
    if not units:
        raise GraphBuildError("LLM semantic slicing is required to build a Paper Argument Graph")
    parsed.semantic_units = ContributionEvidenceAssigner().assign(parsed, units)
    return PaperArgumentGraphBuilder().build(parsed)


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
    suffix = Path(file.filename or "paper.pdf").suffix
    media_type = "application/pdf" if suffix.lower() == ".pdf" else (file.content_type or "application/octet-stream")
    data = await file.read()
    filename = file.filename or f"paper{suffix}"

    async def progress_stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def emit(event: str, progress: int, message: str, **payload: object) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                _upload_progress_line(event, progress, message, **payload),
            )

        def finish() -> None:
            loop.call_soon_threadsafe(queue.put_nowait, None)

        def run_pipeline() -> None:
            tmp_path: Path | None = None
            try:
                emit("upload_received", 60, "Upload received. Parsing source blocks.")
                with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(data)
                    tmp_path = Path(tmp.name)

                try:
                    parsed = PdfParser().parse(tmp_path)
                except Exception as exc:  # noqa: BLE001 - surface parse failures in the progress stream
                    emit("error", 100, f"Failed to parse document: {exc}")
                    return
                finally:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path = None

                parsed.metadata.update(
                    {
                        "source_filename": filename,
                        "source_media_type": media_type,
                    }
                )
                emit(
                    "parsed_source_blocks",
                    68,
                    "Parsed source blocks.",
                    source_block_count=len(parsed.source_blocks),
                )

                units = SemanticUnitSlicer().slice_semantic_units(parsed)
                if not units:
                    raise GraphBuildError("LLM semantic slicing is required to build a Paper Argument Graph")
                emit(
                    "generated_semantic_units",
                    78,
                    "Generated semantic units.",
                    semantic_unit_count=len(units),
                )

                parsed.semantic_units = ContributionEvidenceAssigner().assign(parsed, units)
                emit(
                    "assigned_contribution_evidence",
                    86,
                    "Connected evidence to contributions.",
                    semantic_unit_count=len(parsed.semantic_units),
                )

                graph = PaperArgumentGraphBuilder().build(parsed)
                emit(
                    "built_argument_graph",
                    94,
                    "Built the argument graph.",
                    node_count=len(graph.nodes),
                    edge_count=len(graph.edges),
                )

                store = get_store()
                store.save_paper(parsed, graph)
                if suffix.lower() == ".pdf":
                    store.save_source_document(
                        parsed.paper_id,
                        filename,
                        media_type,
                        data,
                    )
                emit("saved_graph", 98, "Saved graph and source document.")
                emit(
                    "complete",
                    100,
                    "Graph ready.",
                    graph=graph.model_dump(mode="json"),
                )
            except (ContributionEvidenceAssignmentError, GraphBuildError) as exc:
                emit("error", 100, str(exc))
            except Exception as exc:  # noqa: BLE001 - preserve the progress stream contract for unexpected failures
                logger.exception("Unexpected paper upload failure")
                emit("error", 100, f"Upload failed: {exc}")
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                finish()

        pipeline_task = asyncio.create_task(asyncio.to_thread(run_pipeline))
        try:
            while True:
                line = await queue.get()
                if line is None:
                    break
                yield line
            await pipeline_task
        finally:
            if not pipeline_task.done():
                pipeline_task.cancel()

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
    return _get_graph(paper_id)


@router.get("/papers/{paper_id}/blocks", response_model=list[SourceBlock])
def get_blocks(paper_id: str) -> list[SourceBlock]:
    _get_graph(paper_id)
    return get_store().get_blocks(paper_id)


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
        blocks = {block.source_block_id: block for block in store.get_blocks(pid)}
        semantic_units = {unit.semantic_unit_id: unit for unit in store.get_semantic_units(pid)}
        evidence = [
            {
                "semantic_unit_id": semantic_unit_id,
                "role": semantic_units[semantic_unit_id].role if semantic_unit_id in semantic_units else None,
                "title": semantic_units[semantic_unit_id].title if semantic_unit_id in semantic_units else None,
                "text": semantic_units[semantic_unit_id].text if semantic_unit_id in semantic_units else None,
                "source_ranges": [
                    {
                        **source_range.model_dump(),
                        "page": blocks[source_range.source_block_id].page
                        if source_range.source_block_id in blocks else None,
                        "bbox": blocks[source_range.source_block_id].bbox
                        if source_range.source_block_id in blocks else None,
                        "source_text": blocks[source_range.source_block_id].text
                        if source_range.source_block_id in blocks else None,
                    }
                    for source_range in semantic_units[semantic_unit_id].source_ranges
                ] if semantic_unit_id in semantic_units else [],
            }
            for semantic_unit_id in node.semantic_unit_ids
        ]
        return {
            "node_id": node_id,
            "paper_id": pid,
            "semantic_unit_ids": node.semantic_unit_ids,
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
            if content_id in edge.semantic_unit_ids and edge.target_node_id.startswith("contribution-")
        )
    return {"content_id": content_id, "assignments": assignments}


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
    can_expand = policy.can_expand(reference_id, request.depth)
    expansion = _expand_reference(reference, store) if request.expand and can_expand else None
    return {
        "reference_id": reference_id,
        "reference": reference.model_dump(),
        "focus": request.focus,
        "mentions": [mention.model_dump() for mention in mentions],
        "intent_summary": intent_counts,
        "can_expand": can_expand,
        "expansion": expansion,
        "expand_hint": "Upload the referenced paper to build its own argument graph."
        if can_expand
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
    return graph


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


def _expand_reference(reference: PaperReference, store: GraphStore) -> dict:
    cached = _find_cached_reference_graph(reference, store)
    if cached:
        return {"status": "cached", "paper_id": cached["paper_id"], "title": cached["title"]}
    if not reference.arxiv_id:
        return {"status": "unavailable", "reason": "No arXiv identifier or downloadable PDF is known."}

    url = f"https://arxiv.org/pdf/{reference.arxiv_id}.pdf"
    try:
        response = httpx.get(url, timeout=40, follow_redirects=True)
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
        parsed = PdfParser().parse(tmp_path)
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
        graph = _analyze_and_build_graph(parsed)
    except (ContributionEvidenceAssignmentError, GraphBuildError) as exc:
        return {"status": "failed", "reason": str(exc)}
    store.save_paper(parsed, graph)
    store.save_source_document(parsed.paper_id, f"{reference.arxiv_id}.pdf", "application/pdf", data)
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
