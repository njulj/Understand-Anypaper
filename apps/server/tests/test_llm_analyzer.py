from understand_anypaper.analyzers.llm_analyzer import (
    LLMAnalyzer,
    SemanticSliceOutput,
    SemanticUnitSourceQuote,
)
from understand_anypaper.parser.models import ParsedPaper, SourceBlock


def test_quote_span_matches_exact_text():
    text = "We propose ShiftLUT, a novel framework for efficient image restoration."
    quote = "ShiftLUT, a novel framework"
    start = text.find(quote)

    assert LLMAnalyzer._find_quote_span(text, quote) == (start, start + len(quote))


def test_quote_span_matches_case_and_whitespace_variation():
    text = "LSS introduces channel-wise spatial diversity\ninto the LUT."
    expected = text.find("channel-wise")

    assert LLMAnalyzer._find_quote_span(text, "channel-wise spatial diversity into the lut") == (
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

    ranges = LLMAnalyzer._clean_source_quotes(
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


def test_structured_response_format_uses_pydantic_schema():
    response_format = LLMAnalyzer._structured_response_format()
    schema = response_format["json_schema"]["schema"]

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert "semantic_units" in schema["properties"]
    assert SemanticSliceOutput.model_validate(
        {
            "semantic_units": [
                {
                    "role": "method",
                    "title": "Learnable sampling",
                    "text": "AutoSample learns sampling weights during training.",
                    "source_quotes": [
                        {
                            "source_block_id": "paper-block1",
                            "quote": "AutoSample learns sampling weights",
                        }
                    ],
                    "confidence": 0.8,
                }
            ]
        }
    )


def test_slice_semantic_units_retries_when_no_contribution():
    class RetryAnalyzer(LLMAnalyzer):
        def __init__(self) -> None:
            self.system_prompts: list[str] = []

        @property
        def available(self) -> bool:
            return True

        def _chat_json(self, system: str, user: str) -> dict | None:
            self.system_prompts.append(system)
            if len(self.system_prompts) == 1:
                return {
                    "semantic_units": [
                        {
                            "role": "method",
                            "title": "Separable mapping strategy",
                            "text": "SMS decouples the convolution kernel.",
                            "source_quotes": [
                                {
                                    "source_block_id": "paper-block1",
                                    "quote": "we propose an innovative separable mapping strategy",
                                }
                            ],
                            "confidence": 0.8,
                        }
                    ]
                }
            return {
                "semantic_units": [
                    {
                        "role": "contribution",
                        "title": "TinyLUT reduces LUT storage",
                        "text": "The paper contributes separable mapping to reduce LUT storage.",
                        "source_quotes": [
                            {
                                "source_block_id": "paper-block1",
                                "quote": "we propose an innovative separable mapping strategy",
                            }
                        ],
                        "confidence": 0.9,
                    }
                ]
            }

    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        source_blocks=[
            SourceBlock(
                source_block_id="paper-block1",
                order=1,
                page=1,
                text=(
                    "The main contributions can be summarized as follows: "
                    "we propose an innovative separable mapping strategy."
                ),
            )
        ],
    )

    analyzer = RetryAnalyzer()
    units = analyzer.slice_semantic_units(parsed)

    assert units
    assert units[0].role == "contribution"
    assert len(analyzer.system_prompts) == 2
    assert "did not include any contribution role" in analyzer.system_prompts[1]
