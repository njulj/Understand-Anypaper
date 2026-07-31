"""Tool-using agent that authors the complete Paper Argument Graph."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any

from agent_framework import Agent, ChatContext, Content, FunctionTool, Message, chat_middleware
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
from pydantic import BaseModel, Field

from understand_anypaper.analyzers.llm import create_chat_client, create_responses_client
from understand_anypaper.config import Settings, apply_desktop_api_overrides, settings
from understand_anypaper.graph.agent_workspace import AgentGraphWorkspace
from understand_anypaper.graph.schema import EdgeType, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper

class PaperGraphAgentError(RuntimeError):
    """Raised when the graph-authoring agent cannot leave a valid graph.json."""


class ReadArgs(BaseModel):
    path: Annotated[str, Field(description="Workspace-relative file path")]
    offset: Annotated[int, Field(description="First 1-based text line to return", ge=1)] = 1
    limit: Annotated[int, Field(description="Maximum text lines to return", ge=1, le=1000)] = 300


class SearchReplaceArgs(BaseModel):
    path: Annotated[str, Field(description="Must be graph.json")]
    old_text: str
    new_text: str
    replace_all: bool = False
    disable_checks: Annotated[
        bool,
        Field(description="Skip graph validation for this early construction edit"),
    ] = False


class ShellArgs(BaseModel):
    command: Annotated[
        str,
        Field(
            description=(
                "Shell command run with the paper workspace as cwd. Do not use shell to edit "
                "graph.json; use the model-family editing tool."
            )
        ),
    ]


_SYSTEM_PROMPT = """You are the Paper Argument Graph authoring agent.

Your only deliverable is a valid graph.json in the provided workspace. Inspect the paper with
Read: paper_parsed_text.txt contains exact parsed text blocks, rendered/{1,2,...}.png contains
page images, and paper.pdf contains the original PDF. Start by reading graph.json and the parsed
text index. Use page images whenever layout, figures, tables, or equations matter.

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
    """Build a graph via Responses/custom tools for GPT and Chat Completions otherwise."""

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
            if self.is_gpt_model(self._config.openai_model):
                await self._run_responses(workspace)
            else:
                await self._run_chat_completions(workspace)
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

    def _framework_tools(
        self,
        workspace: AgentGraphWorkspace,
        *,
        include_search_replace: bool,
        deferred_images: list[tuple[str, Content]] | None = None,
    ) -> list[FunctionTool]:
        async def read_file(path: str, offset: int = 1, limit: int = 300) -> str | list[Content]:
            result = workspace.read(path, offset, limit)
            if result.kind == "image":
                assert isinstance(result.content, bytes)
                image = Content.from_data(result.content, result.media_type)
                if deferred_images is not None:
                    deferred_images.append((path, image))
                    return f"Loaded {path}; the image is attached to the next model call."
                return [image]
            return str(result.content)

        async def search_replace(
            path: str,
            old_text: str,
            new_text: str,
            replace_all: bool = False,
            disable_checks: bool = False,
        ) -> str:
            return workspace.search_replace(
                path,
                old_text,
                new_text,
                replace_all=replace_all,
                disable_checks=disable_checks,
            )

        async def shell(command: str) -> str:
            return await self._run_shell(workspace, command)

        tools = [
            FunctionTool(
                name="Read",
                description=(
                    "Read text with line numbers or view an image from the paper workspace. "
                    "Use rendered/{page}.png for page images."
                ),
                func=read_file,
                input_model=ReadArgs,
            ),
            FunctionTool(
                name="shell",
                description=(
                    "Run a shell command in the paper workspace for inspection or calculations. "
                    "Do not use shell to edit graph.json; use the editing tool."
                ),
                func=shell,
                input_model=ShellArgs,
            ),
        ]
        if include_search_replace:
            tools.insert(
                1,
                FunctionTool(
                    name="search_replace",
                    description=(
                        "Edit graph.json by exact string replacement. The response validates the "
                        "graph unless disable_checks is true."
                    ),
                    func=search_replace,
                    input_model=SearchReplaceArgs,
                ),
            )
        return tools

    async def _run_shell(self, workspace: AgentGraphWorkspace, command: str) -> str:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={
                key: value
                for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
                if (value := os.environ.get(key)) is not None
            },
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=self._config.graph_agent_shell_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return json.dumps(
                {
                    "exit_code": None,
                    "timed_out": True,
                    "output": (
                        f"command exceeded {self._config.graph_agent_shell_timeout_seconds:g} seconds"
                    ),
                }
            )
        output = stdout.decode(errors="replace")
        limit = self._config.graph_agent_shell_max_output_chars
        if len(output) > limit:
            output = output[:limit] + "\n[output truncated]"
        return json.dumps(
            {"exit_code": process.returncode, "timed_out": False, "output": output},
            ensure_ascii=False,
        )

    async def _run_chat_completions(self, workspace: AgentGraphWorkspace) -> None:
        deferred_images: list[tuple[str, Content]] = []
        tools = self._framework_tools(
            workspace,
            include_search_replace=True,
            deferred_images=deferred_images,
        )
        session_id = f"paper-graph:{workspace.parsed.paper_id}"
        client = self._chat_client or create_chat_client(self._config, session_id=session_id)
        client.function_invocation_configuration["max_iterations"] = self._config.graph_agent_max_turns
        client.function_invocation_configuration["max_function_calls"] = (
            self._config.graph_agent_max_tool_calls
        )

        @chat_middleware
        async def attach_read_images(
            context: ChatContext,
            call_next: Callable[[], Awaitable[None]],
        ) -> None:
            # Chat Completions omits rich content from tool-role messages. Agent
            # Framework runs chat middleware inside its function loop, so attach
            # images as a following user message before the next provider call.
            if deferred_images:
                images = list(deferred_images)
                deferred_images.clear()
                context.messages.append(
                    Message(
                        role="user",
                        contents=[
                            Content.from_text(
                                "Images loaded by Read: " + ", ".join(path for path, _ in images)
                            ),
                            *(image for _, image in images),
                        ],
                    )
                )
            await call_next()

        agent = Agent(
            client=client,
            name="PaperGraphAgent",
            instructions=self._instructions(workspace),
            tools=tools,
            middleware=[attach_read_images],
        )
        options: dict[str, Any] = {"allow_multiple_tool_calls": False}
        if self._config.send_prompt_cache_key:
            options["prompt_cache_key"] = session_id
        if "openrouter.ai" in self._config.openai_base_url.casefold():
            options["extra_body"] = {"provider": {"require_parameters": True}}
        prompt = (
            "Build graph.json now. Read the workspace sources, edit incrementally, and do not stop "
            "until the editing-tool validation says valid=true."
        )
        for _ in range(3):
            try:
                await agent.run(prompt, options=options)
            except Exception as exc:
                raise PaperGraphAgentError(f"Chat Completions graph agent failed: {exc}") from exc
            report = workspace.validate()
            if report.valid:
                return
            prompt = (
                "You stopped before graph.json was valid. Continue from the current file and fix "
                f"every error below with tools:\n{report.model_dump_json(indent=2)}"
            )
        raise PaperGraphAgentError(
            f"Chat Completions graph agent exhausted repair attempts: "
            f"{workspace.validation_payload()}"
        )

    async def _run_responses(self, workspace: AgentGraphWorkspace) -> None:
        """Responses loop with a raw-string custom apply_patch tool.

        Agent Framework 1.10 prepares custom tools for Responses but currently
        drops ``custom_tool_call`` while parsing provider output. We therefore
        keep its ``OpenAIChatClient`` as the configured provider and use the
        client's SDK handle for this loop until the framework exposes that item.
        """
        tools = self._framework_tools(workspace, include_search_replace=False)
        by_name = {tool.name: tool for tool in tools}
        response_tools = [self._responses_function_definition(tool) for tool in tools]
        response_tools.append(
            {
                "type": "custom",
                "name": "apply_patch",
                "description": (
                    "Edit only graph.json using a raw Codex-style patch, without JSON escaping. "
                    "The first input line must be disable_checks=true or disable_checks=false, "
                    "followed by *** Begin Patch, one *** Update File: graph.json section, hunks, "
                    "and *** End Patch. The response validates the graph unless checks are disabled."
                ),
            }
        )
        session_id = f"paper-graph:{workspace.parsed.paper_id}"
        framework_client = self._responses_client or create_responses_client(
            self._config, session_id=session_id
        )
        request: dict[str, Any] = {
            "model": self._config.openai_model,
            "instructions": self._instructions(workspace),
            "input": (
                "Build graph.json now. Read the workspace sources, edit incrementally, and do not "
                "stop until the editing-tool validation says valid=true."
            ),
            "tools": response_tools,
            "parallel_tool_calls": False,
            "store": True,
        }
        if self._config.send_prompt_cache_key:
            request["prompt_cache_key"] = session_id
        tool_call_count = 0
        previous_response_id: str | None = None

        for _ in range(self._config.graph_agent_max_turns):
            if previous_response_id:
                request["previous_response_id"] = previous_response_id
            try:
                response = await framework_client.client.responses.create(**request)
            except Exception as exc:
                raise PaperGraphAgentError(f"Responses graph agent failed: {exc}") from exc
            previous_response_id = response.id
            outputs: list[dict[str, Any]] = []
            for item in response.output:
                if item.type == "custom_tool_call" and item.name == "apply_patch":
                    tool_call_count += 1
                    result = self._safe_apply_patch(workspace, item.input)
                    outputs.append(
                        {
                            "type": "custom_tool_call_output",
                            "call_id": item.call_id,
                            "output": result,
                        }
                    )
                elif item.type == "function_call":
                    tool_call_count += 1
                    outputs.append(
                        await self._responses_function_output(item, by_name)
                    )
            if tool_call_count > self._config.graph_agent_max_tool_calls:
                raise PaperGraphAgentError("Responses graph agent exceeded its tool-call budget")
            if outputs:
                request["input"] = outputs
                continue

            report = workspace.validate()
            if report.valid:
                return
            request["input"] = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You stopped before graph.json was valid. Continue editing and fix "
                                f"all errors:\n{report.model_dump_json(indent=2)}"
                            ),
                        }
                    ],
                }
            ]
        raise PaperGraphAgentError(
            f"Responses graph agent exhausted {self._config.graph_agent_max_turns} turns: "
            f"{workspace.validation_payload()}"
        )

    @staticmethod
    def _responses_function_definition(tool: FunctionTool) -> dict[str, Any]:
        parameters = dict(tool.parameters())
        parameters["additionalProperties"] = False
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
            "strict": False,
        }

    async def _responses_function_output(
        self,
        item: Any,
        tools_by_name: dict[str, FunctionTool],
    ) -> dict[str, Any]:
        tool = tools_by_name.get(item.name)
        if tool is None:
            result: Any = json.dumps({"ok": False, "error": f"unknown tool {item.name!r}"})
        else:
            try:
                arguments = json.loads(item.arguments or "{}")
                result = await tool.invoke(arguments=arguments, tool_call_id=item.call_id, skip_parsing=True)
            except Exception as exc:  # noqa: BLE001 - corrective feedback belongs in the tool loop
                result = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return {
            "type": "function_call_output",
            "call_id": item.call_id,
            "output": self._responses_tool_content(result),
        }

    @staticmethod
    def _responses_tool_content(result: Any) -> str | list[dict[str, Any]]:
        if isinstance(result, list) and all(isinstance(content, Content) for content in result):
            output: list[dict[str, Any]] = []
            for content in result:
                if content.type == "data" and content.uri:
                    output.append(
                        {"type": "input_image", "image_url": content.uri, "detail": "auto"}
                    )
                elif content.text:
                    output.append({"type": "input_text", "text": content.text})
            return output or "tool returned no content"
        if isinstance(result, BaseModel):
            return result.model_dump_json()
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    @staticmethod
    def _safe_apply_patch(workspace: AgentGraphWorkspace, patch: str) -> str:
        try:
            return workspace.apply_patch(patch)
        except Exception as exc:  # noqa: BLE001 - patch errors are model-correctable
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

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
