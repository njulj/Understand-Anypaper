import json

import fitz
from fastapi.testclient import TestClient

from understand_anypaper.api import routes
from understand_anypaper.graph.schema import GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.main import app
from understand_anypaper.parser.models import PageSourceLocation, SemanticUnit
from understand_anypaper.storage import InMemoryGraphStore


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 96), "TinyDemo: contributions and methods.")
    data = doc.tobytes()
    doc.close()
    return data


def _unit(paper_id: str, unit_id: str, role: str, properties: dict | None = None) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id=paper_id,
        role=role,
        title=unit_id,
        text=unit_id,
        source_location=PageSourceLocation(
            page=1, bbox=[0.1, 0.1, 0.2, 0.8], extracted_text=unit_id
        ),
        confidence=0.9,
        properties=properties or {},
    )


def test_upload_paper_streams_progress_and_saves_graph(monkeypatch):
    async def fake_build(self, parsed, *, on_progress=None):
        assert on_progress is not None
        await on_progress(
            {
                "id": "read-1",
                "kind": "read",
                "label": "Read graph.json",
                "path": "graph.json",
            }
        )
        units = [
            _unit(parsed.paper_id, "unit-contribution", "contribution"),
            _unit(parsed.paper_id, "unit-method", "method"),
        ]
        parsed.semantic_units = [
            units[0],
            units[1].model_copy(
                update={"properties": {"contribution_unit_ids": ["unit-contribution"]}}
            ),
        ]
        return PaperArgumentGraph(
            paper_id=parsed.paper_id,
            summary="A concise generated summary for the uploaded paper.",
            nodes=[
                GraphNode(
                    id=f"paper-{parsed.paper_id}",
                    paper_id=parsed.paper_id,
                    node_type=NodeType.PAPER,
                    title=parsed.title,
                )
            ],
        )

    monkeypatch.setattr(routes.PaperGraphAgent, "build", fake_build)
    monkeypatch.setattr(routes, "_store", InMemoryGraphStore())

    with TestClient(app) as client:
        response = client.post(
            "/api/papers",
            files={"file": ("paper.pdf", _pdf_bytes(), "application/pdf")},
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    assert [event["event"] for event in events] == [
        "upload_received",
        "rendered_pages",
        "started_graph_agent",
        "agent_activity",
        "built_argument_graph",
        "saved_graph",
        "complete",
    ]
    graph = events[-1]["graph"]
    assert events[3]["activity"]["label"] == "Read graph.json"
    assert graph["summary"] == "A concise generated summary for the uploaded paper."
    assert graph["nodes"]
    assert routes.get_store().get_graph(graph["paper_id"]) is not None


def test_upload_paper_reports_pipeline_errors_in_stream(monkeypatch):
    async def failing_build(self, parsed, *, on_progress=None):
        raise RuntimeError("graph agent failed")

    monkeypatch.setattr(routes.PaperGraphAgent, "build", failing_build)
    monkeypatch.setattr(routes, "_store", InMemoryGraphStore())

    with TestClient(app) as client:
        response = client.post(
            "/api/papers",
            files={"file": ("paper.pdf", _pdf_bytes(), "application/pdf")},
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    assert events[-1]["event"] == "error"
    assert "graph agent failed" in events[-1]["message"]
