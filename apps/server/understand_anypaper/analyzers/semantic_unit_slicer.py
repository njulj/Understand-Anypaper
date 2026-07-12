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
from understand_anypaper.parser.models import (
    PageSourceLocation,
    PageSourceSegment,
    ParsedPaper,
    SemanticUnit,
)

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

PageCoordinate = Annotated[int, Field(ge=0, le=1000)]


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
    x: PageCoordinate = Field(
        description=(
            "For kind=bbox, left coordinate on the page image using a 0-1000 scale. "
            "For kind=text, use 0."
        ),
    )
    y: PageCoordinate = Field(
        description=(
            "For kind=bbox, top coordinate on the page image using a 0-1000 scale. "
            "For kind=text, use 0."
        ),
    )
    width: PageCoordinate = Field(
        description=(
            "For kind=bbox, width on the page image using a 0-1000 scale. "
            "For kind=text, use 0."
        ),
    )
    height: PageCoordinate = Field(
        description=(
            "For kind=bbox, height on the page image using a 0-1000 scale. "
            "For kind=text, use 0."
        ),
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
        description="The page location where this semantic unit appears. You can describe location by either text matching or bounding box, but not both. For text content, use text matching. Set kind=text and fill start_text and end_text. For formulas, figures and tables, use bounding box. Set kind=bbox and fill x, y, width, height.",
    )
    confidence: float


class SemanticSliceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_units: list[SemanticUnitOutput]


class SemanticUnitSlicingError(RuntimeError):
    """Raised when semantic unit slicing fails before usable units are produced."""


_ROLE_DEFINITIONS = """\
- contribution: an author-claimed contribution, achievement, or explicit contribution-list item.
  Make it a concrete claim/effect/design contribution, not just a framework or method name.
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
You output **semantic units** in the paper. A semantic unit is a part of continuous text (or figure, or table) that has some semantic meaning, e.g. a method or a previous work, or a gap between vision and reality.

## Finding semantic units

Produce a dense extraction, not a paper summary. Almost every argument-bearing sentence
in the abstract and main body should belong to a semantic unit. It is OK for the output
to be verbose: the UI can show contribution and method nodes first, then let users
expand evidence layer by layer.

Any description of a method, contribution, previous work, setup, etc. (full list of roles below)
should be made into a semantic unit, even if the same concept was already described somewhere else.

A description of a method is an SU. A formula that describes an algorithm is an SU.
A paragraph that explains a formula is an SU. A figure/table/caption that explains,
compares, or proves something is an SU.

For a full conference paper, sparse output such as 10-20 units is usually wrong. As a
calibration target:
- extract about 6-12 text units from each dense text page;
- extract every displayed equation as an equation unit;
- extract every proposed-method figure as a figure unit;
- extract every performance, runtime, energy, or ablation table as a table unit;
- extract the sentence(s) that interpret each important figure/table as result,
  qualitative_result, efficiency_analysis, ablation, or design_rationale units.

## Determining boundary of semantic units

Semantic units should be a single thing, e.g. one contribution, one method component,
one equation, one figure, one table, one experiment, one measured result, or one design
rationale. Prefer sentence-level units. Use two adjacent sentences only when the second
sentence cannot be understood without the first.

Do not make "a summary of contributions" as a semantic unit. If the paper has a
contribution list, split it into one semantic unit per numbered/bulleted contribution.

## Contribution quality

Contribution nodes must be informative graph nodes. Do not title a contribution with
only the method/framework name, such as "MuLUT framework" or "Proposed method".
Instead, title the concrete author-claimed contribution, such as:
- "Complementary indexing patterns enable multiple LUT cooperation"
- "Cascaded LUTs use re-indexing for hierarchical indexing"
- "MuLUT improves SR-LUT by up to 1.1 dB while preserving efficiency"
- "MuLUT extends to demosaicing with large gains over SR-LUT"

If a contribution sentence mainly reports a measurement, it may still be a contribution
when the authors present it as a main achievement, but also extract the detailed table
or result sentence as proof evidence.

## Proposed-method coverage

For each proposed method or method section, extract a small subgraph worth of units:
- one method_overview unit for the section-level idea;
- one method_component unit for each named module, indexing pattern, branch, block,
  mechanism, network structure, or pipeline stage;
- one algorithm or inference_strategy unit for each ordered retrieval/caching/indexing
  procedure;
- one training_strategy or implementation_detail unit for training, finetuning,
  sampling, losses, optimizers, quantization, or hyperparameters;
- one equation unit for each displayed formula and one nearby explanation unit when
  the text defines variables or explains why the formula matters;
- one figure unit for each method figure, including the caption and what the figure
  visually explains.

## Proof coverage

Treat evaluation tables and figures as first-class proof nodes. Every table comparing
methods, reporting runtime/energy, or showing ablations must be extracted as a table
unit with a bbox. The restatement should say what claim the table supports. For example,
a "Table 1" comparing many methods across benchmark datasets should become a table
unit whose text says that it is the standard-benchmark performance comparison and that
it supports the restoration-performance proof.

Also extract the nearby prose that interprets the table as result, efficiency_analysis,
baseline, metric, dataset, experimental_setup, or ablation units. Do not rely on one
table node alone to represent all experimental evidence.

## What to skip

Skip author lists, affiliations, acknowledgments, pure section headings, page headers,
page numbers, copyright text, and bibliography entries unless a bibliography entry is
needed as a cited reference node. Do not skip abstract/introduction claims, method
captions, equations, or table captions.

## Types(role) of semantic units to extract

{_ROLE_DEFINITIONS}

Return JSON. Schema:
{SemanticUnitOutput.model_json_schema()}

When outputting coordinates:
- page numbers are 1-indexed.
- bbox coordinates use a 0-1000 scale on the rendered page image, where x/y is the
  top-left corner. For example, x=100, y=200, width=300, height=150 means the box
  starts 10% from the left, 20% from the top, spans 30% page width, and spans 15%
  page height.

When outputting source locations:
- Each semantic unit must have exactly one source_location. If the same idea appears in
  multiple places, output separate semantic units instead of multiple locations.
- For pure text roles, use locator.kind="text" with exact start_text and end_text anchors copied
  from the paper text on that page. The anchors should be short, distinctive visible strings
  at the beginning and ending of the semantic unit span. Also set x=0, y=0, width=0, height=0.
- For figure and table roles, use locator.kind="bbox" with 0-1000 x, y, width, and height.
  Also set start_text="" and end_text="".
"""



_CONTRIBUTION_REQUIRED_RETRY_PROMPT = """\
Your previous semantic slicing did not include any contribution role. Re-slice the same
paper and include at least one contribution unit when the paper contains author-claimed
contribution evidence, especially explicit contribution lists introduced by phrases such
as "the main contributions are", "our contributions", "we propose", or "we introduce".
"""


def _dense_extraction_retry_prompt(previous_count: int, expected_count: int) -> str:
    return f"""\
Your previous semantic slicing returned only {previous_count} semantic units, which is
too sparse for this paper. Re-slice the same paper and return at least {expected_count}
units unless the paper is genuinely very short.

Important missing coverage to fix:
- split contribution lists into concrete contribution units, not framework-name nodes;
- include proposed-method descriptions, method components, algorithms, implementation
  details, training/finetuning details, formulas, and method figures;
- include evaluation tables as table units with bbox locators;
- if the paper contains a Table 1 comparing many methods on benchmark datasets, include
  that Table 1 as a table unit and phrase its text as proof evidence for performance;
- include prose that interprets tables/figures as result, efficiency_analysis,
  qualitative_result, or ablation units.
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

        expected_units = self._minimum_expected_units(parsed)
        if (
            self._chat_client is None
            and expected_units
            and len(output.semantic_units) < expected_units
        ):
            previous_count = len(output.semantic_units)
            logger.warning(
                "LLM semantic slicing returned only %s units; retrying for dense extraction",
                previous_count,
            )
            dense_output = await self._run_with_timeout(
                parsed,
                _dense_extraction_retry_prompt(previous_count, expected_units),
            )
            if self._has_contribution(dense_output) and len(dense_output.semantic_units) > previous_count:
                output = dense_output

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
        plain_text = SemanticUnitSlicer._plain_text_context(parsed)
        text_context = (
            "\n\nPage-numbered plain text source for coverage. Use page images for "
            "figure/table bboxes and visual layout; use this text to avoid skipping "
            f"sentences:\n{plain_text}"
            if plain_text
            else ""
        )
        retry = f"\n\n{retry_instruction}" if retry_instruction else ""
        return (
            f"Title: {parsed.title}\n"
            f"Abstract: {parsed.abstract[:1600]}\n\n"
            f"Pages:\n{page_summaries}"
            f"{text_context}"
            f"{retry}"
        )

    @staticmethod
    def _plain_text_context(parsed: ParsedPaper, max_chars: int = 60000) -> str:
        plain_text = parsed.metadata.get("plain_text")
        if isinstance(plain_text, str) and plain_text.strip():
            return plain_text[:max_chars]
        if parsed.source_media_type != "application/pdf" or not parsed.source_bytes:
            return ""
        try:
            doc = fitz.open(stream=parsed.source_bytes, filetype="pdf")
        except Exception:
            logger.exception("Failed to open PDF for semantic slicing text context")
            return ""
        chunks: list[str] = []
        total = 0
        try:
            for page_index, page in enumerate(doc, start=1):
                text = re.sub(r"\s+", " ", page.get_text("text")).strip()
                if not text:
                    continue
                chunk = f"[PAGE {page_index}] {text}"
                remaining = max_chars - total
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
        finally:
            doc.close()
        return "\n\n".join(chunks)

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
    def _minimum_expected_units(parsed: ParsedPaper) -> int:
        if parsed.source_media_type != "application/pdf" or len(parsed.pages) < 4:
            return 0
        return min(80, max(24, len(parsed.pages) * 4))

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
        words = self._ordered_page_words(page)
        normalized_word_stream = self._normalized_word_stream(words)
        start_spans = self._word_spans_for_anchor(
            page,
            words,
            start_text,
            "start",
            normalized_word_stream,
        )
        if not start_spans:
            return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)
        end_spans = (
            self._word_spans_for_anchor(
                page,
                words,
                end_text,
                "end",
                normalized_word_stream,
            )
            if end_text
            else []
        )

        selection = self._word_range_for_anchor_spans(words, start_spans, end_spans)
        if selection is not None:
            segment = self._page_source_segment_for_word_range(
                item.page,
                page,
                words,
                selection[0],
                selection[1],
                start_text=start_text,
                end_text=end_text,
                extraction_method="pymupdf_text_anchors",
            )
            if segment is not None:
                return self._location_from_segments([segment], start_text=start_text, end_text=end_text)

        cross_page = self._cross_page_source_location_from_text_anchors(
            item,
            doc,
            words,
            start_spans,
            start_text,
            end_text,
        )
        if cross_page is not None:
            return cross_page
        return self._fallback_text_anchor_location(parsed, item, doc, start_text, end_text)

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

    def _cross_page_source_location_from_text_anchors(
        self,
        item: SemanticUnitSourceLocationOutput,
        doc: fitz.Document,
        start_page_words: list[tuple],
        start_spans: list[tuple[int, int]],
        start_text: str,
        end_text: str,
    ) -> PageSourceLocation | None:
        if not end_text or not start_spans:
            return None
        start_index = start_spans[0][0]
        start_page = doc.load_page(item.page - 1)
        segments: list[PageSourceSegment] = []
        first_segment = self._page_source_segment_for_word_range(
            item.page,
            start_page,
            start_page_words,
            start_index,
            len(start_page_words) - 1,
            start_text=start_text,
            end_text="",
            extraction_method="pymupdf_text_anchors_cross_page",
        )
        if first_segment is None:
            return None
        segments.append(first_segment)

        max_page = min(doc.page_count, item.page + 2)
        for page_number in range(item.page + 1, max_page + 1):
            page = doc.load_page(page_number - 1)
            words = self._ordered_page_words(page)
            if not words:
                continue
            normalized_word_stream = self._normalized_word_stream(words)
            end_spans = self._word_spans_for_anchor(
                page,
                words,
                end_text,
                "end",
                normalized_word_stream,
            )
            if end_spans:
                final_segment = self._page_source_segment_for_word_range(
                    page_number,
                    page,
                    words,
                    0,
                    end_spans[0][1],
                    start_text="",
                    end_text=end_text,
                    extraction_method="pymupdf_text_anchors_cross_page",
                )
                if final_segment is None:
                    return None
                segments.append(final_segment)
                return self._location_from_segments(
                    segments,
                    start_text=start_text,
                    end_text=end_text,
                    extraction_method="pymupdf_text_anchors_cross_page",
                )

            middle_segment = self._page_source_segment_for_word_range(
                page_number,
                page,
                words,
                0,
                len(words) - 1,
                start_text="",
                end_text="",
                extraction_method="pymupdf_text_anchors_cross_page",
            )
            if middle_segment is not None:
                segments.append(middle_segment)
        return None

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
                locator.y / 1000,
                locator.x / 1000,
                (locator.y + locator.height) / 1000,
                (locator.x + locator.width) / 1000,
            ]
        )

    @staticmethod
    def _normalize_anchor(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _normalize_text_for_anchor_match(value: str) -> str:
        text = re.sub(r"\s+", " ", value).strip().casefold()
        return re.sub(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])", "", text)

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
    def _word_spans_for_anchor(
        cls,
        page: fitz.Page,
        words: list[tuple],
        anchor: str,
        edge: Literal["start", "end"],
        normalized_word_stream: tuple[str, list[int | None]] | None = None,
    ) -> list[tuple[int, int]]:
        rects = cls._search_anchor_rects(page, anchor, edge)
        spans = cls._word_spans_intersecting_rects(words, rects)
        if spans:
            return spans
        return cls._word_spans_from_normalized_anchor(
            words,
            anchor,
            edge,
            normalized_word_stream,
        )

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

    @classmethod
    def _word_range_for_anchor_spans(
        cls,
        words: list[tuple],
        start_spans: list[tuple[int, int]],
        end_spans: list[tuple[int, int]],
    ) -> tuple[int, int] | None:
        if not words or not start_spans:
            return None
        start_index = start_spans[0][0]
        if not end_spans:
            return start_index, start_spans[0][1]
        for span_start, span_end in end_spans:
            if span_end >= start_index:
                return start_index, max(span_end, start_index)
        return None

    @classmethod
    def _word_spans_from_normalized_anchor(
        cls,
        words: list[tuple],
        anchor: str,
        edge: Literal["start", "end"],
        normalized_word_stream: tuple[str, list[int | None]] | None = None,
    ) -> list[tuple[int, int]]:
        text, char_word_indices = normalized_word_stream or cls._normalized_word_stream(words)
        if not text:
            return []
        spans: list[tuple[int, int]] = []
        for candidate in cls._anchor_candidates(anchor, edge):
            needle = cls._normalize_text_for_anchor_match(candidate)
            if not needle:
                continue
            start = 0
            while True:
                match_start = text.find(needle, start)
                if match_start < 0:
                    break
                match_end = match_start + len(needle)
                word_indices = [
                    index
                    for index in char_word_indices[match_start:match_end]
                    if index is not None
                ]
                if word_indices:
                    spans.append((min(word_indices), max(word_indices)))
                start = match_start + 1
        return sorted(set(spans))

    @classmethod
    def _ordered_page_words(cls, page: fitz.Page) -> list[tuple]:
        words = list(page.get_text("words"))
        if not words:
            return []
        blocks: dict[int, list[tuple]] = {}
        for word in words:
            block_index = int(word[5]) if len(word) > 5 else 0
            blocks.setdefault(block_index, []).append(word)

        block_items = []
        for block_words in blocks.values():
            x0 = min(float(word[0]) for word in block_words)
            y0 = min(float(word[1]) for word in block_words)
            x1 = max(float(word[2]) for word in block_words)
            y1 = max(float(word[3]) for word in block_words)
            block_items.append(
                {
                    "bbox": (x0, y0, x1, y1),
                    "words": sorted(
                        block_words,
                        key=lambda word: (
                            int(word[6]) if len(word) > 6 else 0,
                            int(word[7]) if len(word) > 7 else 0,
                            float(word[1]),
                            float(word[0]),
                        ),
                    ),
                }
            )

        if len(block_items) < 4:
            return sorted(words, key=lambda word: (float(word[1]), float(word[0])))

        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        mid_x = page_width / 2
        narrow_blocks = [
            block
            for block in block_items
            if (block["bbox"][2] - block["bbox"][0]) < page_width * 0.72
        ]
        left = [block for block in narrow_blocks if (block["bbox"][0] + block["bbox"][2]) / 2 < mid_x]
        right = [block for block in narrow_blocks if (block["bbox"][0] + block["bbox"][2]) / 2 >= mid_x]
        two_column = len(left) >= 2 and len(right) >= 2
        if not two_column:
            ordered_blocks = sorted(block_items, key=lambda block: (block["bbox"][1], block["bbox"][0]))
        else:
            def block_key(block: dict) -> tuple[int, float, float]:
                x0, y0, x1, _ = block["bbox"]
                width = x1 - x0
                center_x = (x0 + x1) / 2
                is_wide = width >= page_width * 0.72
                if is_wide and y0 < page_height * 0.25:
                    return (-1, y0, x0)
                if is_wide:
                    return (2, y0, x0)
                return (0 if center_x < mid_x else 1, y0, x0)

            ordered_blocks = sorted(block_items, key=block_key)

        ordered_words: list[tuple] = []
        for block in ordered_blocks:
            ordered_words.extend(block["words"])
        return ordered_words

    @classmethod
    def _normalized_word_stream(cls, words: list[tuple]) -> tuple[str, list[int | None]]:
        chars: list[str] = []
        char_word_indices: list[int | None] = []
        for word_index, word in enumerate(words):
            word_text = str(word[4]).strip().casefold()
            if not word_text:
                continue
            if chars:
                if cls._should_join_hyphenated_words(chars, word_text):
                    chars.pop()
                    char_word_indices.pop()
                else:
                    chars.append(" ")
                    char_word_indices.append(None)
            for character in word_text:
                chars.append(character)
                char_word_indices.append(word_index)
        return "".join(chars), char_word_indices

    @staticmethod
    def _should_join_hyphenated_words(chars: list[str], next_word: str) -> bool:
        return (
            len(chars) >= 2
            and chars[-1] == "-"
            and chars[-2].isalpha()
            and bool(next_word)
            and next_word[0].isalpha()
        )

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
        return SemanticUnitSlicer._normalize_text_for_anchor_match(
            anchor
        ) in SemanticUnitSlicer._normalize_text_for_anchor_match(text)

    @classmethod
    def _page_source_segment_for_word_range(
        cls,
        page_number: int,
        page: fitz.Page,
        words: list[tuple],
        start_index: int,
        end_index: int,
        *,
        start_text: str,
        end_text: str,
        extraction_method: str,
    ) -> PageSourceSegment | None:
        extracted_text = cls._text_from_words(words[start_index : end_index + 1])
        if start_text and not cls._text_contains_anchor(extracted_text, start_text):
            return None
        if end_text and not cls._text_contains_anchor(extracted_text, end_text):
            return None
        rect = cls._rect_for_word_range(words, start_index, end_index)
        if rect is None:
            return None
        bbox = cls._rect_to_normalized_bbox(page, rect)
        if bbox is None:
            return None
        return PageSourceSegment(
            page=page_number,
            bbox=bbox,
            extracted_text=extracted_text,
            start_text=start_text,
            end_text=end_text,
            extraction_method=extraction_method,
        )

    @staticmethod
    def _location_from_segments(
        segments: list[PageSourceSegment],
        *,
        start_text: str,
        end_text: str,
        extraction_method: str | None = None,
    ) -> PageSourceLocation | None:
        if not segments:
            return None
        primary = segments[0]
        combined_text = "\n\n".join(segment.extracted_text for segment in segments if segment.extracted_text).strip()
        return PageSourceLocation(
            page=primary.page,
            bbox=primary.bbox,
            extracted_text=combined_text or primary.extracted_text,
            start_text=start_text,
            end_text=end_text,
            extraction_method=extraction_method or primary.extraction_method,
            segments=segments,
        )

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
