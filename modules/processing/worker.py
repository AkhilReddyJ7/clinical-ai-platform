"""Worker execution loop: claims jobs and dispatches them (ADR-0021).

Orchestration only. The loop claims work via claim_next_job (Increment
2's SKIP LOCKED-backed atomic claim, unchanged here) and hands the
claimed job to process_job unchanged, then records the outcome via the
mark_job_* repository functions (Increment 4): completed on success,
retrying or failed on an exception, per the classification process_job
signals (modules/processing/errors.py) and ADR-0023's already-defined
transient/terminal split. This module does not decide *how* a job should
be classified beyond dispatching on that signal. It does enforce
ADR-0023's retry budget (a transient failure past job_max_retry_attempts
is failed, not retried) before calling mark_job_retry; backoff timing
itself (computing next_attempt_at, and reclaiming a retrying job once it
elapses) lives in repository.py, not here.

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
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeAlias

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.base import FieldExtractionPipeline
from modules.ingestion import service as ingestion_service
from modules.ingestion.models import DocumentStatus
from modules.ingestion.storage import LocalFileStorage, StorageBackend
from modules.ocr.base import ExtractionPipeline
from modules.ocr.tesseract import TesseractExtractionPipeline
from modules.processing.errors import is_retryable
from modules.processing.events import Event, EventType, emit_event
from modules.processing.models import Job, JobStatus
from modules.processing.observability.registry import register_default_subscribers
from modules.processing.pipeline import ProcessingResult, run_processing_pipeline
from modules.processing.repository import (
    claim_next_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_retry,
    reclaim_stale_job,
)
from modules.retrieval.base import EmbeddingPipeline, VectorStore
from modules.retrieval.chroma_store import ChromaVectorStore
from modules.retrieval.fastembed_embeddings import FastEmbedEmbeddingPipeline
from modules.retrieval.service import RetrievalService
from modules.validation.base import ValidationPipeline
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator
from shared.config.settings import get_settings
from shared.database.session import AsyncSessionLocal
from shared.logging.logger import logger

# Startup-context wiring: registers the metrics/logging subscribers
# exactly once (idempotent — pipeline.py makes the same call
# independently). Not inside emit_event, not inside modules.processing.events.
register_default_subscribers()

DEFAULT_POLL_INTERVAL_SECONDS = 1.0

# process_job_fn's return value is discarded by _dispatch below (only
# whether it raised matters to the worker loop) — Any is the correct,
# deliberate type here, not an oversight.
ProcessJobFn: TypeAlias = Callable[[Job], Any]


# Mirrors apps/api/dependencies.py's factory functions (same concrete
# classes, built from the same Settings) rather than importing them
# directly: modules/ may not depend on apps/ (ADR-0001's modular-monolith
# layering — apps/ composes modules/, never the reverse). The worker is
# its own composition root, exactly as the API app is its own.
@lru_cache
def _storage() -> StorageBackend:
    return LocalFileStorage(get_settings().storage_root)


@lru_cache
def _extraction_pipeline() -> ExtractionPipeline:
    settings = get_settings()
    return TesseractExtractionPipeline(
        max_pdf_pages=settings.max_pdf_pages,
        preprocessing_enabled=settings.ocr_preprocessing_enabled,
        psm=settings.ocr_psm,
    )


@lru_cache
def _field_extraction_pipeline() -> FieldExtractionPipeline:
    settings = get_settings()
    return AnthropicFieldExtractionPipeline(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_input_chars=settings.anthropic_max_input_chars,
    )


@lru_cache
def _phi_validator() -> ValidationPipeline:
    return PHIDetectionValidator()


@lru_cache
def _validation_pipeline() -> ValidationPipeline:
    return CompositeValidationPipeline([RequiredFieldsValidator(), PHIDetectionValidator()])


@lru_cache
def _embedding_pipeline() -> EmbeddingPipeline:
    settings = get_settings()
    return FastEmbedEmbeddingPipeline(
        model_name=settings.embedding_model_name,
        cache_dir=settings.embedding_model_cache_dir,
    )


@lru_cache
def _vector_store() -> VectorStore:
    settings = get_settings()
    return ChromaVectorStore(
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=settings.chroma_collection_name,
    )


@lru_cache
def _retrieval_service() -> RetrievalService:
    settings = get_settings()
    return RetrievalService(
        embedding_pipeline=_embedding_pipeline(),
        vector_store=_vector_store(),
        chunk_size_chars=settings.retrieval_chunk_size_chars,
        overlap_chars=settings.retrieval_chunk_overlap_chars,
    )


async def process_job(job: Job) -> ProcessingResult:
    """Default processing boundary: runs the real OCR/PHI/extraction pipeline.

    A thin wiring layer only — modules/processing/pipeline.py holds the
    actual domain logic (Increment 5). Opens its own session, independent
    of whatever session claimed the job, matching claim_next_job's own
    fresh-session-per-call pattern.
    """
    async with AsyncSessionLocal() as db:
        return await run_processing_pipeline(
            job,
            db=db,
            storage=_storage(),
            extraction_pipeline=_extraction_pipeline(),
            field_extraction_pipeline=_field_extraction_pipeline(),
            phi_validator=_phi_validator(),
            validation_pipeline=_validation_pipeline(),
            retrieval_service=_retrieval_service(),
        )


async def _dispatch(job: Job, process_job_fn: ProcessJobFn) -> None:
    result = process_job_fn(job)
    if result is not None:
        await result


async def _finalize_document_as_failed(
    session_factory: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> None:
    """Cascades a job's terminal `failed` outcome to its document (ADR-0020).

    pipeline.py's own terminal-classified failure paths already move the
    document to `failed` themselves (via _persist_failure) before raising
    -- but two paths a job can reach `failed` through never go through
    pipeline.py at all: ADR-0023's retry budget exhausting (decided here,
    in run_worker_loop) and ADR-0024's stale-job reclaim exhausting
    (decided in repository.reclaim_stale_job). Both leave a job correctly
    `failed` but, without this, would leave its document stuck in
    `processing`/`extracted` forever -- exactly the state ADR-0020 warns
    a worker must not produce. Guarding on `processing`/`extracted` (the
    only states `failed` is reachable from) makes this a harmless no-op
    when pipeline.py already did it, and closes the gap when it didn't.
    """
    try:
        async with session_factory() as session:
            document = await ingestion_service.get_document(session, document_id)
            if document is not None and document.status in (
                DocumentStatus.PROCESSING,
                DocumentStatus.EXTRACTED,
            ):
                await ingestion_service.update_status(session, document, DocumentStatus.FAILED)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("worker: error while finalizing document id=%s as failed", document_id)


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

    Whenever an iteration finds no claimable work, it also takes one
    ADR-0024 stale-job detection scan (reclaim_stale_job) before sleeping
    — the polling responsibility ADR-0024 assigns to this loop, on this
    same cadence, rather than a separate sweeper process. It runs only on
    an otherwise-idle iteration, not on every iteration: a worker that
    just claimed real work is about to block on it for a while regardless
    of when the next scan happens, and with multiple worker processes
    (this loop's normal deployment shape) an idle iteration on any one of
    them comes around often enough for this to not matter in practice.
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
            reclaimed: Job | None = None
            try:
                async with session_factory() as session:
                    reclaimed = await reclaim_stale_job(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker: error while reclaiming stale jobs")

            if reclaimed is not None:
                if reclaimed.status == JobStatus.FAILED:
                    await _finalize_document_as_failed(session_factory, reclaimed.document_id)
                emit_event(
                    Event(
                        event_type=EventType.JOB_STALE_SKIPPED,
                        job_id=str(reclaimed.id),
                        document_id=str(reclaimed.document_id),
                        metadata={
                            "outcome_status": reclaimed.status.value,
                            "retry_count": reclaimed.retry_count,
                        },
                    )
                )
            await asyncio.sleep(poll_interval_seconds)
            continue

        emit_event(
            Event(
                event_type=EventType.JOB_CLAIMED,
                job_id=str(job.id),
                document_id=str(job.document_id),
                metadata={"status": job.status.value},
            )
        )
        claim_started_at = time.monotonic()

        outcome: Job | None
        duration: float
        job_newly_failed = False
        try:
            await _dispatch(job, process_job_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("worker: error while processing job id=%s", job.id)
            duration = time.monotonic() - claim_started_at
            async with session_factory() as session:
                budget_remains = job.retry_count < get_settings().job_max_retry_attempts
                if is_retryable(exc) and budget_remains:
                    outcome = await mark_job_retry(session, job.id)
                    # Events mirror state transitions, they don't define
                    # them (Increment 12): outcome is None precisely when
                    # the conditional UPDATE (ADR-0024's fencing) affected
                    # zero rows — no DB transition actually happened, so
                    # no event may claim one did.
                    if outcome is not None:
                        emit_event(
                            Event(
                                event_type=EventType.JOB_RETRYING,
                                job_id=str(job.id),
                                document_id=str(job.document_id),
                                metadata={
                                    "duration_ms": duration * 1000,
                                    "error_type": type(exc).__name__,
                                    "error": str(exc),
                                },
                            )
                        )
                else:
                    outcome = await mark_job_failed(session, job.id, str(exc))
                    job_newly_failed = outcome is not None
                    if outcome is not None:
                        emit_event(
                            Event(
                                event_type=EventType.JOB_FAILED,
                                job_id=str(job.id),
                                document_id=str(job.document_id),
                                metadata={
                                    "duration_ms": duration * 1000,
                                    "error_type": type(exc).__name__,
                                    "error": str(exc),
                                },
                            )
                        )
        else:
            duration = time.monotonic() - claim_started_at
            async with session_factory() as session:
                outcome = await mark_job_completed(session, job.id)
            if outcome is not None:
                emit_event(
                    Event(
                        event_type=EventType.JOB_COMPLETED,
                        job_id=str(job.id),
                        document_id=str(job.document_id),
                        metadata={"duration_ms": duration * 1000},
                    )
                )

        if outcome is None:
            logger.warning(
                "worker: outcome write skipped for job id=%s (no longer running)", job.id
            )

        # A terminal failure decided here (budget exhausted, or any
        # exception is_retryable() doesn't classify as transient) never
        # runs through pipeline.py's own _persist_failure -- only this
        # loop knows the job itself is done, so only it can cascade the
        # document to `failed` for these specific paths (see
        # _finalize_document_as_failed's docstring).
        if job_newly_failed:
            await _finalize_document_as_failed(session_factory, job.document_id)


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
