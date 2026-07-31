import asyncio
import json
from types import SimpleNamespace

from understand_anypaper.analyzers.paper_graph_agent import PaperGraphAgent
from understand_anypaper.config import Settings
from understand_anypaper.graph.agent_workspace import AgentGraphWorkspace
from understand_anypaper.parser.models import (
    DocumentPage,
    ParsedPaper,
    SourceBlock,
    SourceBlockSpan,
)


def _parsed() -> ParsedPaper:
    text = "We introduce ExactGraph, a precise argument graph method."
    return ParsedPaper(
        paper_id="paper-test",
        title="ExactGraph",
        pages=[DocumentPage(page=1, width=100, height=100, image_data=b"png")],
        source_blocks=[
            SourceBlock(
                block_id="p0001-b0001",
                page=1,
                text=text,
                bbox=[0.1, 0.1, 0.2, 0.9],
                spans=[
                    SourceBlockSpan(
                        start_offset=0,
                        end_offset=len(text),
                        bbox=[0.1, 0.1, 0.2, 0.9],
                    )
                ],
            )
        ],
        source_bytes=b"%PDF-demo",
        source_media_type="application/pdf",
    )


def _valid_graph() -> dict:
    paper_id = "paper-test"
    location = {"block_id": "p0001-b0001", "start_offset": 0, "end_offset": 56}
    nodes = [
        {"id": "paper", "paper_id": paper_id, "node_type": "Paper", "title": "ExactGraph"},
        {
            "id": "contribution",
            "paper_id": paper_id,
            "node_type": "Contribution",
            "title": "Exact graph grounding",
            "properties": {
                "source_location": location,
                "citation_markers": ["[1]"],
            },
        },
        {"id": "why", "paper_id": paper_id, "node_type": "Why", "title": "Why"},
        {"id": "how", "paper_id": paper_id, "node_type": "How", "title": "How"},
        {"id": "proof", "paper_id": paper_id, "node_type": "Proof", "title": "Proof"},
    ]
    edges = [
        {
            "id": "paper-contribution",
            "paper_id": paper_id,
            "source_node_id": "paper",
            "target_node_id": "contribution",
            "edge_type": "HAS_CONTRIBUTION",
        },
        *[
            {
                "id": f"contribution-{facet}",
                "paper_id": paper_id,
                "source_node_id": "contribution",
                "target_node_id": facet,
                "edge_type": "CONTAINS",
            }
            for facet in ("why", "how", "proof")
        ],
    ]
    return {"paper_id": paper_id, "nodes": nodes, "edges": edges}


def test_workspace_validates_exact_block_offsets_and_materializes_bbox(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    workspace.graph_path.write_text(json.dumps(_valid_graph()), encoding="utf-8")

    report = workspace.validate()
    graph = workspace.materialize()

    assert report.valid
    contribution = next(node for node in graph.nodes if node.id == "contribution")
    assert contribution.semantic_unit_ids == ["contribution"]
    assert contribution.properties["source_location"]["block_id"] == "p0001-b0001"
    assert contribution.properties["source_location"]["bbox"] == [0.1, 0.1, 0.2, 0.9]
    semantic_unit = workspace.parsed.semantic_units[0]
    assert semantic_unit.source_location.extraction_method == "block_offset"
    assert semantic_unit.properties["citation_markers"] == ["[1]"]


def test_workspace_rejects_quote_and_out_of_range_offset(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    graph = _valid_graph()
    graph["nodes"][1]["properties"]["source_location"] = {
        "block_id": "p0001-b0001",
        "start_offset": 0,
        "end_offset": 999,
        "quote": "not allowed",
    }
    workspace.graph_path.write_text(json.dumps(graph), encoding="utf-8")

    codes = {issue.code for issue in workspace.validate().errors}

    assert "noncanonical_source_location" in codes
    assert "offset_out_of_range" in codes


def test_search_replace_can_disable_checks_and_normal_edits_report_problems(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()

    disabled = json.loads(
        workspace.search_replace(
            "graph.json",
            '"title": "ExactGraph"',
            '"title": "Changed"',
            disable_checks=True,
        )
    )
    checked = json.loads(
        workspace.search_replace(
            "graph.json",
            '"title": "Changed"',
            '"title": "Changed again"',
        )
    )

    assert disabled == {"ok": True, "checks_disabled": True}
    assert checked["validation"]["valid"] is False
    assert any(issue["code"] == "missing_contribution" for issue in checked["validation"]["errors"])


def test_custom_apply_patch_accepts_raw_patch_and_reports_validation(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()

    result = json.loads(
        workspace.apply_patch(
            """disable_checks=false
*** Begin Patch
*** Update File: graph.json
@@
-      "title": "ExactGraph",
+      "title": "Patched",
*** End Patch"""
        )
    )

    assert result["ok"] is True
    assert result["validation"]["valid"] is False
    assert '"title": "Patched"' in workspace.graph_path.read_text(encoding="utf-8")


def test_model_family_routing():
    assert PaperGraphAgent.is_gpt_model("gpt-5-mini")
    assert PaperGraphAgent.is_gpt_model("openai/gpt-5")
    assert not PaperGraphAgent.is_gpt_model("google/gemini-3-flash-preview")


def test_responses_loop_executes_raw_custom_apply_patch(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    old_lines = workspace.graph_path.read_text(encoding="utf-8").splitlines()
    new_lines = json.dumps(_valid_graph(), indent=2).splitlines()
    patch = "\n".join(
        [
            "disable_checks=false",
            "*** Begin Patch",
            "*** Update File: graph.json",
            "@@",
            *[f"-{line}" for line in old_lines],
            *[f"+{line}" for line in new_lines],
            "*** End Patch",
        ]
    )

    class FakeResponses:
        def __init__(self):
            self.requests = []

        async def create(self, **request):
            self.requests.append(request)
            if len(self.requests) == 1:
                output = [
                    SimpleNamespace(
                        type="custom_tool_call",
                        name="apply_patch",
                        input=patch,
                        call_id="call-patch",
                    )
                ]
            else:
                output = []
            return SimpleNamespace(id=f"response-{len(self.requests)}", output=output)

    responses = FakeResponses()
    fake_client = SimpleNamespace(client=SimpleNamespace(responses=responses))
    agent = PaperGraphAgent(
        Settings(
            openai_api_key="test",
            openai_base_url="https://api.openai.com/v1",
            openai_model="gpt-5-mini",
            graph_agent_max_turns=3,
        ),
        responses_client=fake_client,
    )

    asyncio.run(agent._run_responses(workspace))

    assert workspace.validate().valid
    assert any(tool["type"] == "custom" for tool in responses.requests[0]["tools"])
    assert responses.requests[1]["input"][0] == {
        "type": "custom_tool_call_output",
        "call_id": "call-patch",
        "output": workspace.edit_response(disable_checks=False),
    }


def test_chat_completions_read_defers_image_for_middleware(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    workspace.graph_path.write_text(json.dumps(_valid_graph()), encoding="utf-8")

    deferred_images = []
    agent = PaperGraphAgent(Settings())
    read = next(
        tool
        for tool in agent._framework_tools(
            workspace,
            include_search_replace=True,
            deferred_images=deferred_images,
        )
        if tool.name == "Read"
    )

    result = asyncio.run(read.invoke(arguments={"path": "rendered/1.png"}))

    assert "attached to the next model call" in result[0].text
    assert deferred_images[0][0] == "rendered/1.png"
    assert deferred_images[0][1].type == "data"
