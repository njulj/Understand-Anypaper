import threading

from pydantic import BaseModel

from understand_anypaper.analyzers.contribution_evidence_assigner import (
    ContributionEvidenceAssigner,
    ContributionEvidenceSelectionOutput,
)
from understand_anypaper.parser.models import PageSourceLocation, ParsedPaper, SemanticUnit


class FakeAgent:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.cache_keys: list[str | None] = []

    def run(self, prompt: str, output_model: type[BaseModel], prompt_cache_key: str | None = None):
        self.prompts.append(prompt)
        self.cache_keys.append(prompt_cache_key)
        return self.outputs.pop(0)


def _unit(unit_id: str, role: str, title: str, page: int) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id="paper-12345678",
        role=role,
        title=title,
        text=title,
        source_locations=[PageSourceLocation(page=page, bbox=[0.1, 0.1, 0.2, 0.8], extracted_text=title)],
        confidence=0.9,
    )


def test_assigner_selects_evidence_per_contribution():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
    )
    units = [
        _unit("unit-contribution", "contribution", "TinyLUT contribution", 1),
        _unit("unit-motivation", "motivation", "Mobile demand", 1),
        _unit("unit-method", "method", "Separable mapping", 2),
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
    assert agent.cache_keys == ["evidence-assignment:paper-12345678:unit-contribution"]
    assert "TARGET_CONTRIBUTION" in agent.prompts[0]


def test_assigner_runs_contribution_requests_in_parallel_with_limit():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
    )
    units = [
        *[
            _unit(f"unit-contribution-{index}", "contribution", f"Contribution {index}", 1)
            for index in range(6)
        ],
        _unit("unit-method", "method", "Separable mapping", 2),
    ]

    class TrackingAgent:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self.lock = threading.Lock()
            self.five_active = threading.Event()

        def run(
            self,
            prompt: str,
            output_model: type[BaseModel],
            prompt_cache_key: str | None = None,
        ):
            with self.lock:
                self.active += 1
                self.calls += 1
                self.max_active = max(self.max_active, self.active)
                if self.active == 5:
                    self.five_active.set()
            self.five_active.wait(timeout=1)
            with self.lock:
                self.active -= 1
            return ContributionEvidenceSelectionOutput.model_validate({"evidence": []})

    agent = TrackingAgent()

    ContributionEvidenceAssigner(agent=agent).assign(parsed, units)

    assert agent.calls == 6
    assert agent.max_active == 5
