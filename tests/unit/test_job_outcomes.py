import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus
from modules.processing.repository import mark_job_completed, mark_job_failed, mark_job_retry


async def _make_document(session: AsyncSession) -> Document:
    document = Document(
        id=uuid.uuid4(),
        filename="report.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_key=f"{uuid.uuid4()}/report.txt",
        status=DocumentStatus.PROCESSING,
    )
    session.add(document)
    await session.commit()
    return document


async def _make_running_job(session: AsyncSession, document: Document) -> Job:
    job = Job(document_id=document.id, status=JobStatus.RUNNING)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_mark_job_completed_transitions_running_to_completed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document)

        result = await mark_job_completed(session, job.id)

        assert result is not None
        assert result.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_mark_job_failed_transitions_running_to_failed_and_records_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document)

        result = await mark_job_failed(session, job.id, "boom: invalid api key")

        assert result is not None
        assert result.status == JobStatus.FAILED
        assert result.last_error == "boom: invalid api key"


@pytest.mark.asyncio
async def test_mark_job_retry_transitions_running_to_retrying_and_increments_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document)
        assert job.retry_count == 0

        result = await mark_job_retry(session, job.id)
        assert result is not None
        assert result.status == JobStatus.RETRYING
        assert result.retry_count == 1
        assert result.next_attempt_at is not None  # ADR-0023 backoff scheduling

        # Simulate the worker reclaiming it for a second attempt.
        result.status = JobStatus.RUNNING
        session.add(result)
        await session.commit()

        second = await mark_job_retry(session, job.id)
        assert second is not None
        assert second.retry_count == 2


@pytest.mark.asyncio
async def test_mark_job_completed_is_a_noop_when_job_is_not_running(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = Job(document_id=document.id, status=JobStatus.QUEUED)
        session.add(job)
        await session.commit()
        await session.refresh(job)

        result = await mark_job_completed(session, job.id)

        assert result is None
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_second_writer_loses_the_race_after_the_job_already_moved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ADR-0024 section 5: a conditional write that arrives after the job
    already left `running` must be discarded, not applied or raised."""
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document)

        first = await mark_job_completed(session, job.id)
        assert first is not None
        assert first.status == JobStatus.COMPLETED

        second = await mark_job_failed(session, job.id, "arrived too late")

        assert second is None
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED
        assert stored.last_error is None
