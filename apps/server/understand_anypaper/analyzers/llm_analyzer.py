import json
import logging
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit, SourceBlock, SourceRange

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


class SemanticUnitSourceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_block_id: str = Field(description="ID of the source block containing this evidence.")
    quote: str = Field(description="Exact copied text span from that source block.")


class SemanticUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: SemanticRole
    title: str = Field(description="Short graph-node title for this semantic unit.")
    text: str = Field(
        description="Concise faithful restatement of this unit, preserving key terms."
    )
    source_quotes: list[SemanticUnitSourceQuote] = Field(
        min_length=1, description="One or more copied text spans that support this unit."
    )
    confidence: float = Field(ge=0, le=1)


class SemanticSliceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_units: list[SemanticUnitOutput]


_SEMANTIC_UNIT_SYSTEM_PROMPT = """\
You slice a research paper into semantic argument units for a Paper Argument Graph.
Use only the provided source_block_id values. Each semantic unit has exactly one role.
Split mixed paragraphs into separate units when they contain different roles, such as
a contribution claim and a method mechanism in the same source block. Keep units large
enough to be meaningful graph evidence, not sentence fragments.

Role standards:
- contribution: an author-claimed contribution or achieved advance. Prefer explicit
  claims from the abstract, introduction contribution list, method summary, or conclusion.
  A contribution often states the paper's result-level novelty or value: a new framework,
  module, algorithm, capability, benchmark result, efficiency gain, or demonstrated
  generality. If a source block says "the main contributions are", "our contributions",
  or similar, classify each following contribution-list bullet as contribution even when
  it describes a method component or experimental finding. Do not label every
  implementation detail as a contribution outside such author-claimed contexts.
- method: how the work implements a contribution: architecture, module design, training
  procedure, algorithmic step, indexing strategy, inference pipeline, or mechanism. Method
  units explain what is done internally, not the headline claim that it is novel or better.
- motivation: why the work matters; demand, deployment pressure, practical need, or
  research objective.
- gap: a limitation, failure, missing capability, or unresolved trade-off in prior work.
- experiment: evaluation setup, datasets, baselines, metrics, ablations, or protocols.
- result: measured outcomes and comparisons, such as accuracy, PSNR, runtime, storage,
  or statistical improvements.
- conclusion: final summary, implication, or takeaway claimed by the authors.
- background: established context or prior-work description that is not itself a gap.
- equation: mathematical definition, formula, loss, objective, or derivation.
- figure: a figure caption or prose whose primary purpose is to explain a figure.
- table: a table caption or prose whose primary purpose is to explain a table.
- reference: bibliography entries or citation-centered content about another work.

Example:
- 'We propose ShiftLUT, a framework that improves the receptive-field/efficiency trade-off'
  is contribution; 'LSS applies learnable channel-wise spatial shifts to feature maps' is method.
- 'MuLUT enables cooperation of multiple LUTs to overcome single-LUT limits' is contribution;
  'complementary indexing and hierarchical re-indexing connect multiple LUTs' is method.

Return a JSON object with a semantic_units array. Each item must contain role, title, text,
source_quotes, and confidence. Each source_quotes item must contain source_block_id and quote.
For source_quotes, copy short exact spans from the provided source block text. Do not invent
character offsets; the server will match quoted spans back to start_char/end_char.

Example output:
{
  "semantic_units": [
    {
      "role": "contribution",
      "title": "ShiftLUT expands receptive field efficiently",
      "text": "The paper proposes ShiftLUT to expand the receptive field while preserving efficiency.",
      "source_quotes": [
        {
          "source_block_id": "paper-block12",
          "quote": "we propose ShiftLUT, a novel framework that attains the largest receptive field among all LUT-based methods while maintaining high efficiency"
        }
      ],
      "confidence": 0.92
    },
    {
      "role": "method",
      "title": "Learnable Spatial Shift module",
      "text": "LSS expands the effective receptive field by applying learnable channel-wise spatial offsets.",
      "source_quotes": [
        {
          "source_block_id": "paper-block13",
          "quote": "LSS introduces channel-wise spatial diversity into the LUT by learning a distinct spatial offset for each feature channel"
        }
      ],
      "confidence": 0.9
    }
  ]
}
"""

# _CONTRIBUTION_REQUIRED_RETRY_PROMPT = """\
# Your previous semantic slicing did not include any contribution role. Re-slice the same
# paper and include at least one contribution unit when the paper contains author-claimed
# contribution evidence, especially explicit contribution lists introduced by phrases such
# as "the main contributions are", "our contributions", "we propose", or "we introduce".
# The bullets following an explicit contribution-list header should be contribution units,
# not only method or result units.
# """


class LLMAnalyzer:
    """OpenAI-compatible analyzer that performs semantic slicing.

    The PAG builder intentionally depends on this output instead of assigning
    semantic roles to parser blocks with rules.
    """

    def __init__(self, config: Settings = settings) -> None:
        self._config = config

    @property
    def available(self) -> bool:
        return bool(self._config.openai_api_key)

    def slice_semantic_units(self, parsed: ParsedPaper) -> list[SemanticUnit] | None:
        if not self.available or not parsed.source_blocks:
            return None
        blocks = "\n\n".join(
            f"[{block.source_block_id}] page={block.page} section={block.section or 'none'} "
            f"type={block.block_type}\n{block.text[:1600]}"
            for block in parsed.source_blocks[:120]
        )
        user_prompt = (
            f"Title: {parsed.title}\nAbstract: {parsed.abstract[:1600]}\n\nSource blocks:\n{blocks}"
        )
        payload = self._chat_json(
            _SEMANTIC_UNIT_SYSTEM_PROMPT,
            user_prompt,
        )
        output = self._validated_output(payload)
        if output is None:
            return None
        if not self._has_contribution(output):
            logger.error("LLM semantic slicing returned no contribution units; retrying once")
            return None
            # retry_payload = self._chat_json(
            #     f"{_SEMANTIC_UNIT_SYSTEM_PROMPT}\n\n{_CONTRIBUTION_REQUIRED_RETRY_PROMPT}",
            #     user_prompt,
            # )
            # retry_output = self._validated_output(retry_payload)
            # if retry_output is not None:
            #     output = retry_output

        known_blocks = {block.source_block_id: block for block in parsed.source_blocks}
        units: list[SemanticUnit] = []
        prefix = parsed.paper_id[:8]
        for index, item in enumerate(output.semantic_units, start=1):
            ranges = self._clean_source_quotes(item.source_quotes, known_blocks)
            if not ranges:
                continue
            units.append(
                SemanticUnit(
                    semantic_unit_id=f"unit-{prefix}-{index}-{uuid4().hex[:8]}",
                    paper_id=parsed.paper_id,
                    role=item.role,
                    title=item.title.strip()[:160] or item.role.title(),
                    text=item.text.strip(),
                    source_ranges=ranges,
                    confidence=max(0.0, min(item.confidence, 1.0)),
                    created_by="llm-semantic-slicer",
                )
            )
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
    def _clean_source_quotes(
        source_quotes: list[SemanticUnitSourceQuote],
        known_blocks: dict[str, SourceBlock],
    ) -> list[SourceRange]:
        ranges: list[SourceRange] = []
        seen: set[tuple[str, int | None, int | None]] = set()
        for source_quote in source_quotes:
            block = known_blocks.get(source_quote.source_block_id)
            if block is None:
                continue
            start, end = LLMAnalyzer._find_quote_span(block.text, source_quote.quote)
            key = (source_quote.source_block_id, start, end)
            if key in seen:
                continue
            seen.add(key)
            ranges.append(
                SourceRange(
                    source_block_id=source_quote.source_block_id,
                    start_char=start,
                    end_char=end,
                )
            )
        return ranges

    @staticmethod
    def _find_quote_span(source_text: str, quote: str) -> tuple[int | None, int | None]:
        quote = quote.strip()
        if not quote:
            return (None, None)

        start = source_text.find(quote)
        if start >= 0:
            return (start, start + len(quote))

        start = source_text.lower().find(quote.lower())
        if start >= 0:
            return (start, start + len(quote))

        normalized_source, source_map = LLMAnalyzer._normalize_with_index_map(source_text)
        normalized_quote, _ = LLMAnalyzer._normalize_with_index_map(quote)
        if not normalized_quote:
            return (None, None)

        normalized_start = normalized_source.find(normalized_quote)
        if normalized_start < 0:
            return (None, None)
        normalized_end = normalized_start + len(normalized_quote) - 1
        return (source_map[normalized_start], source_map[normalized_end] + 1)

    @staticmethod
    def _normalize_with_index_map(value: str) -> tuple[str, list[int]]:
        normalized_chars: list[str] = []
        index_map: list[int] = []
        previous_was_space = True
        for index, char in enumerate(value):
            if char.isspace():
                if not previous_was_space:
                    normalized_chars.append(" ")
                    index_map.append(index)
                previous_was_space = True
                continue
            normalized_chars.append(char.lower())
            index_map.append(index)
            previous_was_space = False

        if normalized_chars and normalized_chars[-1] == " ":
            normalized_chars.pop()
            index_map.pop()
        return ("".join(normalized_chars), index_map)

    @staticmethod
    def _structured_response_format() -> dict:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "semantic_slice_output",
                "schema": SemanticSliceOutput.model_json_schema(),
                "strict": True,
            },
        }

    @staticmethod
    def _json_object_response_format() -> dict:
        return {"type": "json_object"}

    def _post_chat_completion(
        self, system: str, user: str, response_format: dict
    ) -> httpx.Response:
        return httpx.post(
            f"{self._config.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self._config.openai_api_key}"},
            json={
                "model": self._config.openai_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": response_format,
                "temperature": 0,
            },
            timeout=90,
        )

    def _chat_json(self, system: str, user: str) -> dict | None:
        try:
            response = self._post_chat_completion(system, user, self._structured_response_format())
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {400, 422}:
                logger.warning("LLM semantic slicing failed: %s", exc)
                return None
            logger.warning(
                "Structured output was rejected by the configured OpenAI-compatible endpoint; "
                "falling back to JSON object mode: %s",
                exc,
            )
            try:
                response = self._post_chat_completion(
                    system, user, self._json_object_response_format()
                )
                response.raise_for_status()
            except httpx.HTTPError as fallback_exc:
                logger.warning("LLM semantic slicing failed: %s", fallback_exc)
                return None
        except httpx.HTTPError as exc:
            logger.warning("LLM semantic slicing failed: %s", exc)
            return None

        try:
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("LLM semantic slicing failed: %s", exc)
            return None
