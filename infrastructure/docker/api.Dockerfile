FROM python:3.12-slim

# Local, vendor-free OCR: no API key, no per-call cost, no external service.
# Only tesseract itself is a system package here — pypdfium2 (PDF
# rasterization) ships prebuilt binaries in its wheel, no poppler needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Match the common first-uid Linux convention (most dev hosts' first user)
# so the docker-compose bind mount (.:/app) doesn't produce root-owned
# files on the host filesystem.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser

# Keep the virtualenv outside /app: docker-compose bind-mounts the host repo
# over /app at runtime, which would otherwise shadow the venv built during
# `docker build` and force `uv run` to silently rebuild it — as root, into
# the bind-mounted host directory — on every container start.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

COPY . .

# Bake fastembed's ONNX model into the image at build time (ADR-0034) --
# no runtime download, no first-request latency spike, no CI network
# flakiness, same discipline as baking tesseract-ocr into the image above.
# Outside /app for the same reason the venv is (see above): docker-compose
# bind-mounts the host repo over /app at runtime, which would otherwise
# shadow this bake and force a runtime re-download -- as appuser, into a
# path the bind-mounted host directory doesn't grant write access to,
# producing a PermissionError instead of a silent re-download. Path must
# match Settings.embedding_model_cache_dir's container override
# (EMBEDDING_MODEL_CACHE_DIR in docker-compose.yml) or this bake is wasted.
RUN uv run python3 -c "\
from fastembed import TextEmbedding; \
TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed_cache')"

# Pre-create the upload storage directory so it exists, correctly owned, in
# the image *before* docker-compose mounts the uploads_data named volume
# there. Docker initializes a fresh named volume from whatever is already
# at its mount point in the image (content and ownership) on first use; if
# the path doesn't exist in the image, Docker creates the mount point
# itself as root, and appuser can never write to it.
RUN mkdir -p /app/data/uploads \
    && chown -R appuser:appuser /app /opt/venv /opt/fastembed_cache

USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
