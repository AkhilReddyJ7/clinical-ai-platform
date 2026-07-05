from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routers.audit import router as audit_router
from apps.api.routers.documents import router as documents_router
from apps.api.routers.metrics import router as metrics_router
from apps.api.routers.retrieval import router as retrieval_router
from modules.auth.middleware import ApiKeyGateMiddleware
from shared.config.settings import get_settings
from shared.database.session import get_db
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

app.add_middleware(ApiKeyGateMiddleware, protected_prefix="/documents")
app.include_router(documents_router)
app.include_router(audit_router)
app.include_router(metrics_router)
app.include_router(retrieval_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": f"Welcome to the {settings.app_name}"}


@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    payload = {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": "connected",
    }
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("health check: database unreachable")
        payload["status"] = "unhealthy"
        payload["database"] = "unreachable"
        return JSONResponse(status_code=503, content=payload)

    return JSONResponse(status_code=200, content=payload)
