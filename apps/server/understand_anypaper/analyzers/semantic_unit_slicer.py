import base64
import json
import logging
import re
from typing import Literal
from uuid import uuid4

import fitz
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from understand_anypaper.analyzers.structured_agent import StructuredAgent, StructuredAgentError
from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import PageEvidence, ParsedPaper, SemanticUnit

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


class SemanticUnitEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1, description="1-indexed page number.")
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Normalized [ymin, xmin, ymax, xmax] coordinates on the page image.",
    )


class SemanticUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: SemanticRole
    title: str = Field(description="Short graph-node title for this semantic unit.")
    text: str = Field(description="Concise faithful restatement of this unit.")
    evidence: list[SemanticUnitEvidenceOutput] = Field(
        min_length=1,
        description="One or more page regions that visually support this semantic unit.",
    )
    confidence: float = Field(ge=0, le=1)


class SemanticSliceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_units: list[SemanticUnitOutput]


_SEMANTIC_UNIT_SYSTEM_PROMPT = """\
You slice a research paper into semantic argument units for a Paper Argument Graph.
Use the provided page images as the primary source. Each semantic unit has exactly
one role and must include at least one visual evidence region.

Coordinate convention:
- page numbers are 1-indexed.
- bbox is normalized [ymin, xmin, ymax, xmax] on the rendered page image.
- each coordinate must be between 0 and 1.
- bbox should tightly cover the region that supports the unit: contribution text,
  method description, equation, figure, table, experiment paragraph, result row, or
  reference entry.

Split mixed regions into separate units when they contain different roles, such as
a contribution claim and a method mechanism in the same paragraph. Keep units large
enough to be meaningful graph evidence, not sentence fragments.

Role standards:
- contribution: an author-claimed contribution or achieved advance. Prefer explicit
  claims from the abstract, introduction contribution list, method summary, or conclusion.
  If a page says "the main contributions are", "our contributions", or similar,
  classify each following contribution-list bullet as contribution even when it
  describes a method component or experimental finding.
- method: how the work implements a contribution: architecture, module design, training
  procedure, algorithmic step, indexing strategy, inference pipeline, or mechanism.
- motivation: why the work matters; demand, deployment pressure, practical need, or
  research objective.
- gap: a limitation, failure, missing capability, or unresolved trade-off in prior work.
- experiment: evaluation setup, datasets, baselines, metrics, ablations, or protocols.
- result: measured outcomes and comparisons, such as accuracy, PSNR, runtime, storage,
  or statistical improvements.
- conclusion: final summary, implication, or takeaway claimed by the authors.
- background: established context or prior-work description that is not itself a gap.
- equation: mathematical definition, formula, loss, objective, or derivation.
- figure: a figure region, figure caption, or prose whose primary purpose is to explain a figure.
- table: a table region, table caption, or prose whose primary purpose is to explain a table.
- reference: bibliography entries or citation-centered content about another work.

Return a JSON object with a semantic_units array. Each item must contain role, title,
text, evidence, and confidence. Do not invent content that is not visible in the page images.
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
                evidence = self._clean_evidence(parsed, item.evidence, doc)
                if not evidence:
                    continue
                units.append(
                    SemanticUnit(
                        semantic_unit_id=f"unit-{prefix}-{index}-{uuid4().hex[:8]}",
                        paper_id=parsed.paper_id,
                        role=item.role,
                        title=item.title.strip()[:160] or item.role.title(),
                        text=item.text.strip(),
                        evidence=evidence,
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

    def _clean_evidence(
        self,
        parsed: ParsedPaper,
        evidence_items: list[SemanticUnitEvidenceOutput],
        doc: fitz.Document | None,
    ) -> list[PageEvidence]:
        valid_pages = {page.page: page for page in parsed.pages}
        evidence: list[PageEvidence] = []
        seen: set[tuple[int, tuple[float, float, float, float]]] = set()
        for item in evidence_items:
            if item.page not in valid_pages:
                continue
            bbox = self._normalize_bbox(item.bbox)
            if bbox is None:
                continue
            key = (item.page, tuple(bbox))
            if key in seen:
                continue
            seen.add(key)
            extracted_text = self._extract_text(parsed, item.page, bbox, doc)
            evidence.append(
                PageEvidence(
                    page=item.page,
                    bbox=bbox,
                    extracted_text=extracted_text,
                    extraction_method="pymupdf_clip" if doc is not None else "plain_text",
                )
            )
        return evidence

    @staticmethod
    def _normalize_bbox(value: list[float]) -> list[float] | None:
        if len(value) != 4:
            return None
        ymin, xmin, ymax, xmax = [max(0.0, min(1.0, float(item))) for item in value]
        if ymax <= ymin or xmax <= xmin:
            return None
        return [round(ymin, 5), round(xmin, 5), round(ymax, 5), round(xmax, 5)]

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
