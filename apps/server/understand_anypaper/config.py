from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://understand:understand@localhost:5432/understand_anypaper"
    recursion_max_depth: int = 1
    recursion_max_papers: int = 5
    codex_cli: str = "codex"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAG_", extra="ignore")


settings = Settings()
