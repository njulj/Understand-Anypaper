"""Shared agent-framework helpers for LLM-backed analyzers."""

from typing import TypeVar

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI
from pydantic import BaseModel

from understand_anypaper.config import Settings, settings

OutputT = TypeVar("OutputT", bound=BaseModel)


def create_chat_client(
    config: Settings = settings,
    session_id: str | None = None,
) -> OpenAIChatCompletionClient:
    if not config.openai_api_key:
        raise RuntimeError("OpenAI API key is not configured")

    default_headers = {"x-session-id": session_id[:256]} if session_id else None
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


async def run_structured(
    agent: Agent,
    messages: str | Message,
    output_model: type[OutputT],
) -> OutputT:
    """Run an agent and return its typed structured output."""
    response = await agent.run(
        messages,
        options={"response_format": output_model},
    )

    if not response.value:
        raise RuntimeError("Model did not return structured output")
    return response.value
