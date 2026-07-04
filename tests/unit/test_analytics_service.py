"""ADR-0029: operational metrics computed from durable Job/Document/
ExtractionResult rows via SQL aggregation -- never from
modules.processing.metrics.WorkerMetrics (process-local, see the Sprint 4
baseline section 2).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.analytics.service import (
    get_confidence_metrics,
    get_document_metrics,
    get_job_metrics,
)
from modules.ingestion.models import Document, DocumentStatus
from modules.ocr.models import ExtractionResult
from modules.processing.models import Job, JobStatus


async def _make_document(session: AsyncSession, status: DocumentStatus) -> Document:
    document = Document(
        id=uuid.uuid4(),
        filename="report.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_key=f"{uuid.uuid4()}/report.txt",
        status=status,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@pytest.mark.asyncio
async def test_job_metrics_report_zero_for_every_status_with_no_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        metrics = await get_job_metrics(session)

        assert metrics.by_status == {status.value: 0 for status in JobStatus}
        assert metrics.avg_retry_count == 0.0
        assert metrics.max_retry_count == 0


@pytest.mark.asyncio
async def test_job_metrics_counts_by_status_and_retry_stats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, DocumentStatus.UPLOADED)
        session.add_all(
            [
                Job(document_id=document.id, status=JobStatus.QUEUED, retry_count=0),
                Job(document_id=document.id, status=JobStatus.RETRYING, retry_count=2),
                Job(document_id=document.id, status=JobStatus.FAILED, retry_count=4),
            ]
        )
        await session.commit()

        metrics = await get_job_metrics(session)

        assert metrics.by_status[JobStatus.QUEUED.value] == 1
        assert metrics.by_status[JobStatus.RETRYING.value] == 1
        assert metrics.by_status[JobStatus.FAILED.value] == 1
        assert metrics.by_status[JobStatus.RUNNING.value] == 0
        assert metrics.avg_retry_count == pytest.approx(2.0)
        assert metrics.max_retry_count == 4


@pytest.mark.asyncio
async def test_document_metrics_report_zero_for_every_status_with_no_documents(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        metrics = await get_document_metrics(session)

        assert metrics.by_status == {status.value: 0 for status in DocumentStatus}


@pytest.mark.asyncio
async def test_document_metrics_counts_by_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _make_document(session, DocumentStatus.UPLOADED)
        await _make_document(session, DocumentStatus.VALIDATED)
        await _make_document(session, DocumentStatus.VALIDATED)

        metrics = await get_document_metrics(session)

        assert metrics.by_status[DocumentStatus.UPLOADED.value] == 1
        assert metrics.by_status[DocumentStatus.VALIDATED.value] == 2
        assert metrics.by_status[DocumentStatus.FAILED.value] == 0


@pytest.mark.asyncio
async def test_confidence_metrics_are_none_with_no_extraction_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        metrics = await get_confidence_metrics(session)

        assert metrics.count == 0
        assert metrics.min is None
        assert metrics.avg is None
        assert metrics.max is None


@pytest.mark.asyncio
async def test_confidence_metrics_aggregate_across_extraction_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, DocumentStatus.VALIDATED)
        session.add_all(
            [
                ExtractionResult(document_id=document.id, raw_text="a", confidence=0.2),
                ExtractionResult(document_id=document.id, raw_text="b", confidence=0.8),
            ]
        )
        await session.commit()

        metrics = await get_confidence_metrics(session)

        assert metrics.count == 2
        assert metrics.min == pytest.approx(0.2)
        assert metrics.avg == pytest.approx(0.5)
        assert metrics.max == pytest.approx(0.8)
