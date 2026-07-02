import logging

import httpx

from understand_anypaper.config import Settings, settings

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """OpenAI-compatible embedding client. Returns None when unavailable or failing."""

    def __init__(self, config: Settings = settings) -> None:
        self._config = config

    @property
    def available(self) -> bool:
        return bool(self._config.openai_api_key)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.available or not texts:
            return None
        try:
            response = httpx.post(
                f"{self._config.openai_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {self._config.openai_api_key}"},
                json={
                    "model": self._config.embedding_model,
                    "input": [text[:6000] for text in texts],
                    "dimensions": self._config.embedding_dimensions,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in data]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("Embedding request failed: %s", exc)
            return None
