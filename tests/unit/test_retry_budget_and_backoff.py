"""ADR-0023's retry budget and backoff policy: the gap this increment closes.

Before this increment: mark_job_retry incremented retry_count with no
ceiling and scheduled no delay, and claim_next_job only ever looked at
`queued` jobs — a job that entered `retrying` had no path back to
`running` at all (confirmed by reading repository.py and by
test_cross_system_state_consistency.py's own docstring, which manually
simulated the missing transition). This file exercises the real
mechanism that closes that gap: repository.py's `_compute_backoff_seconds`
/ `_claim_ready_retrying_job`, and worker.py's retry-budget check before
calling `mark_job_retry`.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.ingestion.models import Document, DocumentStatus
from modules.processing.errors import TransientProcessingError
from modules.processing.models import Job, JobStatus
from modules.processing.repository import claim_next_job, mark_job_retry
from modules.processing.worker import start_worker, stop_worker
from shared.config.settings import get_settings

POLL_INTERVAL = 0.01


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    """SQLite (the test engine, per ADR-0004) drops tzinfo on round-trip
    through a DateTime(timezone=True) column even though the stored wall-
    clock value is still UTC -- normalize both sides before comparing."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


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


async def _make_running_job(
    session: AsyncSession, document: Document, *, retry_count: int = 0
) -> Job:
    job = Job(document_id=document.id, status=JobStatus.RUNNING, retry_count=retry_count)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


def _fake_claim_once(
    job: Job, drained: asyncio.Event
) -> Callable[[AsyncSession], Awaitable[Job | None]]:
    claimed = {"done": False}

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        if not claimed["done"]:
            claimed["done"] = True
            return job
        drained.set()
        return None

    return fake_claim_next_job


# --- Backoff scheduling (mark_job_retry) -----------------------------------


@pytest.mark.asyncio
async def test_mark_job_retry_schedules_next_attempt_in_the_future(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document)

        result = await mark_job_retry(session, job.id)

        assert result is not None
        assert result.next_attempt_at is not None
        assert _naive(result.next_attempt_at) > _naive(_utcnow())


@pytest.mark.asyncio
async def test_backoff_delay_grows_with_each_successive_retry(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "job_retry_backoff_initial_seconds", 10.0)
    monkeypatch.setattr(settings, "job_retry_backoff_multiplier", 2.0)
    monkeypatch.setattr(settings, "job_retry_backoff_max_seconds", 1000.0)
    monkeypatch.setattr(settings, "job_retry_backoff_jitter_seconds", 0.0)

    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document, retry_count=0)

        first = await mark_job_retry(session, job.id)
        assert first is not None and first.next_attempt_at is not None
        first_delay = (_naive(first.next_attempt_at) - _naive(_utcnow())).total_seconds()

        first.status = JobStatus.RUNNING
        session.add(first)
        await session.commit()

        second = await mark_job_retry(session, job.id)
        assert second is not None and second.next_attempt_at is not None
        second_delay = (_naive(second.next_attempt_at) - _naive(_utcnow())).total_seconds()

        assert second_delay > first_delay
        assert 8 <= first_delay <= 12  # 10 * 2**0, generous tolerance for test wall-clock cost
        assert 18 <= second_delay <= 22  # 10 * 2**1


@pytest.mark.asyncio
async def test_backoff_delay_is_capped(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "job_retry_backoff_initial_seconds", 10.0)
    monkeypatch.setattr(settings, "job_retry_backoff_multiplier", 100.0)
    monkeypatch.setattr(settings, "job_retry_backoff_max_seconds", 30.0)
    monkeypatch.setattr(settings, "job_retry_backoff_jitter_seconds", 0.0)

    async with session_factory() as session:
        document = await _make_document(session)
        # Uncapped this would be 10 * 100**5 seconds -- must clamp to 30.
        job = await _make_running_job(session, document, retry_count=5)

        result = await mark_job_retry(session, job.id)

        assert result is not None and result.next_attempt_at is not None
        delay = (_naive(result.next_attempt_at) - _naive(_utcnow())).total_seconds()
        assert delay <= 31


# --- Reclaim gating (claim_next_job / _claim_ready_retrying_job) ----------


@pytest.mark.asyncio
async def test_claim_next_job_does_not_reclaim_before_backoff_elapses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = Job(
            document_id=document.id,
            status=JobStatus.RETRYING,
            retry_count=1,
            next_attempt_at=_utcnow() + timedelta(seconds=60),
        )
        session.add(job)
        await session.commit()

        claimed = await claim_next_job(session)

        assert claimed is None


@pytest.mark.asyncio
async def test_claim_next_job_reclaims_a_retrying_job_once_backoff_elapses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = Job(
            document_id=document.id,
            status=JobStatus.RETRYING,
            retry_count=1,
            next_attempt_at=_utcnow() - timedelta(seconds=1),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == JobStatus.RUNNING
        assert claimed.next_attempt_at is None


@pytest.mark.asyncio
async def test_claim_next_job_prefers_queued_over_a_ready_retrying_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        retrying_document = await _make_document(session)
        retrying_job = Job(
            document_id=retrying_document.id,
            status=JobStatus.RETRYING,
            retry_count=1,
            next_attempt_at=_utcnow() - timedelta(seconds=1),
        )
        session.add(retrying_job)
        await session.commit()

        queued_document = await _make_document(session)
        queued_job = Job(document_id=queued_document.id, status=JobStatus.QUEUED)
        session.add(queued_job)
        await session.commit()
        await session.refresh(queued_job)

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == queued_job.id


@pytest.mark.asyncio
async def test_claim_next_job_falls_back_to_a_ready_retrying_job_when_queue_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        retrying_job = Job(
            document_id=document.id,
            status=JobStatus.RETRYING,
            retry_count=1,
            next_attempt_at=_utcnow() - timedelta(seconds=1),
        )
        session.add(retrying_job)
        await session.commit()
        await session.refresh(retrying_job)

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == retrying_job.id
        assert claimed.status == JobStatus.RUNNING


# --- Retry budget enforcement (worker.py) ----------------------------------


@pytest.mark.asyncio
async def test_transient_failure_retries_while_budget_remains(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "job_max_retry_attempts", 3)

    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document, retry_count=2)  # one retry left

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def flaky(claimed: Job) -> None:
        raise TransientProcessingError("rate limited")

    task = await start_worker(
        session_factory, process_job_fn=flaky, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING
        assert stored.retry_count == 3


@pytest.mark.asyncio
async def test_transient_failure_fails_the_job_once_budget_is_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "job_max_retry_attempts", 3)

    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_running_job(session, document, retry_count=3)  # budget already spent

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def flaky(claimed: Job) -> None:
        raise TransientProcessingError("rate limited again")

    task = await start_worker(
        session_factory, process_job_fn=flaky, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert stored.retry_count == 3  # unchanged: budget exhaustion consumes no new retry
        assert stored.last_error == "rate limited again"
