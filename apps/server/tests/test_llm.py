import asyncio

import pytest
from agent_framework import ChatResponse, Content, Message
from agent_framework.openai import OpenAIChatCompletionClient
from pydantic import BaseModel

from understand_anypaper.analyzers.llm import (
    LlmError,
    create_chat_client,
    run_structured,
    structured_output_options,
)
from understand_anypaper.config import Settings


class DemoOutput(BaseModel):
    name: str
    count: int


class FakeAgent:
    name = "FakeAgent"

    def __init__(self, text: str) -> None:
        self.text = text

    async def run(self, messages, options=None):
        return ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_text(self.text)])]
        )


def test_run_structured_parses_json_text_when_value_is_missing():
    output = asyncio.run(run_structured(FakeAgent('{"name":"paper","count":2}'), "", DemoOutput))

    assert output == DemoOutput(name="paper", count=2)


def test_run_structured_parses_fenced_json_text_when_value_is_missing():
    output = asyncio.run(
        run_structured(FakeAgent('```json\n{"name":"paper","count":2}\n```'), "", DemoOutput)
    )

    assert output == DemoOutput(name="paper", count=2)


def test_run_structured_reports_response_preview_for_unparseable_text():
    with pytest.raises(LlmError, match="response preview='not json'"):
        asyncio.run(run_structured(FakeAgent("not json"), "", DemoOutput))


def test_create_chat_client_uses_chat_completions_api():
    client = create_chat_client(
        Settings(
            openai_api_key="test-key",
            openai_base_url="https://openrouter.ai/api/v1",
            openai_model="openai/gpt-4o-mini",
        ),
        session_id="semantic-slice:test",
    )

    assert isinstance(client, OpenAIChatCompletionClient)
    assert client.default_headers is None


def test_openrouter_structured_options_stay_stateless():
    options = structured_output_options(
        DemoOutput,
        prompt_cache_key="semantic-slice:test",
        base_url="https://openrouter.ai/api/v1",
    )

    assert options["extra_body"] == {"provider": {"require_parameters": True}}
    assert "prompt_cache_key" not in options
    assert options["store"] is False
