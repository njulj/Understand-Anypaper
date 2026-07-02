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
