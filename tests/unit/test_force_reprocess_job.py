"""ADR-0032: force_reprocess_job is the one deliberate, audited bypass of
the validated -> processing edge ADR-0020 left disallowed by default.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import JobStatus, JobTrigger
from modules.processing.repository import enqueue_job, force_reprocess_job
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
async def test_force_reprocess_job_returns_none_when_document_does_not_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await force_reprocess_job(session, uuid.uuid4())
        assert result is None


@pytest.mark.asyncio
async def test_force_reprocess_job_succeeds_from_validated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.VALIDATED)

        job = await force_reprocess_job(
            session, document.id, trigger_note="manual reprocess: bad OCR"
        )

        assert job is not None
        assert job.status == JobStatus.QUEUED
        assert job.trigger == JobTrigger.FORCED_REPROCESS
        assert job.trigger_note == "manual reprocess: bad OCR"
        assert job.attempt_number == 1

        stored = await session.get(Document, document.id)
        assert stored is not None
        assert stored.status == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_force_reprocess_job_trigger_note_defaults_to_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.VALIDATED)

        job = await force_reprocess_job(session, document.id)

        assert job is not None
        assert job.trigger_note is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.UPLOADED,
        DocumentStatus.PROCESSING,
        DocumentStatus.EXTRACTED,
        DocumentStatus.FAILED,
    ],
)
async def test_force_reprocess_job_rejects_any_non_validated_status(
    session_factory: async_sessionmaker[AsyncSession], status: DocumentStatus
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=status)

        with pytest.raises(IllegalTransitionError):
            await force_reprocess_job(session, document.id)

        stored = await session.get(Document, document.id)
        assert stored is not None
        assert stored.status == status


@pytest.mark.asyncio
async def test_force_reprocess_job_computes_attempt_number_after_prior_attempts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session, status=DocumentStatus.UPLOADED)
        first_job = await enqueue_job(session, document.id)
        assert first_job is not None

        stored = await session.get(Document, document.id)
        assert stored is not None
        stored.status = DocumentStatus.VALIDATED
        session.add(stored)
        await session.commit()

        second_job = await force_reprocess_job(
            session, document.id, trigger_note="backfill: batch-1"
        )

        assert second_job is not None
        assert second_job.attempt_number == 2
