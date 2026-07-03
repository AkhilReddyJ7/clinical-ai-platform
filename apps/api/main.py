from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from apps.api.routers.documents import router as documents_router
from shared.config.settings import get_settings
from shared.logging.logger import configure_logging, logger

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Schema is managed by Alembic migrations (see alembic/), applied before
    # the app starts (docker-compose command / `make migrate`) — not here.
    configure_logging()
    logger.info(
        "Starting %s version=%s environment=%s",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Production-grade AI platform for clinical document intelligence",
    lifespan=lifespan,
)

app.include_router(documents_router)


@app.get("/")
async def root():
    return {"message": f"Welcome to the {settings.app_name}"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }
