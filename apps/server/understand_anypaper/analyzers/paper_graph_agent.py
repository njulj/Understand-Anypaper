"""Tool-using agent that authors the complete Paper Argument Graph."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_framework import (
    Agent,
    AgentResponseUpdate,
    ChatContext,
    Content,
    FunctionInvocationContext,
    Message,
    chat_middleware,
    function_middleware,
    tool,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from understand_anypaper.analyzers.llm import create_chat_client, create_responses_client
from understand_anypaper.config import Settings, apply_desktop_api_overrides, settings
from understand_anypaper.graph.agent_workspace import AgentGraphWorkspace
from understand_anypaper.graph.schema import EdgeType, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper


logger = logging.getLogger(__name__)
AgentProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class PaperGraphAgentError(RuntimeError):
    """Raised when the graph-authoring agent cannot leave a valid graph.json."""


@dataclass(frozen=True, slots=True)
class PaperGraphBuildResult:
    graph: PaperArgumentGraph
    authoring_graph: PaperArgumentGraph


_EDIT_TOOL_NAMES = frozenset({"apply_patch", "search_replace"})
_SIMPLE_JSON_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _partial_json_string(arguments: str, field: str) -> str | None:
    """Decode the available part of a JSON string field from partial tool arguments."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', arguments)
    if match is None:
        return None

    decoded: list[str] = []
    index = match.end()
    while index < len(arguments):
        character = arguments[index]
        if character == '"':
            break
        if character != "\\":
            decoded.append(character)
            index += 1
            continue

        if index + 1 >= len(arguments):
            break
        escape = arguments[index + 1]
        if escape in _SIMPLE_JSON_ESCAPES:
            decoded.append(_SIMPLE_JSON_ESCAPES[escape])
            index += 2
            continue
        if escape != "u" or index + 6 > len(arguments):
            break

        digits = arguments[index + 2 : index + 6]
        if any(digit not in "0123456789abcdefABCDEF" for digit in digits):
            break
        codepoint = int(digits, 16)
        index += 6
        if 0xD800 <= codepoint <= 0xDBFF and arguments[index : index + 2] == "\\u":
            if index + 6 > len(arguments):
                break
            low_digits = arguments[index + 2 : index + 6]
            if all(digit in "0123456789abcdefABCDEF" for digit in low_digits):
                low = int(low_digits, 16)
                if 0xDC00 <= low <= 0xDFFF:
                    codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                    index += 6
        decoded.append(chr(codepoint))
    return "".join(decoded)


def _diff_line_counts(before: str, after: str) -> tuple[int, int]:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    additions = 0
    deletions = 0
    for line in diff:
        additions += line.startswith("+") and not line.startswith("+++")
        deletions += line.startswith("-") and not line.startswith("---")
    return additions, deletions


def _streaming_edit_counts(tool_name: str, arguments: str) -> tuple[int, int] | None:
    if tool_name == "apply_patch":
        patch = _partial_json_string(arguments, "patch")
        if patch is None:
            return None
        additions = sum(
            line.startswith("+") and not line.startswith("+++") for line in patch.splitlines()
        )
        deletions = sum(
            line.startswith("-") and not line.startswith("---") for line in patch.splitlines()
        )
        return additions, deletions

    old_text = _partial_json_string(arguments, "old_text")
    if old_text is None:
        return None
    new_text = _partial_json_string(arguments, "new_text")
    if new_text is None:
        return 0, len(old_text.splitlines())
    return _diff_line_counts(old_text, new_text)


@dataclass(slots=True)
class _StreamingEditState:
    activity_id: str
    tool_name: str
    call_id: str | None
    arguments: str = ""
    last_counts: tuple[int, int] | None = None


@dataclass(slots=True)
class _ToolRuntime:
    workspace: AgentGraphWorkspace
    config: Settings
    deferred_images: list[tuple[str, Content]] | None = None
    on_progress: AgentProgressCallback | None = None
    activity_sequence: int = 0
    thinking_id: str | None = None
    thinking_started_at: float | None = None
    streaming_edit: _StreamingEditState | None = None
    clock: Callable[[], float] = time.monotonic

    async def _emit(self, activity: dict[str, Any]) -> None:
        if self.on_progress is None:
            return
        try:
            await self.on_progress(activity)
        except Exception:  # noqa: BLE001 - progress reporting must not stop graph generation
            logger.exception("Failed to report paper graph agent progress")

    def _next_id(self, kind: str) -> str:
        self.activity_sequence += 1
        return f"{kind}-{self.activity_sequence}"

    async def begin_thinking(self) -> None:
        if self.thinking_started_at is not None:
            return
        self.thinking_id = self._next_id("thinking")
        self.thinking_started_at = self.clock()
        await self._emit({"id": self.thinking_id, "kind": "thinking", "label": "Thinking…"})

    async def finish_thinking(self) -> None:
        if self.thinking_started_at is None or self.thinking_id is None:
            return
        thinking_id = self.thinking_id
        elapsed = self.clock() - self.thinking_started_at
        self.thinking_id = None
        self.thinking_started_at = None
        if elapsed > 10:
            seconds = max(1, round(elapsed))
            await self._emit(
                {
                    "id": thinking_id,
                    "kind": "thought",
                    "label": f"Thought for {seconds}s",
                    "duration_seconds": seconds,
                }
            )
        else:
            await self._emit({"id": thinking_id, "kind": "thinking_done"})

    async def record_read(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> None:
        await self._emit(
            {
                "id": self._next_id("read"),
                "kind": "read",
                "label": f"Read {path}",
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    async def record_shell(self, command: str) -> None:
        compact = " ".join(command.split())
        if len(compact) > 88:
            compact = compact[:85] + "…"
        await self._emit(
            {
                "id": self._next_id("shell"),
                "kind": "shell",
                "label": f"Ran {compact}",
                "command": command,
            }
        )

    async def observe_stream_update(self, update: AgentResponseUpdate) -> None:
        for content in update.contents:
            if content.type != "function_call":
                continue
            explicit_name = content.name or None
            if explicit_name not in _EDIT_TOOL_NAMES and self.streaming_edit is None:
                continue
            if explicit_name and explicit_name not in _EDIT_TOOL_NAMES:
                continue

            call_id = content.call_id or None
            state = self.streaming_edit
            if state is None or (call_id and state.call_id and call_id != state.call_id):
                assert explicit_name in _EDIT_TOOL_NAMES
                await self.finish_thinking()
                state = _StreamingEditState(
                    activity_id=self._next_id("edit"),
                    tool_name=explicit_name,
                    call_id=call_id,
                )
                self.streaming_edit = state
            elif call_id and state.call_id is None:
                state.call_id = call_id

            arguments = content.arguments
            if isinstance(arguments, str):
                state.arguments += arguments
            elif isinstance(arguments, Mapping):
                state.arguments += json.dumps(arguments, ensure_ascii=False)
            else:
                continue

            counts = _streaming_edit_counts(state.tool_name, state.arguments)
            if counts is None or counts == state.last_counts:
                continue
            state.last_counts = counts
            additions, deletions = counts
            await self._emit(
                {
                    "id": state.activity_id,
                    "kind": "edit",
                    "status": "streaming",
                    "label": "Editing graph.json…",
                    "path": "graph.json",
                    "additions": additions,
                    "deletions": deletions,
                }
            )

    def _matches_streaming_edit(self, context: FunctionInvocationContext | None) -> bool:
        state = self.streaming_edit
        if state is None:
            return False
        if context is None:
            return True
        call_id = context.metadata.get("call_id")
        return not call_id or not state.call_id or call_id == state.call_id

    def has_streaming_edit(self, context: FunctionInvocationContext | None = None) -> bool:
        return self._matches_streaming_edit(context)

    def _take_streaming_edit(
        self, context: FunctionInvocationContext | None
    ) -> _StreamingEditState | None:
        if not self._matches_streaming_edit(context):
            return None
        state = self.streaming_edit
        self.streaming_edit = None
        return state

    async def fail_streaming_edit(self, context: FunctionInvocationContext | None = None) -> None:
        state = self._take_streaming_edit(context)
        if state is None:
            return
        additions, deletions = state.last_counts or (0, 0)
        await self._emit(
            {
                "id": state.activity_id,
                "kind": "edit",
                "status": "failed",
                "label": "Edit failed",
                "path": "graph.json",
                "additions": additions,
                "deletions": deletions,
            }
        )

    async def record_edit(
        self,
        before: str,
        *,
        context: FunctionInvocationContext | None = None,
    ) -> None:
        after = self.workspace.graph_path.read_text(encoding="utf-8")
        additions, deletions = _diff_line_counts(before, after)
        report = self.workspace.validate()
        try:
            payload = json.loads(after)
            nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
            node_count = len(nodes) if isinstance(nodes, list) else 0
        except json.JSONDecodeError:
            node_count = 0
        state = self._take_streaming_edit(context)
        await self._emit(
            {
                "id": state.activity_id if state is not None else self._next_id("edit"),
                "kind": "edit",
                "status": "complete",
                "label": "Edited graph.json",
                "path": "graph.json",
                "additions": additions,
                "deletions": deletions,
                "problem_count": len(report.errors) + len(report.warnings),
                "node_count": node_count,
            }
        )


def _runtime(context: FunctionInvocationContext) -> _ToolRuntime:
    runtime = context.kwargs.get("runtime")
    if not isinstance(runtime, _ToolRuntime):
        raise RuntimeError("paper graph tool runtime is missing")
    return runtime


@function_middleware
async def _track_tool_lifecycle(
    context: FunctionInvocationContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """Keep the shared thinking activity in sync around every tool invocation."""
    runtime = _runtime(context)
    await runtime.finish_thinking()
    try:
        await call_next()
    finally:
        await runtime.begin_thinking()


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
    await runtime.record_read(
        path,
        start_line=result.start_line,
        end_line=result.end_line,
    )
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
    runtime = _runtime(context)
    before = runtime.workspace.graph_path.read_text(encoding="utf-8")
    try:
        result = runtime.workspace.search_replace(
            path,
            old_text,
            new_text,
            replace_all=replace_all,
            disable_checks=disable_checks,
        )
    except Exception:
        await runtime.fail_streaming_edit(context)
        raise
    await runtime.record_edit(before, context=context)
    return result


@tool
async def shell(command: str, context: FunctionInvocationContext) -> str:
    """Run a shell command; never edit graph.json using this tool."""
    runtime = _runtime(context)
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(runtime.workspace.root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=dict(os.environ),
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=runtime.config.graph_agent_shell_timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        result = json.dumps(
            {
                "exit_code": None,
                "timed_out": True,
                "output": (
                    f"command exceeded {runtime.config.graph_agent_shell_timeout_seconds:g} seconds"
                ),
            }
        )
    else:
        output = stdout.decode(errors="replace")
        limit = runtime.config.graph_agent_shell_max_output_chars
        if len(output) > limit:
            output = output[:limit] + "\n[output truncated]"
        result = json.dumps(
            {"exit_code": process.returncode, "timed_out": False, "output": output},
            ensure_ascii=False,
        )
    await runtime.record_shell(command)
    return result


@tool
async def apply_patch(
    patch: str,
    context: FunctionInvocationContext,
    disable_checks: bool = False,
) -> str:
    """Apply a Codex-style patch to graph.json and return validation results."""
    runtime = _runtime(context)
    before = runtime.workspace.graph_path.read_text(encoding="utf-8")
    header = f"disable_checks={'true' if disable_checks else 'false'}"
    failed = False
    try:
        result = runtime.workspace.apply_patch(f"{header}\n{patch}")
    except Exception as exc:  # noqa: BLE001 - patch errors are model-correctable
        failed = True
        result = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    if runtime.workspace.graph_path.read_text(encoding="utf-8") != before:
        await runtime.record_edit(before, context=context)
    elif failed:
        await runtime.fail_streaming_edit(context)
    elif runtime.has_streaming_edit(context):
        await runtime.record_edit(before, context=context)
    return result


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

Also write graph.summary as a self-contained Markdown overview of the entire paper. Synthesize its
motivation and research gap, core approach, main contributions, most important quantitative or
qualitative results, and conclusion. Use short paragraphs and Markdown structure where it improves
readability; do not merely copy the abstract or add source locators to this graph-level field.
Whenever the summary names an important Contribution, Method, Module, Equation, Experiment, Result,
Figure, or Table represented in the graph, link the useful phrase to that exact node using
`[descriptive text](graph://node-id)`. Every graph:// target must exactly match an existing node ID.
Normal https:// links are allowed only when they materially help the reader.

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
node.paper_id and every edge source_paper_id/target_paper_id to the graph paper_id, and ensure every
edge endpoint exists.

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

    async def build(
        self,
        parsed: ParsedPaper,
        *,
        on_progress: AgentProgressCallback | None = None,
    ) -> PaperArgumentGraph:
        result = await self.build_with_authoring(parsed, on_progress=on_progress)
        return result.graph

    async def build_with_authoring(
        self,
        parsed: ParsedPaper,
        *,
        initial_graph: PaperArgumentGraph | dict[str, Any] | None = None,
        source_diff: str | None = None,
        on_progress: AgentProgressCallback | None = None,
    ) -> PaperGraphBuildResult:
        apply_desktop_api_overrides(self._config)
        if not parsed.source_blocks:
            raise PaperGraphAgentError("parser produced no source blocks for graph grounding")
        with TemporaryDirectory(prefix=f"anypaper-{parsed.paper_id[:8]}-") as directory:
            workspace = AgentGraphWorkspace(Path(directory), parsed)
            workspace.initialize(initial_graph=initial_graph, source_diff=source_diff)
            await self._run_agent(workspace, on_progress=on_progress)
            report = workspace.validate()
            if not report.valid:
                raise PaperGraphAgentError(
                    f"graph agent stopped with an invalid graph: {report.model_dump_json()}"
                )
            authoring_graph = workspace.authoring_graph()
            return PaperGraphBuildResult(
                graph=workspace.materialize(),
                authoring_graph=authoring_graph,
            )

    @staticmethod
    def is_gpt_model(model: str) -> bool:
        slug = model.strip().casefold().split("/")[-1]
        return slug.startswith("gpt-") or slug.startswith("chatgpt-")

    async def _run_agent(
        self,
        workspace: AgentGraphWorkspace,
        *,
        on_progress: AgentProgressCallback | None = None,
    ) -> None:
        use_responses = self.is_gpt_model(self._config.openai_model)
        session_id = f"paper-graph:{workspace.parsed.paper_id}"
        deferred_images: list[tuple[str, Content]] | None = None

        if use_responses:
            client = self._responses_client or create_responses_client(
                self._config, session_id=session_id
            )
            tools = [read_file, apply_patch, shell]
            middleware = [_track_tool_lifecycle]
            options: dict[str, Any] = {
                "allow_multiple_tool_calls": False,
                # Compatible gateways do not consistently persist Responses
                # conversations, so keep function history local.
                "store": False,
                "reasoning": {"effort": "medium", "summary": "auto"},
            }
            api_name = "Responses"
        else:
            deferred_images = []
            client = self._chat_client or create_chat_client(self._config, session_id=session_id)
            tools = [read_file, search_replace, shell]
            middleware = [_track_tool_lifecycle, _attach_read_images(deferred_images)]
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
            on_progress=on_progress,
        )
        prompt = (
            "Build or update graph.json now. Read the workspace sources and source_changes.diff "
            "when present, preserve stable node IDs for unchanged concepts, edit incrementally, "
            "and do not stop until the editing-tool validation says valid=true."
        )
        for _ in range(3):
            try:
                await runtime.begin_thinking()
                response_stream = agent.run(
                    prompt,
                    stream=True,
                    options=options,
                    function_invocation_kwargs={"runtime": runtime},
                )
                async for update in response_stream:
                    await runtime.observe_stream_update(update)
                await response_stream.get_final_response()
                await runtime.finish_thinking()
            except Exception as exc:
                await runtime.fail_streaming_edit()
                await runtime.finish_thinking()
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
