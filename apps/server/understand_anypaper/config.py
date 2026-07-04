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
    codex_cli: str = "codex"

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
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAG_", extra="ignore")


settings = Settings()
