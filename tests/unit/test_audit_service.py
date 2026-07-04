"""ADR-0027: audit_log_entries recording. record_action is the only write
path -- these tests cover both recorded actions (document upload, job
enqueue), the schema's redaction guarantee (no free-text field exists to
assert against, so this instead asserts the *shape* is exactly what
ADR-0027 specifies), and that a write failure never propagates.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.audit.models import AuditAction, AuditLogEntry
from modules.audit.service import record_action
from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus


async def _make_document(session: AsyncSession) -> Document:
    document = Document(
        id=uuid.uuid4(),
        filename="report.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_key=f"{uuid.uuid4()}/report.txt",
        status=DocumentStatus.UPLOADED,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


async def _make_job(session: AsyncSession, document: Document) -> Job:
    job = Job(document_id=document.id, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_record_action_persists_a_document_only_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)

        entry = await record_action(
            session,
            caller="alice",
            action=AuditAction.DOCUMENT_UPLOADED,
            document_id=document.id,
        )

        assert entry is not None
        assert entry.caller == "alice"
        assert entry.action == AuditAction.DOCUMENT_UPLOADED
        assert entry.document_id == document.id
        assert entry.job_id is None
        assert entry.created_at is not None

        stored = await session.get(AuditLogEntry, entry.id)
        assert stored is not None
        assert stored.caller == "alice"


@pytest.mark.asyncio
async def test_record_action_persists_a_document_and_job_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_job(session, document)

        entry = await record_action(
            session,
            caller="bob",
            action=AuditAction.JOB_ENQUEUED,
            document_id=document.id,
            job_id=job.id,
        )

        assert entry is not None
        assert entry.action == AuditAction.JOB_ENQUEUED
        assert entry.document_id == document.id
        assert entry.job_id == job.id


@pytest.mark.asyncio
async def test_multiple_entries_can_be_queried_back_by_caller(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        await record_action(
            session, caller="alice", action=AuditAction.DOCUMENT_UPLOADED, document_id=document.id
        )
        job = await _make_job(session, document)
        await record_action(
            session,
            caller="alice",
            action=AuditAction.JOB_ENQUEUED,
            document_id=document.id,
            job_id=job.id,
        )
        await record_action(
            session, caller="bob", action=AuditAction.DOCUMENT_UPLOADED, document_id=document.id
        )

        alices_entries = (
            (await session.execute(select(AuditLogEntry).where(AuditLogEntry.caller == "alice")))
            .scalars()
            .all()
        )
        assert len(alices_entries) == 2
        assert {e.action for e in alices_entries} == {
            AuditAction.DOCUMENT_UPLOADED,
            AuditAction.JOB_ENQUEUED,
        }


@pytest.mark.asyncio
async def test_the_schema_has_no_free_text_field(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Structural assertion for ADR-0027's redaction policy: there is no
    column capable of holding raw text/PHI-shaped content in the first
    place, so no runtime filtering can ever be forgotten. Locks in the
    exact column set the ADR specifies -- a future column addition should
    have to consciously break this test, not slip in silently.
    """
    columns = {column.name for column in AuditLogEntry.__table__.columns}
    assert columns == {"id", "caller", "action", "document_id", "job_id", "created_at"}


@pytest.mark.asyncio
async def test_record_action_returns_none_and_does_not_raise_on_failure(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0027: audit recording must never be able to fail the action it
    audits. Simulates a DB failure at commit time directly (rather than
    relying on e.g. an FK violation, which SQLite doesn't enforce by
    default) -- record_action must catch it, roll back, and return None,
    not raise.
    """
    async with session_factory() as session:
        document = await _make_document(session)
        document_id = document.id  # captured before the rollback expires `document`

        async def _broken_commit() -> None:
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session, "commit", _broken_commit)

        result = await record_action(
            session,
            caller="alice",
            action=AuditAction.DOCUMENT_UPLOADED,
            document_id=document_id,
        )

        assert result is None

    # The session must still be usable afterward (rollback succeeded) --
    # verified with a fresh session against the same underlying data.
    async with session_factory() as verify_session:
        stored = await verify_session.get(Document, document_id)
        assert stored is not None
        entries = (await verify_session.execute(select(AuditLogEntry))).scalars().all()
        assert entries == []
