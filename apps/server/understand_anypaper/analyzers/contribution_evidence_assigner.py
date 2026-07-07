import asyncio
import json
import logging

from agent_framework import Agent, SupportsChatGetResponse
from pydantic import BaseModel, ConfigDict, Field

from understand_anypaper.analyzers.llm import LlmError, create_chat_client, run_structured
from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit

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
- all semantic evidence units extracted from the paper,
- one target contribution unit.

Select every evidence unit that directly helps explain this contribution: motivation,
gap, method, equation, figure, experiment, table, result, conclusion, background, or
reference context. Use semantic relevance, not textual proximity. It is valid to return
an empty evidence list if none of the evidence units support this contribution.

Use only the provided semantic_unit_id values. Do not select contribution units. Return
JSON matching the schema.
"""

_MAX_PARALLEL_ASSIGNMENTS = 5


class ContributionEvidenceAssigner:
    """Agent-backed contribution -> evidence selector."""

    def __init__(
        self,
        config: Settings = settings,
        chat_client: SupportsChatGetResponse | None = None,
    ) -> None:
        self._config = config
        self._chat_client = chat_client

    @property
    def available(self) -> bool:
        return self._chat_client is not None or bool(self._config.openai_api_key)

    async def assign(
        self,
        parsed: ParsedPaper,
        semantic_units: list[SemanticUnit],
    ) -> list[SemanticUnit]:
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

        outputs = await self._select_evidence_for_contributions(parsed, base_context, contributions)
        for contribution, output in zip(contributions, outputs, strict=True):
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

    async def _select_evidence_for_contributions(
        self,
        parsed: ParsedPaper,
        base_context: str,
        contributions: list[SemanticUnit],
    ) -> list[ContributionEvidenceSelectionOutput]:
        session_id = f"evidence-assignment:{parsed.paper_id}"
        client = self._chat_client or create_chat_client(self._config, session_id=session_id)
        # All requests share the same base-context prefix. Run one request
        # first so it writes the provider prompt cache; the parallel remainder
        # then reuses the cached prefix instead of racing before it exists.
        first = await self._select_evidence_for_contribution(
            client, session_id, base_context, contributions[0]
        )
        rest = contributions[1:]
        if not rest:
            return [first]

        semaphore = asyncio.Semaphore(_MAX_PARALLEL_ASSIGNMENTS)

        async def bounded(unit: SemanticUnit) -> ContributionEvidenceSelectionOutput:
            async with semaphore:
                return await self._select_evidence_for_contribution(
                    client, session_id, base_context, unit
                )

        # return_exceptions lets every request finish before the first error is
        # raised, so no task is abandoned mid-flight.
        results = await asyncio.gather(*(bounded(unit) for unit in rest), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return [first, *results]

    async def _select_evidence_for_contribution(
        self,
        client: SupportsChatGetResponse,
        session_id: str,
        base_context: str,
        contribution: SemanticUnit,
    ) -> ContributionEvidenceSelectionOutput:
        prompt = f"{base_context}\n\nTARGET_CONTRIBUTION:\n{self._unit_json(contribution)}"
        try:
            agent = Agent(
                client=client,
                name="ContributionEvidenceAssigner",
                instructions=_ASSIGNER_INSTRUCTIONS,
            )
            return await run_structured(
                agent,
                prompt,
                ContributionEvidenceSelectionOutput,
                prompt_cache_key=session_id,
                timeout_seconds=self._config.llm_request_timeout_seconds,
                base_url=self._config.openai_base_url,
            )
        except LlmError as exc:
            raise ContributionEvidenceAssignmentError(str(exc)) from exc

    def _base_context(self, parsed: ParsedPaper, evidence_units: list[SemanticUnit]) -> str:
        payload = {
            "paper": {
                "paper_id": parsed.paper_id,
                "title": parsed.title,
                "abstract": parsed.abstract,
            },
            "evidence_units": [self._unit_payload(unit) for unit in evidence_units],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _unit_payload(unit: SemanticUnit) -> dict:
        return {
            "semantic_unit_id": unit.semantic_unit_id,
            "role": unit.role,
            "title": unit.title,
            "text": unit.text,
            "source_location": unit.source_location.model_dump(),
        }

    @classmethod
    def _unit_json(cls, unit: SemanticUnit) -> str:
        return json.dumps(cls._unit_payload(unit), ensure_ascii=False)


class ContributionEvidenceAssignmentError(RuntimeError):
    pass
