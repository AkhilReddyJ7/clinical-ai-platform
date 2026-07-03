from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from apps.api.routers.documents import router as documents_router
from modules.ingestion import models as ingestion_models  # noqa: F401  (registers ORM table)
from modules.ocr import models as ocr_models  # noqa: F401  (registers ORM table)
from modules.validation import models as validation_models  # noqa: F401  (registers ORM table)
from shared.config.settings import get_settings
from shared.database.base import Base
from shared.database.session import engine
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
