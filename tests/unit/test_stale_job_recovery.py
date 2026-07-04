"""ADR-0024: stale `running` job recovery after a worker crash.

Before this increment, nothing detected or recovered a job whose worker
died mid-execution -- claim_next_job only ever moved queued/retrying jobs
forward, and Job.updated_at (already present since Increment 1) was never
read as a liveness signal anywhere. A crashed worker permanently stranded
its claimed job (and, per ADR-0020, the document behind it) in `running`
forever. This file exercises repository.py's reclaim_stale_job directly,
and worker.py's wiring of it into the idle-poll path (JOB_STALE_SKIPPED,
metrics.stale_reclaims).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.ingestion.models import Document, DocumentStatus
from modules.processing.events import Event, EventType, subscribe, unsubscribe
from modules.processing.metrics import metrics
from modules.processing.models import Job, JobStatus
from modules.processing.repository import claim_next_job, reclaim_stale_job
from modules.processing.worker import start_worker, stop_worker
from shared.config.settings import get_settings

POLL_INTERVAL = 0.01


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


async def _make_stale_running_job(
    session: AsyncSession, document: Document, *, retry_count: int = 0, age_seconds: float = 999.0
) -> Job:
    job = Job(document_id=document.id, status=JobStatus.RUNNING, retry_count=retry_count)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    # updated_at has an ORM onupdate default, not settable via the
    # constructor -- backdate it directly so the row looks genuinely
    # untouched for `age_seconds`, matching what a real crashed worker's
    # last write would look like.
    job.updated_at = _utcnow() - timedelta(seconds=age_seconds)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    metrics.reset()


@pytest.fixture
def collected_events() -> Iterator[list[Event]]:
    events: list[Event] = []
    subscribe(events.append)
    yield events
    unsubscribe(events.append)


# --- reclaim_stale_job (repository layer) ----------------------------------


@pytest.mark.asyncio
async def test_reclaim_stale_job_returns_none_when_nothing_is_stale(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        # Fresh: updated_at defaults to now, nowhere near the timeout.
        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()

        reclaimed = await reclaim_stale_job(session)

        assert reclaimed is None


@pytest.mark.asyncio
async def test_reclaim_stale_job_moves_a_stale_job_to_retrying_when_budget_remains(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_stale_running_job(session, document, retry_count=0)

        reclaimed = await reclaim_stale_job(session)

        assert reclaimed is not None
        assert reclaimed.id == job.id
        assert reclaimed.status == JobStatus.RETRYING
        assert reclaimed.retry_count == 1
        assert reclaimed.next_attempt_at is not None


@pytest.mark.asyncio
async def test_reclaim_stale_job_fails_the_job_once_budget_is_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "job_max_retry_attempts", 3)

    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_stale_running_job(session, document, retry_count=3)

        reclaimed = await reclaim_stale_job(session)

        assert reclaimed is not None
        assert reclaimed.id == job.id
        assert reclaimed.status == JobStatus.FAILED
        assert reclaimed.retry_count == 3  # unchanged: this path doesn't consume a retry
        assert reclaimed.last_error is not None
        assert "stale" in reclaimed.last_error


@pytest.mark.asyncio
async def test_reclaim_stale_job_ignores_a_running_job_that_is_still_fresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await _make_document(session)
        fresh = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(fresh)
        await session.commit()
        await session.refresh(fresh)

        reclaimed = await reclaim_stale_job(session)

        assert reclaimed is None
        stored = await session.get(Job, fresh.id)
        assert stored is not None
        assert stored.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_a_reclaimed_job_is_later_claimable_once_its_backoff_elapses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end recovery: stale -> retrying (this increment) -> running
    again (ADR-0023's existing backoff-driven reclaim, unchanged here)."""
    async with session_factory() as session:
        document = await _make_document(session)
        job = await _make_stale_running_job(session, document)

        reclaimed = await reclaim_stale_job(session)
        assert reclaimed is not None and reclaimed.status == JobStatus.RETRYING

        # Simulate the backoff having elapsed.
        reclaimed.next_attempt_at = _utcnow() - timedelta(seconds=1)
        session.add(reclaimed)
        await session.commit()

        claimed = await claim_next_job(session)

        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == JobStatus.RUNNING


# --- worker.py wiring (idle-poll scan, JOB_STALE_SKIPPED, metrics) --------


def _fake_claim_always_empty() -> Callable[[AsyncSession], Awaitable[Job | None]]:
    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        return None

    return fake_claim_next_job


@pytest.mark.asyncio
async def test_worker_loop_reclaims_a_stale_job_and_emits_the_event(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    async with session_factory() as setup_session:
        document = await _make_document(setup_session)
        job = await _make_stale_running_job(setup_session, document, retry_count=0)

    # The queue itself is empty/irrelevant here -- claim_next_job never
    # finds anything, so every iteration falls into the stale-scan path.
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_always_empty())

    async def unreachable_process_job(claimed: Job) -> None:
        raise AssertionError("process_job_fn must never run for a stale-only scenario")

    task = await start_worker(
        session_factory,
        process_job_fn=unreachable_process_job,
        poll_interval_seconds=POLL_INTERVAL,
    )
    await asyncio.sleep(POLL_INTERVAL * 5)
    await stop_worker(task)

    stale_events = [e for e in collected_events if e.event_type == EventType.JOB_STALE_SKIPPED]
    assert len(stale_events) == 1
    assert stale_events[0].job_id == str(job.id)
    assert stale_events[0].metadata["outcome_status"] == "retrying"
    assert metrics.stale_reclaims == 1

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING


@pytest.mark.asyncio
async def test_worker_loop_finalizes_the_document_when_a_stale_reclaim_exhausts_the_budget(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    """Regression check: repository.reclaim_stale_job deliberately never
    touches the document (it stays a pure job-table write, per
    repository.py's documented boundary) -- so when a stale reclaim
    exhausts the retry budget and the job goes straight to FAILED,
    nothing else would move the document out of `processing` unless
    run_worker_loop's idle-poll branch calls
    _finalize_document_as_failed itself. Reproduced directly before this
    fix existed: the job reached FAILED while the document stayed
    PROCESSING forever.
    """
    monkeypatch.setattr(get_settings(), "job_max_retry_attempts", 3)

    async with session_factory() as setup_session:
        document = await _make_document(setup_session)
        job = await _make_stale_running_job(setup_session, document, retry_count=3)

    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_always_empty())

    async def unreachable_process_job(claimed: Job) -> None:
        raise AssertionError("process_job_fn must never run for a stale-only scenario")

    task = await start_worker(
        session_factory,
        process_job_fn=unreachable_process_job,
        poll_interval_seconds=POLL_INTERVAL,
    )
    await asyncio.sleep(POLL_INTERVAL * 5)
    await stop_worker(task)

    stale_events = [e for e in collected_events if e.event_type == EventType.JOB_STALE_SKIPPED]
    assert len(stale_events) == 1
    assert stale_events[0].metadata["outcome_status"] == "failed"

    async with session_factory() as session:
        stored_job = await session.get(Job, job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.FAILED

        stored_document = await session.get(Document, document.id)
        assert stored_document is not None
        assert stored_document.status == DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_worker_loop_does_not_reclaim_when_nothing_is_stale(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_always_empty())

    async def unreachable_process_job(claimed: Job) -> None:
        raise AssertionError("nothing should ever be claimed or reclaimed here")

    task = await start_worker(
        session_factory,
        process_job_fn=unreachable_process_job,
        poll_interval_seconds=POLL_INTERVAL,
    )
    await asyncio.sleep(POLL_INTERVAL * 5)
    await stop_worker(task)

    assert collected_events == []
    assert metrics.stale_reclaims == 0
