import json
import logging

import httpx

from understand_anypaper.config import Settings, settings
from understand_anypaper.parser.models import ContentBlock, ParsedPaper

logger = logging.getLogger(__name__)

_ROLE_VALUES = {
    "contribution", "motivation", "gap", "method", "experiment",
    "result", "conclusion", "background", "equation", "figure", "table",
}
_LINK_ROLES = _ROLE_VALUES - {"contribution"}

_ROLE_SYSTEM_PROMPT = (
    "You classify paragraphs of a research paper into semantic roles. "
    "Valid roles: contribution, motivation, gap, method, experiment, result, "
    "conclusion, background. Respond with JSON only: "
    '{"roles": {"<content_id>": "<role>", ...}}'
)

_CONTRIBUTION_SYSTEM_PROMPT = (
    "You extract the concrete contributions of a research paper. Each contribution "
    "needs a short title, a one-paragraph summary grounded in the given text, and the "
    "content_ids of the blocks that state or support it. Respond with JSON only: "
    '{"contributions": [{"title": "...", "summary": "...", "evidence_content_ids": ["..."]}]}'
)

_EVIDENCE_LINK_SYSTEM_PROMPT = (
    "You link paper evidence blocks to extracted contributions. Use only the provided "
    "contribution indexes and content_ids. Pick blocks that explain WHY the contribution "
    "is needed, HOW it works, or PROOF that it works. Valid roles: motivation, gap, "
    "method, equation, figure, table, experiment, result, conclusion, background. "
    "Respond with JSON only: "
    '{"links": [{"contribution_index": 1, "content_id": "...", "role": "method", '
    '"confidence": 0.8, "reason": "..."}]}'
)


class LLMAnalyzer:
    """OpenAI-compatible chat analyzer for semantic roles and contribution extraction.

    Every method returns None when the LLM is not configured or the call fails,
    so callers can fall back to the rule-based analyzers.
    """

    def __init__(self, config: Settings = settings) -> None:
        self._config = config

    @property
    def available(self) -> bool:
        return bool(self._config.openai_api_key)

    def classify_roles(self, blocks: list[ContentBlock]) -> dict[str, str] | None:
        if not self.available or not blocks:
            return None
        listing = "\n\n".join(f"[{b.content_id}] ({b.section or 'no section'}) {b.text[:600]}" for b in blocks)
        payload = self._chat_json(_ROLE_SYSTEM_PROMPT, listing)
        if not payload:
            return None
        roles = payload.get("roles")
        if not isinstance(roles, dict):
            return None
        known_ids = {b.content_id for b in blocks}
        return {
            content_id: role
            for content_id, role in roles.items()
            if content_id in known_ids and isinstance(role, str) and role in _ROLE_VALUES
        }

    def extract_contributions(self, parsed: ParsedPaper) -> list[dict] | None:
        if not self.available or not parsed.blocks:
            return None
        listing = "\n\n".join(
            f"[{b.content_id}] {b.text[:600]}" for b in parsed.blocks[:60]
        )
        user = f"Title: {parsed.title}\nAbstract: {parsed.abstract[:1200]}\n\nBlocks:\n{listing}"
        payload = self._chat_json(_CONTRIBUTION_SYSTEM_PROMPT, user)
        if not payload:
            return None
        contributions = payload.get("contributions")
        if not isinstance(contributions, list):
            return None
        known_ids = {b.content_id for b in parsed.blocks}
        cleaned = []
        for item in contributions:
            if not isinstance(item, dict) or not item.get("title") or not item.get("summary"):
                continue
            evidence = [cid for cid in item.get("evidence_content_ids", []) if cid in known_ids]
            cleaned.append({"title": str(item["title"]), "summary": str(item["summary"]), "evidence_content_ids": evidence})
        return cleaned or None

    def link_evidence(self, parsed: ParsedPaper, contribution_specs: list[dict]) -> dict[int, list[dict]] | None:
        if not self.available or not parsed.blocks or not contribution_specs:
            return None
        contributions = "\n".join(
            f"{index}. {spec['title']}: {spec['summary'][:600]}"
            for index, spec in enumerate(contribution_specs, start=1)
        )
        blocks = "\n\n".join(
            f"[{block.content_id}] ({block.section or 'no section'}, {block.semantic_role}) {block.text[:700]}"
            for block in parsed.blocks[:90]
        )
        payload = self._chat_json(
            _EVIDENCE_LINK_SYSTEM_PROMPT,
            f"Title: {parsed.title}\n\nContributions:\n{contributions}\n\nBlocks:\n{blocks}",
        )
        if not payload:
            return None
        links = payload.get("links")
        if not isinstance(links, list):
            return None
        known_ids = {block.content_id for block in parsed.blocks}
        by_contribution: dict[int, list[dict]] = {}
        for item in links:
            if not isinstance(item, dict):
                continue
            try:
                contribution_index = int(item.get("contribution_index"))
                confidence = float(item.get("confidence", 0.75))
            except (TypeError, ValueError):
                continue
            content_id = item.get("content_id")
            role = item.get("role")
            if (
                not 1 <= contribution_index <= len(contribution_specs)
                or content_id not in known_ids
                or role not in _LINK_ROLES
            ):
                continue
            by_contribution.setdefault(contribution_index, []).append(
                {
                    "content_id": content_id,
                    "role": role,
                    "confidence": max(0.0, min(confidence, 1.0)),
                    "reason": str(item.get("reason") or ""),
                }
            )
        return by_contribution or None

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
            logger.warning("LLM analysis failed, falling back to rules: %s", exc)
            return None
