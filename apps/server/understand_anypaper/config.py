import json
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="postgresql+psycopg://understand:understand@localhost:5432/understand_anypaper",
        validation_alias=AliasChoices("PAG_DATABASE_URL", "DATABASE_URL"),
    )
    recursion_max_depth: int = 1
    recursion_max_papers: int = 5
    document_store_dir: str = "data/documents"
    openalex_api_key: str = ""

    # LLM configuration. Graph building requires the graph-authoring agent.
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PAG_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("PAG_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    openai_model: str = "gpt-4o-mini"
    send_prompt_cache_key: bool = True
    llm_request_timeout_seconds: float = 600
    graph_agent_max_turns: int = 40
    graph_agent_max_tool_calls: int = 120
    graph_agent_shell_timeout_seconds: float = 30
    graph_agent_shell_max_output_chars: int = 20000
    desktop_settings_path: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PAG_",
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()


def apply_desktop_api_overrides(target: Settings = settings) -> Settings:
    """Refresh LLM config from the desktop app's persisted settings file when present."""
    if not target.desktop_settings_path:
        return target

    path = Path(target.desktop_settings_path).expanduser()
    if not path.exists() or not path.is_file():
        return target

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return target

    if "openaiApiKey" in payload:
        target.openai_api_key = str(payload["openaiApiKey"] or "")
    if "openaiBaseUrl" in payload:
        target.openai_base_url = str(payload["openaiBaseUrl"] or "https://api.openai.com/v1")
    if "openaiModel" in payload:
        target.openai_model = str(payload["openaiModel"] or "gpt-4o-mini")
    if "sendPromptCacheKey" in payload:
        value = payload["sendPromptCacheKey"]
        if isinstance(value, bool):
            target.send_prompt_cache_key = value
        elif isinstance(value, str):
            target.send_prompt_cache_key = value.strip().casefold() not in {
                "0",
                "false",
                "no",
                "off",
            }

    return target


apply_desktop_api_overrides(settings)
