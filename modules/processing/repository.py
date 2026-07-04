"""Job-queue repository: atomic claiming and outcome writes (ADR-0021).

Claiming (``claim_next_job``) uses ``SELECT ... FOR UPDATE SKIP LOCKED``
so that concurrent workers polling the same table each claim a distinct
job rather than racing over the same row — the atomicity guarantee
ADR-0021 requires from the Postgres-backed queue. On SQLite (the fast
test suite's engine, per ADR-0004) this clause compiles away to nothing;
correctness under real concurrent claims is exercised against Postgres
directly (see tests/integration).

The outcome functions (``mark_job_completed``, ``mark_job_failed``,
``mark_job_retry``) are the symmetric write on the way out: each is a
conditional ``UPDATE ... WHERE status = 'running'``, per ADR-0024
section 5 — a worker whose job was reclaimed out from under it (stale-job
recovery, ADR-0024) affects zero rows and gets None back rather than
clobbering a state some other writer already moved past. These functions
perform only that guarded state update; which outcome to call, and why,
is the caller's decision (the worker loop), not this module's.

``reclaim_stale_job`` is ADR-0024's detection-and-recovery half: it finds
a `running` job nothing has touched recently and routes it through
``mark_job_retry``/``mark_job_failed`` itself (unlike the outcome
functions above, this one *does* decide which to call — staleness isn't
an execution-reported outcome, there's no caller-side classification to
defer to).

``enqueue_job`` is the way *in*, symmetric with claiming as the way out:
it creates a job and moves the document to `processing` atomically (a
locked read of the document row, per ADR-0022), closing the same
concurrent-double-submission race claim_next_job's SKIP LOCKED closes on
the claim side — two callers racing to enqueue against the same document
must not both succeed.
"""

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus
from modules.processing.state_machine import validate_document_transition, validate_job_transition
from shared.config.settings import Settings, get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _compute_backoff_seconds(retry_count: int, settings: Settings) -> float:
    """Exponential backoff with jitter, capped (ADR-0023 section 3).

    retry_count is how many retries this job has already consumed before
    the one being scheduled now (0 for the first retry), so the delay
    grows with each subsequent attempt rather than starting at the
    multiplier on the very first one.
    """
    delay = settings.job_retry_backoff_initial_seconds * (
        settings.job_retry_backoff_multiplier**retry_count
    )
    delay = min(delay, settings.job_retry_backoff_max_seconds)
    return delay + random.uniform(0, settings.job_retry_backoff_jitter_seconds)


async def _claim_queued_job(db: AsyncSession) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    validate_job_transition(job.status, JobStatus.RUNNING)
    job.status = JobStatus.RUNNING
    await db.commit()
    await db.refresh(job)
    return job


async def _claim_ready_retrying_job(db: AsyncSession) -> Job | None:
    """Reclaim a job whose backoff delay (ADR-0023 section 3) has elapsed.

    Same SKIP LOCKED atomicity as _claim_queued_job, against the
    ``retrying -> running`` edge ADR-0020 already legalizes.
    """
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.RETRYING, Job.next_attempt_at <= _utcnow())
        .order_by(Job.next_attempt_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    validate_job_transition(job.status, JobStatus.RUNNING)
    job.status = JobStatus.RUNNING
    job.next_attempt_at = None
    await db.commit()
    await db.refresh(job)
    return job


async def claim_next_job(db: AsyncSession) -> Job | None:
    """Atomically claim the next runnable job, transitioning it to running.

    Prefers a never-started `queued` job over a `retrying` job whose
    backoff has elapsed, if both are available — a queued job has been
    waiting since submission with no attempt yet, while a retrying job's
    wait is exactly the backoff delay it was already given. This ordering
    is an engineering default, not mandated by ADR-0020/0023.

    Returns None if nothing is claimable right now (queue empty and no
    retrying job's backoff has elapsed yet, or every candidate is already
    locked by another concurrent claimant). Never blocks waiting for a
    locked row — SKIP LOCKED means a locked candidate is simply passed
    over in favor of the next unlocked one, or None if none remain.
    """
    job = await _claim_queued_job(db)
    if job is not None:
        return job
    return await _claim_ready_retrying_job(db)


async def _transition_from_running(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    to_status: JobStatus,
    last_error: str | None = None,
    increment_retry_count: bool = False,
    next_attempt_at: datetime | None = None,
) -> Job | None:
    """Conditionally move a job out of ``running``, per ADR-0020/ADR-0024.

    Returns None (no-op, nothing raised) if the job is no longer
    ``running`` by the time this executes — it was already reclaimed or
    its outcome already recorded by another writer, and per ADR-0024
    section 5, that result is simply discarded, not retried or errored.
    """
    validate_job_transition(JobStatus.RUNNING, to_status)

    values: dict[str, object] = {"status": to_status}
    if last_error is not None:
        values["last_error"] = last_error
    if increment_retry_count:
        values["retry_count"] = Job.retry_count + 1
    if next_attempt_at is not None:
        values["next_attempt_at"] = next_attempt_at

    stmt = update(Job).where(Job.id == job_id, Job.status == JobStatus.RUNNING).values(**values)
    result = cast("CursorResult[object]", await db.execute(stmt))
    await db.commit()

    if result.rowcount == 0:
        return None
    return await db.get(Job, job_id, populate_existing=True)


async def mark_job_completed(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Record that a job's execution finished successfully (running -> completed)."""
    return await _transition_from_running(db, job_id, to_status=JobStatus.COMPLETED)


async def mark_job_failed(db: AsyncSession, job_id: uuid.UUID, error: str) -> Job | None:
    """Record a terminal failure (running -> failed), per ADR-0023."""
    return await _transition_from_running(db, job_id, to_status=JobStatus.FAILED, last_error=error)


async def mark_job_retry(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Record a transient failure eligible for retry (running -> retrying), per ADR-0023.

    Increments retry_count and schedules next_attempt_at using exponential
    backoff with jitter, keyed off the job's retry_count *before* this
    call (i.e. this is the Nth retry). Whether a retry should happen at
    all (budget check) is the caller's decision (worker.py) made before
    calling this — by the time this runs, a retry has already been
    decided; this function only records it and schedules the backoff.
    """
    current = await db.get(Job, job_id)
    if current is None:
        return None
    delay = _compute_backoff_seconds(current.retry_count, get_settings())
    return await _transition_from_running(
        db,
        job_id,
        to_status=JobStatus.RETRYING,
        increment_retry_count=True,
        next_attempt_at=_utcnow() + timedelta(seconds=delay),
    )


async def reclaim_stale_job(db: AsyncSession) -> Job | None:
    """Detect and recover one stale `running` job, per ADR-0024.

    A job is stale if it's `running` and Job.updated_at (the sole liveness
    signal — no heartbeat column, per ADR-0024 section 1) is older than
    job_stale_timeout_seconds: its worker is presumed dead (crash, OOM
    kill, host failure), not merely slow. Recovery reuses the exact
    retry-budget-aware path ADR-0023 already defines for an ordinary
    transient failure — running -> retrying if budget remains, running ->
    failed if it's already exhausted (ADR-0024 section 4) — because a
    stale claim is a recovery condition on an existing state, not a new
    failure classification (ADR-0024 explicitly does not extend ADR-0023's
    taxonomy). A reclaimed-to-retrying job re-enters the same
    retrying -> running backoff path claim_next_job already implements
    (ADR-0023) — no separate resume mechanism.

    Same SKIP LOCKED atomicity as claim_next_job: a stale job already
    being reclaimed by another worker's concurrent scan is simply skipped
    in favor of the next stale candidate, or None.
    """
    settings = get_settings()
    cutoff = _utcnow() - timedelta(seconds=settings.job_stale_timeout_seconds)
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.RUNNING, Job.updated_at < cutoff)
        .order_by(Job.updated_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    if job.retry_count < settings.job_max_retry_attempts:
        return await mark_job_retry(db, job.id)
    return await mark_job_failed(
        db, job.id, "stale job: exceeded job_stale_timeout_seconds without completing"
    )


async def enqueue_job(db: AsyncSession, document_id: uuid.UUID) -> Job | None:
    """Create a new job for a document and move it to `processing`
    (ADR-0020, ADR-0022's `POST /process`).

    Returns None if the document doesn't exist -- the caller (the
    /process route) maps that to 404. Raises IllegalTransitionError
    (state_machine.py) if the document exists but isn't currently in a
    legal starting state (`uploaded` or `failed`) -- the caller maps that
    to 409. A document with an active job is always `processing` by
    construction once this function is the only path that ever creates a
    job (ADR-0020's "at most one non-terminal job per document"), so the
    document-status check alone is sufficient; no separate Job-table
    query is needed here.

    Locks the document row (SELECT ... FOR UPDATE) for the duration of
    the check-and-create, closing the same race claim_next_job's SKIP
    LOCKED closes on the claim side: two concurrent POST /process calls
    for the same document must not both succeed. Unlike claim_next_job,
    this does not skip locked rows -- a second caller should wait for the
    first's transaction to finish and then see its updated status, not
    silently miss a document that's mid-check.
    """
    stmt = select(Document).where(Document.id == document_id).with_for_update()
    document = (await db.execute(stmt)).scalar_one_or_none()
    if document is None:
        return None

    validate_document_transition(document.status, DocumentStatus.PROCESSING)

    job = Job(document_id=document.id, status=JobStatus.QUEUED)
    db.add(job)
    document.status = DocumentStatus.PROCESSING
    await db.commit()
    await db.refresh(job)
    return job
