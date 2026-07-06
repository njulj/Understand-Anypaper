import asyncio
import threading
from typing import TypeVar

from pydantic import BaseModel

from understand_anypaper.config import Settings, settings

OutputT = TypeVar("OutputT", bound=BaseModel)


class StructuredAgentError(RuntimeError):
    pass


class StructuredAgent:
    """Thin Microsoft Agent Framework wrapper for typed Pydantic outputs."""

    def __init__(
        self,
        name: str,
        instructions: str,
        config: Settings = settings,
    ) -> None:
        self._name = name
        self._instructions = instructions
        self._config = config

    def run(
        self,
        prompt: str,
        output_model: type[OutputT],
        prompt_cache_key: str | None = None,
    ) -> OutputT:
        return _run_blocking(self.run_async(prompt, output_model, prompt_cache_key))

    async def run_async(
        self,
        prompt: str,
        output_model: type[OutputT],
        prompt_cache_key: str | None = None,
    ) -> OutputT:
        if not self._config.openai_api_key:
            raise StructuredAgentError("OpenAI API key is not configured")
        try:
            from agent_framework import Agent
            from agent_framework.openai import OpenAIChatClient
        except ImportError as exc:
            raise StructuredAgentError(
                "agent-framework-openai is required for agent-backed analysis"
            ) from exc

        client = OpenAIChatClient(
            model=self._config.openai_model,
            api_key=self._config.openai_api_key,
            base_url=self._config.openai_base_url,
            # OpenRouter session affinity: requests sharing an x-session-id are
            # routed to the same provider so its prompt cache stays warm. The
            # header is ignored by other OpenAI-compatible backends. Max 256 chars.
            default_headers=(
                {"x-session-id": prompt_cache_key[:256]} if prompt_cache_key else None
            ),
        )
        agent = Agent(
            client=client,
            name=self._name,
            instructions=self._instructions,
            default_options={"temperature": 0},
        )
        options = {
            "response_format": output_model,
            "temperature": 0,
            "store": False,
        }
        if prompt_cache_key:
            options["prompt_cache_key"] = prompt_cache_key
            options["prompt_cache_retention"] = "24h"
        response = await agent.run(prompt, options=options)
        value = response.value
        if not isinstance(value, output_model):
            raise StructuredAgentError(f"{self._name} returned no structured value")
        return value


def _run_blocking(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = None
    error: BaseException | None = None

    def runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            error = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result
