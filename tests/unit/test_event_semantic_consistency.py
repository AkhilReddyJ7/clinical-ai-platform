"""Increment 11: audits EventType usage for consistent meaning across the
codebase, and validates that a single job attempt's real emitted event
sequence always matches the documented lifecycle contract.

Semantic contract (restated here, not enforced at runtime — this
increment adds no dispatch logic, per its own "no behavioral changes"
scope):

    JOB_CLAIMED    = job successfully reserved for processing (worker.py,
                      queue-level)
    JOB_STARTED    = processing actually began (pipeline.py, domain-level)
    JOB_COMPLETED  = full successful pipeline completion (worker.py, after
                      process_job_fn returns without raising)
    JOB_FAILED     = terminal failure after processing begins (worker.py,
                      is_retryable(exc) is False)
    JOB_RETRYING   = transient failure with retry scheduled (worker.py,
                      is_retryable(exc) is True)
    JOB_STALE_SKIPPED = a `running` job was detected stale and recovered
                      (worker.py's ADR-0024 detection scan, queue-level —
                      distinct from JOB_RETRYING/JOB_FAILED even when the
                      underlying transition is the same edge, because the
                      *reason* differs: crash recovery, not an outcome
                      this worker's own attempt reached)

validate_event_sequence() below is a test-only diagnostic (explicitly
not runtime enforcement, per this increment's section 5) that flags
sequences violating this contract. It is exercised two ways: against
hand-built synthetic sequences (proving violations are detectable at
all), and against the *actual* event sequence captured from a real
worker+pipeline run for each of the three attempt outcomes (proving the
real system already satisfies the contract it claims to).
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
from modules.ocr.mock import MockExtractionPipeline
from modules.processing.events import Event, EventType, subscribe, unsubscribe
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import run_processing_pipeline
from modules.processing.worker import start_worker, stop_worker
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
POLL_INTERVAL = 0.01

_TERMINAL_TYPES = (EventType.JOB_COMPLETED, EventType.JOB_FAILED, EventType.JOB_RETRYING)


def validate_event_sequence(events: list[EventType]) -> list[str]:
    """Flags violations of the job-attempt lifecycle contract in an
    ordered list of EventTypes for a single attempt. Returns an empty
    list iff the sequence is fully consistent. Test-only: no production
    code calls this.
    """
    violations: list[str] = []

    if events and events[0] != EventType.JOB_CLAIMED and EventType.JOB_CLAIMED in events:
        violations.append("JOB_CLAIMED did not occur first")

    claimed_count = events.count(EventType.JOB_CLAIMED)
    if claimed_count > 1:
        violations.append(f"JOB_CLAIMED occurred {claimed_count} times in one attempt")

    started_count = events.count(EventType.JOB_STARTED)
    if started_count > 1:
        violations.append(f"JOB_STARTED occurred {started_count} times in one attempt")

    terminal_events = [e for e in events if e in _TERMINAL_TYPES]
    if len(terminal_events) > 1:
        violations.append(
            f"more than one terminal outcome in a single attempt: {[e.value for e in terminal_events]}"
        )

    if EventType.JOB_CLAIMED in events and EventType.JOB_STARTED in events:
        if events.index(EventType.JOB_STARTED) < events.index(EventType.JOB_CLAIMED):
            violations.append("JOB_STARTED occurred before JOB_CLAIMED")

    for terminal in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
        if terminal not in events:
            continue
        if EventType.JOB_STARTED not in events:
            violations.append(f"{terminal.value} occurred without a preceding JOB_STARTED")
        elif events.index(terminal) < events.index(EventType.JOB_STARTED):
            violations.append(f"{terminal.value} occurred before JOB_STARTED")

    stage_indexes = [
        i
        for i, e in enumerate(events)
        if e in (EventType.PIPELINE_STAGE_STARTED, EventType.PIPELINE_STAGE_COMPLETED)
    ]
    if stage_indexes and EventType.JOB_STARTED in events:
        started_index = events.index(EventType.JOB_STARTED)
        if any(i < started_index for i in stage_indexes):
            violations.append("a PIPELINE_STAGE_* event occurred before JOB_STARTED")
    if stage_indexes and terminal_events:
        terminal_index = events.index(terminal_events[0])
        if any(i > terminal_index for i in stage_indexes):
            violations.append("a PIPELINE_STAGE_* event occurred after the terminal event")

    return violations


# --- Synthetic sequences: proving violations are detectable at all ------


def test_a_fully_valid_sequence_has_no_violations() -> None:
    sequence = [
        EventType.JOB_CLAIMED,
        EventType.JOB_STARTED,
        EventType.PIPELINE_STAGE_STARTED,
        EventType.PIPELINE_STAGE_COMPLETED,
        EventType.JOB_COMPLETED,
    ]
    assert validate_event_sequence(sequence) == []


def test_a_valid_sequence_with_no_stages_has_no_violations() -> None:
    assert validate_event_sequence([EventType.JOB_CLAIMED, EventType.JOB_STARTED]) == []


def test_missing_job_started_before_job_completed_is_flagged() -> None:
    violations = validate_event_sequence([EventType.JOB_CLAIMED, EventType.JOB_COMPLETED])
    assert any("job_completed" in v and "JOB_STARTED" in v for v in violations)


def test_missing_job_started_before_job_failed_is_flagged() -> None:
    violations = validate_event_sequence([EventType.JOB_CLAIMED, EventType.JOB_FAILED])
    assert any("job_failed" in v and "JOB_STARTED" in v for v in violations)


def test_retry_event_alongside_a_terminal_failure_in_the_same_attempt_is_flagged() -> None:
    # "retry events never appear after terminal failure in same flow"
    violations = validate_event_sequence(
        [
            EventType.JOB_CLAIMED,
            EventType.JOB_STARTED,
            EventType.JOB_FAILED,
            EventType.JOB_RETRYING,
        ]
    )
    assert any("more than one terminal outcome" in v for v in violations)


def test_job_claimed_twice_in_one_attempt_is_flagged() -> None:
    violations = validate_event_sequence(
        [EventType.JOB_CLAIMED, EventType.JOB_STARTED, EventType.JOB_CLAIMED]
    )
    assert any("JOB_CLAIMED occurred 2 times" in v for v in violations)


def test_job_started_twice_without_a_new_claim_is_flagged() -> None:
    violations = validate_event_sequence(
        [EventType.JOB_CLAIMED, EventType.JOB_STARTED, EventType.JOB_STARTED]
    )
    assert any("JOB_STARTED occurred 2 times" in v for v in violations)


def test_job_completed_and_job_failed_together_is_flagged() -> None:
    violations = validate_event_sequence(
        [
            EventType.JOB_CLAIMED,
            EventType.JOB_STARTED,
            EventType.JOB_COMPLETED,
            EventType.JOB_FAILED,
        ]
    )
    assert any("more than one terminal outcome" in v for v in violations)


def test_stage_event_after_the_terminal_event_is_flagged() -> None:
    violations = validate_event_sequence(
        [
            EventType.JOB_CLAIMED,
            EventType.JOB_STARTED,
            EventType.JOB_COMPLETED,
            EventType.PIPELINE_STAGE_STARTED,
        ]
    )
    assert any("after the terminal event" in v for v in violations)


def test_stage_event_before_job_started_is_flagged() -> None:
    violations = validate_event_sequence(
        [EventType.JOB_CLAIMED, EventType.PIPELINE_STAGE_STARTED, EventType.JOB_STARTED]
    )
    assert any("before JOB_STARTED" in v for v in violations)


# --- Real system: capturing an actual attempt's sequence -----------------


@pytest.fixture
def collected_events() -> Iterator[list[Event]]:
    events: list[Event] = []
    subscribe(events.append)
    yield events
    unsubscribe(events.append)


async def _make_document_and_job(
    session_factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage
) -> Job:
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, b"a clinical note with some content")

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type="text/plain",
            size_bytes=10,
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


def _sequence_for_job(collected_events: list[Event], job_id: uuid.UUID) -> list[EventType]:
    return [e.event_type for e in collected_events if e.job_id == str(job_id)]


@pytest.mark.asyncio
async def test_real_successful_attempt_matches_the_lifecycle_contract(
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
    assert sequence[0] == EventType.JOB_CLAIMED
    assert EventType.JOB_STARTED in sequence
    assert sequence[-1] == EventType.JOB_COMPLETED
    assert validate_event_sequence(sequence) == []


@pytest.mark.asyncio
async def test_real_transient_failure_attempt_matches_the_lifecycle_contract(
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
    assert sequence[0] == EventType.JOB_CLAIMED
    assert EventType.JOB_STARTED in sequence
    assert sequence[-1] == EventType.JOB_RETRYING
    assert EventType.JOB_COMPLETED not in sequence
    assert EventType.JOB_FAILED not in sequence
    assert validate_event_sequence(sequence) == []


@pytest.mark.asyncio
async def test_real_terminal_failure_attempt_matches_the_lifecycle_contract(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    # No API key configured -> a terminal FieldExtractionError.
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
    assert sequence[0] == EventType.JOB_CLAIMED
    assert EventType.JOB_STARTED in sequence
    assert sequence[-1] == EventType.JOB_FAILED
    assert EventType.JOB_COMPLETED not in sequence
    assert EventType.JOB_RETRYING not in sequence
    assert validate_event_sequence(sequence) == []


@pytest.mark.asyncio
async def test_real_phi_detected_attempt_completes_the_job_not_fails_it(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_events: list[Event],
) -> None:
    """PHI-detected halts the pipeline early, but per ADR-0023 that's a
    *completed* job (the gate did its job correctly), not a failed one —
    this is the one place a naive reading of "JOB_FAILED = pipeline didn't
    reach validation" could be mistakenly applied. Confirms the real
    system gets this right at the event level too.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, b"ssn 123-45-6789")

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()
        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()
        await session.refresh(job)

    from modules.ocr.base import ExtractionOutput, ExtractionPipeline

    class _FakeOCR(ExtractionPipeline):
        def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
            return ExtractionOutput(raw_text="patient ssn 123-45-6789 needs follow-up")

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=_FakeOCR(),
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
    assert sequence[-1] == EventType.JOB_COMPLETED
    assert EventType.JOB_FAILED not in sequence
    assert validate_event_sequence(sequence) == []
