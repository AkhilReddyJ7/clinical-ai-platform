import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus
from modules.processing.repository import claim_next_job


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


async def _make_job(session: AsyncSession, document: Document, status: JobStatus) -> Job:
    job = Job(document_id=document.id, status=status)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_claims_a_queued_job_and_marks_it_running(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_job(session, document, JobStatus.QUEUED)

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_returns_none_when_queue_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await claim_next_job(session) is None


@pytest.mark.asyncio
async def test_returns_none_when_no_job_has_queued_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        for status in (
            JobStatus.RUNNING,
            JobStatus.RETRYING,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ):
            await _make_job(session, document, status)

        assert await claim_next_job(session) is None


@pytest.mark.asyncio
async def test_claims_the_oldest_queued_job_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        older = await _make_job(session, document, JobStatus.QUEUED)
        # Force a distinguishable ordering regardless of clock resolution.
        older.created_at = older.created_at.replace(year=older.created_at.year - 1)
        session.add(older)
        await session.commit()
        await _make_job(session, document, JobStatus.QUEUED)

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == older.id


@pytest.mark.asyncio
async def test_sequential_claims_never_return_the_same_job_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        first = await _make_job(session, document, JobStatus.QUEUED)
        second = await _make_job(session, document, JobStatus.QUEUED)

        claimed_ids = set()
        for _ in range(2):
            claimed = await claim_next_job(session)
            assert claimed is not None
            claimed_ids.add(claimed.id)

        assert claimed_ids == {first.id, second.id}
        assert await claim_next_job(session) is None
