import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.processing.errors import TerminalProcessingError, TransientProcessingError
from modules.processing.events import Event, EventType, subscribe, unsubscribe
from modules.processing.metrics import metrics
from modules.processing.models import Job, JobStatus
from modules.processing.worker import start_worker, stop_worker

POLL_INTERVAL = 0.01


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    metrics.reset()


@pytest.fixture
def collected_events() -> Iterator[list[Event]]:
    events: list[Event] = []
    subscribe(events.append)
    yield events
    unsubscribe(events.append)


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
async def test_job_claim_emits_event_and_is_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        return None

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    claimed_events = [e for e in collected_events if e.event_type == EventType.JOB_CLAIMED]
    assert len(claimed_events) == 1
    assert claimed_events[0].job_id == job.id
    assert claimed_events[0].document_id == job.document_id
    assert claimed_events[0].metadata["status"] == "running"
    assert metrics.jobs_claimed == 1


@pytest.mark.asyncio
async def test_successful_completion_emits_event_and_is_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        return None

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    completed_events = [e for e in collected_events if e.event_type == EventType.JOB_COMPLETED]
    assert len(completed_events) == 1
    assert isinstance(completed_events[0].metadata["duration_ms"], (int, float))
    assert metrics.completions == 1
    assert metrics.stage_summary("job_total") is not None


@pytest.mark.asyncio
async def test_transient_failure_emits_retrying_event_and_is_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        raise TransientProcessingError("rate limited")

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    retrying_events = [e for e in collected_events if e.event_type == EventType.JOB_RETRYING]
    assert len(retrying_events) == 1
    assert retrying_events[0].job_id == job.id
    assert retrying_events[0].metadata["error_type"] == "TransientProcessingError"
    assert retrying_events[0].metadata["error"] == "rate limited"
    assert metrics.retries == 1
    assert metrics.terminal_failures == 0


@pytest.mark.asyncio
async def test_terminal_failure_emits_failed_event_and_is_counted(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    job = _fake_job()
    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    async def process_job_fn(claimed: Job) -> None:
        raise TerminalProcessingError("invalid api key")

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    failed_events = [e for e in collected_events if e.event_type == EventType.JOB_FAILED]
    assert len(failed_events) == 1
    assert failed_events[0].metadata["error"] == "invalid api key"
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
