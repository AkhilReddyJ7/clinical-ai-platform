from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from modules.analytics import service as analytics_service
from modules.analytics.schemas import MetricsOut
from modules.auth.api_key import require_api_key
from shared.database.session import get_db

router = APIRouter(prefix="/metrics", tags=["metrics"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=MetricsOut)
async def get_metrics(db: AsyncSession = Depends(get_db)) -> MetricsOut:
    """GET /metrics (ADR-0029): one composite, globally-visible read over
    Job/Document/ExtractionResult -- queue depth, retry/failure counts,
    document throughput by status, and confidence distribution. Entirely
    SQL-aggregated; no per-stage timing (never durably persisted, see the
    Sprint 4 baseline section 2) and no in-memory WorkerMetrics involved.
    """
    return MetricsOut(
        jobs=await analytics_service.get_job_metrics(db),
        documents=await analytics_service.get_document_metrics(db),
        confidence=await analytics_service.get_confidence_metrics(db),
    )
