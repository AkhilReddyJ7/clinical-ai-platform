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
"""

import uuid
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from modules.processing.models import Job, JobStatus
from modules.processing.state_machine import validate_job_transition


async def claim_next_job(db: AsyncSession) -> Job | None:
    """Atomically claim the oldest queued job, transitioning it to running.

    Returns None if no job is currently queued (or every queued job is
    already locked by another concurrent claimant). Never blocks waiting
    for a locked row — SKIP LOCKED means a locked candidate is simply
    passed over in favor of the next unlocked one, or None if none remain.
    """
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


async def _transition_from_running(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    to_status: JobStatus,
    last_error: str | None = None,
    increment_retry_count: bool = False,
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

    Increments retry_count; does not check it against a budget or decide
    backoff timing — that policy decision belongs to a future increment,
    not this state update.
    """
    return await _transition_from_running(
        db, job_id, to_status=JobStatus.RETRYING, increment_retry_count=True
    )
