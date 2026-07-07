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
                                    "x": 0.1,
                                    "y": 0.1,
                                    "width": 0.7,
                                    "height": 0.1,
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
                                    "x": 0.1,
                                    "y": 0.1,
                                    "width": 0.7,
                                    "height": 0.1,
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
