"""Shared agent-framework chat client and options for LLM-backed analyzers."""

import asyncio
import threading
from typing import Any, TypeVar

from agent_framework import Agent, Message
from agent_framework.exceptions import AgentFrameworkException
from agent_framework.openai import OpenAIChatClient
from pydantic import BaseModel

from understand_anypaper.config import Settings, settings

OutputT = TypeVar("OutputT", bound=BaseModel)


class LlmError(RuntimeError):
    pass


def create_chat_client(
    config: Settings = settings,
    session_id: str | None = None,
) -> OpenAIChatClient:
    if not config.openai_api_key:
        raise LlmError("OpenAI API key is not configured")
    return OpenAIChatClient(
        model=config.openai_model,
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
        # OpenRouter session affinity: requests sharing an x-session-id are
        # routed to the same provider so its prompt cache stays warm. The
        # header is ignored by other OpenAI-compatible backends. Max 256 chars.
        default_headers={"x-session-id": session_id[:256]} if session_id else None,
    )


def structured_output_options(
    output_model: type[BaseModel],
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "response_format": output_model,
        "temperature": 0,
        "store": False,
    }
    if prompt_cache_key:
        options["prompt_cache_key"] = prompt_cache_key
        options["prompt_cache_retention"] = "24h"
    return options


def run_structured(
    agent: Agent,
    messages: str | Message,
    output_model: type[OutputT],
    prompt_cache_key: str | None = None,
) -> OutputT:
    """Run an agent from synchronous pipeline code and return its typed output."""
    try:
        response = _run_blocking(
            agent.run(
                messages,
                options=structured_output_options(output_model, prompt_cache_key),
            )
        )
        value = response.value
    except AgentFrameworkException as exc:
        raise LlmError(f"{agent.name} request failed: {exc}") from exc
    if not isinstance(value, output_model):
        raise LlmError(f"{agent.name} returned no structured value")
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
