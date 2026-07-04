"""Worker execution loop: claims jobs and dispatches them (ADR-0021).

Orchestration only. The loop claims work via claim_next_job (Increment
2's SKIP LOCKED-backed atomic claim, unchanged here) and hands the
claimed job to process_job unchanged, then records the outcome via the
mark_job_* repository functions (Increment 4): completed on success,
retrying or failed on an exception, per the classification process_job
signals (modules/processing/errors.py) and ADR-0023's already-defined
transient/terminal split. This module does not decide *how* a job should
be classified beyond dispatching on that signal, and it does not
implement retry scheduling (backoff timing, budget limits) — only the
state update for whichever outcome already occurred.

Safe to run as multiple concurrent instances (processes, containers, or
asyncio tasks): claim_next_job's SKIP LOCKED semantics guarantee no two
callers ever claim the same job, so this loop needs no coordination of
its own to scale horizontally. The outcome writes are similarly safe
under concurrent/stale-claim scenarios (ADR-0024) — mark_job_* silently
no-ops if the job is no longer running by the time the outcome is
recorded.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.processing.errors import is_retryable
from modules.processing.models import Job
from modules.processing.repository import (
    claim_next_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_retry,
)
from shared.logging.logger import logger

DEFAULT_POLL_INTERVAL_SECONDS = 1.0

ProcessJobFn: TypeAlias = Callable[[Job], "None | Awaitable[None]"]


async def process_job(job: Job) -> None:
    """Processing boundary stub. Deliberately does nothing but log.

    Real OCR / PHI-gate / field-extraction dispatch is out of scope for
    this increment — a future increment replaces this stub, not the loop
    that calls it.
    """
    logger.info("worker: claimed job id=%s document_id=%s", job.id, job.document_id)


async def _dispatch(job: Job, process_job_fn: ProcessJobFn) -> None:
    result = process_job_fn(job)
    if result is not None:
        await result


async def run_worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    process_job_fn: ProcessJobFn = process_job,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Claim and dispatch jobs forever, until this task is cancelled.

    Every iteration claims at most one job through a fresh session (so a
    long-running process_job_fn call never holds a claim's transaction
    open). An empty queue or a transient database error both fall through
    to the same poll-interval sleep, so the loop never busy-spins.
    """
    while True:
        job: Job | None = None
        try:
            async with session_factory() as session:
                job = await claim_next_job(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker: error while claiming next job")

        if job is None:
            await asyncio.sleep(poll_interval_seconds)
            continue

        outcome: Job | None
        try:
            await _dispatch(job, process_job_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("worker: error while processing job id=%s", job.id)
            async with session_factory() as session:
                if is_retryable(exc):
                    outcome = await mark_job_retry(session, job.id)
                else:
                    outcome = await mark_job_failed(session, job.id, str(exc))
        else:
            async with session_factory() as session:
                outcome = await mark_job_completed(session, job.id)

        if outcome is None:
            logger.warning(
                "worker: outcome write skipped for job id=%s (no longer running)", job.id
            )


async def start_worker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    process_job_fn: ProcessJobFn = process_job,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Start the worker loop as a background task and return it.

    The returned Task is the cancellation token — pass it to stop_worker
    to shut the loop down cleanly.
    """
    return asyncio.create_task(
        run_worker_loop(
            session_factory,
            process_job_fn=process_job_fn,
            poll_interval_seconds=poll_interval_seconds,
        )
    )


async def stop_worker(task: asyncio.Task[None]) -> None:
    """Cancel a worker task started by start_worker and wait for clean exit."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
