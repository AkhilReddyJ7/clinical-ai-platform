import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.ingestion.models import Document, DocumentStatus
from modules.processing.errors import TerminalProcessingError, TransientProcessingError
from modules.processing.models import Job, JobStatus
from modules.processing.worker import start_worker, stop_worker

POLL_INTERVAL = 0.01


async def _make_running_job(session: AsyncSession) -> Job:
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

    job = Job(document_id=document.id, status=JobStatus.RUNNING)
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


@pytest.mark.asyncio
async def test_successful_job_is_marked_completed(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as setup_session:
        job = await _make_running_job(setup_session)

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def succeeding_process_job(claimed: Job) -> None:
        return None

    task = await start_worker(
        session_factory, process_job_fn=succeeding_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_transient_exception_marks_job_retrying_with_incremented_count(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as setup_session:
        job = await _make_running_job(setup_session)

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def flaky_process_job(claimed: Job) -> None:
        raise TransientProcessingError("rate limited")

    task = await start_worker(
        session_factory, process_job_fn=flaky_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING
        assert stored.retry_count == 1


@pytest.mark.asyncio
async def test_terminal_exception_marks_job_failed_with_error_recorded(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as setup_session:
        job = await _make_running_job(setup_session)

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def broken_process_job(claimed: Job) -> None:
        raise TerminalProcessingError("invalid api key")

    task = await start_worker(
        session_factory, process_job_fn=broken_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert stored.last_error == "invalid api key"
        assert stored.retry_count == 0


@pytest.mark.asyncio
async def test_an_unclassified_exception_defaults_to_terminal_failure(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as setup_session:
        job = await _make_running_job(setup_session)

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def surprising_process_job(claimed: Job) -> None:
        raise ValueError("nobody classified this one")

    task = await start_worker(
        session_factory, process_job_fn=surprising_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_process_job_is_never_called_more_than_once_for_a_single_claim(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as setup_session:
        job = await _make_running_job(setup_session)

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    call_count = 0

    async def counting_process_job(claimed: Job) -> None:
        nonlocal call_count
        call_count += 1

    task = await start_worker(
        session_factory, process_job_fn=counting_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    # Let several more empty-queue polls happen to prove no reprocessing.
    await asyncio.sleep(POLL_INTERVAL * 5)
    await stop_worker(task)

    assert call_count == 1
