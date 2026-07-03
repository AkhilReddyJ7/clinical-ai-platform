from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from shared.config.settings import get_settings
from shared.logging.logger import configure_logging, logger

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
