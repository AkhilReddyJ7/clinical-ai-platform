from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.schemas import LowConfidenceDocumentListOut
from modules.analytics import service as analytics_service
from modules.analytics.schemas import LowConfidenceDocumentOut, MetricsOut
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


@router.get("/low-confidence-documents", response_model=LowConfidenceDocumentListOut)
async def get_low_confidence_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> LowConfidenceDocumentListOut:
    """ADR-0036: documents whose *current* extraction attempt is below
    settings.low_confidence_threshold -- informational only, does not
    gate or change any pipeline behavior (ADR-0025 stays exactly as
    accepted). Global visibility, same reasoning as every other read
    surface in this project (ADR-0026).
    """
    items, total = await analytics_service.list_low_confidence_documents(
        db, limit=limit, offset=offset
    )
    return LowConfidenceDocumentListOut(
        items=[
            LowConfidenceDocumentOut(
                document_id=item.document_id,
                extraction_id=item.id,
                confidence=item.confidence,
                created_at=item.created_at,
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
