from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Clinical AI Intelligence Platform"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/clinical_ai"
    storage_root: str = "./data/uploads"
    max_upload_size_bytes: int = 25 * 1024 * 1024

    # Caps per-document OCR cost: each page is rasterized and OCR'd
    # synchronously (in a threadpool worker, see docs/adr/0013), so an
    # unbounded page count is an unbounded per-request resource cost. Real
    # clinical documents are rarely more than a few dozen pages.
    max_pdf_pages: int = 50

    # Comma-separated list of valid X-API-Key values. Local/dev-only default
    # below — generate real keys and set them via .env (gitignored) for
    # anything beyond a local docker compose run; never commit real keys.
    api_keys: str = "local-dev-key"

    # Empty by default: fails closed (AnthropicFieldExtractionPipeline
    # refuses to construct without a real key) rather than silently calling
    # the API with an invalid one. Set a real key via .env (gitignored);
    # never commit it. See docs/adr/0019.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_timeout_seconds: float = 30.0
    # Bounds per-document LLM cost the same way max_pdf_pages bounds
    # per-document OCR cost — an unbounded raw_text length sent to a paid
    # API is an unbounded per-request cost.
    anthropic_max_input_chars: int = 12_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
