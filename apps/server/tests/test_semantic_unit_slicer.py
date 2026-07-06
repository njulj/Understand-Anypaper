from pydantic import BaseModel

from understand_anypaper.analyzers.semantic_unit_slicer import (
    SemanticSliceOutput,
    SemanticUnitSlicer,
)
from understand_anypaper.parser.models import DocumentPage, ParsedPaper


class FakeAgent:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    def run(self, prompt: str, output_model: type[BaseModel], prompt_cache_key: str | None = None):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def test_normalize_bbox_clamps_and_rounds():
    assert SemanticUnitSlicer._normalize_bbox([-0.1, 0.123456, 1.1, 0.987654]) == [
        0.0,
        0.12346,
        1.0,
        0.98765,
    ]


def test_normalize_bbox_rejects_empty_region():
    assert SemanticUnitSlicer._normalize_bbox([0.2, 0.1, 0.2, 0.8]) is None
    assert SemanticUnitSlicer._normalize_bbox([0.2, 0.8, 0.4, 0.1]) is None


def test_slice_semantic_units_uses_agent_output():
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
    )
    agent = FakeAgent(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "TinyLUT contribution",
                            "text": "The paper proposes TinyLUT.",
                            "evidence": [
                                {
                                    "page": 1,
                                    "bbox": [0.1, 0.1, 0.2, 0.8],
                                }
                            ],
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = SemanticUnitSlicer(agent=agent).slice_semantic_units(parsed)

    assert units
    assert units[0].role == "contribution"
    assert units[0].evidence[0].bbox == [0.1, 0.1, 0.2, 0.8]
    assert units[0].created_by == "semantic-unit-slicer-agent"
    assert agent.prompts
