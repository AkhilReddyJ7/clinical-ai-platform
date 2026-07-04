import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.processing.errors import TerminalProcessingError, TransientProcessingError
from modules.processing.metrics import metrics
from modules.processing.models import Job, JobStatus
from modules.processing.worker import start_worker, stop_worker

POLL_INTERVAL = 0.01


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    metrics.reset()


def _fake_job() -> Job:
    return Job(id=uuid.uuid4(), document_id=uuid.uuid4(), status=JobStatus.RUNNING)


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
async def test_job_claim_is_logged_and_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        return None

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        task = await start_worker(
            session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
        )
        await asyncio.wait_for(drained.wait(), timeout=2)
        await stop_worker(task)

    claim_logs = [r.message for r in caplog.records if "job claimed" in r.message]
    assert len(claim_logs) == 1
    assert f"job_id={job.id}" in claim_logs[0]
    assert f"document_id={job.document_id}" in claim_logs[0]
    assert "status=running" in claim_logs[0]
    assert metrics.jobs_claimed == 1


@pytest.mark.asyncio
async def test_successful_completion_is_logged_and_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        return None

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        task = await start_worker(
            session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
        )
        await asyncio.wait_for(drained.wait(), timeout=2)
        await stop_worker(task)

    completed_logs = [r.message for r in caplog.records if "job completed" in r.message]
    assert len(completed_logs) == 1
    assert "duration_seconds=" in completed_logs[0]
    assert metrics.completions == 1
    assert metrics.stage_summary("job_total") is not None


@pytest.mark.asyncio
async def test_transient_failure_is_logged_as_retrying_and_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        raise TransientProcessingError("rate limited")

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        task = await start_worker(
            session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
        )
        await asyncio.wait_for(drained.wait(), timeout=2)
        await stop_worker(task)

    retry_logs = [r.message for r in caplog.records if "job retrying" in r.message]
    assert len(retry_logs) == 1
    assert f"job_id={job.id}" in retry_logs[0]
    assert metrics.retries == 1
    assert metrics.terminal_failures == 0


@pytest.mark.asyncio
async def test_terminal_failure_is_logged_as_failed_and_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        raise TerminalProcessingError("invalid api key")

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        task = await start_worker(
            session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
        )
        await asyncio.wait_for(drained.wait(), timeout=2)
        await stop_worker(task)

    failed_logs = [r.message for r in caplog.records if "job failed" in r.message]
    assert len(failed_logs) == 1
    assert "error=invalid api key" in failed_logs[0]
    assert metrics.terminal_failures == 1
    assert metrics.retries == 0


@pytest.mark.asyncio
async def test_metrics_accumulate_across_multiple_jobs(
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

    async def succeed(claimed: Job) -> None:
        return None

    async def retry(claimed: Job) -> None:
        raise TransientProcessingError("rate limited")

    async def fail(claimed: Job) -> None:
        raise TerminalProcessingError("bad input")

    call_plan = iter([succeed, retry, fail])

    async def process_job_fn(claimed: Job) -> None:
        await next(call_plan)(claimed)

    monkeypatch.setattr(worker_module, "claim_next_job", fake_claim_next_job)

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(all_claimed.wait(), timeout=2)
    await stop_worker(task)

    assert metrics.jobs_claimed == 3
    assert metrics.completions == 1
    assert metrics.retries == 1
    assert metrics.terminal_failures == 1
