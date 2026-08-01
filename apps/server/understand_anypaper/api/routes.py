import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from difflib import SequenceMatcher
from functools import lru_cache
from html import unescape
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlunparse
from uuid import uuid4

import fitz
import httpx
from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from understand_anypaper.analyzers.citation_contribution_matcher import (
    CitationContributionMatcher,
)
from understand_anypaper.analyzers.paper_graph_agent import AgentProgressCallback, PaperGraphAgent
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


async def _analyze_and_build_graph(
    parsed: ParsedPaper,
    on_progress: AgentProgressCallback | None = None,
) -> PaperArgumentGraph:
    return await PaperGraphAgent().build(parsed, on_progress=on_progress)


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
            activity_queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def report_agent_activity(activity: dict) -> None:
                await activity_queue.put(activity)

            async def build_graph() -> PaperArgumentGraph:
                try:
                    return await _analyze_and_build_graph(parsed, report_agent_activity)
                finally:
                    await activity_queue.put(None)

            graph_task = asyncio.create_task(build_graph())
            try:
                while True:
                    activity = await activity_queue.get()
                    if activity is None:
                        break
                    yield _upload_progress_line(
                        "agent_activity",
                        78,
                        str(activity.get("label", "Graph agent is working.")),
                        activity=activity,
                    )
                graph = await graph_task
            finally:
                if not graph_task.done():
                    graph_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await graph_task
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
    enriched = _resolve_reference_metadata(reference)
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
    return await _expand_node_references_impl(paper_id, node_id, request)


@router.post("/papers/{paper_id}/nodes/{node_id}/references/expand/stream")
async def stream_node_references(
    paper_id: str,
    node_id: str,
    request: NodeReferenceExpansionRequest,
) -> StreamingResponse:
    async def progress_stream():
        progress_queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def report_progress(progress: dict) -> None:
            await progress_queue.put(progress)

        async def run_expansion() -> dict:
            try:
                return await _expand_node_references_impl(
                    paper_id,
                    node_id,
                    request,
                    on_progress=report_progress,
                )
            finally:
                await progress_queue.put(None)

        expansion_task = asyncio.create_task(run_expansion())
        try:
            while True:
                progress = await progress_queue.get()
                if progress is None:
                    break
                yield json.dumps(progress, ensure_ascii=False) + "\n"
            expansion = await expansion_task
            yield json.dumps(
                {
                    "event": "complete",
                    "progress": 100,
                    "message": "Citation analysis complete.",
                    "expansion": expansion,
                },
                ensure_ascii=False,
            ) + "\n"
        except Exception as exc:  # noqa: BLE001 - preserve the NDJSON stream contract
            logger.exception(
                "Citation analysis stream failed paper_id=%s node_id=%s",
                paper_id,
                node_id,
            )
            yield json.dumps(
                {
                    "event": "error",
                    "progress": 100,
                    "message": f"Citation analysis failed: {exc}",
                },
                ensure_ascii=False,
            ) + "\n"
        finally:
            if not expansion_task.done():
                expansion_task.cancel()
                with suppress(asyncio.CancelledError):
                    await expansion_task

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")


async def _emit_citation_progress(
    on_progress: AgentProgressCallback | None,
    event: str,
    progress: int,
    message: str,
    **payload: object,
) -> None:
    if on_progress is None:
        return
    try:
        await on_progress(
            {
                "event": event,
                "progress": max(0, min(100, progress)),
                "message": message,
                **payload,
            }
        )
    except Exception:  # noqa: BLE001 - progress reporting must not stop citation analysis
        logger.exception("Failed to report citation analysis progress")


async def _expand_node_references_impl(
    paper_id: str,
    node_id: str,
    request: NodeReferenceExpansionRequest,
    *,
    on_progress: AgentProgressCallback | None = None,
) -> dict:
    """Resolve a node's citations directly to contributions in referenced papers."""
    trace_id = f"{paper_id[:8]}:{node_id}:{uuid4().hex[:8]}"
    started_at = time.monotonic()
    logger.info(
        "[citation-expand:%s] started paper_id=%s node_id=%s depth=%s",
        trace_id,
        paper_id,
        node_id,
        request.depth,
    )
    await _emit_citation_progress(
        on_progress,
        "started",
        2,
        "Finding citation contexts for the selected node.",
        source_paper_id=paper_id,
        source_node_id=node_id,
    )
    store = get_store()
    graph = await asyncio.to_thread(_get_graph, paper_id)
    source_node = next((node for node in graph.nodes if node.id == node_id), None)
    if source_node is None:
        raise HTTPException(status_code=404, detail="Node not found in the current paper")

    contexts = await asyncio.to_thread(_citation_contexts_for_node, graph, source_node, store)
    scheduled_contexts = contexts[: settings.recursion_max_papers]
    logger.info(
        "[citation-expand:%s] found citation contexts count=%s scheduled=%s limit=%s",
        trace_id,
        len(contexts),
        len(scheduled_contexts),
        settings.recursion_max_papers,
    )
    await _emit_citation_progress(
        on_progress,
        "contexts_found",
        5,
        f"Found {len(scheduled_contexts)} cited paper context(s) to analyze.",
        reference_count=len(scheduled_contexts),
    )
    policy = TraversalPolicy(
        max_depth=min(request.depth, settings.recursion_max_depth),
        max_papers=settings.recursion_max_papers,
    )
    matcher = CitationContributionMatcher()
    results: list[dict] = []
    graph_changed = False

    for context_index, context in enumerate(scheduled_contexts, start=1):
        reference_started_at = time.monotonic()
        reference: PaperReference = context["reference"]
        slot_start = 5 + round(((context_index - 1) / len(scheduled_contexts)) * 85)
        slot_end = 5 + round((context_index / len(scheduled_contexts)) * 85)
        logger.info(
            "[citation-expand:%s] reference %s/%s started reference_id=%s marker=%s arxiv_id=%s doi=%s",
            trace_id,
            context_index,
            len(scheduled_contexts),
            reference.reference_id,
            reference.marker,
            reference.arxiv_id,
            reference.doi,
        )
        await _emit_citation_progress(
            on_progress,
            "reference_started",
            slot_start,
            f"Analyzing cited paper {context_index} of {len(scheduled_contexts)}.",
            reference_id=reference.reference_id,
            reference_marker=reference.marker,
            reference_index=context_index,
            reference_count=len(scheduled_contexts),
        )
        existing = next(
            (
                edge
                for edge in graph.edges
                if edge.source_paper_id == paper_id
                and edge.source_node_id == source_node.id
                and edge.properties.get("cross_paper") is True
                and edge.properties.get("reference_id") == reference.reference_id
            ),
            None,
        )
        if existing is not None:
            logger.info(
                "[citation-expand:%s] reference_id=%s reused existing cross-paper link elapsed=%.2fs",
                trace_id,
                reference.reference_id,
                time.monotonic() - reference_started_at,
            )
            await _emit_citation_progress(
                on_progress,
                "reference_cached",
                slot_end,
                "Reused an existing contribution link.",
                reference_id=reference.reference_id,
                target_paper_id=existing.target_paper_id,
                target_node_id=existing.target_node_id,
            )
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": "cached_link",
                    "target_paper_id": existing.target_paper_id,
                    "target_node_id": existing.target_node_id,
                    "relation_type": str(existing.edge_type),
                    "confidence": existing.confidence,
                }
            )
            continue

        logger.info(
            "[citation-expand:%s] reference_id=%s checking graph cache",
            trace_id,
            reference.reference_id,
        )
        cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)
        if cached is None:
            logger.info(
                "[citation-expand:%s] reference_id=%s cache miss; resolving metadata",
                trace_id,
                reference.reference_id,
            )
            metadata_started_at = time.monotonic()
            await _emit_citation_progress(
                on_progress,
                "resolving_metadata",
                slot_start + round((slot_end - slot_start) * 0.08),
                "Resolving cited-paper metadata.",
                reference_id=reference.reference_id,
            )
            enriched = await asyncio.to_thread(_resolve_reference_metadata, reference)
            logger.info(
                "[citation-expand:%s] reference_id=%s metadata resolved elapsed=%.2fs arxiv_id=%s doi=%s changed=%s",
                trace_id,
                reference.reference_id,
                time.monotonic() - metadata_started_at,
                enriched.arxiv_id,
                enriched.doi,
                enriched != reference,
            )
            if enriched != reference:
                await asyncio.to_thread(store.update_reference, enriched)
                reference = enriched
            cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)
        else:
            logger.info(
                "[citation-expand:%s] reference_id=%s graph cache hit paper_id=%s",
                trace_id,
                reference.reference_id,
                cached.get("paper_id"),
            )

        expansion: dict
        if cached is not None:
            expansion = {
                "status": "cached",
                "paper_id": cached["paper_id"],
                "title": cached["title"],
            }
        elif policy.can_expand(reference.reference_id, request.depth):
            policy.visited_paper_ids.add(reference.reference_id)
            logger.info(
                "[citation-expand:%s] reference_id=%s starting recursive paper analysis",
                trace_id,
                reference.reference_id,
            )
            expansion = await _expand_reference(
                reference,
                store,
                trace_id=trace_id,
                on_progress=on_progress,
                progress_start=slot_start,
                progress_end=slot_end,
            )
        else:
            expansion = {"status": "unavailable", "reason": "Traversal policy limit reached."}
        logger.info(
            "[citation-expand:%s] reference_id=%s expansion finished status=%s target_paper_id=%s elapsed=%.2fs",
            trace_id,
            reference.reference_id,
            expansion.get("status"),
            expansion.get("paper_id"),
            time.monotonic() - reference_started_at,
        )

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
        logger.info(
            "[citation-expand:%s] reference_id=%s matching citation to contributions candidates=%s",
            trace_id,
            reference.reference_id,
            len(target_contributions),
        )
        match_started_at = time.monotonic()
        await _emit_citation_progress(
            on_progress,
            "matching_contribution",
            slot_start + round((slot_end - slot_start) * 0.9),
            "Matching the citation context to a contribution in the cited paper.",
            reference_id=reference.reference_id,
            target_paper_id=target_paper_id,
            candidate_count=len(target_contributions),
        )
        match_heartbeat = asyncio.create_task(
            _log_citation_heartbeat(
                trace_id,
                reference.reference_id,
                "contribution matching",
                match_started_at,
            )
        )
        try:
            match = await matcher.match(
                source_node=source_node,
                citation_context=context["citation_text"],
                reference=reference,
                target_paper_title=target_title,
                candidate_contributions=target_contributions,
            )
        finally:
            match_heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await match_heartbeat
        logger.info(
            "[citation-expand:%s] reference_id=%s contribution match finished matched=%s target_node_id=%s confidence=%.3f elapsed=%.2fs",
            trace_id,
            reference.reference_id,
            match.matched,
            match.target_contribution_node_id,
            match.confidence,
            time.monotonic() - match_started_at,
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
            logger.info(
                "[citation-expand:%s] reference_id=%s left unmatched elapsed=%.2fs",
                trace_id,
                reference.reference_id,
                time.monotonic() - reference_started_at,
            )
            await _emit_citation_progress(
                on_progress,
                "reference_unmatched",
                slot_end,
                "The cited paper was analyzed, but no contribution matched confidently.",
                reference_id=reference.reference_id,
                target_paper_id=target_paper_id,
            )
            results.append(
                {
                    "reference_id": reference.reference_id,
                    "status": "unmatched",
                    "reason": match.rationale,
                    "confidence": match.confidence,
                    "target_paper_id": target_paper_id,
                }
            )
            continue

        edge = GraphEdge(
            id=f"edge-cross-paper-{uuid4()}",
            source_paper_id=paper_id,
            source_node_id=source_node.id,
            target_paper_id=target_paper_id,
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
                "target_paper_title": target_title,
                "target_contribution_title": target_node.title,
                "match_rationale": match.rationale,
            },
        )
        graph.edges.append(edge)
        graph_changed = True
        logger.info(
            "[citation-expand:%s] reference_id=%s linked target_node_id=%s relation=%s elapsed=%.2fs",
            trace_id,
            reference.reference_id,
            target_node.id,
            match.relation_type,
            time.monotonic() - reference_started_at,
        )
        await _emit_citation_progress(
            on_progress,
            "reference_linked",
            slot_end,
            f"Linked citation to contribution: {target_node.title}",
            reference_id=reference.reference_id,
            target_paper_id=target_paper_id,
            target_node_id=target_node.id,
        )
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
        logger.info("[citation-expand:%s] saving updated source graph", trace_id)
        await _emit_citation_progress(
            on_progress,
            "saving_links",
            95,
            "Saving cross-paper contribution links.",
        )
        await asyncio.to_thread(store.replace_graph, paper_id, graph)
    view_graph = await asyncio.to_thread(_materialize_cross_paper_contributions, graph, store)
    logger.info(
        "[citation-expand:%s] completed results=%s graph_changed=%s elapsed=%.2fs",
        trace_id,
        len(results),
        graph_changed,
        time.monotonic() - started_at,
    )
    await _emit_citation_progress(
        on_progress,
        "finished",
        98,
        "Citation links are ready.",
        result_count=len(results),
    )
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
        and edge.target_paper_id == target_paper_id
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
        operation.edge.source_paper_id = graph.paper_id
        operation.edge.target_paper_id = graph.paper_id
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
    references_by_id = {reference.reference_id: reference for reference in references}
    bound_references = [
        references_by_id[reference_id]
        for reference_id in node.reference_ids
        if reference_id in references_by_id
    ]
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

        matched_references = bound_references
        if not matched_references:
            matched_references = []
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
                if marker_matches:
                    matched_references.append(reference)

        for reference in matched_references:
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
    # An arXiv identifier is already enough both to locate a cached graph and to
    # download the paper. Avoid a redundant Semantic Scholar lookup here: this
    # path runs immediately before recursive analysis and unauthenticated Graph
    # API requests are commonly rate limited.
    if reference.arxiv_id:
        return reference
    enriched = _crossref_enrich(reference) or reference
    # A DOI is sufficient for OpenAlex, while a title is sufficient for the
    # official venue + DBLP lookup. Do not make either path depend on Semantic
    # Scholar, whose unauthenticated API is commonly rate limited.
    if enriched.doi or enriched.title:
        return enriched
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
    known_node_keys = {(node.paper_id, node.id) for node in nodes}
    for edge in graph.edges:
        target_key = (edge.target_paper_id, edge.target_node_id)
        if edge.properties.get("cross_paper") is not True or target_key in known_node_keys:
            continue
        target_paper_id = edge.target_paper_id
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
        known_node_keys.add(target_key)
    return graph.model_copy(update={"nodes": nodes})


def _crossref_enrich(reference: PaperReference) -> PaperReference | None:
    """Best-effort metadata enrichment via Crossref. Returns None when unavailable."""
    try:
        headers = {"User-Agent": "Understand-AnyPaper/1.0"}
        if reference.doi:
            response = _http_get_with_retry(
                f"https://api.crossref.org/works/{reference.doi}",
                timeout=8,
                headers=headers,
            )
        else:
            query = reference.title or re.sub(r"\[\d+\]", "", reference.raw_text)[:200]
            response = _http_get_with_retry(
                "https://api.crossref.org/works",
                params={"query.bibliographic": query, "rows": 1},
                timeout=8,
                headers=headers,
            )
        payload = response.json()["message"]
        item = payload["items"][0] if "items" in payload else payload
        if not item:
            return None
        titles = item.get("title") or []
        issued = (item.get("issued", {}).get("date-parts") or [[None]])[0][0]
        if not reference.doi:
            if reference.title and titles and not _titles_match(reference.title, titles[0]):
                logger.info(
                    "Crossref rejected title mismatch for %s: expected=%r candidate=%r",
                    reference.reference_id,
                    reference.title,
                    titles[0],
                )
                return None
            if reference.year and issued and abs(reference.year - int(issued)) > 1:
                logger.info(
                    "Crossref rejected year mismatch for %s: expected=%s candidate=%s",
                    reference.reference_id,
                    reference.year,
                    issued,
                )
                return None

        updated = reference.model_copy()
        if titles:
            updated.title = titles[0]
        updated.doi = item.get("DOI", updated.doi)
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


def _http_get_with_retry(url: str, **kwargs) -> httpx.Response:
    """Retry short-lived catalogue failures without hiding permanent client errors."""
    for attempt in range(3):
        try:
            response = httpx.get(url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            retryable = status == 429 or (status is not None and status >= 500) or status is None
            if not retryable or attempt == 2:
                raise
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError("unreachable")


def _openalex_pdf_urls(reference: PaperReference) -> list[str]:
    """Resolve direct open-access PDF candidates for a DOI via OpenAlex."""
    if not reference.doi:
        return []

    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", reference.doi, flags=re.I)
    work_id = quote(f"doi:{doi.strip()}", safe=":/")
    params: dict[str, str] = {
        "select": "best_oa_location,locations,primary_location,open_access",
    }
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key

    try:
        response = httpx.get(
            f"https://api.openalex.org/works/{work_id}",
            params=params,
            timeout=10,
            headers={"User-Agent": "Understand-AnyPaper/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("OpenAlex PDF resolution failed for %s: %s", reference.reference_id, exc)
        return []
    if not isinstance(payload, dict):
        logger.warning("OpenAlex returned an invalid work payload for %s", reference.reference_id)
        return []

    candidates: list[str] = []

    def add_location(location: object, *, require_open_access: bool = True) -> None:
        if not isinstance(location, dict):
            return
        if require_open_access and location.get("is_oa") is not True:
            return
        pdf_url = location.get("pdf_url")
        if isinstance(pdf_url, str):
            candidates.append(pdf_url)

    # best_oa_location is OpenAlex's preferred OA copy. Other locations are
    # retained as fallbacks because repositories occasionally return stale URLs.
    add_location(payload.get("best_oa_location"), require_open_access=False)
    for location in payload.get("locations") or []:
        add_location(location)
    add_location(payload.get("primary_location"))

    open_access = payload.get("open_access")
    if isinstance(open_access, dict) and open_access.get("is_oa") is True:
        oa_url = open_access.get("oa_url")
        if isinstance(oa_url, str):
            candidates.append(oa_url)

    normalized: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate.startswith(("http://", "https://")):
            continue
        arxiv_match = re.match(
            r"https?://(?:export\.)?arxiv\.org/abs/([^?#]+)", candidate, flags=re.I
        )
        if arxiv_match:
            candidate = f"https://arxiv.org/pdf/{arxiv_match.group(1)}.pdf"
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized[:6]


def _titles_match(left: str, right: str) -> bool:
    """Conservatively match a citation title to a catalogue result."""
    compact_left = re.sub(r"\W+", "", unescape(left)).casefold()
    compact_right = re.sub(r"\W+", "", unescape(right)).casefold()
    if compact_left and compact_left == compact_right:
        return True
    normalized_left = _normalize_title(left)
    normalized_right = _normalize_title(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    shorter, longer = sorted((normalized_left, normalized_right), key=len)
    if len(shorter.split()) >= 5 and shorter in longer:
        return True
    left_tokens = set(normalized_left.split())
    right_tokens = set(normalized_right.split())
    token_overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return token_overlap >= 0.9 and SequenceMatcher(
        None, normalized_left, normalized_right
    ).ratio() >= 0.94


def _reference_http_urls(reference: PaperReference) -> list[str]:
    text = unescape(" ".join(filter(None, (reference.raw_text, reference.title))))
    urls: list[str] = []
    for match in re.findall(r"https?://[^\s<>\"'\]]+", text, flags=re.I):
        candidate = match.rstrip(".,;:)}]")
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _dblp_publication_urls(reference: PaperReference) -> list[str]:
    """Find exact-title publication landing pages via DBLP's public search API."""
    if not reference.title:
        return []
    query_title = re.sub(r"(?<=\w)-\s+(?=\w)", "-", reference.title)
    first_author = reference.authors[0] if reference.authors else ""
    query = " ".join(part for part in (query_title, first_author) if part)
    try:
        response = _http_get_with_retry(
            "https://dblp.org/search/publ/api",
            params={"q": query, "format": "json", "h": "20"},
            timeout=10,
            headers={"User-Agent": "Understand-AnyPaper/1.0"},
        )
        hits = response.json().get("result", {}).get("hits", {}).get("hit", [])
    except (httpx.HTTPError, AttributeError, ValueError, TypeError) as exc:
        logger.warning("DBLP venue resolution failed for %s: %s", reference.reference_id, exc)
        return []
    if isinstance(hits, dict):
        hits = [hits]
    if not isinstance(hits, list):
        return []

    matches: list[tuple[int, dict]] = []
    for hit in hits:
        info = hit.get("info") if isinstance(hit, dict) else None
        if not isinstance(info, dict) or not _titles_match(
            reference.title, str(info.get("title", ""))
        ):
            continue
        year_score = 0
        try:
            result_year = int(info.get("year"))
            if reference.year is not None:
                year_score = max(0, 2 - abs(result_year - reference.year))
        except (TypeError, ValueError):
            pass
        matches.append((year_score, info))
    matches.sort(key=lambda item: item[0], reverse=True)

    urls: list[str] = []
    for _score, info in matches[:3]:
        external = info.get("ee") or []
        if isinstance(external, str):
            external = [external]
        if isinstance(external, list):
            urls.extend(url for url in external if isinstance(url, str))
        doi = info.get("doi")
        if isinstance(doi, str):
            urls.append(f"https://doi.org/{doi}")
    return list(dict.fromkeys(urls))


def _acl_anthology_pdf_url(candidate: str) -> str | None:
    parsed = urlparse(candidate)
    host = parsed.netloc.casefold().split(":", 1)[0]
    path = unquote(parsed.path).strip("/")
    if host in {"doi.org", "dx.doi.org"}:
        match = re.match(r"10\.18653/v1/(.+)", path, flags=re.I)
        if not match:
            return None
        path = match.group(1)
    elif host not in {"aclanthology.org", "www.aclanthology.org"}:
        return None
    if not path or path.startswith(("info/", "search/", "people/", "events/", "volumes/")):
        return None
    if path.casefold().endswith(".pdf"):
        return f"https://aclanthology.org/{path}"
    return f"https://aclanthology.org/{path.rstrip('/')}.pdf"


def _cvf_pdf_url(candidate: str) -> str | None:
    parsed = urlparse(candidate)
    if parsed.netloc.casefold().split(":", 1)[0] != "openaccess.thecvf.com":
        return None
    path = parsed.path
    if path.casefold().endswith(".pdf"):
        pdf_path = path
    elif "/html/" in path and path.casefold().endswith(".html"):
        pdf_path = path.replace("/html/", "/papers/", 1)[:-5] + ".pdf"
    else:
        return None
    return urlunparse(("https", "openaccess.thecvf.com", pdf_path, "", "", ""))


def _html_anchors(document: str) -> list[tuple[str, str]]:
    anchors: list[tuple[str, str]] = []
    for href, label in re.findall(
        r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        document,
        flags=re.I | re.S,
    ):
        text = unescape(re.sub(r"<[^>]+>", " ", label))
        anchors.append((unescape(href), re.sub(r"\s+", " ", text).strip()))
    return anchors


@lru_cache(maxsize=24)
def _cvf_index_anchors(index_url: str) -> list[tuple[str, str]]:
    try:
        response = _http_get_with_retry(
            index_url,
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Understand-AnyPaper/1.0"},
        )
        return _html_anchors(response.text)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("CVF index lookup failed url=%s error=%s", index_url, exc)
        return []


def _cvf_index_pdf_urls(reference: PaperReference) -> list[str]:
    """Resolve older CVF papers whose DBLP record exposes only the IEEE DOI."""
    if not reference.title or not reference.year:
        return []
    venue_match = re.search(r"\b(CVPR|ICCV|WACV|ECCV)(W|\s+WORKSHOPS?)?\b", reference.raw_text, re.I)
    if not venue_match:
        return []
    venue = venue_match.group(1).upper()
    is_workshop = bool(venue_match.group(2))
    root = f"https://openaccess.thecvf.com/{venue}{reference.year}"
    indexes = [f"{root}?day=all"]
    if is_workshop:
        menu_url = f"{root}_workshops/menu"
        title_tokens = {
            token
            for token in _normalize_title(reference.title).split()
            if len(token) >= 5
        }
        matching_indexes = [
            urljoin(menu_url, href)
            for href, label in _cvf_index_anchors(menu_url)
            if title_tokens & set(_normalize_title(label).split())
        ]
        indexes = matching_indexes[:5]

    candidates: list[str] = []
    for index_url in indexes:
        for href, label in _cvf_index_anchors(index_url):
            if not _titles_match(reference.title, label):
                continue
            if href.startswith("content_"):
                href = f"/{href}"
            pdf_url = _cvf_pdf_url(urljoin(index_url, href))
            if pdf_url and pdf_url not in candidates:
                candidates.append(pdf_url)
    return candidates[:3]


def _pmlr_pdf_url(candidate: str) -> str | None:
    parsed = urlparse(candidate)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if host not in {"proceedings.mlr.press", "www.proceedings.mlr.press"}:
        return None
    path = parsed.path
    if path.casefold().endswith(".pdf"):
        pdf_path = path
    elif path.casefold().endswith(".html"):
        paper_id = Path(path).stem
        pdf_path = f"{path[:-5]}/{paper_id}.pdf"
    else:
        return None
    return urlunparse(("https", "proceedings.mlr.press", pdf_path, "", "", ""))


def _openreview_pdf_url(candidate: str) -> str | None:
    parsed = urlparse(candidate)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if host not in {"openreview.net", "www.openreview.net"}:
        return None
    note_id = (parse_qs(parsed.query).get("id") or [None])[0]
    if not note_id:
        return None
    return urlunparse(
        ("https", "openreview.net", "/pdf", "", urlencode({"id": note_id}), "")
    )


def _arxiv_pdf_url(candidate: str) -> str | None:
    parsed = urlparse(candidate)
    host = parsed.netloc.casefold().split(":", 1)[0]
    path = unquote(parsed.path).strip("/")
    if host in {"doi.org", "dx.doi.org"}:
        match = re.match(r"10\.48550/arxiv\.(.+)", path, flags=re.I)
        if not match:
            return None
        arxiv_id = match.group(1)
    elif host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        match = re.match(r"(?:abs|pdf)/(.+)$", path, flags=re.I)
        if not match:
            return None
        arxiv_id = re.sub(r"\.pdf$", "", match.group(1), flags=re.I)
    else:
        return None
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def _official_venue_pdf_candidates(
    reference: PaperReference,
    publication_urls: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Return direct PDFs from supported proceedings sites and known arXiv mirrors."""
    inputs = [*_reference_http_urls(reference), *(publication_urls or [])]
    if reference.doi:
        doi = re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", reference.doi, flags=re.I
        )
        inputs.append(f"https://doi.org/{doi.strip()}")

    resolvers = (
        ("ACL Anthology", _acl_anthology_pdf_url),
        ("CVF Open Access", _cvf_pdf_url),
        ("PMLR", _pmlr_pdf_url),
        ("OpenReview", _openreview_pdf_url),
        ("arXiv mirror", _arxiv_pdf_url),
    )
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in inputs:
        for source, resolver in resolvers:
            pdf_url = resolver(candidate)
            if pdf_url and pdf_url not in seen:
                candidates.append((source, pdf_url))
                seen.add(pdf_url)
                break
    return candidates


async def _download_reference_pdf(
    reference: PaperReference,
    *,
    trace_id: str,
    on_progress: AgentProgressCallback | None,
    progress: int,
) -> tuple[bytes, str] | None:
    headers = {
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "User-Agent": "Understand-AnyPaper/1.0",
    }

    async def try_candidates(
        client: httpx.AsyncClient,
        candidates: list[tuple[str, str]],
    ) -> tuple[bytes, str] | None:
        for source, candidate in candidates:
            try:
                response = await client.get(candidate)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(
                    "[citation-expand:%s] reference_id=%s %s PDF candidate failed "
                    "url=%s error=%s",
                    trace_id,
                    reference.reference_id,
                    source,
                    candidate,
                    exc,
                )
                continue
            data = response.content
            if data.startswith(b"%PDF"):
                logger.info(
                    "[citation-expand:%s] reference_id=%s downloaded PDF via %s url=%s",
                    trace_id,
                    reference.reference_id,
                    source,
                    response.url,
                )
                return data, str(response.url)
            logger.warning(
                "[citation-expand:%s] reference_id=%s %s candidate returned non-PDF "
                "url=%s content_type=%s bytes=%s",
                trace_id,
                reference.reference_id,
                source,
                candidate,
                response.headers.get("content-type"),
                len(data),
            )
        return None

    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers=headers) as client:
        if reference.arxiv_id:
            return await try_candidates(
                client,
                [("arXiv", f"https://arxiv.org/pdf/{reference.arxiv_id}.pdf")],
            )

        await _emit_citation_progress(
            on_progress,
            "resolving_open_access_pdf",
            progress,
            "Checking ACL Anthology, CVF, PMLR, and OpenReview for an official PDF.",
            reference_id=reference.reference_id,
            doi=reference.doi,
        )
        direct_candidates = _official_venue_pdf_candidates(reference)
        downloaded = await try_candidates(client, direct_candidates)
        if downloaded is not None:
            return downloaded

        publication_urls = await asyncio.to_thread(_dblp_publication_urls, reference)
        discovered_candidates = _official_venue_pdf_candidates(reference, publication_urls)
        direct_urls = {url for _source, url in direct_candidates}
        discovered_candidates = [
            candidate for candidate in discovered_candidates if candidate[1] not in direct_urls
        ]
        downloaded = await try_candidates(client, discovered_candidates)
        if downloaded is not None:
            return downloaded

        cvf_urls = await asyncio.to_thread(_cvf_index_pdf_urls, reference)
        downloaded = await try_candidates(
            client,
            [("CVF Open Access", url) for url in cvf_urls],
        )
        if downloaded is not None:
            return downloaded

        if reference.doi:
            await _emit_citation_progress(
                on_progress,
                "resolving_open_access_pdf",
                progress,
                "No official proceedings PDF matched; trying OpenAlex repositories.",
                reference_id=reference.reference_id,
                doi=reference.doi,
            )
            openalex_urls = await asyncio.to_thread(_openalex_pdf_urls, reference)
            downloaded = await try_candidates(
                client,
                [("OpenAlex open-access", url) for url in openalex_urls],
            )
            if downloaded is not None:
                return downloaded

    logger.warning(
        "[citation-expand:%s] reference_id=%s no usable official or open-access PDF found",
        trace_id,
        reference.reference_id,
    )
    return None


async def _log_citation_heartbeat(
    trace_id: str,
    reference_id: str,
    stage: str,
    started_at: float,
) -> None:
    while True:
        await asyncio.sleep(30)
        logger.info(
            "[citation-expand:%s] reference_id=%s stage=%s still running elapsed=%.2fs",
            trace_id,
            reference_id,
            stage,
            time.monotonic() - started_at,
        )


async def _expand_reference(
    reference: PaperReference,
    store: GraphStore,
    *,
    trace_id: str | None = None,
    on_progress: AgentProgressCallback | None = None,
    progress_start: int = 10,
    progress_end: int = 90,
) -> dict:
    log_id = trace_id or reference.reference_id
    started_at = time.monotonic()

    def stage_progress(fraction: float) -> int:
        return progress_start + round((progress_end - progress_start) * fraction)

    logger.info(
        "[citation-expand:%s] reference_id=%s recursive expansion entered",
        log_id,
        reference.reference_id,
    )
    cached = await asyncio.to_thread(_find_cached_reference_graph, reference, store)
    if cached:
        logger.info(
            "[citation-expand:%s] reference_id=%s recursive cache hit paper_id=%s",
            log_id,
            reference.reference_id,
            cached.get("paper_id"),
        )
        return {"status": "cached", "paper_id": cached["paper_id"], "title": cached["title"]}
    download_started_at = time.monotonic()
    logger.info(
        "[citation-expand:%s] reference_id=%s resolving and downloading PDF "
        "arxiv_id=%s doi=%s title=%r timeout=40s",
        log_id,
        reference.reference_id,
        reference.arxiv_id,
        reference.doi,
        reference.title,
    )
    await _emit_citation_progress(
        on_progress,
        "downloading_reference",
        stage_progress(0.12),
        (
            f"Downloading arXiv:{reference.arxiv_id}."
            if reference.arxiv_id
            else "Finding an official or open-access PDF for the cited paper."
        ),
        reference_id=reference.reference_id,
        arxiv_id=reference.arxiv_id,
        doi=reference.doi,
    )
    downloaded = await _download_reference_pdf(
        reference,
        trace_id=log_id,
        on_progress=on_progress,
        progress=stage_progress(0.14),
    )
    if downloaded is None:
        return {
            "status": "unavailable",
            "reason": (
                "No downloadable PDF was found via arXiv, ACL Anthology, CVF, PMLR, "
                "OpenReview, or OpenAlex."
            ),
        }
    data, downloaded_url = downloaded
    logger.info(
        "[citation-expand:%s] reference_id=%s PDF downloaded url=%s bytes=%s elapsed=%.2fs",
        log_id,
        reference.reference_id,
        downloaded_url,
        len(data),
        time.monotonic() - download_started_at,
    )
    await _emit_citation_progress(
        on_progress,
        "reference_downloaded",
        stage_progress(0.25),
        f"Downloaded cited paper ({len(data) / 1024 / 1024:.1f} MB).",
        reference_id=reference.reference_id,
        downloaded_bytes=len(data),
    )
    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    parse_started_at = time.monotonic()
    logger.info(
        "[citation-expand:%s] reference_id=%s parsing downloaded PDF",
        log_id,
        reference.reference_id,
    )
    await _emit_citation_progress(
        on_progress,
        "parsing_reference",
        stage_progress(0.3),
        "Rendering and parsing the cited paper.",
        reference_id=reference.reference_id,
    )
    try:
        parsed = await asyncio.to_thread(PdfParser().parse, tmp_path)
    except Exception as exc:  # noqa: BLE001 - reference expansion should not break citation analysis
        logger.warning("Recursive reference parse failed for %s: %s", reference.reference_id, exc)
        return {"status": "failed", "reason": f"Failed to parse referenced PDF: {exc}"}
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info(
        "[citation-expand:%s] reference_id=%s PDF parsed paper_id=%s pages=%s blocks=%s references=%s elapsed=%.2fs",
        log_id,
        reference.reference_id,
        parsed.paper_id,
        len(parsed.pages),
        len(parsed.source_blocks),
        len(parsed.references),
        time.monotonic() - parse_started_at,
    )
    await _emit_citation_progress(
        on_progress,
        "reference_parsed",
        stage_progress(0.4),
        f"Parsed {len(parsed.pages)} page(s); starting its argument graph.",
        reference_id=reference.reference_id,
        target_paper_id=parsed.paper_id,
        page_count=len(parsed.pages),
        block_count=len(parsed.source_blocks),
    )

    parsed.metadata.update(
        {
            "source_reference_id": reference.reference_id,
            "source_arxiv_id": reference.arxiv_id,
            "source_doi": reference.doi,
            "source_url": downloaded_url,
            "source_filename": f"{reference.arxiv_id or parsed.paper_id}.pdf",
            "source_media_type": "application/pdf",
        }
    )
    agent_started_at = time.monotonic()
    logger.info(
        "[citation-expand:%s] reference_id=%s starting graph agent paper_id=%s model=%s request_timeout=%ss",
        log_id,
        reference.reference_id,
        parsed.paper_id,
        settings.openai_model,
        settings.llm_request_timeout_seconds,
    )
    await _emit_citation_progress(
        on_progress,
        "started_graph_agent",
        stage_progress(0.45),
        "Started the cited-paper graph authoring agent.",
        reference_id=reference.reference_id,
        target_paper_id=parsed.paper_id,
    )

    async def log_agent_activity(activity: dict) -> None:
        logger.info(
            "[citation-expand:%s] reference_id=%s graph-agent kind=%s label=%s",
            log_id,
            reference.reference_id,
            activity.get("kind"),
            activity.get("label"),
        )
        activity_payload = {
            **activity,
            "id": f"{reference.reference_id}:{activity.get('id', 'activity')}",
            "reference_id": reference.reference_id,
            "target_paper_id": parsed.paper_id,
        }
        await _emit_citation_progress(
            on_progress,
            "agent_activity",
            stage_progress(0.65),
            str(activity.get("label") or "Cited-paper graph agent is working."),
            reference_id=reference.reference_id,
            target_paper_id=parsed.paper_id,
            activity=activity_payload,
        )

    agent_heartbeat = asyncio.create_task(
        _log_citation_heartbeat(
            log_id,
            reference.reference_id,
            "graph agent",
            agent_started_at,
        )
    )
    try:
        graph = await _analyze_and_build_graph(parsed, log_agent_activity)
    except Exception as exc:  # noqa: BLE001 - reference expansion reports failures per reference
        logger.exception(
            "[citation-expand:%s] reference_id=%s graph agent failed elapsed=%.2fs",
            log_id,
            reference.reference_id,
            time.monotonic() - agent_started_at,
        )
        return {"status": "failed", "reason": str(exc)}
    finally:
        agent_heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await agent_heartbeat
    logger.info(
        "[citation-expand:%s] reference_id=%s graph agent finished nodes=%s edges=%s elapsed=%.2fs",
        log_id,
        reference.reference_id,
        len(graph.nodes),
        len(graph.edges),
        time.monotonic() - agent_started_at,
    )
    await _emit_citation_progress(
        on_progress,
        "reference_graph_built",
        stage_progress(0.85),
        f"Built cited-paper graph with {len(graph.nodes)} nodes.",
        reference_id=reference.reference_id,
        target_paper_id=parsed.paper_id,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
    )
    logger.info(
        "[citation-expand:%s] reference_id=%s saving analyzed paper and source document",
        log_id,
        reference.reference_id,
    )
    await asyncio.to_thread(store.save_paper, parsed, graph)
    await asyncio.to_thread(
        store.save_source_document,
        parsed.paper_id,
        f"{reference.arxiv_id or parsed.paper_id}.pdf",
        "application/pdf",
        data,
    )
    logger.info(
        "[citation-expand:%s] reference_id=%s recursive expansion saved elapsed=%.2fs",
        log_id,
        reference.reference_id,
        time.monotonic() - started_at,
    )
    await _emit_citation_progress(
        on_progress,
        "reference_saved",
        stage_progress(0.88),
        "Saved the cited paper and its source document.",
        reference_id=reference.reference_id,
        target_paper_id=parsed.paper_id,
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
    dehyphenated = re.sub(r"(?<=\w)-\s+(?=\w)", "-", title)
    return re.sub(r"\W+", " ", dehyphenated).strip().casefold()


def _get_graph(paper_id: str) -> PaperArgumentGraph:
    graph = get_store().get_graph(paper_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return graph


def _is_pdf_media_type(media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() == "application/pdf"
