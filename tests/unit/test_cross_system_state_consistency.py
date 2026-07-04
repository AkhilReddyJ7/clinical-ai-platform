"""Increment 12: verifies that DB job state, pipeline execution, and
emitted events all represent the same lifecycle truth — DB state is
authoritative; events must mirror it, never assert something the DB
doesn't back up (this increment's own closing principle).

This increment found and fixed one real violation of that principle:
worker.py emitted JOB_COMPLETED/JOB_RETRYING/JOB_FAILED unconditionally,
even when mark_job_completed/mark_job_retry/mark_job_failed's conditional
UPDATE (ADR-0024's fencing) matched zero rows — i.e. even when no DB
transition actually happened. Fixed by guarding each emission with the
same `outcome is not None` check the code already used for its warning
log, just applied *before* emitting, not after. See
test_no_event_fires_when_the_outcome_write_is_a_no_op below for the
regression proof.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.base import ExtractionOutput, ExtractionPipeline
from modules.ocr.mock import MockExtractionPipeline
from modules.processing.events import Event, EventType, subscribe, unsubscribe
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import run_processing_pipeline
from modules.processing.repository import mark_job_completed, mark_job_failed, mark_job_retry
from modules.processing.worker import start_worker, stop_worker
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
POLL_INTERVAL = 0.01

_TERMINAL_EVENT_TO_JOB_STATUS = {
    EventType.JOB_COMPLETED: JobStatus.COMPLETED,
    EventType.JOB_RETRYING: JobStatus.RETRYING,
    EventType.JOB_FAILED: JobStatus.FAILED,
}


@pytest.fixture
def collected_events() -> Iterator[list[Event]]:
    events: list[Event] = []
    subscribe(events.append)
    yield events
    unsubscribe(events.append)


async def _make_document_and_job(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    *,
    data: bytes = b"a clinical note with some content",
) -> Job:
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, data)

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type="text/plain",
            size_bytes=len(data),
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
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


def _sequence_for_job(collected_events: list[Event], job_id: uuid.UUID) -> list[Event]:
    return [e for e in collected_events if e.job_id == str(job_id)]


# --- 1. Regression: the bug this increment found and fixed ---------------


@pytest.mark.asyncio
async def test_no_event_fires_when_the_outcome_write_is_a_no_op(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    """Simulates the ADR-0024 race this bug allowed to go unnoticed: some
    other writer completes the job *before* this attempt's own outcome
    write runs. mark_job_completed's conditional UPDATE then matches zero
    rows (the job is no longer `running`) — before the fix, worker.py
    still emitted JOB_COMPLETED anyway; after the fix, it must not.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async def process_job_fn(claimed_job: Job) -> None:
        # A "someone else already finished this job" race, simulated
        # directly: completes the job out from under the worker's own
        # upcoming outcome write.
        async with session_factory() as db:
            result = await mark_job_completed(db, claimed_job.id)
            assert result is not None  # the race's own write must succeed
        return None

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    # The worker's own mark_job_completed call (in its `else:` branch)
    # found the job already `completed` and returned None — so it must
    # emit *zero* JOB_COMPLETED events for this attempt, not a spurious
    # second one.
    completed_events = [
        e
        for e in _sequence_for_job(collected_events, job.id)
        if e.event_type == EventType.JOB_COMPLETED
    ]
    assert completed_events == []

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED  # from the simulated race, not double-applied


@pytest.mark.asyncio
async def test_no_retrying_event_fires_for_an_already_terminal_job(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, collected_events: list[Event]
) -> None:
    """Direct repository-level proof, complementing the worker-loop one
    above: mark_job_retry against a job that's already FAILED must be a
    no-op (ADR-0024 fencing), and per the fix, must not be paired with an
    event anywhere in the system (this test just confirms the repository
    layer's own contract, since worker.py is what actually emits events).
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        first = await mark_job_failed(db, job.id, "already terminal")
        assert first is not None
        assert first.status == JobStatus.FAILED

        second = await mark_job_retry(db, job.id)
        assert second is None  # retry must NOT coexist with a terminal state

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED  # unchanged by the rejected retry
        assert stored.retry_count == 0  # the no-op retry did not increment anything


# --- 2 & 3. Cross-layer alignment: DB state vs. terminal event ------------


@pytest.mark.asyncio
async def test_successful_run_db_state_matches_the_emitted_terminal_event(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=MockExtractionPipeline(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))
    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    sequence = _sequence_for_job(collected_events, job.id)
    terminal_events = [e for e in sequence if e.event_type in _TERMINAL_EVENT_TO_JOB_STATUS]
    assert len(terminal_events) == 1

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == _TERMINAL_EVENT_TO_JOB_STATUS[terminal_events[0].event_type]


@pytest.mark.asyncio
async def test_transient_failure_db_state_matches_the_emitted_terminal_event(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="test-key", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )
    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
    )

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            with patch.object(
                field_extraction_pipeline._client.messages, "create", side_effect=rate_limit_error
            ):
                return await run_processing_pipeline(
                    claimed_job,
                    db=db,
                    storage=storage,
                    extraction_pipeline=MockExtractionPipeline(),
                    field_extraction_pipeline=field_extraction_pipeline,
                    phi_validator=PHIDetectionValidator(),
                    validation_pipeline=CompositeValidationPipeline(
                        [RequiredFieldsValidator(), PHIDetectionValidator()]
                    ),
                )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))
    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    sequence = _sequence_for_job(collected_events, job.id)
    terminal_events = [e for e in sequence if e.event_type in _TERMINAL_EVENT_TO_JOB_STATUS]
    assert len(terminal_events) == 1
    assert terminal_events[0].event_type == EventType.JOB_RETRYING

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING  # non-terminal, per this increment's invariant
        assert stored.retry_count == 1


@pytest.mark.asyncio
async def test_terminal_failure_db_state_matches_the_emitted_terminal_event(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=MockExtractionPipeline(),
                field_extraction_pipeline=field_extraction_pipeline,
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))
    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    sequence = _sequence_for_job(collected_events, job.id)
    terminal_events = [e for e in sequence if e.event_type in _TERMINAL_EVENT_TO_JOB_STATUS]
    assert len(terminal_events) == 1
    assert terminal_events[0].event_type == EventType.JOB_FAILED

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert stored.last_error is not None and "API key" in stored.last_error


# --- 3C. Pipeline stage alignment: stages map to in-progress, not terminal


@pytest.mark.asyncio
async def test_stage_completed_events_occur_while_the_job_is_still_running(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, collected_events: list[Event]
) -> None:
    """Every PIPELINE_STAGE_COMPLETED event is emitted by pipeline.py
    itself, strictly before worker.py ever calls mark_job_completed/
    mark_job_failed/mark_job_retry (which only run *after*
    run_processing_pipeline returns or raises) — so the JOB's own DB
    status must still be RUNNING at the moment every one of them fires,
    regardless of outcome.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        await run_processing_pipeline(
            job,
            db=db,
            storage=storage,
            extraction_pipeline=MockExtractionPipeline(),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            phi_validator=PHIDetectionValidator(),
            validation_pipeline=CompositeValidationPipeline(
                [RequiredFieldsValidator(), PHIDetectionValidator()]
            ),
        )

    # run_processing_pipeline never calls mark_job_*, so job.status in the
    # DB is untouched by it — still RUNNING, exactly as the invariant
    # requires for every stage-completed event that fired during the call.
    stage_completed = [
        e
        for e in _sequence_for_job(collected_events, job.id)
        if e.event_type == EventType.PIPELINE_STAGE_COMPLETED
    ]
    assert len(stage_completed) == 4  # ocr, field_extraction, validation, pipeline_total

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_document_reaches_terminal_status_before_the_job_does(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Documents ADR-0020's intentional separation: run_processing_pipeline
    sets the *document* to its final status (VALIDATED/FAILED) internally,
    but never touches the *job* row — the job only reaches its own
    terminal state afterward, when worker.py calls mark_job_completed.
    This is expected (two independent state models, per ADR-0020), not a
    divergence bug — this test pins it down explicitly so a future change
    doesn't "fix" it by accident.
    """
    from modules.ingestion.models import Document

    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        await run_processing_pipeline(
            job,
            db=db,
            storage=storage,
            extraction_pipeline=MockExtractionPipeline(),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            phi_validator=PHIDetectionValidator(),
            validation_pipeline=CompositeValidationPipeline(
                [RequiredFieldsValidator(), PHIDetectionValidator()]
            ),
        )

    async with session_factory() as session:
        document = await session.get(Document, job.document_id)
        stored_job = await session.get(Job, job.id)
        assert document is not None
        assert stored_job is not None
        assert document.status == DocumentStatus.VALIDATED  # document: already terminal
        assert stored_job.status == JobStatus.RUNNING  # job: not yet (worker.py's job)


# --- 4. Failure-mode simulations: multi-attempt lifecycles ----------------


@pytest.mark.asyncio
async def test_transient_failure_then_retry_then_eventual_success(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    """Simulates the full retry lifecycle ADR-0023 describes. The actual
    backoff-driven reclaim (RETRYING -> RUNNING) has no implementation yet
    (claim_next_job only claims QUEUED jobs — confirmed by reading
    repository.py; ADR-0023's "worker picks the job back up" is not yet
    built, consistent with Increment 4's explicit deferral). This test
    manually performs that one legal transition (already valid per
    ADR-0020's state machine) to simulate what the eventual reclaim loop
    will do, so the *rest* of the lifecycle — second attempt, success,
    final state+event consistency — can be verified now.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="test-key", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )
    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
    )

    async def failing_process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            with patch.object(
                field_extraction_pipeline._client.messages, "create", side_effect=rate_limit_error
            ):
                return await run_processing_pipeline(
                    claimed_job,
                    db=db,
                    storage=storage,
                    extraction_pipeline=MockExtractionPipeline(),
                    field_extraction_pipeline=field_extraction_pipeline,
                    phi_validator=PHIDetectionValidator(),
                    validation_pipeline=CompositeValidationPipeline(
                        [RequiredFieldsValidator(), PHIDetectionValidator()]
                    ),
                )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))
    task = await start_worker(
        session_factory, process_job_fn=failing_process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING
        assert stored.retry_count == 1

        # Simulate the not-yet-implemented reclaim: retrying -> running is
        # already legal per ADR-0020's state machine.
        stored.status = JobStatus.RUNNING
        session.add(stored)
        await session.commit()

    async def succeeding_process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=MockExtractionPipeline(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    drained_2 = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained_2))
    task_2 = await start_worker(
        session_factory,
        process_job_fn=succeeding_process_job_fn,
        poll_interval_seconds=POLL_INTERVAL,
    )
    await asyncio.wait_for(drained_2.wait(), timeout=2)
    await stop_worker(task_2)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED
        assert stored.retry_count == 1  # unchanged by the successful attempt

    all_events_for_job = _sequence_for_job(collected_events, job.id)
    terminal_events = [
        e for e in all_events_for_job if e.event_type in _TERMINAL_EVENT_TO_JOB_STATUS
    ]
    assert [e.event_type for e in terminal_events] == [
        EventType.JOB_RETRYING,
        EventType.JOB_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_pipeline_failure_mid_stage_leaves_a_fully_consistent_failed_state(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    """ "Correct rollback state" for this system means: no partial/
    half-written state — document FAILED with a persisted failure record,
    job FAILED, exactly one JOB_FAILED event, all mutually consistent.
    Uses an OCR failure (always terminal per ADR-0023), the "mid-stage"
    case: the failure happens before validation ever runs.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    class _FailingOCR(ExtractionPipeline):
        def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
            from modules.ocr.base import ExtractionError

            raise ExtractionError("corrupted input bytes")

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=_FailingOCR(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))
    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    from sqlalchemy import select

    from modules.ingestion.models import Document
    from modules.ocr.models import ExtractionResult

    async with session_factory() as session:
        document = await session.get(Document, job.document_id)
        stored_job = await session.get(Job, job.id)
        assert document is not None and document.status == DocumentStatus.FAILED
        assert stored_job is not None and stored_job.status == JobStatus.FAILED

        extraction = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == job.document_id)
        )
        assert extraction is not None
        assert extraction.job_id == job.id
        assert extraction.raw_text.startswith("[EXTRACTION FAILED:")

    failed_events = [
        e
        for e in _sequence_for_job(collected_events, job.id)
        if e.event_type == EventType.JOB_FAILED
    ]
    assert len(failed_events) == 1
