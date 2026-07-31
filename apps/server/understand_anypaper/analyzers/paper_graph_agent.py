"""Tool-using agent that authors the complete Paper Argument Graph."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_framework import (
    Agent,
    ChatContext,
    Content,
    FunctionInvocationContext,
    Message,
    chat_middleware,
    tool,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from understand_anypaper.analyzers.llm import create_chat_client, create_responses_client
from understand_anypaper.config import Settings, apply_desktop_api_overrides, settings
from understand_anypaper.graph.agent_workspace import AgentGraphWorkspace
from understand_anypaper.graph.schema import EdgeType, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper


class PaperGraphAgentError(RuntimeError):
    """Raised when the graph-authoring agent cannot leave a valid graph.json."""


@dataclass(slots=True)
class _ToolRuntime:
    workspace: AgentGraphWorkspace
    config: Settings
    deferred_images: list[tuple[str, Content]] | None = None


def _runtime(context: FunctionInvocationContext) -> _ToolRuntime:
    runtime = context.kwargs.get("runtime")
    if not isinstance(runtime, _ToolRuntime):
        raise RuntimeError("paper graph tool runtime is missing")
    return runtime


@tool(name="Read")
async def read_file(
    path: str,
    context: FunctionInvocationContext,
    offset: int = 1,
    limit: int = 300,
) -> str | list[Content]:
    """Read text or a image from the paper workspace."""
    runtime = _runtime(context)
    result = runtime.workspace.read(path, offset, limit)
    if result.kind != "image":
        return str(result.content)

    assert isinstance(result.content, bytes)
    image = Content.from_data(result.content, result.media_type)
    if runtime.deferred_images is None:
        return [image]
    runtime.deferred_images.append((path, image))
    return f"Loaded {path}; the image is attached to the next model call."


@tool
async def search_replace(
    path: str,
    old_text: str,
    new_text: str,
    context: FunctionInvocationContext,
    replace_all: bool = False,
    disable_checks: bool = False,
) -> str:
    """Edit graph.json by exact string replacement and return validation results."""
    return _runtime(context).workspace.search_replace(
        path,
        old_text,
        new_text,
        replace_all=replace_all,
        disable_checks=disable_checks,
    )


@tool
async def shell(command: str, context: FunctionInvocationContext) -> str:
    """Run a shell command; never edit graph.json using this tool."""
    runtime = _runtime(context)
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(runtime.workspace.root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=runtime.config.graph_agent_shell_timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        return json.dumps(
            {
                "exit_code": None,
                "timed_out": True,
                "output": (
                    f"command exceeded {runtime.config.graph_agent_shell_timeout_seconds:g} seconds"
                ),
            }
        )
    output = stdout.decode(errors="replace")
    limit = runtime.config.graph_agent_shell_max_output_chars
    if len(output) > limit:
        output = output[:limit] + "\n[output truncated]"
    return json.dumps(
        {"exit_code": process.returncode, "timed_out": False, "output": output},
        ensure_ascii=False,
    )


@tool
async def apply_patch(
    patch: str,
    context: FunctionInvocationContext,
    disable_checks: bool = False,
) -> str:
    """Apply a Codex-style patch to graph.json and return validation results."""
    header = f"disable_checks={'true' if disable_checks else 'false'}"
    try:
        return _runtime(context).workspace.apply_patch(f"{header}\n{patch}")
    except Exception as exc:  # noqa: BLE001 - patch errors are model-correctable
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _attach_read_images(deferred_images: list[tuple[str, Content]]):
    @chat_middleware
    async def middleware(
        context: ChatContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if deferred_images:
            images = list(deferred_images)
            deferred_images.clear()
            context.messages = [
                *context.messages,
                Message(
                    role="user",
                    contents=[
                        Content.from_text(
                            "Images loaded by Read: " + ", ".join(path for path, _ in images)
                        ),
                        *(image for _, image in images),
                    ],
                ),
            ]
        await call_next()

    return middleware


_SYSTEM_PROMPT = """You are the Paper Argument Graph authoring agent.

Your only deliverable is a valid graph.json in the provided workspace. Inspect the paper with
Read: paper_parsed_text.txt contains exact parsed text blocks, rendered/{1,2,...}.png contains
page images, paper.pdf contains the original PDF, and graph_schema.json contains the authoritative
JSON Schema for graph.json, including descriptions for every field. paper_references.json maps
visible bibliography markers to stable PaperReference IDs. Start by reading graph.json,
graph_schema.json, paper_references.json, and the parsed text index. Use page images whenever
layout, figures, tables, or equations matter.

Build a traceable argument graph centered on Contribution nodes. The Paper root connects to each
Contribution with HAS_CONTRIBUTION. Every Contribution has Why, How, and Proof structural child
nodes via CONTAINS. Put motivation/gap evidence under Why, methods/equations/modules under How,
and experiments/results/tables under Proof. Additional meaningful relationships are encouraged.

## Extraction density and boundaries

Build a dense learning graph, not a paper summary. Almost every argument-bearing sentence in the
abstract and main body should be represented by a content node. A full conference paper with only
10-20 content nodes is normally too sparse; use roughly 6-12 text nodes per dense page as a
calibration target. Repeated descriptions in different source spans may be separate nodes.

Each content node must express one thing: one contribution, claim, motivation, gap, method
component, equation, figure, table, experiment, measured result, or design rationale. Prefer one
sentence. Use two adjacent sentences only when the second cannot be understood independently.
Split numbered or bulleted contribution lists into one Contribution node per concrete claim.

Skip author lists, affiliations, acknowledgments, bare section headings, page headers/numbers,
copyright text, and uncited bibliography entries. Do not skip abstract/introduction claims,
method captions, equations, table captions, or prose interpreting results.

## Contribution and method quality

Contribution titles must state the concrete claimed contribution or effect, not merely a method
or framework name. A measured achievement may be both a Contribution and a separate Result/Table
proof node when the authors present it as a main contribution.

For every proposed method, cover its subgraph rather than collapsing it into one node:
- a Method node for the section-level approach;
- a Module node for each named mechanism, branch, architecture block, indexing pattern, or stage;
- an Algorithm or Inference node for ordered procedures, retrieval, caching, or indexing;
- a Training or Implementation node for losses, optimizers, sampling, quantization,
  hyperparameters, and engineering choices;
- every displayed formula as an Equation plus nearby Definition/DesignRationale nodes when needed;
- every proposed-method figure as a Figure node, grounded to its caption or explanatory text block.

## Proof coverage

Treat evaluation tables and figures as first-class proof nodes. Extract every important performance,
runtime, energy, memory, complexity, qualitative-comparison, and ablation table/figure. A Table or
Figure summary must say what claim it supports. Also make separate Dataset, Metric, Baseline,
Experiment, Ablation, Result, QualitativeResult, and Efficiency nodes for nearby prose; do not use
one table node as a substitute for all experimental evidence.

## Node-type semantics

- Contribution: concrete author-claimed contribution, achievement, or contribution-list item.
- Claim: important author assertion that is not itself a contribution or measured result.
- Motivation / Problem / ResearchGap: why the work matters, the setting/constraint, and the
  limitation or unresolved trade-off in prior work.
- PriorWork: a specific previous method, family of methods, or cited work.
- Definition / Observation / DesignRationale: notation or concept definitions, motivating
  observations, and reasons for a design choice.
- Method / Module / Algorithm / Implementation / Training / Inference: respectively the approach,
  concrete components, ordered procedures, engineering details, training, and test-time behavior.
- Equation / Figure / Table: mathematical, visual, and structured evidence units.
- Dataset / Metric / Baseline / Experiment / Ablation: evaluation ingredients and controlled tests.
- Result / QualitativeResult / Efficiency: quantitative, perceptual, and efficiency findings.
- Extension / Conclusion / Reference: second-domain applications, final implications, and cited
  bibliography entries. Use TextBlock only for necessary background or experimental setup that has
  no more specific node type.

## Citation grounding

When a source span contains citations, preserve the exact visible markers in
properties.citation_markers, splitting grouped numeric markers such as "[2, 5]" into ["[2]", "[5]"].
Keep the semantic node type (PriorWork, Module, Baseline, etc.); do not replace an in-text citation
with a Reference node. Do not copy a second citation quote into properties: the exact block-offset
source span remains authoritative. Reference is reserved for an actually cited bibliography entry.
For every citation marker that resolves in paper_references.json, copy its exact reference_id into
node.reference_ids. A node may cite multiple references. Never invent a reference ID.

Every node except Paper, Why, How, and Proof must have exactly this authoring locator in
properties.source_location:
  {"block_id":"p0001-b0001","start_offset":0,"end_offset":10}
Offsets are zero-based, end-exclusive Unicode character offsets into the exact block text. Do not
write page, bbox, copied source text, or any second locator form. A locator cannot span
blocks; make separate nodes when necessary. Use IDs that are unique within this graph, set every
node/edge paper_id to the graph paper_id, and ensure every edge endpoint exists.

Editing tools persist changes even when the returned validation reports errors. Editing responses
normally include the complete current validation report. During initial construction you may set
disable_checks=true to avoid noisy partial-graph diagnostics; turn checks back on before finishing.
Warnings identify desirable evidence coverage but do not make the graph invalid. Stop only after
graph.json is valid. Never paste the full graph into chat as your final answer.
"""


class PaperGraphAgent:
    """Build a graph with Agent Framework via Responses for GPT or Chat Completions otherwise."""

    def __init__(
        self,
        config: Settings = settings,
        *,
        responses_client: OpenAIChatClient | None = None,
        chat_client: OpenAIChatCompletionClient | None = None,
    ) -> None:
        self._config = config
        self._responses_client = responses_client
        self._chat_client = chat_client

    async def build(self, parsed: ParsedPaper) -> PaperArgumentGraph:
        apply_desktop_api_overrides(self._config)
        if not parsed.source_blocks:
            raise PaperGraphAgentError("parser produced no source blocks for graph grounding")
        with TemporaryDirectory(prefix=f"anypaper-{parsed.paper_id[:8]}-") as directory:
            workspace = AgentGraphWorkspace(Path(directory), parsed)
            workspace.initialize()
            await self._run_agent(workspace)
            report = workspace.validate()
            if not report.valid:
                raise PaperGraphAgentError(
                    f"graph agent stopped with an invalid graph: {report.model_dump_json()}"
                )
            return workspace.materialize()

    @staticmethod
    def is_gpt_model(model: str) -> bool:
        slug = model.strip().casefold().split("/")[-1]
        return slug.startswith("gpt-") or slug.startswith("chatgpt-")

    async def _run_agent(self, workspace: AgentGraphWorkspace) -> None:
        use_responses = self.is_gpt_model(self._config.openai_model)
        session_id = f"paper-graph:{workspace.parsed.paper_id}"
        deferred_images: list[tuple[str, Content]] | None = None

        if use_responses:
            client = self._responses_client or create_responses_client(
                self._config, session_id=session_id
            )
            tools = [read_file, apply_patch, shell]
            middleware = []
            options: dict[str, Any] = {
                "allow_multiple_tool_calls": False,
                # Compatible gateways do not consistently persist Responses
                # conversations, so keep function history local.
                "store": False,
            }
            api_name = "Responses"
        else:
            deferred_images = []
            client = self._chat_client or create_chat_client(self._config, session_id=session_id)
            tools = [read_file, search_replace, shell]
            middleware = [_attach_read_images(deferred_images)]
            options: dict[str, Any] = {"allow_multiple_tool_calls": False}
            if "openrouter.ai" in self._config.openai_base_url.casefold():
                options["extra_body"] = {"provider": {"require_parameters": True}}
            api_name = "Chat Completions"

        client.function_invocation_configuration["max_iterations"] = (
            self._config.graph_agent_max_turns
        )
        client.function_invocation_configuration["max_function_calls"] = (
            self._config.graph_agent_max_tool_calls
        )

        agent = Agent[Any](
            client=client,
            name="PaperGraphAgent",
            instructions=self._instructions(workspace),
            tools=tools,
            middleware=middleware,
        )
        if self._config.send_prompt_cache_key:
            options["prompt_cache_key"] = session_id

        runtime = _ToolRuntime(
            workspace=workspace,
            config=self._config,
            deferred_images=deferred_images,
        )
        prompt = (
            "Build graph.json now. Read the workspace sources, edit incrementally, and do not stop "
            "until the editing-tool validation says valid=true."
        )
        for _ in range(3):
            try:
                await agent.run(
                    prompt,
                    options=options,
                    function_invocation_kwargs={"runtime": runtime},
                )
            except Exception as exc:
                raise PaperGraphAgentError(f"{api_name} graph agent failed: {exc}") from exc
            report = workspace.validate()
            if report.valid:
                return
            prompt = (
                "You stopped before graph.json was valid. Continue from the current file and fix "
                f"every error below with tools:\n{report.model_dump_json(indent=2)}"
            )
        raise PaperGraphAgentError(
            f"{api_name} graph agent exhausted repair attempts: {workspace.validation_payload()}"
        )

    @staticmethod
    def _instructions(workspace: AgentGraphWorkspace) -> str:
        node_types = ", ".join(node_type.value for node_type in NodeType)
        edge_types = ", ".join(edge_type.value for edge_type in EdgeType)
        return (
            f"{_SYSTEM_PROMPT}\n"
            f"The required graph paper_id is {workspace.parsed.paper_id!r}.\n"
            f"Allowed node_type values: {node_types}.\n"
            f"Allowed edge_type values: {edge_types}.\n"
        )
