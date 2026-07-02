from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from understand_anypaper.graph.graph_builder import PaperArgumentGraphBuilder
from understand_anypaper.graph.graph_validator import GraphValidator
from understand_anypaper.graph.schema import PaperArgumentGraph
from understand_anypaper.parser.pdf_parser import PdfParser
from understand_anypaper.recursive.traversal_policy import TraversalPolicy

router = APIRouter(prefix="/api")
_PAPERS: dict[str, PaperArgumentGraph] = {}


class ReferenceAnalyzeRequest(BaseModel):
    depth: int = 1
    focus: str = "current_citation_context"


class GraphSearchRequest(BaseModel):
    query: str
    paper_id: str
    node_types: list[str] = []


@router.post("/papers", response_model=PaperArgumentGraph)
async def upload_paper(file: Annotated[UploadFile, File(...)]) -> PaperArgumentGraph:
    suffix = Path(file.filename or "paper.pdf").suffix
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    parsed = PdfParser().parse(tmp_path)
    graph = PaperArgumentGraphBuilder().build(parsed)
    _PAPERS[graph.paper_id] = graph
    tmp_path.unlink(missing_ok=True)
    return graph


@router.get("/papers/{paper_id}/graph", response_model=PaperArgumentGraph)
def get_graph(paper_id: str) -> PaperArgumentGraph:
    return _get_graph(paper_id)


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
def get_node_evidence(node_id: str) -> dict:
    for graph in _PAPERS.values():
        for node in graph.nodes:
            if node.id == node_id:
                return {"node_id": node_id, "evidence_ids": node.evidence_ids, "page_ranges": node.page_ranges}
    raise HTTPException(status_code=404, detail="Node not found")


@router.get("/content/{content_id}/assignments")
def get_content_assignments(content_id: str) -> dict:
    assignments = []
    for graph in _PAPERS.values():
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


@router.post("/references/{reference_id}/resolve")
def resolve_reference(reference_id: str) -> dict:
    return {"reference_id": reference_id, "status": "metadata_resolution_queued"}


@router.post("/references/{reference_id}/analyze")
def analyze_reference(reference_id: str, request: ReferenceAnalyzeRequest) -> dict:
    policy = TraversalPolicy(max_depth=request.depth)
    return {"reference_id": reference_id, "focus": request.focus, "can_expand": policy.can_expand(reference_id, request.depth)}


@router.post("/graph/search")
def search_graph(request: GraphSearchRequest) -> dict:
    graph = _get_graph(request.paper_id)
    matches = [
        node for node in graph.nodes
        if request.query.lower() in f"{node.title} {node.summary}".lower()
        and (not request.node_types or node.node_type in request.node_types)
    ]
    return {"query": request.query, "matches": matches}


@router.get("/papers/{paper_id}/completeness")
def completeness(paper_id: str) -> dict:
    return {"scores": GraphValidator().score_completeness(_get_graph(paper_id))}


def _get_graph(paper_id: str) -> PaperArgumentGraph:
    if paper_id not in _PAPERS:
        raise HTTPException(status_code=404, detail="Paper not found")
    return _PAPERS[paper_id]
