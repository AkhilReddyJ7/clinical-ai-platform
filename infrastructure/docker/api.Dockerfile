FROM python:3.12-slim

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

RUN chown -R appuser:appuser /app /opt/venv

USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
