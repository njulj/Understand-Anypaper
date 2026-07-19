import asyncio

from agent_framework import ChatResponse, Content, Message
from pydantic import BaseModel

from understand_anypaper.analyzers.contribution_evidence_assigner import (
    ContributionEvidenceAssigner,
    ContributionEvidenceSelectionOutput,
)
from understand_anypaper.config import Settings
from understand_anypaper.parser.models import PageSourceLocation, ParsedPaper, SemanticUnit


def _response(output: BaseModel) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(output.model_dump_json())])]
    )


class FakeChatClient:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.cache_keys: list[str | None] = []

    async def get_response(self, messages=None, *, stream=False, options=None, **kwargs):
        self.prompts.append("\n".join(message.text for message in messages))
        self.cache_keys.append(options.get("prompt_cache_key"))
        return _response(self.outputs.pop(0))


def _unit(unit_id: str, role: str, title: str, page: int) -> SemanticUnit:
    return SemanticUnit(
        semantic_unit_id=unit_id,
        paper_id="paper-12345678",
        role=role,
        title=title,
        text=title,
        source_location=PageSourceLocation(page=page, bbox=[0.1, 0.1, 0.2, 0.8], extracted_text=title),
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
    client = FakeChatClient(
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

    assigned = asyncio.run(
        ContributionEvidenceAssigner(
            config=Settings(PAG_OPENAI_BASE_URL="https://api.openai.com/v1"),
            chat_client=client,
        ).assign(parsed, units)
    )
    by_id = {unit.semantic_unit_id: unit for unit in assigned}

    assert by_id["unit-motivation"].properties["contribution_unit_ids"] == ["unit-contribution"]
    assert by_id["unit-method"].properties["contribution_unit_ids"] == ["unit-contribution"]
    assert client.cache_keys == ["evidence-assignment:paper-12345678"]
    assert "TARGET_CONTRIBUTION" in client.prompts[0]


def test_assigner_omits_cache_key_when_disabled():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
    )
    units = [
        _unit("unit-contribution", "contribution", "TinyLUT contribution", 1),
        _unit("unit-method", "method", "Separable mapping", 2),
    ]
    client = FakeChatClient(
        [ContributionEvidenceSelectionOutput.model_validate({"evidence": []})]
    )

    asyncio.run(
        ContributionEvidenceAssigner(
            config=Settings(send_prompt_cache_key=False),
            chat_client=client,
        ).assign(parsed, units)
    )

    assert client.cache_keys == [None]


def test_assigner_warms_cache_then_runs_remaining_in_parallel_with_limit():
    parsed = ParsedPaper(
        paper_id="paper-12345678",
        title="TinyLUT",
        abstract="A compact lookup-table method.",
    )
    # 1 warm-up call + 6 parallel calls: the semaphore must cap concurrency at 5.
    units = [
        *[
            _unit(f"unit-contribution-{index}", "contribution", f"Contribution {index}", 1)
            for index in range(7)
        ],
        _unit("unit-method", "method", "Separable mapping", 2),
    ]

    class TrackingChatClient:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self.first_done = asyncio.Event()
            self.five_active = asyncio.Event()

        async def get_response(self, messages=None, *, stream=False, options=None, **kwargs):
            self.calls += 1
            call_index = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 5:
                self.five_active.set()
            if call_index > 1:
                assert self.first_done.is_set(), "parallel batch started before warm-up call finished"
                await asyncio.wait_for(self.five_active.wait(), timeout=1)
            self.active -= 1
            if call_index == 1:
                self.first_done.set()
            return _response(ContributionEvidenceSelectionOutput.model_validate({"evidence": []}))

    client = TrackingChatClient()

    asyncio.run(ContributionEvidenceAssigner(chat_client=client).assign(parsed, units))

    assert client.calls == 7
    assert client.max_active == 5
