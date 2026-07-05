from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Clinical AI Intelligence Platform"
    app_version: str = "0.3.0"
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

    # Comma-separated `label:key` pairs (ADR-0026) -- each valid X-API-Key
    # value is now named, not an interchangeable shared secret. Local/dev-
    # only default below — generate real keys and set them via .env
    # (gitignored) for anything beyond a local docker compose run; never
    # commit real keys.
    api_keys: str = "local-dev:local-dev-key"

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

    # Grayscale + contrast normalization + upscaling for small images, and
    # a best-effort gross-rotation fix via Tesseract's own orientation
    # detection (see modules/ocr/tesseract.py). Tunable off if it ever
    # regresses a particular document population, same posture as the
    # other OCR knobs above.
    ocr_preprocessing_enabled: bool = True
    # Tesseract page segmentation mode. 3 ("fully automatic page
    # segmentation, no OSD") is Tesseract's own default and already
    # handles ordinary multi-column layouts; exposed explicitly so a
    # deployment with a different document population (e.g. dense forms)
    # can tune it without a code change.
    ocr_psm: int = 3

    # Below this, a ProcessingResult's aggregate confidence is flagged
    # `low_confidence` in its metadata — a signal for downstream review,
    # not a validation failure (see modules/processing/pipeline.py).
    low_confidence_threshold: float = 0.5

    # ADR-0023 retry budget and backoff. A job attempt classified transient
    # may retry up to this many additional times before the job is failed
    # outright; the delay between attempts grows exponentially (with
    # jitter), capped, per ADR-0023 section 3. Tunable defaults, not fixed
    # architectural constants, same posture as the OCR/Anthropic knobs above.
    job_max_retry_attempts: int = 3
    job_retry_backoff_initial_seconds: float = 2.0
    job_retry_backoff_multiplier: float = 2.0
    job_retry_backoff_max_seconds: float = 60.0
    job_retry_backoff_jitter_seconds: float = 1.0

    # ADR-0024: a `running` job is stale if no writer has touched it (i.e.
    # Job.updated_at) within this many seconds -- its worker is presumed
    # dead (crash, OOM kill, host failure). Reused as the sole liveness
    # signal, no heartbeat column. Comfortably above any single job
    # attempt's expected wall-clock cost (OCR + one Anthropic call, itself
    # capped by anthropic_timeout_seconds) so a merely slow attempt is
    # never mistaken for a dead one.
    job_stale_timeout_seconds: float = 300.0

    # Chroma vector-store connection (ADR-0033) -- api/worker talk to
    # Chroma's own docker-compose service over HTTP via the lightweight
    # `chromadb-client` package, not the full server-side `chromadb`
    # package this project doesn't need to embed.
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection_name: str = "clinical_documents"

    # fastembed's local ONNX embedding model (ADR-0034) -- runs in-process,
    # no network call at query/index time, no paid API key: consistent
    # with this project's existing bias toward local/free tooling over a
    # better-but-paid option (Tesseract over cloud OCR, regex over
    # Presidio, and now fastembed over Voyage AI here).
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    # Explicit, not fastembed's own default (which resolves under /tmp).
    # The Dockerfile bakes the model into this exact path at build time
    # (overridden to /opt/fastembed_cache in docker-compose.yml -- outside
    # /app, same reason the venv lives in /opt/venv: docker-compose
    # bind-mounts the host repo over /app at runtime, which would
    # otherwise shadow the bake and force a runtime re-download as
    # appuser into a path it can't write to) so the container never
    # downloads it at runtime; must match here and there or the bake is
    # silently wasted.
    embedding_model_cache_dir: str = "./.fastembed_cache"

    # Chunking parameters for ExtractionResult.raw_text before embedding
    # (ADR-0034). Character-based, not token-based -- no new tokenizer
    # dependency, the same posture as anthropic_max_input_chars.
    retrieval_chunk_size_chars: int = 2_000
    retrieval_chunk_overlap_chars: int = 200

    # POST /retrieval/query's top_k bounds (ADR-0035).
    retrieval_default_top_k: int = 5
    retrieval_max_top_k: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
