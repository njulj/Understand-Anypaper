import os
from argparse import Namespace

from understand_anypaper.config import Settings, apply_desktop_api_overrides
from understand_anypaper.desktop_server import configure_runtime_environment


def test_configure_runtime_environment_uses_workspace_sqlite_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PAG_DOCUMENT_STORE_DIR", raising=False)

    configure_runtime_environment(Namespace(document_store_dir=None))

    assert os.environ["DATABASE_URL"] == f"sqlite:///{(tmp_path / 'data' / 'uap.sqlite').as_posix()}"
    assert os.environ["PAG_DOCUMENT_STORE_DIR"] == str((tmp_path / "data" / "documents").resolve())


def test_configure_runtime_environment_preserves_explicit_settings(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom-documents"
    monkeypatch.setenv("DATABASE_URL", "postgresql://kept")

    configure_runtime_environment(Namespace(document_store_dir=str(custom_dir)))

    assert os.environ["DATABASE_URL"] == "postgresql://kept"
    assert os.environ["PAG_DOCUMENT_STORE_DIR"] == str(custom_dir.resolve())


def test_apply_desktop_api_overrides_reads_saved_desktop_config(tmp_path):
    settings_path = tmp_path / "desktop-api-config.json"
    settings_path.write_text(
        '{"openaiApiKey":"desktop-key","openaiBaseUrl":"https://openrouter.ai/api/v1","openaiModel":"google/gemini-3-flash-preview"}',
        encoding="utf-8",
    )

    config = Settings(
        openai_api_key="env-key",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini",
        desktop_settings_path=str(settings_path),
    )

    apply_desktop_api_overrides(config)

    assert config.openai_api_key == "desktop-key"
    assert config.openai_base_url == "https://openrouter.ai/api/v1"
    assert config.openai_model == "google/gemini-3-flash-preview"
