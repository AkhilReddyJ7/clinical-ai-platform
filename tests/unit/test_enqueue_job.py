"""ADR-0022's `/process` enqueue contract, at the repository layer:
repository.enqueue_job creates a job and moves the document to
`processing`, or signals why it couldn't (document not found -> None;
illegal starting state -> IllegalTransitionError) so the route can map
either to the right HTTP response (404 / 409).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus
from modules.processing.repository import enqueue_job
from modules.processing.state_machine import IllegalTransitionError


async def _make_document(session: AsyncSession, *, status: DocumentStatus) -> Document:
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
async def test_enqueue_job_returns_none_when_document_does_not_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await enqueue_job(session, uuid.uuid4())
        assert result is None


@pytest.mark.asyncio
async def test_enqueue_job_creates_a_queued_job_from_uploaded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.UPLOADED)

        job = await enqueue_job(session, document.id)

        assert job is not None
        assert job.document_id == document.id
        assert job.status == JobStatus.QUEUED

        stored = await session.get(Document, document.id)
        assert stored is not None
        assert stored.status == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_enqueue_job_creates_a_queued_job_from_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.FAILED)

        job = await enqueue_job(session, document.id)

        assert job is not None
        assert job.status == JobStatus.QUEUED
        stored = await session.get(Document, document.id)
        assert stored is not None
        assert stored.status == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_enqueue_job_rejects_an_already_validated_document(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.VALIDATED)

        with pytest.raises(IllegalTransitionError):
            await enqueue_job(session, document.id)

        # No partial job/state mutation on the illegal path.
        stored = await session.get(Document, document.id)
        assert stored is not None
        assert stored.status == DocumentStatus.VALIDATED


@pytest.mark.asyncio
async def test_enqueue_job_rejects_a_document_already_processing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.PROCESSING)

        with pytest.raises(IllegalTransitionError):
            await enqueue_job(session, document.id)


@pytest.mark.asyncio
async def test_enqueue_job_rejects_an_at_rest_extracted_document(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ADR-0022: an `extracted` document observed at rest (e.g. after a
    crash mid-job) must be treated the same as an active job, not as a
    legal re-processing target."""
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.EXTRACTED)

        with pytest.raises(IllegalTransitionError):
            await enqueue_job(session, document.id)


@pytest.mark.asyncio
async def test_a_second_enqueue_after_the_first_completes_creates_a_new_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ADR-0020: a manual retry of a `failed` document creates a new job,
    never mutates the exhausted one."""
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.UPLOADED)

        first_job = await enqueue_job(session, document.id)
        assert first_job is not None

        # Simulate the first job finishing (terminal failure).
        stored = await session.get(Document, document.id)
        assert stored is not None
        stored.status = DocumentStatus.FAILED
        session.add(stored)
        await session.commit()

        second_job = await enqueue_job(session, document.id)

        assert second_job is not None
        assert second_job.id != first_job.id

        all_jobs = (
            (await session.execute(select(Job).where(Job.document_id == document.id)))
            .scalars()
            .all()
        )
        assert len(all_jobs) == 2
