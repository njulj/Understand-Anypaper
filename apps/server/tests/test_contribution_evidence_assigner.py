from pydantic import BaseModel

from understand_anypaper.analyzers.contribution_evidence_assigner import (
    ContributionEvidenceAssigner,
    ContributionEvidenceSelectionOutput,
)
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit, SourceBlock, SourceRange


class FakeAgent:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.cache_keys: list[str | None] = []

    def run(self, prompt: str, output_model: type[BaseModel], prompt_cache_key: str | None = None):
        self.prompts.append(prompt)
        self.cache_keys.append(prompt_cache_key)
        return self.outputs.pop(0)


def _unit(unit_id: str, role: str, title: str, block_id: str) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id="paper-12345678",
        role=role,
        title=title,
        text=title,
        source_ranges=[SourceRange(source_block_id=block_id)],
        confidence=0.9,
    )


def test_assigner_selects_evidence_per_contribution():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
        source_blocks=[
            SourceBlock(source_block_id="paper-block1", order=1, page=1, text="We contribute TinyLUT."),
            SourceBlock(source_block_id="paper-block2", order=2, page=1, text="Mobile demand motivates it."),
            SourceBlock(source_block_id="paper-block3", order=3, page=2, text="The method uses mapping."),
        ],
    )
    units = [
        _unit("unit-contribution", "contribution", "TinyLUT contribution", "paper-block1"),
        _unit("unit-motivation", "motivation", "Mobile demand", "paper-block2"),
        _unit("unit-method", "method", "Separable mapping", "paper-block3"),
    ]
    agent = FakeAgent(
        [
            ContributionEvidenceSelectionOutput.model_validate(
                {
                    "evidence": [
                        {
                            "semantic_unit_id": "unit-motivation",
                            "rationale": "It explains why TinyLUT is needed.",
                            "confidence": 0.8,
                        },
                        {
                            "semantic_unit_id": "unit-method",
                            "rationale": "It explains how TinyLUT is implemented.",
                            "confidence": 0.85,
                        },
                    ]
                }
            )
        ]
    )

    assigned = ContributionEvidenceAssigner(agent=agent).assign(parsed, units)
    by_id = {unit.semantic_unit_id: unit for unit in assigned}

    assert by_id["unit-motivation"].properties["contribution_unit_ids"] == ["unit-contribution"]
    assert by_id["unit-method"].properties["contribution_unit_ids"] == ["unit-contribution"]
    assert agent.cache_keys == ["evidence-assignment:paper-12345678"]
    assert "TARGET_CONTRIBUTION" in agent.prompts[0]
