import base64
import json
import logging
import re
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

import fitz
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from understand_anypaper.analyzers.structured_agent import StructuredAgent, StructuredAgentError
from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import PageSourceLocation, ParsedPaper, SemanticUnit

logger = logging.getLogger(__name__)

SemanticRole = Literal[
    "contribution",
    "motivation",
    "gap",
    "method",
    "experiment",
    "result",
    "conclusion",
    "background",
    "equation",
    "figure",
    "table",
    "reference",
]


class SourceLocatorKind(StrEnum):
    TEXT = "text"
    BBOX = "bbox"


class TextSourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[SourceLocatorKind.TEXT]
    start_text: str = Field(
        description=(
            "Exact visible text copied from the beginning of the semantic unit span."
        ),
    )
    end_text: str = Field(
        description=(
            "Exact visible text copied from the end of the semantic unit span."
        ),
    )


class BboxSourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[SourceLocatorKind.BBOX]
    x: float = Field(ge=0, le=1, description="Normalized left coordinate on the page image.")
    y: float = Field(ge=0, le=1, description="Normalized top coordinate on the page image.")
    width: float = Field(gt=0, le=1, description="Normalized width on the page image.")
    height: float = Field(gt=0, le=1, description="Normalized height on the page image.")


SourceLocator = Annotated[TextSourceLocator | BboxSourceLocator, Field(discriminator="kind")]


class SemanticUnitSourceLocationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1, description="1-indexed page number.")
    locator: SourceLocator = Field(
        description="How to locate this semantic unit source on the page.",
    )


class SemanticUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: SemanticRole
    title: str = Field(description="Short graph-node title for this semantic unit.")
    text: str = Field(description="Concise faithful restatement of this unit.")
    source_locations: list[SemanticUnitSourceLocationOutput] = Field(
        min_length=1,
        description="One or more page locations where this semantic unit appears.",
    )
    confidence: float = Field(ge=0, le=1)


class SemanticSliceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_units: list[SemanticUnitOutput]


_SEMANTIC_UNIT_SYSTEM_PROMPT = f"""\
You are a paper extractor in Understand-Anypaper, a project that generates a graph to help user learn/understand a paper.
You output **semantic units** in the paper. A sementic unit is a part of text that has some semantic meaning, e.g. a method
or a previous work, or a gap between vision and reality.

Types(role) of semantic units to extract:
- contribution: an author-claimed contribution or achievement.
- method: how the work implements a contribution: architecture, module design, etc.
- motivation: why the authors did something
- gap: a limitation, failure, missing capability, or unresolved trade-off in prior work.
- experiment
- result
- conclusion
- background: related works, sota performance, etc.
- equation
- figure
- table
- reference. Only include a biblography if it's referred to in another semantic unit. For example, citing DDPM in the background of Flux. Don't blindly output every single reference.

Return JSON. Schema:
{SemanticUnitOutput.model_json_schema()}

When outputting coordinates:
- page numbers are 1-indexed.
- bbox coordinates are normalized on the rendered page image, where x/y is the top-left corner.

When outputting source locations:
- For pure text roles, use locator.kind="text" with exact start_text and end_text anchors copied
  from the paper text on that page. The anchors should be short, distinctive visible strings
  at the beginning and ending of the semantic unit span.
- For figure and table roles, use locator.kind="bbox" with normalized x, y, width, and height.
"""



_CONTRIBUTION_REQUIRED_RETRY_PROMPT = """\
Your previous semantic slicing did not include any contribution role. Re-slice the same
paper and include at least one contribution unit when the paper contains author-claimed
contribution evidence, especially explicit contribution lists introduced by phrases such
as "the main contributions are", "our contributions", "we propose", or "we introduce".
"""


class SemanticUnitSlicer:
    """Image-backed semantic unit slicer."""

    def __init__(
        self,
        config: Settings = settings,
        agent: StructuredAgent | None = None,
    ) -> None:
        self._config = config
        self._agent_injected = agent is not None
        self._agent = agent or StructuredAgent(
            name="SemanticUnitSlicer",
            instructions=_SEMANTIC_UNIT_SYSTEM_PROMPT,
            config=config,
        )

    @property
    def available(self) -> bool:
        return self._agent_injected or bool(self._config.openai_api_key)

    def slice_semantic_units(self, parsed: ParsedPaper) -> list[SemanticUnit] | None:
        if not self.available or not parsed.pages:
            return None

        try:
            output = self._run(parsed)
        except StructuredAgentError as exc:
            logger.warning("LLM semantic slicing failed: %s", exc)
            return None

        if not self._has_contribution(output):
            logger.error("LLM semantic slicing returned no contribution units; retrying once")
            try:
                output = self._run(parsed, _CONTRIBUTION_REQUIRED_RETRY_PROMPT)
            except StructuredAgentError as exc:
                logger.warning("LLM semantic slicing failed: %s", exc)
                return None
            if not self._has_contribution(output):
                return None

        return self._semantic_units_from_output(parsed, output)

    def _run(self, parsed: ParsedPaper, retry_instruction: str = "") -> SemanticSliceOutput:
        if self._agent_injected:
            return self._agent.run(
                self._text_prompt(parsed, retry_instruction),
                SemanticSliceOutput,
                prompt_cache_key=f"semantic-slice:{parsed.paper_id}:{bool(retry_instruction)}",
            )
        if any(page.image_data for page in parsed.pages):
            return self._run_multimodal(parsed, retry_instruction)
        try:
            return self._agent.run(
                self._text_prompt(parsed, retry_instruction),
                SemanticSliceOutput,
                prompt_cache_key=f"semantic-slice:{parsed.paper_id}:{bool(retry_instruction)}",
            )
        except StructuredAgentError:
            raise

    def _run_multimodal(self, parsed: ParsedPaper, retry_instruction: str = "") -> SemanticSliceOutput:
        content: list[dict] = [{"type": "text", "text": self._text_prompt(parsed, retry_instruction)}]
        for page in parsed.pages:
            if not page.image_data:
                continue
            encoded = base64.b64encode(page.image_data).decode("ascii")
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"PAGE {page.page}: PDF size={page.width:.1f}x{page.height:.1f}; "
                        f"image size={page.image_width}x{page.image_height}."
                    ),
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{page.image_mime_type};base64,{encoded}"},
                }
            )

        payload = {
            "model": self._config.openai_model,
            "messages": [
                {"role": "system", "content": _SEMANTIC_UNIT_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "SemanticSliceOutput",
                    "strict": True,
                    "schema": SemanticSliceOutput.model_json_schema(),
                },
            },
        }
        url = f"{self._config.openai_base_url.rstrip('/')}/chat/completions"
        try:
            with httpx.Client(timeout=180) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._config.openai_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StructuredAgentError(f"OpenAI multimodal request failed: {exc}") from exc

        try:
            message = response.json()["choices"][0]["message"]["content"]
            data = json.loads(message if isinstance(message, str) else message[0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise StructuredAgentError("OpenAI multimodal response did not contain valid JSON") from exc
        validated = self._validated_output(data)
        if validated is None:
            raise StructuredAgentError("OpenAI multimodal response did not match semantic slice schema")
        return validated

    @staticmethod
    def _text_prompt(parsed: ParsedPaper, retry_instruction: str = "") -> str:
        page_summaries = "\n".join(
            f"- page {page.page}: pdf={page.width:.1f}x{page.height:.1f}, "
            f"image={page.image_width or 'none'}x{page.image_height or 'none'}"
            for page in parsed.pages
        )
        plain_text = parsed.metadata.get("plain_text")
        text_context = f"\n\nPlain text source:\n{plain_text[:20000]}" if isinstance(plain_text, str) else ""
        retry = f"\n\n{retry_instruction}" if retry_instruction else ""
        return (
            f"Title: {parsed.title}\n"
            f"Abstract: {parsed.abstract[:1600]}\n\n"
            f"Pages:\n{page_summaries}"
            f"{text_context}"
            f"{retry}"
        )

    def _semantic_units_from_output(
        self,
        parsed: ParsedPaper,
        output: SemanticSliceOutput,
    ) -> list[SemanticUnit] | None:
        units: list[SemanticUnit] = []
        prefix = parsed.paper_id[:8]
        doc = self._open_pdf(parsed)
        try:
            for index, item in enumerate(output.semantic_units, start=1):
                source_locations = self._clean_source_locations(parsed, item.source_locations, doc)
                if not source_locations:
                    continue
                units.append(
                    SemanticUnit(
                        semantic_unit_id=f"unit-{prefix}-{index}-{uuid4().hex[:8]}",
                        paper_id=parsed.paper_id,
                        role=item.role,
                        title=item.title.strip()[:160] or item.role.title(),
                        text=item.text.strip(),
                        source_locations=source_locations,
                        confidence=max(0.0, min(item.confidence, 1.0)),
                        created_by="semantic-unit-slicer-agent",
                    )
                )
        finally:
            if doc is not None:
                doc.close()
        return units or None

    @staticmethod
    def _validated_output(payload: dict | None) -> SemanticSliceOutput | None:
        if not payload:
            return None
        try:
            return SemanticSliceOutput.model_validate(payload)
        except ValidationError as exc:
            logger.warning("LLM semantic slicing returned invalid structured output: %s", exc)
            return None

    @staticmethod
    def _has_contribution(output: SemanticSliceOutput) -> bool:
        return any(unit.role == "contribution" for unit in output.semantic_units)

    @staticmethod
    def _open_pdf(parsed: ParsedPaper) -> fitz.Document | None:
        if parsed.source_media_type != "application/pdf" or not parsed.source_bytes:
            return None
        return fitz.open(stream=parsed.source_bytes, filetype="pdf")

    def _clean_source_locations(
        self,
        parsed: ParsedPaper,
        source_location_items: list[SemanticUnitSourceLocationOutput],
        doc: fitz.Document | None,
    ) -> list[PageSourceLocation]:
        valid_pages = {page.page: page for page in parsed.pages}
        source_locations: list[PageSourceLocation] = []
        seen: set[tuple[int, tuple[float, float, float, float]]] = set()
        for item in source_location_items:
            if item.page not in valid_pages:
                continue
            if isinstance(item.locator, TextSourceLocator):
                anchor_location = self._source_location_from_text_anchors(parsed, item, doc)
                if anchor_location is None:
                    continue
                key = (anchor_location.page, tuple(anchor_location.bbox))
                if key in seen:
                    continue
                seen.add(key)
                source_locations.append(anchor_location)
                continue
            bbox = self._bbox_locator_to_normalized(item.locator)
            if bbox is None:
                continue
            key = (item.page, tuple(bbox))
            if key in seen:
                continue
            seen.add(key)
            extracted_text = self._extract_text(parsed, item.page, bbox, doc)
            source_locations.append(
                PageSourceLocation(
                    page=item.page,
                    bbox=bbox,
                    extracted_text=extracted_text,
                    extraction_method="pymupdf_clip" if doc is not None else "plain_text",
                )
            )
        return source_locations

    def _source_location_from_text_anchors(
        self,
        parsed: ParsedPaper,
        item: SemanticUnitSourceLocationOutput,
        doc: fitz.Document | None,
    ) -> PageSourceLocation | None:
        if not isinstance(item.locator, TextSourceLocator):
            return None
        start_text = self._normalize_anchor(item.locator.start_text)
        end_text = self._normalize_anchor(item.locator.end_text)
        if not start_text:
            return None
        if doc is None:
            plain_text = parsed.metadata.get("plain_text")
            return PageSourceLocation(
                page=item.page,
                bbox=[0.0, 0.0, 1.0, 1.0],
                extracted_text=plain_text[:4000] if isinstance(plain_text, str) else "",
                start_text=start_text,
                end_text=end_text,
                extraction_method="plain_text_anchors",
            )
        if item.page < 1 or item.page > doc.page_count:
            return None

        page = doc.load_page(item.page - 1)
        start_rects = self._search_anchor_rects(page, start_text, "start")
        if not start_rects:
            return None
        end_rects = self._search_anchor_rects(page, end_text, "end") if end_text else []

        rect = self._rect_for_anchor_range(page, start_rects, end_rects)
        if rect is None:
            return None
        bbox = self._rect_to_normalized_bbox(page, rect)
        if bbox is None:
            return None
        extracted_text = self._extract_text(parsed, item.page, bbox, doc)
        return PageSourceLocation(
            page=item.page,
            bbox=bbox,
            extracted_text=extracted_text,
            start_text=start_text,
            end_text=end_text,
            extraction_method="pymupdf_text_anchors",
        )

    @staticmethod
    def _normalize_bbox(value: list[float]) -> list[float] | None:
        if len(value) != 4:
            return None
        ymin, xmin, ymax, xmax = [max(0.0, min(1.0, float(item))) for item in value]
        if ymax <= ymin or xmax <= xmin:
            return None
        return [round(ymin, 5), round(xmin, 5), round(ymax, 5), round(xmax, 5)]

    @classmethod
    def _bbox_locator_to_normalized(cls, locator: SourceLocator) -> list[float] | None:
        if not isinstance(locator, BboxSourceLocator):
            return None
        return cls._normalize_bbox(
            [
                locator.y,
                locator.x,
                locator.y + locator.height,
                locator.x + locator.width,
            ]
        )

    @staticmethod
    def _normalize_anchor(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _anchor_candidates(value: str, edge: Literal["start", "end"]) -> list[str]:
        text = re.sub(r"\s+", " ", value).strip()
        if not text:
            return []
        words = text.split()
        candidates = [text]
        if len(text) > 120:
            candidates.append(" ".join(words[:18] if edge == "start" else words[-18:]))
        if len(words) > 8:
            candidates.append(" ".join(words[:8] if edge == "start" else words[-8:]))
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))

    @classmethod
    def _search_anchor_rects(
        cls,
        page: fitz.Page,
        anchor: str,
        edge: Literal["start", "end"],
    ) -> list[fitz.Rect]:
        for candidate in cls._anchor_candidates(anchor, edge):
            rects = page.search_for(candidate)
            if rects:
                return sorted(rects, key=lambda rect: (rect.y0, rect.x0, rect.y1, rect.x1))
        return []

    @classmethod
    def _rect_for_anchor_range(
        cls,
        page: fitz.Page,
        start_rects: list[fitz.Rect],
        end_rects: list[fitz.Rect],
    ) -> fitz.Rect | None:
        start_rect = start_rects[0]
        end_rect = cls._first_rect_after(start_rect, end_rects) if end_rects else start_rect
        words = page.get_text("words", sort=True)
        if not words:
            return start_rect | end_rect

        start_index = cls._first_word_intersecting(words, start_rect)
        end_index = cls._last_word_intersecting(words, end_rect)
        if start_index is None or end_index is None or end_index < start_index:
            return start_rect | end_rect

        rect = fitz.Rect(words[start_index][:4])
        for word in words[start_index + 1 : end_index + 1]:
            rect |= fitz.Rect(word[:4])
        return rect

    @staticmethod
    def _first_rect_after(start_rect: fitz.Rect, rects: list[fitz.Rect]) -> fitz.Rect:
        for rect in sorted(rects, key=lambda item: (item.y0, item.x0, item.y1, item.x1)):
            if (rect.y0, rect.x0) >= (start_rect.y0, start_rect.x0):
                return rect
        return rects[-1] if rects else start_rect

    @staticmethod
    def _first_word_intersecting(words: list[tuple], rect: fitz.Rect) -> int | None:
        for index, word in enumerate(words):
            if fitz.Rect(word[:4]).intersects(rect):
                return index
        return None

    @staticmethod
    def _last_word_intersecting(words: list[tuple], rect: fitz.Rect) -> int | None:
        for index in range(len(words) - 1, -1, -1):
            if fitz.Rect(words[index][:4]).intersects(rect):
                return index
        return None

    @classmethod
    def _rect_to_normalized_bbox(cls, page: fitz.Page, rect: fitz.Rect) -> list[float] | None:
        if page.rect.width <= 0 or page.rect.height <= 0:
            return None
        return cls._normalize_bbox(
            [
                rect.y0 / page.rect.height,
                rect.x0 / page.rect.width,
                rect.y1 / page.rect.height,
                rect.x1 / page.rect.width,
            ]
        )

    @staticmethod
    def _extract_text(
        parsed: ParsedPaper,
        page_number: int,
        bbox: list[float],
        doc: fitz.Document | None,
    ) -> str:
        if doc is None:
            plain_text = parsed.metadata.get("plain_text")
            return plain_text[:4000] if isinstance(plain_text, str) else ""
        if page_number < 1 or page_number > doc.page_count:
            return ""
        page = doc.load_page(page_number - 1)
        ymin, xmin, ymax, xmax = bbox
        rect = fitz.Rect(
            xmin * page.rect.width,
            ymin * page.rect.height,
            xmax * page.rect.width,
            ymax * page.rect.height,
        )
        text = page.get_text("text", clip=rect)
        return re.sub(r"\s+", " ", text).strip()
