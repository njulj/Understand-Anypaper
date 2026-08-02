"""Ephemeral paper workspace and validation for the graph-authoring agent."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from understand_anypaper.graph.graph_validator import GraphValidator
from understand_anypaper.graph.schema import EdgeType, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import (
    PageSourceLocation,
    PageSourceSegment,
    ParsedPaper,
    SemanticUnit,
    SourceBlock,
)


_STRUCTURAL_NODE_TYPES = {NodeType.PAPER, NodeType.WHY, NodeType.HOW, NodeType.PROOF}
_FACET_NODE_TYPES = {NodeType.WHY, NodeType.HOW, NodeType.PROOF}
_GRAPH_LINK_PATTERN = re.compile(r"\]\(graph://([^\s)]+)\)")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class GraphIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    path: str = "graph.json"
    message: str


class GraphValidationReport(BaseModel):
    valid: bool
    errors: list[GraphIssue] = Field(default_factory=list)
    warnings: list[GraphIssue] = Field(default_factory=list)


class ReadResult(BaseModel):
    kind: Literal["text", "image"]
    content: str | bytes
    media_type: str = "text/plain"
    start_line: int | None = None
    end_line: int | None = None


class AgentGraphWorkspace:
    """Owns the model-visible files and exact graph checks for one paper."""

    def __init__(self, root: Path, parsed: ParsedPaper) -> None:
        self.root = root.resolve()
        self.parsed = parsed
        self.graph_path = self.root / "graph.json"
        self.blocks_by_id = {block.block_id: block for block in parsed.source_blocks}

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        rendered = self.root / "rendered"
        rendered.mkdir(exist_ok=True)
        if self.parsed.source_media_type == "application/pdf":
            (self.root / "paper.pdf").write_bytes(self.parsed.source_bytes)
        else:
            (self.root / "paper.txt").write_bytes(self.parsed.source_bytes)
        for page in self.parsed.pages:
            if page.image_data:
                (rendered / f"{page.page}.png").write_bytes(page.image_data)
        (self.root / "paper_parsed_text.txt").write_text(
            self._parsed_text_document(), encoding="utf-8"
        )
        (self.root / "graph_schema.json").write_text(
            json.dumps(PaperArgumentGraph.model_json_schema(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.root / "paper_references.json").write_text(
            json.dumps(
                [reference.model_dump(mode="json") for reference in self.parsed.references],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        initial = PaperArgumentGraph(
            paper_id=self.parsed.paper_id,
            summary="",
            nodes=[
                {
                    "id": f"paper-{self.parsed.paper_id}",
                    "paper_id": self.parsed.paper_id,
                    "node_type": NodeType.PAPER,
                    "title": self.parsed.title,
                    "summary": self.parsed.abstract,
                    "confidence": 1.0,
                    "source_type": "document",
                    "created_by": "paper-graph-agent",
                }
            ],
            edges=[],
        )
        self._write_graph(initial.model_dump(mode="json"))

    def _parsed_text_document(self) -> str:
        lines = [
            "# Parsed paper text",
            "# Locator format: block_id + zero-based [start_offset, end_offset).",
            "# Offsets count Unicode characters in the exact block text below.",
            "",
        ]
        for block in self.parsed.source_blocks:
            lines.extend(
                [
                    f"<<< BLOCK {block.block_id} | page={block.page} | kind={block.kind} "
                    f"| length={len(block.text)} >>>",
                    block.text,
                    f"<<< END BLOCK {block.block_id} >>>",
                    "",
                ]
            )
        return "\n".join(lines)

    def resolve_path(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("path must be relative to the agent workspace")
        resolved = (self.root / relative_path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("path escapes the agent workspace")
        return resolved

    def read(self, path: str, offset: int = 1, limit: int = 300) -> ReadResult:
        target = self.resolve_path(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        if target.suffix.casefold() in _IMAGE_SUFFIXES:
            media_type = mimetypes.guess_type(target.name)[0] or "image/png"
            return ReadResult(kind="image", content=target.read_bytes(), media_type=media_type)
        if target.suffix.casefold() == ".pdf":
            return ReadResult(
                kind="text",
                content="PDF binary is available as paper.pdf. Read rendered/{page}.png for visual content.",
            )
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, offset - 1)
        count = min(max(1, limit), 1000)
        selected = lines[start : start + count]
        numbered = "\n".join(f"{index:>6} | {line}" for index, line in enumerate(selected, start + 1))
        suffix = "\n[more lines available]" if start + count < len(lines) else ""
        return ReadResult(
            kind="text",
            content=numbered + suffix,
            start_line=start + 1 if selected else None,
            end_line=start + len(selected) if selected else None,
        )

    def validate(self) -> GraphValidationReport:
        errors: list[GraphIssue] = []
        warnings: list[GraphIssue] = []
        try:
            payload = json.loads(self.graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(self._issue("invalid_json", f"graph.json is not valid JSON: {exc}"))
            return GraphValidationReport(valid=False, errors=errors)
        try:
            graph = PaperArgumentGraph.model_validate(payload)
        except ValidationError as exc:
            for item in exc.errors(include_url=False):
                location = ".".join(str(part) for part in item["loc"])
                errors.append(
                    self._issue(
                        "schema_error",
                        item["msg"],
                        path=f"graph.json.{location}" if location else "graph.json",
                    )
                )
            return GraphValidationReport(valid=False, errors=errors)

        if graph.paper_id != self.parsed.paper_id:
            errors.append(
                self._issue(
                    "paper_id_mismatch",
                    f"paper_id must be {self.parsed.paper_id!r}",
                    path="graph.json.paper_id",
                )
            )
        node_ids = [node.id for node in graph.nodes]
        edge_ids = [edge.id for edge in graph.edges]
        self._check_unique_ids(node_ids, "node", errors)
        self._check_unique_ids(edge_ids, "edge", errors)
        nodes_by_id = {node.id: node for node in graph.nodes}
        roots = [node for node in graph.nodes if node.node_type == NodeType.PAPER]
        if not graph.summary.strip():
            errors.append(
                self._issue(
                    "missing_summary",
                    "summary must contain a self-contained Markdown summary of the paper",
                    path="graph.json.summary",
                )
            )
        for linked_node_id in _GRAPH_LINK_PATTERN.findall(graph.summary):
            if linked_node_id not in nodes_by_id:
                errors.append(
                    self._issue(
                        "unknown_summary_graph_link",
                        f"summary links to unknown graph node {linked_node_id!r}",
                        path="graph.json.summary",
                    )
                )
        if len(roots) != 1:
            errors.append(self._issue("paper_root_count", "graph must contain exactly one Paper node"))
        contributions = [node for node in graph.nodes if node.node_type == NodeType.CONTRIBUTION]
        if not contributions:
            errors.append(self._issue("missing_contribution", "graph must contain at least one Contribution"))

        for index, node in enumerate(graph.nodes):
            path = f"graph.json.nodes.{index}"
            if node.paper_id != graph.paper_id:
                errors.append(self._issue("node_paper_id_mismatch", "node paper_id differs from graph", path))
            known_reference_ids = {
                reference.reference_id for reference in self.parsed.references
            }
            unknown_reference_ids = [
                reference_id
                for reference_id in node.reference_ids
                if reference_id not in known_reference_ids
            ]
            if unknown_reference_ids:
                errors.append(
                    self._issue(
                        "unknown_reference_ids",
                        f"reference_ids are not present in paper_references.json: {unknown_reference_ids}",
                        f"{path}.reference_ids",
                    )
                )
            citation_markers = node.properties.get("citation_markers")
            if citation_markers is not None and (
                not isinstance(citation_markers, list)
                or not all(isinstance(marker, str) for marker in citation_markers)
            ):
                errors.append(
                    self._issue(
                        "invalid_citation_markers",
                        "citation_markers must be a list of exact marker strings",
                        f"{path}.properties.citation_markers",
                    )
                )
            elif citation_markers:
                references_by_marker = {
                    self._normalize_citation_marker(reference.marker): reference
                    for reference in self.parsed.references
                    if reference.marker
                }
                unresolved = [
                    marker
                    for marker in citation_markers
                    if self._normalize_citation_marker(marker) not in references_by_marker
                ]
                if unresolved:
                    warnings.append(
                        GraphIssue(
                            severity="warning",
                            code="unresolved_citation_markers",
                            message=f"citation markers do not match parsed references: {unresolved}",
                            path=f"{path}.properties.citation_markers",
                        )
                    )
                missing_reference_ids = [
                    references_by_marker[self._normalize_citation_marker(marker)].reference_id
                    for marker in citation_markers
                    if self._normalize_citation_marker(marker) in references_by_marker
                    and references_by_marker[
                        self._normalize_citation_marker(marker)
                    ].reference_id
                    not in node.reference_ids
                ]
                if missing_reference_ids:
                    errors.append(
                        self._issue(
                            "missing_reference_ids",
                            "citation_markers must be paired with these reference_ids: "
                            f"{missing_reference_ids}",
                            f"{path}.reference_ids",
                        )
                    )
            if node.node_type not in _STRUCTURAL_NODE_TYPES:
                self._validate_locator(node.properties.get("source_location"), path, errors)

        for index, edge in enumerate(graph.edges):
            path = f"graph.json.edges.{index}"
            if edge.source_paper_id != graph.paper_id:
                errors.append(
                    self._issue(
                        "edge_source_paper_id_mismatch",
                        "edge source_paper_id differs from graph",
                        path,
                    )
                )
            if edge.target_paper_id != graph.paper_id:
                errors.append(
                    self._issue(
                        "edge_target_paper_id_mismatch",
                        "authored edge target_paper_id differs from graph",
                        path,
                    )
                )
            if edge.source_node_id not in nodes_by_id:
                errors.append(self._issue("missing_edge_source", f"unknown node {edge.source_node_id!r}", path))
            if edge.target_node_id not in nodes_by_id:
                errors.append(self._issue("missing_edge_target", f"unknown node {edge.target_node_id!r}", path))

        if len(roots) == 1:
            reachable = self._reachable_node_ids(roots[0].id, graph)
            for node in graph.nodes:
                if node.id not in reachable:
                    code = "orphan_evidence" if node.node_type not in _STRUCTURAL_NODE_TYPES else "orphan_node"
                    errors.append(
                        self._issue(code, f"node {node.id!r} is not reachable from the Paper root")
                    )
            directly_linked_contributions = {
                edge.target_node_id
                for edge in graph.edges
                if edge.source_node_id == roots[0].id
                and edge.edge_type == EdgeType.HAS_CONTRIBUTION
            }
            for contribution in contributions:
                if contribution.id not in directly_linked_contributions:
                    errors.append(
                        self._issue(
                            "missing_contribution_edge",
                            f"Paper root must link to {contribution.id!r} with HAS_CONTRIBUTION",
                        )
                    )

        for contribution in contributions:
            child_facets = {
                nodes_by_id[edge.target_node_id].node_type
                for edge in graph.edges
                if edge.source_node_id == contribution.id
                and edge.edge_type == EdgeType.CONTAINS
                and edge.target_node_id in nodes_by_id
                and nodes_by_id[edge.target_node_id].node_type in _FACET_NODE_TYPES
            }
            for facet_type in sorted(_FACET_NODE_TYPES - child_facets, key=str):
                errors.append(
                    self._issue(
                        "missing_facet",
                        f"Contribution {contribution.id!r} needs a {facet_type.value} child via CONTAINS",
                    )
                )

        if not errors:
            for score in GraphValidator().score_completeness(graph):
                missing = [
                    name
                    for name in ("motivation", "method", "equations", "experimental_evidence", "references")
                    if getattr(score, name) == 0
                ]
                if missing:
                    warnings.append(
                        GraphIssue(
                            severity="warning",
                            code="incomplete_contribution",
                            message=f"Contribution {score.contribution_id!r} has no {', '.join(missing)} evidence",
                        )
                    )
        return GraphValidationReport(valid=not errors, errors=errors, warnings=warnings)

    @staticmethod
    def _issue(code: str, message: str, path: str = "graph.json") -> GraphIssue:
        return GraphIssue(severity="error", code=code, path=path, message=message)

    def _check_unique_ids(self, ids: list[str], kind: str, errors: list[GraphIssue]) -> None:
        seen: set[str] = set()
        for value in ids:
            if value in seen:
                errors.append(self._issue(f"duplicate_{kind}_id", f"duplicate {kind} id {value!r}"))
            seen.add(value)

    def _validate_locator(
        self,
        locator: Any,
        path: str,
        errors: list[GraphIssue],
    ) -> None:
        if not isinstance(locator, dict):
            errors.append(
                self._issue(
                    "missing_source_location",
                    "content node needs properties.source_location with block_id/start_offset/end_offset",
                    path,
                )
            )
            return
        allowed = {"block_id", "start_offset", "end_offset"}
        unexpected = set(locator) - allowed
        if unexpected:
            errors.append(
                self._issue(
                    "noncanonical_source_location",
                    f"source_location only accepts {sorted(allowed)}; remove {sorted(unexpected)}",
                    path,
                )
            )
        block_id = locator.get("block_id")
        start = locator.get("start_offset")
        end = locator.get("end_offset")
        if not isinstance(block_id, str) or block_id not in self.blocks_by_id:
            errors.append(self._issue("unknown_block_id", f"unknown block_id {block_id!r}", path))
            return
        if type(start) is not int or type(end) is not int:
            errors.append(self._issue("invalid_offsets", "start_offset and end_offset must be integers", path))
            return
        block = self.blocks_by_id[block_id]
        if block.kind == "image":
            if (start, end) != (0, 0):
                errors.append(self._issue("invalid_image_offsets", "image blocks require offsets 0, 0", path))
        elif start < 0 or end <= start or end > len(block.text):
            errors.append(
                self._issue(
                    "offset_out_of_range",
                    f"expected 0 <= start_offset < end_offset <= {len(block.text)} for {block_id}",
                    path,
                )
            )

    @staticmethod
    def _reachable_node_ids(root_id: str, graph: PaperArgumentGraph) -> set[str]:
        outgoing: dict[str, list[str]] = {}
        for edge in graph.edges:
            outgoing.setdefault(edge.source_node_id, []).append(edge.target_node_id)
        seen = {root_id}
        queue = deque([root_id])
        while queue:
            for target in outgoing.get(queue.popleft(), []):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        return seen

    def validation_payload(self) -> str:
        return self.validate().model_dump_json(indent=2)

    def edit_response(self, *, disable_checks: bool) -> str:
        payload: dict[str, Any] = {"ok": True}
        if disable_checks:
            payload["checks_disabled"] = True
        else:
            payload["validation"] = self.validate().model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False)

    def search_replace(
        self,
        path: str,
        old_text: str,
        new_text: str,
        *,
        replace_all: bool = False,
        disable_checks: bool = False,
    ) -> str:
        target = self.resolve_path(path)
        if target != self.graph_path:
            raise ValueError("search_replace may edit only graph.json")
        current = target.read_text(encoding="utf-8")
        if not old_text:
            raise ValueError("old_text must not be empty")
        occurrences = current.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text was not found in graph.json")
        if occurrences > 1 and not replace_all:
            raise ValueError(f"old_text occurs {occurrences} times; use a larger match or replace_all=true")
        updated = current.replace(old_text, new_text, -1 if replace_all else 1)
        self._atomic_write(target, updated)
        return self.edit_response(disable_checks=disable_checks)

    def apply_patch(self, raw_input: str) -> str:
        disable_checks, patch = self._parse_patch_header(raw_input)
        updated = self._apply_graph_patch(self.graph_path.read_text(encoding="utf-8"), patch)
        self._atomic_write(self.graph_path, updated)
        return self.edit_response(disable_checks=disable_checks)

    @staticmethod
    def _parse_patch_header(raw_input: str) -> tuple[bool, str]:
        lines = raw_input.splitlines()
        if not lines:
            raise ValueError("empty patch")
        match = re.fullmatch(r"disable_checks\s*=\s*(true|false)", lines[0].strip(), re.IGNORECASE)
        if not match:
            raise ValueError("first line must be disable_checks=true or disable_checks=false")
        return match.group(1).casefold() == "true", "\n".join(lines[1:])

    @staticmethod
    def _apply_graph_patch(current: str, patch: str) -> str:
        lines = patch.splitlines()
        if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
            raise ValueError("patch must be wrapped in *** Begin Patch / *** End Patch")
        if any(line.startswith("*** Add File:") or line.startswith("*** Delete File:") for line in lines):
            raise ValueError("apply_patch may update only the existing graph.json")
        update_markers = [index for index, line in enumerate(lines) if line.startswith("*** Update File:")]
        if update_markers != [1] or lines[1].split(":", 1)[1].strip() != "graph.json":
            raise ValueError("patch must contain one *** Update File: graph.json section")
        body = lines[2:-1]
        hunks: list[list[str]] = []
        active: list[str] = []
        for line in body:
            if line.startswith("@@"):
                if active:
                    hunks.append(active)
                    active = []
                continue
            if line.startswith("*** "):
                raise ValueError(f"unexpected patch directive {line!r}")
            active.append(line)
        if active:
            hunks.append(active)
        if not hunks:
            raise ValueError("patch contains no hunks")

        result = current.splitlines()
        cursor = 0
        for hunk in hunks:
            old_lines: list[str] = []
            new_lines: list[str] = []
            for line in hunk:
                if line == r"\ No newline at end of file":
                    continue
                if not line or line[0] not in {" ", "+", "-"}:
                    raise ValueError(f"invalid patch line {line!r}")
                value = line[1:]
                if line[0] in {" ", "-"}:
                    old_lines.append(value)
                if line[0] in {" ", "+"}:
                    new_lines.append(value)
            position = AgentGraphWorkspace._find_sequence(result, old_lines, cursor)
            if position < 0:
                position = AgentGraphWorkspace._find_sequence(result, old_lines, 0)
            if position < 0:
                preview = "\n".join(old_lines[:5])
                raise ValueError(f"patch context was not found in graph.json: {preview!r}")
            result[position : position + len(old_lines)] = new_lines
            cursor = position + len(new_lines)
        return "\n".join(result) + "\n"

    @staticmethod
    def _find_sequence(lines: list[str], sequence: list[str], start: int) -> int:
        if not sequence:
            return start
        last = len(lines) - len(sequence)
        for index in range(start, last + 1):
            if lines[index : index + len(sequence)] == sequence:
                return index
        return -1

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)

    def _write_graph(self, payload: dict[str, Any]) -> None:
        self._atomic_write(
            self.graph_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def materialize(self) -> PaperArgumentGraph:
        report = self.validate()
        if not report.valid:
            raise ValueError(f"cannot materialize invalid graph: {report.model_dump_json()}")
        graph = PaperArgumentGraph.model_validate_json(self.graph_path.read_text(encoding="utf-8"))
        contribution_by_node = self._contribution_ancestors(graph)
        units: list[SemanticUnit] = []
        materialized_nodes = []
        for node in graph.nodes:
            if node.node_type in _STRUCTURAL_NODE_TYPES:
                materialized_nodes.append(
                    node.model_copy(update={"semantic_unit_ids": [], "reference_ids": []})
                )
                continue
            locator = node.properties["source_location"]
            block = self.blocks_by_id[locator["block_id"]]
            source_location = self._materialize_location(
                block,
                locator["start_offset"],
                locator["end_offset"],
            )
            contribution_ids = sorted(contribution_by_node.get(node.id, set()))
            unit_properties: dict[str, Any] = {
                key: value
                for key, value in node.properties.items()
                if key != "source_location"
            }
            if node.node_type != NodeType.CONTRIBUTION and contribution_ids:
                unit_properties["contribution_unit_ids"] = contribution_ids
            unit = SemanticUnit(
                semantic_unit_id=node.id,
                paper_id=graph.paper_id,
                role=self._role_for_node_type(node.node_type),
                title=node.title,
                text=source_location.extracted_text or node.summary or node.title,
                source_location=source_location,
                confidence=node.confidence,
                created_by="paper-graph-agent",
                properties=unit_properties,
            )
            units.append(unit)
            properties = {
                **node.properties,
                "source_location": source_location.model_dump(mode="json"),
            }
            materialized_nodes.append(
                node.model_copy(
                    update={
                        "semantic_unit_ids": [node.id],
                        "page_ranges": [(block.page, block.page)],
                        "properties": properties,
                        "source_type": "pdf_block_span",
                        "created_by": "paper-graph-agent",
                    }
                )
            )

        unit_ids = {unit.semantic_unit_id for unit in units}
        materialized_edges = []
        for edge in graph.edges:
            semantic_ids: list[str] = []
            if edge.target_node_id in unit_ids:
                semantic_ids = [edge.target_node_id]
            elif edge.source_node_id in unit_ids:
                semantic_ids = [edge.source_node_id]
            materialized_edges.append(edge.model_copy(update={"semantic_unit_ids": semantic_ids}))
        self.parsed.semantic_units = units
        result = PaperArgumentGraph(
            paper_id=graph.paper_id,
            summary=graph.summary,
            nodes=materialized_nodes,
            edges=materialized_edges,
        )
        return result

    @staticmethod
    def _normalize_citation_marker(marker: str) -> str:
        return re.sub(r"\s+", " ", marker).strip().casefold()

    def _materialize_location(
        self,
        block: SourceBlock,
        start_offset: int,
        end_offset: int,
    ) -> PageSourceLocation:
        matching = [
            span
            for span in block.spans
            if span.end_offset > start_offset and span.start_offset < end_offset
        ]
        bbox = self._bbox_union([span.bbox for span in matching]) if matching else block.bbox
        extracted_text = block.text[start_offset:end_offset] if block.kind == "text" else ""
        segment = PageSourceSegment(
            page=block.page,
            bbox=bbox,
            extracted_text=extracted_text,
            block_id=block.block_id,
            start_offset=start_offset,
            end_offset=end_offset,
            extraction_method="block_offset",
        )
        return PageSourceLocation(**segment.model_dump(), segments=[segment])

    @staticmethod
    def _bbox_union(boxes: list[list[float]]) -> list[float]:
        return [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]

    @staticmethod
    def _role_for_node_type(node_type: NodeType) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", node_type.value).casefold()

    @staticmethod
    def _contribution_ancestors(graph: PaperArgumentGraph) -> dict[str, set[str]]:
        nodes_by_id = {node.id: node for node in graph.nodes}
        incoming: dict[str, list[str]] = {}
        for edge in graph.edges:
            incoming.setdefault(edge.target_node_id, []).append(edge.source_node_id)
        result: dict[str, set[str]] = {}
        for node in graph.nodes:
            if node.node_type == NodeType.CONTRIBUTION:
                result[node.id] = {node.id}
                continue
            contributions: set[str] = set()
            seen = {node.id}
            queue = deque(incoming.get(node.id, []))
            while queue:
                parent = queue.popleft()
                if parent in seen:
                    continue
                seen.add(parent)
                parent_node = nodes_by_id.get(parent)
                if parent_node is None:
                    continue
                if parent_node.node_type == NodeType.CONTRIBUTION:
                    contributions.add(parent)
                else:
                    queue.extend(incoming.get(parent, []))
            result[node.id] = contributions
        return result
