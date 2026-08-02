import asyncio
import json
from types import SimpleNamespace

from agent_framework import FunctionInvocationContext

from understand_anypaper.analyzers.paper_graph_agent import (
    PaperGraphAgent,
    _ToolRuntime,
    apply_patch as apply_patch_tool,
    read_file,
    search_replace,
    shell,
)
from understand_anypaper.config import Settings
from understand_anypaper.graph.agent_workspace import AgentGraphWorkspace
from understand_anypaper.parser.models import (
    DocumentPage,
    PaperReference,
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
        references=[
            PaperReference(
                reference_id="ref-paper-test-1",
                marker="[1]",
                raw_text="A. Author. A cited paper. 2024.",
                title="A cited paper",
                year=2024,
            )
        ],
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
            "reference_ids": ["ref-paper-test-1"],
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
            "source_paper_id": paper_id,
            "source_node_id": "paper",
            "target_paper_id": paper_id,
            "target_node_id": "contribution",
            "edge_type": "HAS_CONTRIBUTION",
        },
        *[
            {
                "id": f"contribution-{facet}",
                "source_paper_id": paper_id,
                "source_node_id": "contribution",
                "target_paper_id": paper_id,
                "target_node_id": facet,
                "edge_type": "CONTAINS",
            }
            for facet in ("why", "how", "proof")
        ],
    ]
    return {
        "paper_id": paper_id,
        "summary": (
            "ExactGraph turns paper claims into a [traceable argument graph](graph://contribution) "
            "grounded in precise source spans, making each contribution and its evidence directly "
            "inspectable."
        ),
        "nodes": nodes,
        "edges": edges,
    }


def test_workspace_validates_exact_block_offsets_and_materializes_bbox(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    workspace.graph_path.write_text(json.dumps(_valid_graph()), encoding="utf-8")

    report = workspace.validate()
    graph = workspace.materialize()

    assert report.valid
    contribution = next(node for node in graph.nodes if node.id == "contribution")
    assert contribution.semantic_unit_ids == ["contribution"]
    assert contribution.reference_ids == ["ref-paper-test-1"]
    assert contribution.properties["source_location"]["block_id"] == "p0001-b0001"
    assert contribution.properties["source_location"]["bbox"] == [0.1, 0.1, 0.2, 0.9]
    semantic_unit = workspace.parsed.semantic_units[0]
    assert semantic_unit.source_location.extraction_method == "block_offset"
    assert semantic_unit.properties["citation_markers"] == ["[1]"]


def test_workspace_exposes_described_graph_schema(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()

    schema = json.loads((tmp_path / "graph_schema.json").read_text(encoding="utf-8"))
    references = json.loads((tmp_path / "paper_references.json").read_text(encoding="utf-8"))
    node_schema = schema["$defs"]["GraphNode"]

    assert schema["description"] == "The complete, traceable argument graph for one paper."
    assert schema["properties"]["summary"]["description"].startswith(
        "A self-contained Markdown summary"
    )
    assert node_schema["description"] == "A typed argument or evidence node extracted from one paper."
    assert node_schema["properties"]["reference_ids"]["description"].startswith(
        "PaperReference identifiers"
    )
    assert references[0]["reference_id"] == "ref-paper-test-1"


def test_workspace_requires_graph_level_summary(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()

    codes = {issue.code for issue in workspace.validate().errors}

    assert "missing_summary" in codes


def test_workspace_rejects_summary_link_to_unknown_graph_node(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    graph = _valid_graph()
    graph["summary"] += " See [missing evidence](graph://not-a-node)."
    workspace.graph_path.write_text(json.dumps(graph), encoding="utf-8")

    codes = {issue.code for issue in workspace.validate().errors}

    assert "unknown_summary_graph_link" in codes


def test_workspace_rejects_unknown_reference_ids(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    graph = _valid_graph()
    graph["nodes"][1]["reference_ids"] = ["ref-invented"]
    workspace.graph_path.write_text(json.dumps(graph), encoding="utf-8")

    codes = {issue.code for issue in workspace.validate().errors}

    assert "unknown_reference_ids" in codes
    assert "missing_reference_ids" in codes


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


def test_top_level_tool_schemas_hide_runtime_context():
    assert set(read_file.parameters()["properties"]) == {"path", "offset", "limit"}
    assert set(search_replace.parameters()["properties"]) == {
        "path",
        "old_text",
        "new_text",
        "replace_all",
        "disable_checks",
    }
    assert set(shell.parameters()["properties"]) == {"command"}
    assert set(apply_patch_tool.parameters()["properties"]) == {"patch", "disable_checks"}


def test_responses_agent_uses_framework_function_loop_for_apply_patch(tmp_path, monkeypatch):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    old_lines = workspace.graph_path.read_text(encoding="utf-8").splitlines()
    new_lines = json.dumps(_valid_graph(), indent=2).splitlines()
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: graph.json",
            "@@",
            *[f"-{line}" for line in old_lines],
            *[f"+{line}" for line in new_lines],
            "*** End Patch",
        ]
    )

    class FakeFrameworkClient:
        def __init__(self):
            self.function_invocation_configuration = {}

    runs = []

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *, client, name, instructions, tools, middleware):
            self.client = client
            self.tools = tools

        async def run(self, prompt, options, function_invocation_kwargs):
            runs.append((prompt, options, function_invocation_kwargs))
            apply_patch = next(tool for tool in self.tools if tool.name == "apply_patch")
            await apply_patch.invoke(
                arguments={"patch": patch, "disable_checks": False},
                context=FunctionInvocationContext(
                    function=apply_patch,
                    arguments={},
                    kwargs=dict(function_invocation_kwargs),
                ),
            )
            return SimpleNamespace()

    monkeypatch.setattr(
        "understand_anypaper.analyzers.paper_graph_agent.Agent",
        FakeAgent,
    )
    fake_client = FakeFrameworkClient()
    agent = PaperGraphAgent(
        Settings(
            openai_api_key="test",
            openai_base_url="https://api.openai.com/v1",
            openai_model="gpt-5-mini",
            graph_agent_max_turns=3,
        ),
        responses_client=fake_client,
    )

    asyncio.run(agent._run_agent(workspace))

    assert workspace.validate().valid
    assert fake_client.function_invocation_configuration == {
        "max_iterations": 3,
        "max_function_calls": 120,
    }
    assert runs[0][1]["allow_multiple_tool_calls"] is False
    assert runs[0][1]["store"] is False
    assert isinstance(runs[0][2]["runtime"], _ToolRuntime)


def test_chat_completions_read_defers_image_for_middleware(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    workspace.graph_path.write_text(json.dumps(_valid_graph()), encoding="utf-8")

    deferred_images = []
    context = FunctionInvocationContext(
        function=read_file,
        arguments={},
        kwargs={
            "runtime": _ToolRuntime(
                workspace=workspace,
                config=Settings(),
                deferred_images=deferred_images,
            )
        },
    )

    result = asyncio.run(
        read_file.invoke(
            arguments={"path": "rendered/1.png"},
            context=context,
        )
    )

    assert "attached to the next model call" in result[0].text
    assert deferred_images[0][0] == "rendered/1.png"
    assert deferred_images[0][1].type == "data"


def test_shell_passes_plain_environment_dict_and_resumes_thinking(tmp_path, monkeypatch):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    activities = []
    invocation = {}

    async def capture(activity):
        activities.append(activity)

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok", None

    async def fake_create_subprocess_shell(command, **kwargs):
        invocation.update(command=command, **kwargs)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    runtime = _ToolRuntime(workspace=workspace, config=Settings(), on_progress=capture)
    context = FunctionInvocationContext(
        function=shell,
        arguments={},
        kwargs={"runtime": runtime},
    )

    result = asyncio.run(
        shell.invoke(
            arguments={"command": "printf ok"},
            context=context,
        )
    )

    assert json.loads(result[0].text)["output"] == "ok"
    assert type(invocation["env"]) is dict
    assert [activity["kind"] for activity in activities] == ["shell", "thinking"]


def test_tool_progress_reports_thinking_read_and_edit_metadata(tmp_path):
    workspace = AgentGraphWorkspace(tmp_path, _parsed())
    workspace.initialize()
    activities = []

    async def capture(activity):
        activities.append(activity)

    timestamps = iter([100.0, 112.4, 200.0, 200.5])
    runtime = _ToolRuntime(
        workspace=workspace,
        config=Settings(),
        on_progress=capture,
        clock=lambda: next(timestamps),
    )

    async def exercise_tools():
        await runtime.begin_thinking()
        read_context = FunctionInvocationContext(
            function=read_file,
            arguments={},
            kwargs={"runtime": runtime},
        )
        await read_file.invoke(arguments={"path": "graph.json"}, context=read_context)

        workspace.graph_path.write_text(json.dumps(_valid_graph(), indent=2), encoding="utf-8")
        await runtime.record_edit('{"nodes": []}')
        await runtime.finish_thinking()

    asyncio.run(exercise_tools())

    assert [activity["kind"] for activity in activities] == [
        "thinking",
        "thought",
        "read",
        "thinking",
        "edit",
        "thinking_done",
    ]
    assert activities[1]["duration_seconds"] == 12
    assert activities[2]["start_line"] == 1
    assert activities[2]["end_line"] > activities[2]["start_line"]
    assert activities[4]["additions"] > 0
    assert activities[4]["deletions"] == 1
    assert activities[4]["node_count"] == 5
    assert activities[4]["problem_count"] == len(workspace.validate().warnings)
