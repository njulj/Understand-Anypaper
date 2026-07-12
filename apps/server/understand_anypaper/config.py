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

    # LLM / embeddings. Empty api key disables semantic slicing and vector search;
    # graph building requires semantic units from the LLM analyzer.
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PAG_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("PAG_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    openai_model: str = "gpt-4o-mini"
    llm_request_timeout_seconds: float = 180
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    desktop_settings_path: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAG_", extra="ignore")


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

    return target


apply_desktop_api_overrides(settings)
