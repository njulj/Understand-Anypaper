"""Shared agent-framework helpers for LLM-backed analyzers."""

import json
import re
from typing import TypeVar

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
from openai import AsyncOpenAI
from pydantic import BaseModel

from understand_anypaper.config import Settings, apply_desktop_api_overrides, settings

OutputT = TypeVar("OutputT", bound=BaseModel)


class LlmError(RuntimeError):
    """Raised when an LLM-backed analyzer cannot produce typed output."""


def _is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in base_url.casefold()


def structured_output_options(
    output_model: type[OutputT],
    *,
    base_url: str = settings.openai_base_url,
    prompt_cache_key: str | None = None,
    send_prompt_cache_key: bool = True,
) -> dict:
    options: dict = {"response_format": output_model}
    if _is_openrouter(base_url):
        options["extra_body"] = {"provider": {"require_parameters": True}}
    if send_prompt_cache_key and prompt_cache_key:
        options["prompt_cache_key"] = prompt_cache_key
    return options


def create_chat_client(
    config: Settings = settings,
    session_id: str | None = None,
) -> OpenAIChatCompletionClient:
    apply_desktop_api_overrides(config)
    if not config.openai_api_key:
        raise RuntimeError("OpenAI API key is not configured")

    default_headers = (
        None
        if _is_openrouter(config.openai_base_url)
        else {"x-session-id": session_id[:256]} if session_id else None
    )
    return OpenAIChatCompletionClient(
        model=config.openai_model,
        async_client=AsyncOpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            default_headers=default_headers,
            timeout=config.llm_request_timeout_seconds,
            max_retries=0,
        ),
        default_headers=default_headers,
    )


def create_responses_client(
    config: Settings = settings,
    session_id: str | None = None,
) -> OpenAIChatClient:
    """Create Agent Framework's OpenAI Responses API client.

    The graph agent uses the framework client as its provider boundary. Its raw
    SDK handle is used only for custom-tool calls, which the current framework
    release sends but does not yet surface back as invokable FunctionTool calls.
    """
    apply_desktop_api_overrides(config)
    if not config.openai_api_key:
        raise RuntimeError("OpenAI API key is not configured")

    default_headers = (
        None
        if _is_openrouter(config.openai_base_url)
        else {"x-session-id": session_id[:256]} if session_id else None
    )
    return OpenAIChatClient(
        model=config.openai_model,
        async_client=AsyncOpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            default_headers=default_headers,
            timeout=config.llm_request_timeout_seconds,
            max_retries=0,
        ),
        default_headers=default_headers,
    )


async def run_structured(
    agent: Agent,
    messages: str | Message,
    output_model: type[OutputT],
    *,
    base_url: str = settings.openai_base_url,
    prompt_cache_key: str | None = None,
    send_prompt_cache_key: bool = True,
) -> OutputT:
    """Run an agent and return its typed structured output."""
    try:
        response = await agent.run(
            messages,
            options=structured_output_options(
                output_model,
                base_url=base_url,
                prompt_cache_key=prompt_cache_key,
                send_prompt_cache_key=send_prompt_cache_key,
            ),
        )
    except Exception as exc:
        agent_name = getattr(agent, "name", agent.__class__.__name__)
        raise LlmError(
            f"{agent_name} failed before returning a response: {exc}"
        ) from exc

    if response.value:
        return response.value

    text = _response_text(response)
    try:
        return output_model.model_validate_json(_json_text(text))
    except Exception as exc:
        preview = re.sub(r"\s+", " ", text).strip()[:500]
        agent_name = getattr(agent, "name", agent.__class__.__name__)
        raise LlmError(
            f"{agent_name} did not return valid structured output; response preview={preview!r}"
        ) from exc


def _response_text(response) -> str:
    messages = getattr(response, "messages", None) or []
    if messages:
        return "\n".join(str(getattr(message, "text", "") or "") for message in messages).strip()
    return str(getattr(response, "text", "") or "")


def _json_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    json.loads(stripped)
    return stripped
