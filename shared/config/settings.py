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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
