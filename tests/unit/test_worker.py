import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.processing.models import Job, JobStatus
from modules.processing.worker import start_worker, stop_worker

POLL_INTERVAL = 0.01


def _fake_job() -> Job:
    return Job(id=uuid.uuid4(), document_id=uuid.uuid4(), status=JobStatus.RUNNING)


@pytest.mark.asyncio
async def test_claims_job_and_calls_process_job_once(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _fake_job()
    claim_calls = 0
    queue_drained = asyncio.Event()

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            return job
        queue_drained.set()
        return None

    processed: list[Job] = []

    async def fake_process_job(claimed: Job) -> None:
        processed.append(claimed)

    monkeypatch.setattr(worker_module, "claim_next_job", fake_claim_next_job)

    task = await start_worker(
        session_factory, process_job_fn=fake_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(queue_drained.wait(), timeout=2)
    await stop_worker(task)

    assert processed == [job]
    assert task.done()
    assert task.cancelled()  # stop_worker cancelled it; that's clean shutdown, not a crash


@pytest.mark.asyncio
async def test_does_nothing_when_queue_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_calls = 0
    polled_a_few_times = asyncio.Event()

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls >= 3:
            polled_a_few_times.set()
        return None

    processed: list[Job] = []

    async def fake_process_job(claimed: Job) -> None:
        processed.append(claimed)

    monkeypatch.setattr(worker_module, "claim_next_job", fake_claim_next_job)

    task = await start_worker(
        session_factory, process_job_fn=fake_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(polled_a_few_times.wait(), timeout=2)
    await stop_worker(task)

    assert processed == []
    assert claim_calls >= 3


@pytest.mark.asyncio
async def test_respects_cancellation_and_exits_cleanly(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        started.set()
        return None

    monkeypatch.setattr(worker_module, "claim_next_job", fake_claim_next_job)

    task = await start_worker(session_factory, poll_interval_seconds=POLL_INTERVAL)
    await asyncio.wait_for(started.wait(), timeout=2)

    await stop_worker(task)

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_does_not_double_process_jobs_under_repeated_polling(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = [_fake_job(), _fake_job(), _fake_job()]
    remaining = list(jobs)
    all_claimed = asyncio.Event()

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        if remaining:
            return remaining.pop(0)
        all_claimed.set()
        return None

    processed: list[Job] = []

    async def fake_process_job(claimed: Job) -> None:
        processed.append(claimed)

    monkeypatch.setattr(worker_module, "claim_next_job", fake_claim_next_job)

    task = await start_worker(
        session_factory, process_job_fn=fake_process_job, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(all_claimed.wait(), timeout=2)
    # Let a few more empty-queue polls happen to prove they don't reprocess.
    await asyncio.sleep(POLL_INTERVAL * 5)
    await stop_worker(task)

    assert processed == jobs
    assert len({j.id for j in processed}) == len(jobs)
