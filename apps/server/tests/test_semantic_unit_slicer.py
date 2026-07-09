import asyncio

import pytest
from pydantic import BaseModel

import fitz
from agent_framework import ChatResponse, Content, Message

from understand_anypaper.config import Settings
from understand_anypaper.analyzers.semantic_unit_slicer import (
    SemanticSliceOutput,
    SemanticUnitSlicer,
    SemanticUnitSlicingError,
    SourceLocatorOutput,
)
from understand_anypaper.parser.models import DocumentPage, ParsedPaper


class FakeChatClient:
    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.options: list[dict] = []

    async def get_response(self, messages=None, *, stream=False, options=None, **kwargs):
        self.prompts.append("\n".join(message.text for message in messages))
        self.options.append(options or {})
        payload = self.outputs.pop(0).model_dump_json()
        return ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_text(payload)])]
        )


class SlowChatClient:
    async def get_response(self, messages=None, *, stream=False, options=None, **kwargs):
        await asyncio.sleep(1)


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


def test_bbox_locator_converts_gemini_scale_to_normalized_bbox():
    locator = SourceLocatorOutput.model_validate(
        {
            "kind": "bbox",
            "start_text": "",
            "end_text": "",
            "x": 100,
            "y": 200,
            "width": 300,
            "height": 150,
        }
    )

    assert SemanticUnitSlicer._bbox_locator_to_normalized(locator) == [0.2, 0.1, 0.35, 0.4]


def test_slice_semantic_units_uses_agent_output():
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "TinyLUT contribution",
                            "text": "The paper proposes TinyLUT.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "bbox",
                                    "start_text": "",
                                    "end_text": "",
                                    "x": 100,
                                    "y": 100,
                                    "width": 700,
                                    "height": 100,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    assert units[0].role == "contribution"
    assert units[0].source_location.bbox == [0.1, 0.1, 0.2, 0.8]
    assert units[0].created_by == "semantic-unit-slicer-agent"
    assert client.prompts


def test_slice_semantic_units_prefers_text_anchors_for_text_roles():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 96),
        "We propose TinyLUT, a compact lookup approach that improves latency.",
    )
    source_bytes = doc.tobytes()
    doc.close()
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
        source_bytes=source_bytes,
        source_media_type="application/pdf",
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "TinyLUT contribution",
                            "text": "The paper proposes TinyLUT.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "text",
                                    "start_text": "We propose TinyLUT",
                                    "end_text": "improves latency",
                                    "x": 0,
                                    "y": 0,
                                    "width": 0,
                                    "height": 0,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    location = units[0].source_location
    assert location.extraction_method == "pymupdf_text_anchors"
    assert location.bbox != [0.0, 0.0, 1.0, 1.0]
    assert location.start_text == "We propose TinyLUT"
    assert location.end_text == "improves latency"
    assert "We propose TinyLUT" in location.extracted_text
    assert "improves latency" in location.extracted_text


def test_dehyphenated_anchor_matching_maps_back_to_original_words():
    words = [
        (0, 0, 10, 10, "structure", 0, 0, 0),
        (12, 0, 20, 10, "inside", 0, 0, 1),
        (22, 0, 28, 10, "the", 0, 0, 2),
        (30, 0, 38, 10, "im-", 0, 0, 3),
        (0, 12, 10, 22, "age.", 1, 0, 0),
    ]

    assert SemanticUnitSlicer._word_spans_from_normalized_anchor(
        words,
        "structure inside the image.",
        "end",
    ) == [(0, 4)]
    assert SemanticUnitSlicer._text_contains_anchor(
        "structure inside the im- age.",
        "structure inside the image.",
    )


def test_slice_semantic_units_matches_dehyphenated_end_anchor():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 96),
        "Interpolation-based methods, including nearest, often ignore local "
        "structure inside the im-",
    )
    page.insert_text((72, 110), "age.")
    source_bytes = doc.tobytes()
    doc.close()
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
        source_bytes=source_bytes,
        source_media_type="application/pdf",
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "Interpolation limitation",
                            "text": "Interpolation methods ignore local image structure.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "text",
                                    "start_text": "Interpolation-based methods,",
                                    "end_text": "structure inside the image.",
                                    "x": 0,
                                    "y": 0,
                                    "width": 0,
                                    "height": 0,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    location = units[0].source_location
    assert location.extraction_method == "pymupdf_text_anchors"
    assert location.bbox != [0.0, 0.0, 1.0, 1.0]
    assert "Interpolation-based methods" in location.extracted_text
    assert "structure inside the im- age." in location.extracted_text
    assert SemanticUnitSlicer._text_contains_anchor(
        location.extracted_text,
        "structure inside the image.",
    )


def test_text_anchor_resolution_uses_end_anchor_after_start_anchor():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 96), "Result appears before the target span.")
    page.insert_text((72, 120), "Target starts here and the final Result appears here.")
    source_bytes = doc.tobytes()
    doc.close()
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
        source_bytes=source_bytes,
        source_media_type="application/pdf",
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "Anchor ordering",
                            "text": "The target span ends at the later Result.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "text",
                                    "start_text": "Target starts here",
                                    "end_text": "Result appears here",
                                    "x": 0,
                                    "y": 0,
                                    "width": 0,
                                    "height": 0,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    assert "Target starts here" in units[0].source_location.extracted_text
    assert "Result appears here" in units[0].source_location.extracted_text


def test_slice_semantic_units_resolves_cross_page_text_anchor_segments():
    doc = fitz.open()
    page1 = doc.new_page(width=612, height=792)
    page2 = doc.new_page(width=612, height=792)
    page1.insert_text(
        (72, 96),
        "We propose a compact pipeline that starts here and continues",
    )
    page2.insert_text(
        (72, 96),
        "across the next page before ending with strong gains.",
    )
    source_bytes = doc.tobytes()
    doc.close()
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[
            DocumentPage(page=1, width=612, height=792),
            DocumentPage(page=2, width=612, height=792),
        ],
        source_bytes=source_bytes,
        source_media_type="application/pdf",
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "Cross-page contribution",
                            "text": "The contribution span crosses a page boundary.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "text",
                                    "start_text": "starts here",
                                    "end_text": "strong gains.",
                                    "x": 0,
                                    "y": 0,
                                    "width": 0,
                                    "height": 0,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    location = units[0].source_location
    assert location.extraction_method == "pymupdf_text_anchors_cross_page"
    assert [segment.page for segment in location.segments] == [1, 2]
    assert "starts here and continues" in location.extracted_text
    assert "ending with strong gains." in location.extracted_text


def test_unresolved_text_anchors_fall_back_to_page_location():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 96), "The visible contribution text is present on this page.")
    source_bytes = doc.tobytes()
    doc.close()
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
        source_bytes=source_bytes,
        source_media_type="application/pdf",
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "Unresolved anchor",
                            "text": "The paper has a contribution.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "text",
                                    "start_text": "The model invented this anchor",
                                    "end_text": "and this one too",
                                    "x": 0,
                                    "y": 0,
                                    "width": 0,
                                    "height": 0,
                                },
                            },
                            "confidence": 0.7,
                        }
                    ]
                }
            )
        ]
    )

    units = asyncio.run(SemanticUnitSlicer(chat_client=client).slice_semantic_units(parsed))

    assert units
    location = units[0].source_location
    assert location.extraction_method == "unresolved_text_anchors"
    assert location.bbox == [0.0, 0.0, 1.0, 1.0]
    assert "visible contribution text" in location.extracted_text


def test_slice_semantic_units_reports_llm_timeout():
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
    )

    with pytest.raises(SemanticUnitSlicingError, match="timed out"):
        asyncio.run(
            SemanticUnitSlicer(
                config=Settings(llm_request_timeout_seconds=0.01),
                chat_client=SlowChatClient(),
            ).slice_semantic_units(parsed)
        )


def test_openrouter_requests_require_structured_output_support():
    parsed = ParsedPaper(
        paper_id="paper",
        title="TinyLUT",
        pages=[DocumentPage(page=1, width=612, height=792)],
    )
    client = FakeChatClient(
        [
            SemanticSliceOutput.model_validate(
                {
                    "semantic_units": [
                        {
                            "role": "contribution",
                            "title": "TinyLUT contribution",
                            "text": "The paper proposes TinyLUT.",
                            "source_location": {
                                "page": 1,
                                "locator": {
                                    "kind": "bbox",
                                    "start_text": "",
                                    "end_text": "",
                                    "x": 100,
                                    "y": 100,
                                    "width": 700,
                                    "height": 100,
                                },
                            },
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ]
    )

    asyncio.run(
        SemanticUnitSlicer(
            config=Settings(openai_base_url="https://openrouter.ai/api/v1"),
            chat_client=client,
        ).slice_semantic_units(parsed)
    )

    assert client.options[0]["extra_body"] == {"provider": {"require_parameters": True}}
