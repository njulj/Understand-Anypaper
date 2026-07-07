"""Shared agent-framework chat client and options for LLM-backed analyzers."""

import asyncio
from typing import Any, TypeVar

from agent_framework import Agent, Message
from agent_framework.exceptions import AgentFrameworkException
from agent_framework.openai import OpenAIChatCompletionClient
from pydantic import BaseModel, ValidationError

from understand_anypaper.config import Settings, settings

OutputT = TypeVar("OutputT", bound=BaseModel)


class LlmError(RuntimeError):
    pass


def create_chat_client(
    config: Settings = settings,
    session_id: str | None = None,
) -> OpenAIChatCompletionClient:
    if not config.openai_api_key:
        raise LlmError("OpenAI API key is not configured")
    default_headers = None
    if session_id and "openrouter.ai" not in config.openai_base_url:
        default_headers = {"x-session-id": session_id[:256]}
    return OpenAIChatCompletionClient(
        model=config.openai_model,
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
        default_headers=default_headers,
    )


def structured_output_options(
    output_model: type[BaseModel],
    prompt_cache_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "response_format": output_model,
        "temperature": 0,
        "store": False,
    }
    if base_url and "openrouter.ai" in base_url:
        options["extra_body"] = {"provider": {"require_parameters": True}}
    if prompt_cache_key and (not base_url or "openrouter.ai" not in base_url):
        options["prompt_cache_key"] = prompt_cache_key
        options["prompt_cache_retention"] = "24h"
    return options


async def run_structured(
    agent: Agent,
    messages: str | Message,
    output_model: type[OutputT],
    prompt_cache_key: str | None = None,
    timeout_seconds: float | None = None,
    base_url: str | None = None,
) -> OutputT:
    """Run an agent and return its typed structured output."""
    timeout = timeout_seconds or settings.llm_request_timeout_seconds
    try:
        response = await asyncio.wait_for(
            agent.run(
                messages,
                options=structured_output_options(output_model, prompt_cache_key, base_url),
            ),
            timeout=timeout,
        )
        value = response.value
    except TimeoutError as exc:
        raise LlmError(f"{agent.name} request timed out after {timeout:g}s") from exc
    except AgentFrameworkException as exc:
        raise LlmError(f"{agent.name} request failed: {exc}") from exc
    except (ValueError, ValidationError) as exc:
        value = _parse_structured_text_response(output_model, getattr(response, "text", ""))
        if value is None:
            raise LlmError(
                f"{agent.name} returned invalid structured value: {exc}; "
                f"response preview={_response_preview(getattr(response, 'text', ''))}"
            ) from exc
    if not isinstance(value, output_model):
        value = _parse_structured_text_response(output_model, getattr(response, "text", ""))
    if not isinstance(value, output_model):
        raise LlmError(
            f"{agent.name} returned no structured value; "
            f"response preview={_response_preview(getattr(response, 'text', ''))}"
        )
    return value


def _parse_structured_text_response(
    output_model: type[OutputT],
    text: str,
) -> OutputT | None:
    payload = _clean_json_text(text)
    for candidate in (payload, _json_object_substring(payload)):
        if not candidate:
            continue
        try:
            return output_model.model_validate_json(candidate)
        except ValidationError:
            continue
    return None


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _json_object_substring(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def _response_preview(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return repr(text)
    return repr(f"{text[:limit]}...")
