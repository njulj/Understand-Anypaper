import json
import logging
from uuid import uuid4

import httpx

from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit, SourceRange

logger = logging.getLogger(__name__)

_ROLE_VALUES = {
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
}

_SEMANTIC_UNIT_SYSTEM_PROMPT = (
    "You slice a research paper into semantic argument units for a Paper Argument Graph. "
    "Use only the provided source_block_id values. A semantic unit has exactly one role. "
    "Split mixed paragraphs into multiple units when they contain different roles, such as "
    "a contribution statement and a method mechanism in the same source block. Keep units "
    "large enough to be meaningful graph evidence, not sentence fragments. Valid roles: "
    "contribution, motivation, gap, method, experiment, result, conclusion, background, "
    "equation, figure, table, reference. Respond with JSON only: "
    '{"semantic_units": [{"role": "contribution", "title": "...", "text": "...", '
    '"source_ranges": [{"source_block_id": "...", "start_char": 0, "end_char": 120}], '
    '"confidence": 0.9}]}'
)


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
        payload = self._chat_json(
            _SEMANTIC_UNIT_SYSTEM_PROMPT,
            f"Title: {parsed.title}\nAbstract: {parsed.abstract[:1600]}\n\nSource blocks:\n{blocks}",
        )
        if not payload:
            return None
        raw_units = payload.get("semantic_units")
        if not isinstance(raw_units, list):
            return None

        known_blocks = {block.source_block_id for block in parsed.source_blocks}
        units: list[SemanticUnit] = []
        prefix = parsed.paper_id[:8]
        for index, item in enumerate(raw_units, start=1):
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            title = item.get("title")
            text = item.get("text")
            if role not in _ROLE_VALUES or not isinstance(title, str) or not isinstance(text, str):
                continue
            ranges = self._clean_source_ranges(item.get("source_ranges"), known_blocks)
            if not ranges:
                continue
            try:
                confidence = float(item.get("confidence", 0.75))
            except (TypeError, ValueError):
                confidence = 0.75
            units.append(
                SemanticUnit(
                    semantic_unit_id=f"unit-{prefix}-{index}-{uuid4().hex[:8]}",
                    paper_id=parsed.paper_id,
                    role=role,
                    title=title.strip()[:160] or role.title(),
                    text=text.strip(),
                    source_ranges=ranges,
                    confidence=max(0.0, min(confidence, 1.0)),
                    created_by="llm-semantic-slicer",
                )
            )
        return units or None

    @staticmethod
    def _clean_source_ranges(raw_ranges: object, known_blocks: set[str]) -> list[SourceRange]:
        if not isinstance(raw_ranges, list):
            return []
        ranges: list[SourceRange] = []
        for raw in raw_ranges:
            if not isinstance(raw, dict):
                continue
            source_block_id = raw.get("source_block_id")
            if source_block_id not in known_blocks:
                continue
            start = raw.get("start_char")
            end = raw.get("end_char")
            ranges.append(
                SourceRange(
                    source_block_id=source_block_id,
                    start_char=start if isinstance(start, int) and start >= 0 else None,
                    end_char=end if isinstance(end, int) and end >= 0 else None,
                )
            )
        return ranges

    def _chat_json(self, system: str, user: str) -> dict | None:
        try:
            response = httpx.post(
                f"{self._config.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._config.openai_api_key}"},
                json={
                    "model": self._config.openai_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
                timeout=90,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("LLM semantic slicing failed: %s", exc)
            return None
