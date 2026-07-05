from pydantic import BaseModel

from understand_anypaper.analyzers.semantic_unit_slicer import (
    SemanticSliceOutput,
    SemanticUnitSlicer,
    SemanticUnitSourceQuote,
)
from understand_anypaper.parser.models import ParsedPaper, SourceBlock


class FakeAgent:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    def run(self, prompt: str, output_model: type[BaseModel], prompt_cache_key: str | None = None):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def test_quote_span_matches_exact_text():
    text = "We propose ShiftLUT, a novel framework for efficient image restoration."
    quote = "ShiftLUT, a novel framework"
    start = text.find(quote)

    assert SemanticUnitSlicer._find_quote_span(text, quote) == (start, start + len(quote))


def test_quote_span_matches_case_and_whitespace_variation():
    text = "LSS introduces channel-wise spatial diversity\ninto the LUT."
    expected = text.find("channel-wise")

    assert SemanticUnitSlicer._find_quote_span(
        text, "channel-wise spatial diversity into the lut"
    ) == (
        expected,
        len(text) - 1,
    )


def test_clean_source_quotes_keeps_unknown_span_without_offsets():
    source_block = SourceBlock(
        source_block_id="paper-block1",
        order=1,
        page=1,
        text="AutoSample automatically learns how to sample during training.",
    )

    ranges = SemanticUnitSlicer._clean_source_quotes(
        [
            SemanticUnitSourceQuote(
                source_block_id="paper-block1",
                quote="a paraphrase that is not an exact span",
            )
        ],
        {"paper-block1": source_block},
    )

    assert len(ranges) == 1
    assert ranges[0].source_block_id == "paper-block1"
    assert ranges[0].start_char is None
    assert ranges[0].end_char is None


def test_slice_semantic_units_uses_agent_output():
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        source_blocks=[
            SourceBlock(
                source_block_id="paper-block1",
                order=1,
                page=1,
                text="We propose TinyLUT for compact lookup-table restoration.",
            )
        ],
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
                            "source_quotes": [
                                {
                                    "source_block_id": "paper-block1",
                                    "quote": "We propose TinyLUT",
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
    assert units[0].created_by == "semantic-unit-slicer-agent"
    assert agent.prompts
