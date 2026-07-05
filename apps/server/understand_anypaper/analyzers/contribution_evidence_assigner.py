import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from understand_anypaper.analyzers.structured_agent import StructuredAgent, StructuredAgentError
from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit, SourceBlock, SourceRange

logger = logging.getLogger(__name__)


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_unit_id: str = Field(description="Selected evidence semantic unit ID.")
    rationale: str = Field(description="Why this evidence belongs to the target contribution.")
    confidence: float = Field(ge=0, le=1)


class ContributionEvidenceSelectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[EvidenceSelection]


_ASSIGNER_INSTRUCTIONS = """\
You select evidence for one target contribution in a Paper Argument Graph.

You receive:
- the full paper text as source blocks,
- all semantic evidence units extracted from the paper,
- one target contribution unit.

Select every evidence unit that directly helps explain this contribution: motivation,
gap, method, equation, figure, experiment, table, result, conclusion, background, or
reference context. Use semantic relevance, not textual proximity. It is valid to return
an empty evidence list if none of the evidence units support this contribution.

Use only the provided semantic_unit_id values. Do not select contribution units. Return
JSON matching the schema.
"""


class ContributionEvidenceAssigner:
    """Agent-backed contribution -> evidence selector."""

    def __init__(
        self,
        config: Settings = settings,
        agent: StructuredAgent | None = None,
    ) -> None:
        self._config = config
        self._agent_injected = agent is not None
        self._agent = agent or StructuredAgent(
            name="ContributionEvidenceAssigner",
            instructions=_ASSIGNER_INSTRUCTIONS,
            config=config,
        )

    @property
    def available(self) -> bool:
        return self._agent_injected or bool(self._config.openai_api_key)

    def assign(self, parsed: ParsedPaper, semantic_units: list[SemanticUnit]) -> list[SemanticUnit]:
        if not self.available:
            raise ContributionEvidenceAssignmentError("LLM contribution evidence assignment is required")

        contributions = [unit for unit in semantic_units if unit.role == "contribution"]
        evidence_units = [unit for unit in semantic_units if unit.role != "contribution"]
        if not contributions:
            raise ContributionEvidenceAssignmentError("No contribution units are available")
        if not evidence_units:
            return semantic_units

        evidence_ids = {unit.semantic_unit_id for unit in evidence_units}
        contribution_ids_by_evidence: dict[str, list[str]] = {
            unit.semantic_unit_id: [] for unit in evidence_units
        }
        assignment_details: dict[str, list[dict]] = {
            unit.semantic_unit_id: [] for unit in evidence_units
        }
        base_context = self._base_context(parsed, evidence_units)

        for contribution in contributions:
            prompt = f"{base_context}\n\nTARGET_CONTRIBUTION:\n{self._unit_json(contribution)}"
            try:
                output = self._agent.run(
                    prompt,
                    ContributionEvidenceSelectionOutput,
                    prompt_cache_key=f"evidence-assignment:{parsed.paper_id}",
                )
            except StructuredAgentError as exc:
                raise ContributionEvidenceAssignmentError(str(exc)) from exc
            for selected in output.evidence:
                if selected.semantic_unit_id not in evidence_ids:
                    raise ContributionEvidenceAssignmentError(
                        f"LLM selected unknown evidence unit: {selected.semantic_unit_id}"
                    )
                contribution_ids_by_evidence[selected.semantic_unit_id].append(
                    contribution.semantic_unit_id
                )
                assignment_details[selected.semantic_unit_id].append(
                    {
                        "contribution_unit_id": contribution.semantic_unit_id,
                        "rationale": selected.rationale,
                        "confidence": selected.confidence,
                    }
                )

        assigned: list[SemanticUnit] = []
        for unit in semantic_units:
            if unit.role == "contribution":
                assigned.append(unit)
                continue
            assigned.append(
                unit.model_copy(
                    update={
                        "properties": {
                            **unit.properties,
                            "contribution_unit_ids": contribution_ids_by_evidence[
                                unit.semantic_unit_id
                            ],
                            "contribution_evidence_assignment": {
                                "source": "llm_contribution_evidence_agent",
                                "selections": assignment_details[unit.semantic_unit_id],
                            },
                        }
                    }
                )
            )
        return assigned

    def _base_context(self, parsed: ParsedPaper, evidence_units: list[SemanticUnit]) -> str:
        full_text = "\n\n".join(
            self._source_block_text(block) for block in sorted(parsed.source_blocks, key=lambda b: b.order)
        )
        payload = {
            "paper": {
                "paper_id": parsed.paper_id,
                "title": parsed.title,
                "abstract": parsed.abstract,
            },
            "full_text": full_text,
            "evidence_units": [
                self._unit_payload(unit, parsed.source_blocks) for unit in evidence_units
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _source_block_text(block: SourceBlock) -> str:
        metadata = {
            "source_block_id": block.source_block_id,
            "order": block.order,
            "page": block.page,
            "section": block.section,
            "heading": block.heading,
            "block_type": block.block_type,
        }
        return f"{json.dumps(metadata, ensure_ascii=False)}\n{block.text}"

    @staticmethod
    def _unit_payload(unit: SemanticUnit, source_blocks: list[SourceBlock]) -> dict:
        blocks = {block.source_block_id: block for block in source_blocks}
        return {
            "semantic_unit_id": unit.semantic_unit_id,
            "role": unit.role,
            "title": unit.title,
            "text": unit.text,
            "source_ranges": [
                {
                    **source_range.model_dump(),
                    "source_text": ContributionEvidenceAssigner._source_range_text(
                        blocks.get(source_range.source_block_id),
                        source_range,
                    ),
                }
                for source_range in unit.source_ranges
            ],
        }

    @staticmethod
    def _source_range_text(block: SourceBlock | None, source_range: SourceRange) -> str | None:
        if block is None:
            return None
        if source_range.start_char is not None and source_range.end_char is not None:
            return block.text[source_range.start_char:source_range.end_char]
        return block.text

    @staticmethod
    def _unit_json(unit: SemanticUnit) -> str:
        return json.dumps(
            {
                "semantic_unit_id": unit.semantic_unit_id,
                "role": unit.role,
                "title": unit.title,
                "text": unit.text,
                "source_ranges": [source_range.model_dump() for source_range in unit.source_ranges],
            },
            ensure_ascii=False,
        )


class ContributionEvidenceAssignmentError(RuntimeError):
    pass
