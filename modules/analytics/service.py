from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.analytics.schemas import ConfidenceMetricsOut, DocumentMetricsOut, JobMetricsOut
from modules.ingestion.models import Document, DocumentStatus
from modules.ocr.models import ExtractionResult
from modules.processing.models import Job, JobStatus
from shared.config.settings import get_settings


async def get_job_metrics(db: AsyncSession) -> JobMetricsOut:
    """ADR-0029: queue depth and retry/failure stats, entirely SQL-side.
    `by_status` reports every JobStatus member, defaulting absent ones to
    0 -- a status with no rows simply doesn't appear in a GROUP BY, and a
    caller shouldn't have to know that to treat it as zero.
    """
    rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    by_status = {status.value: 0 for status in JobStatus}
    for status, count in rows:
        by_status[JobStatus(status).value] = count

    avg_retry_count, max_retry_count = (
        await db.execute(select(func.avg(Job.retry_count), func.max(Job.retry_count)))
    ).one()
    return JobMetricsOut(
        by_status=by_status,
        avg_retry_count=float(avg_retry_count) if avg_retry_count is not None else 0.0,
        max_retry_count=max_retry_count if max_retry_count is not None else 0,
    )


async def get_document_metrics(db: AsyncSession) -> DocumentMetricsOut:
    rows = (await db.execute(select(Document.status, func.count()).group_by(Document.status))).all()
    by_status = {status.value: 0 for status in DocumentStatus}
    for status, count in rows:
        by_status[DocumentStatus(status).value] = count
    return DocumentMetricsOut(by_status=by_status)


async def get_confidence_metrics(db: AsyncSession) -> ConfidenceMetricsOut:
    """`min`/`avg`/`max` are `None` (not `0.0`) when no ExtractionResult
    rows exist yet -- `0.0` would misrepresent "no data" as "confirmed
    zero confidence" (ADR-0029).
    """
    count, min_confidence, avg_confidence, max_confidence = (
        await db.execute(
            select(
                func.count(),
                func.min(ExtractionResult.confidence),
                func.avg(ExtractionResult.confidence),
                func.max(ExtractionResult.confidence),
            )
        )
    ).one()
    low_confidence_count = await db.scalar(
        select(func.count())
        .select_from(ExtractionResult)
        .where(ExtractionResult.confidence < get_settings().low_confidence_threshold)
    )
    return ConfidenceMetricsOut(
        count=count,
        min=float(min_confidence) if min_confidence is not None else None,
        avg=float(avg_confidence) if avg_confidence is not None else None,
        max=float(max_confidence) if max_confidence is not None else None,
        low_confidence_count=low_confidence_count or 0,
    )


async def list_low_confidence_documents(
    db: AsyncSession, *, limit: int = 20, offset: int = 0
) -> tuple[list[ExtractionResult], int]:
    """Documents whose *current* (latest-by-created_at) ExtractionResult
    is below settings.low_confidence_threshold (ADR-0036). Portable
    self-join (GROUP BY document_id, MAX(created_at)), not a Postgres-only
    DISTINCT ON/window function -- this project's tests run against
    SQLite (ADR-0004). A document reprocessed to a better result (ADR-0032)
    correctly drops out of this list once its latest attempt improves,
    even though the earlier low-confidence attempt still counts toward
    get_confidence_metrics's low_confidence_count above.
    """
    threshold = get_settings().low_confidence_threshold

    latest_per_document = (
        select(
            ExtractionResult.document_id,
            func.max(ExtractionResult.created_at).label("max_created_at"),
        )
        .group_by(ExtractionResult.document_id)
        .subquery()
    )

    latest_low_confidence = (
        select(ExtractionResult)
        .join(
            latest_per_document,
            and_(
                ExtractionResult.document_id == latest_per_document.c.document_id,
                ExtractionResult.created_at == latest_per_document.c.max_created_at,
            ),
        )
        .where(ExtractionResult.confidence < threshold)
    )

    total = await db.scalar(select(func.count()).select_from(latest_low_confidence.subquery()))
    result = await db.execute(
        latest_low_confidence.order_by(ExtractionResult.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total or 0
