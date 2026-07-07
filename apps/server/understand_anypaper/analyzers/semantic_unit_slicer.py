import asyncio
import logging
import re
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import uuid4

import fitz
from agent_framework import Agent, Content, Message, SupportsChatGetResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from understand_anypaper.analyzers.llm import create_chat_client, run_structured
from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import PageSourceLocation, ParsedPaper, SemanticUnit

logger = logging.getLogger(__name__)

SemanticRole = Literal[
    "contribution",
    "claim",
    "motivation",
    "problem",
    "gap",
    "background",
    "prior_work",
    "definition",
    "observation",
    "design_rationale",
    "method",
    "method_overview",
    "method_component",
    "algorithm",
    "implementation_detail",
    "training_strategy",
    "inference_strategy",
    "equation",
    "figure",
    "table",
    "experimental_setup",
    "dataset",
    "metric",
    "baseline",
    "experiment",
    "ablation",
    "result",
    "qualitative_result",
    "efficiency_analysis",
    "extension",
    "conclusion",
    "reference",
]

NormalizedFloat = Annotated[float, Field(ge=0, le=1)]


class SourceLocatorKind(StrEnum):
    TEXT = "text"
    BBOX = "bbox"


class SourceLocatorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceLocatorKind
    start_text: str = Field(
        description=(
            "For kind=text, exact visible text copied from the beginning of the semantic "
            "unit span. For kind=bbox, use an empty string."
        ),
    )
    end_text: str = Field(
        description=(
            "For kind=text, exact visible text copied from the end of the semantic unit "
            "span. For kind=bbox, use an empty string."
        ),
    )
    x: NormalizedFloat = Field(
        description=(
            "For kind=bbox, normalized left coordinate on the page image. "
            "For kind=text, use 0."
        ),
    )
    y: NormalizedFloat = Field(
        description=(
            "For kind=bbox, normalized top coordinate on the page image. "
            "For kind=text, use 0."
        ),
    )
    width: NormalizedFloat = Field(
        description="For kind=bbox, normalized width on the page image. For kind=text, use 0.",
    )
    height: NormalizedFloat = Field(
        description="For kind=bbox, normalized height on the page image. For kind=text, use 0.",
    )

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Self:
        if self.kind == SourceLocatorKind.TEXT:
            if not self.start_text.strip():
                raise ValueError("text locator requires start_text")
            return self
        return self


class SemanticUnitSourceLocationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(description="1-indexed page number.")
    locator: SourceLocatorOutput = Field(
        description="How to locate this semantic unit source on the page.",
    )


class SemanticUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: SemanticRole
    title: str = Field(description="Short graph-node title for this semantic unit.")
    text: str = Field(description="Concise faithful restatement of this unit.")
    source_location: SemanticUnitSourceLocationOutput = Field(
        description="The page location where this semantic unit appears.",
    )
    confidence: float


class SemanticSliceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_units: list[SemanticUnitOutput]


class SemanticUnitSlicingError(RuntimeError):
    """Raised when semantic unit slicing fails before usable units are produced."""


_ROLE_DEFINITIONS = """\
- contribution: an author-claimed contribution, achievement, or explicit contribution-list item.
- claim: an important author assertion that is not itself a contribution or measured result.
- motivation: why the authors care about the problem or design goal.
- problem: the task, setting, practical constraint, or problem formulation being addressed.
- gap: a limitation, failure, missing capability, or unresolved trade-off in prior work.
- background: general domain context needed to understand the paper.
- prior_work: a sentence describing a specific previous method, family of methods, or cited work.
- definition: a definition of a concept, operator, notation, task, or metric.
- observation: an empirical or conceptual observation that motivates a design choice.
- design_rationale: why a proposed method component is designed a certain way.
- method: legacy broad method tag; prefer one of the more specific method roles below when possible.
- method_overview: a high-level description of the proposed approach or pipeline.
- method_component: a concrete module, architecture block, indexing pattern, mechanism, or data flow.
- algorithm: an ordered procedure, retrieval process, optimization process, or pseudocode-like step.
- implementation_detail: hyperparameters, optimizer, loss, sampling interval, data type, storage detail, or engineering choice.
- training_strategy: how the model/LUT is trained or finetuned.
- inference_strategy: how the trained/cached method is used at test time.
- equation: a displayed or inline formula and its immediate mathematical meaning.
- figure: a figure or figure caption as a visual evidence unit.
- table: a table or table caption as a structured evidence unit.
- experimental_setup: evaluation protocol, hardware, task setup, train/test split, or comparison setup.
- dataset: dataset names, sizes, sources, or dataset construction.
- metric: evaluation metric or measurement definition.
- baseline: compared method, baseline variant, or comparison group.
- experiment: an experiment being run, excluding its numeric outcome.
- ablation: an ablation factor, controlled variant, or component-effect study.
- result: quantitative outcome, measured improvement, or table-backed finding.
- qualitative_result: visual-quality finding or figure-backed perceptual comparison.
- efficiency_analysis: runtime, energy, memory, LUT size, complexity, or deployment-efficiency evidence.
- extension: applying or adapting the method to a second task/domain.
- conclusion: final takeaway, implication, or closing summary.
- reference: a bibliography entry only when it is cited by another semantic unit.
"""


_SEMANTIC_UNIT_SYSTEM_PROMPT = f"""\
You are a paper extractor in Understand-Anypaper, a project that generates a graph to help user learn/understand a paper.
You output **semantic units** in the paper. A sementic unit is a part of continuous text (or figure, or table) that has some semantic meaning, e.g. a method or a previous work, or a gap between vision and reality.

## Finding semantic units

Aim to cover every argument-bearing sentence in the abstract and body. It is OK for
the output to be verbose: the UI can show contribution and method nodes first, then
let users expand evidence layer by layer.

Any description of a method, contribution, previous work, setup, etc. (full list of roles below)
should be made into a semantic unit, even if the same concept was already described somewhere else.

A description of a method is an SU. A formula that describes an algorithm is an SU. A paragraph that explains a formula is an SU.

## Determining boundary of semantic units

Semantic units should be a single thing, e.g. one contribution, or one method, or one experiment.
Do not make "a summary of contributions" as a semantic unit.

## Types(role) of semantic units to extract

{_ROLE_DEFINITIONS}

Return JSON. Schema:
{SemanticUnitOutput.model_json_schema()}

When outputting coordinates:
- page numbers are 1-indexed.
- bbox coordinates are normalized on the rendered page image, where x/y is the top-left corner.

When outputting source locations:
- Each semantic unit must have exactly one source_location. If the same idea appears in
  multiple places, output separate semantic units instead of multiple locations.
- For pure text roles, use locator.kind="text" with exact start_text and end_text anchors copied
  from the paper text on that page. The anchors should be short, distinctive visible strings
  at the beginning and ending of the semantic unit span. Also set x=0, y=0, width=0, height=0.
- For figure and table roles, use locator.kind="bbox" with normalized x, y, width, and height.
  Also set start_text="" and end_text="".
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
        chat_client: SupportsChatGetResponse | None = None,
    ) -> None:
        self._config = config
        self._chat_client = chat_client

    @property
    def available(self) -> bool:
        return self._chat_client is not None or bool(self._config.openai_api_key)

    async def slice_semantic_units(self, parsed: ParsedPaper) -> list[SemanticUnit]:
        if not self.available:
            raise RuntimeError("LLM semantic slicing requires OPENAI_API_KEY or PAG_OPENAI_API_KEY")
        if not parsed.pages:
            raise RuntimeError("LLM semantic slicing requires rendered document pages")

        output = await self._run_with_timeout(parsed)
        if not self._has_contribution(output):
            logger.error("LLM semantic slicing returned no contribution units; retrying once")
            output = await self._run_with_timeout(parsed, _CONTRIBUTION_REQUIRED_RETRY_PROMPT)
            if not self._has_contribution(output):
                raise SemanticUnitSlicingError(
                    "LLM semantic slicing returned no contribution units after retry"
                )

        return self._semantic_units_from_output(parsed, output)

    async def _run_with_timeout(
        self,
        parsed: ParsedPaper,
        retry_instruction: str = "",
    ) -> SemanticSliceOutput:
        try:
            return await asyncio.wait_for(
                self._run(parsed, retry_instruction),
                timeout=self._config.llm_request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise SemanticUnitSlicingError(
                f"LLM semantic slicing timed out after "
                f"{self._config.llm_request_timeout_seconds:g} seconds"
            ) from exc

    async def _run(self, parsed: ParsedPaper, retry_instruction: str = "") -> SemanticSliceOutput:
        session_id = f"semantic-slice:{parsed.paper_id}"
        agent = Agent(
            client=self._chat_client or create_chat_client(self._config, session_id=session_id),
            name="SemanticUnitSlicer",
            instructions=_SEMANTIC_UNIT_SYSTEM_PROMPT,
        )
        # Page images form the stable (cacheable) prefix; the text prompt goes
        # last because it varies between the first attempt and the retry.
        contents: list[Content] = []
        for page in parsed.pages:
            if not page.image_data:
                continue
            contents.append(
                Content.from_text(
                    f"PAGE {page.page}: PDF size={page.width:.1f}x{page.height:.1f}; "
                    f"image size={page.image_width}x{page.image_height}."
                )
            )
            contents.append(Content.from_data(page.image_data, page.image_mime_type))
        contents.append(Content.from_text(self._text_prompt(parsed, retry_instruction)))
        return await run_structured(
            agent,
            Message(role="user", contents=contents),
            SemanticSliceOutput,
            base_url=self._config.openai_base_url,
            prompt_cache_key=session_id,
        )

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
    ) -> list[SemanticUnit]:
        units: list[SemanticUnit] = []
        rejected_units = 0
        prefix = parsed.paper_id[:8]
        doc = self._open_pdf(parsed)
        try:
            for index, item in enumerate(output.semantic_units, start=1):
                source_location = self._clean_source_location(parsed, item.source_location, doc)
                if source_location is None:
                    rejected_units += 1
                    continue
                units.append(
                    SemanticUnit(
                        semantic_unit_id=f"unit-{prefix}-{index}-{uuid4().hex[:8]}",
                        paper_id=parsed.paper_id,
                        role=item.role,
                        title=item.title.strip()[:160] or item.role.title(),
                        text=item.text.strip(),
                        source_location=source_location,
                        confidence=max(0.0, min(item.confidence, 1.0)),
                        created_by="semantic-unit-slicer-agent",
                    )
                )
        finally:
            if doc is not None:
                doc.close()
        if not units:
            raise RuntimeError(
                "LLM semantic slicing produced no usable source locations "
                f"({rejected_units} semantic units were rejected)"
            )
        if rejected_units:
            logger.warning("Dropped %s semantic units with unusable source locations", rejected_units)
        return units

    @staticmethod
    def _has_contribution(output: SemanticSliceOutput) -> bool:
        return any(unit.role == "contribution" for unit in output.semantic_units)

    @staticmethod
    def _open_pdf(parsed: ParsedPaper) -> fitz.Document | None:
        if parsed.source_media_type != "application/pdf" or not parsed.source_bytes:
            return None
        return fitz.open(stream=parsed.source_bytes, filetype="pdf")

    def _clean_source_location(
        self,
        parsed: ParsedPaper,
        item: SemanticUnitSourceLocationOutput,
        doc: fitz.Document | None,
    ) -> PageSourceLocation | None:
        valid_pages = {page.page: page for page in parsed.pages}
        if item.page not in valid_pages:
            return None
        if item.locator.kind == SourceLocatorKind.TEXT:
            return self._source_location_from_text_anchors(parsed, item, doc)
        bbox = self._bbox_locator_to_normalized(item.locator)
        if bbox is None:
            return None
        extracted_text = self._extract_text(parsed, item.page, bbox, doc)
        return PageSourceLocation(
            page=item.page,
            bbox=bbox,
            extracted_text=extracted_text,
            extraction_method="pymupdf_clip" if doc is not None else "plain_text",
        )

    def _source_location_from_text_anchors(
        self,
        parsed: ParsedPaper,
        item: SemanticUnitSourceLocationOutput,
        doc: fitz.Document | None,
    ) -> PageSourceLocation | None:
        if item.locator.kind != SourceLocatorKind.TEXT:
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
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        end_rects = self._search_anchor_rects(page, end_text, "end") if end_text else []

        words = page.get_text("words", sort=True)
        selection = self._word_range_for_anchor_rects(words, start_rects, end_rects)
        if selection is None:
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        start_index, end_index = selection
        extracted_text = self._text_from_words(words[start_index : end_index + 1])
        if not self._text_contains_anchor(extracted_text, start_text):
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        if end_text and not self._text_contains_anchor(extracted_text, end_text):
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)

        rect = self._rect_for_word_range(words, start_index, end_index)
        if rect is None:
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        bbox = self._rect_to_normalized_bbox(page, rect)
        if bbox is None:
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        return PageSourceLocation(
            page=item.page,
            bbox=bbox,
            extracted_text=extracted_text,
            start_text=start_text,
            end_text=end_text,
            extraction_method="pymupdf_text_anchors",
        )

    def _fallback_text_anchor_location(
        self,
        parsed: ParsedPaper,
        item: SemanticUnitSourceLocationOutput,
        doc: fitz.Document | None,
        start_text: str,
        end_text: str,
    ) -> PageSourceLocation | None:
        if item.page < 1:
            return None
        if doc is None:
            plain_text = parsed.metadata.get("plain_text")
            extracted_text = plain_text[:4000] if isinstance(plain_text, str) else ""
        elif item.page <= doc.page_count:
            extracted_text = re.sub(
                r"\s+",
                " ",
                doc.load_page(item.page - 1).get_text("text"),
            ).strip()[:4000]
        else:
            return None
        return PageSourceLocation(
            page=item.page,
            bbox=[0.0, 0.0, 1.0, 1.0],
            extracted_text=extracted_text,
            start_text=start_text,
            end_text=end_text,
            extraction_method="unresolved_text_anchors",
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
    def _bbox_locator_to_normalized(cls, locator: SourceLocatorOutput) -> list[float] | None:
        if locator.kind != SourceLocatorKind.BBOX:
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
    def _word_range_for_anchor_rects(
        cls,
        words: list[tuple],
        start_rects: list[fitz.Rect],
        end_rects: list[fitz.Rect],
    ) -> tuple[int, int] | None:
        if not words:
            return None
        start_spans = cls._word_spans_intersecting_rects(words, start_rects)
        if not start_spans:
            return None
        start_index = start_spans[0][0]
        if not end_rects:
            return start_index, start_spans[0][1]
        for span_start, span_end in cls._word_spans_intersecting_rects(words, end_rects):
            if span_end >= start_index:
                return start_index, max(span_end, start_index)
        return None

    @staticmethod
    def _word_spans_intersecting_rects(
        words: list[tuple],
        rects: list[fitz.Rect],
    ) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for rect in sorted(rects, key=lambda item: (item.y0, item.x0, item.y1, item.x1)):
            indices = [
                index
                for index, word in enumerate(words)
                if fitz.Rect(word[:4]).intersects(rect)
            ]
            if indices:
                spans.append((min(indices), max(indices)))
        return sorted(set(spans))

    @staticmethod
    def _rect_for_word_range(words: list[tuple], start_index: int, end_index: int) -> fitz.Rect | None:
        if not words or end_index < start_index:
            return None
        rect = fitz.Rect(words[start_index][:4])
        for word in words[start_index + 1 : end_index + 1]:
            rect |= fitz.Rect(word[:4])
        return fitz.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + 1, rect.y1 + 1)

    @staticmethod
    def _text_from_words(words: list[tuple]) -> str:
        return re.sub(r"\s+", " ", " ".join(str(word[4]) for word in words)).strip()

    @staticmethod
    def _text_contains_anchor(text: str, anchor: str) -> bool:
        return re.sub(r"\s+", " ", anchor).strip().casefold() in re.sub(
            r"\s+",
            " ",
            text,
        ).strip().casefold()

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
