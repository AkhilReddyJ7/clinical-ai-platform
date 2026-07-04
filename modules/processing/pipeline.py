"""Real document-processing domain logic for a claimed job (Increment 5).

This calls the *existing* Sprint 2 pipeline stages (OCR -> PHI gate ->
field extraction -> validation) unchanged, per the Sprint 3 baseline:
"the worker must call the same OCR -> PHI-gate -> field-extraction logic
Sprint 2 already built... that logic does not change or move." This
module only decides how a claimed Job drives that existing logic and
persists its output — it does not reimplement OCR, extraction, or
validation, and it does not touch the queue, retry, or stale-job-recovery
mechanics (ADR-0021/0023/0024), which live entirely in repository.py and
worker.py and are untouched here.

Failure classification follows ADR-0023 exactly:
- An OCR ExtractionError is always terminal (a property of the input
  bytes, not of network conditions).
- The PHI gate correctly halting the call, and RequiredFieldsValidator
  correctly reporting a missing field, are both *not failures* — the job
  completes; only the document's outcome is `failed`.
- A FieldExtractionError is classified by inspecting the chained
  `__cause__` the Anthropic SDK call raised it from (RateLimitError /
  APIConnectionError / a 5xx APIStatusError -> transient; anything else,
  including a missing API key or a malformed response -> terminal) —
  without touching modules/extraction/anthropic_extractor.py itself.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic
from starlette.concurrency import run_in_threadpool

from modules.extraction.base import FieldExtractionError, FieldExtractionPipeline
from modules.ingestion import service as ingestion_service
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import StorageBackend
from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.models import ExtractionResult
from modules.processing.errors import TerminalProcessingError, TransientProcessingError
from modules.processing.models import Job
from modules.validation.base import ValidationPipeline
from modules.validation.models import ValidationResult
from sqlalchemy.ext.asyncio import AsyncSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProcessingResult:
    """Summary of one job's completed pipeline execution.

    Returned by run_processing_pipeline / process_job — not persisted
    directly by the worker. Persistence of the same data happens as a
    side effect of the pipeline itself (see _persist_outcome /
    _persist_failure below); this is a return value for the caller
    (logging, future observability), not a second write path.
    """

    job_id: uuid.UUID
    document_id: uuid.UUID
    raw_text: str
    fields: dict[str, str]
    confidence: float
    is_valid: bool
    issues: list[str]
    completed_at: datetime = field(default_factory=_utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


def _is_transient_field_extraction_error(exc: FieldExtractionError) -> bool:
    """ADR-0023 section 1's classification, read off the chained cause."""
    cause = exc.__cause__
    if isinstance(cause, anthropic.RateLimitError):
        return True
    if isinstance(cause, anthropic.APIConnectionError):
        return True
    if isinstance(cause, anthropic.APIStatusError):
        return cause.status_code >= 500
    return False


async def _persist_failure(
    db: AsyncSession,
    document: Document,
    job: Job,
    *,
    raw_text: str,
    issues: list[str],
    confidence: float = 0.0,
) -> None:
    """Write a failure ExtractionResult/ValidationResult and fail the document.

    Only for outcomes that are actually finished (terminal failures, and
    the PHI-gate/validation "not a failure" cases) — never called for a
    transient failure, which leaves the document `processing` and writes
    nothing, since ADR-0023's retry re-runs the whole job from scratch.
    """
    extraction = ExtractionResult(
        document_id=document.id,
        job_id=job.id,
        raw_text=raw_text,
        fields={},
        confidence=confidence,
    )
    db.add(extraction)
    validation = ValidationResult(
        document_id=document.id,
        job_id=job.id,
        is_valid=False,
        issues=issues,
    )
    db.add(validation)
    await db.commit()
    await ingestion_service.update_status(db, document, DocumentStatus.FAILED)


async def run_processing_pipeline(
    job: Job,
    *,
    db: AsyncSession,
    storage: StorageBackend,
    extraction_pipeline: ExtractionPipeline,
    field_extraction_pipeline: FieldExtractionPipeline,
    phi_validator: ValidationPipeline,
    validation_pipeline: ValidationPipeline,
) -> ProcessingResult:
    """Run OCR -> PHI gate -> field extraction -> validation for a claimed job.

    Raises TransientProcessingError / TerminalProcessingError for outcomes
    the existing worker retry logic (Increment 4, ADR-0023) already knows
    how to handle; returns normally (job completes) for every outcome
    ADR-0023 classifies as "not a failure," including a document that
    ends up `failed` (PHI detected, or a missing required field) — a
    failed *document* is still a completed *job*.
    """
    document = await ingestion_service.get_document(db, job.document_id)
    if document is None:
        # Not reachable in normal operation (documents are never deleted);
        # nothing to retry against, and nothing to attach a failure record
        # to, so this is unconditionally terminal.
        raise TerminalProcessingError(f"document {job.document_id} not found")

    await ingestion_service.update_status(db, document, DocumentStatus.PROCESSING)

    data = await run_in_threadpool(storage.read, document.storage_key)

    try:
        extraction_output = await run_in_threadpool(
            extraction_pipeline.extract, data=data, content_type=document.content_type
        )
    except ExtractionError as exc:
        await _persist_failure(
            db,
            document,
            job,
            raw_text=f"[EXTRACTION FAILED: {exc}]",
            issues=[f"extraction failed: {exc}"],
        )
        raise TerminalProcessingError(str(exc)) from exc

    # PHI-check the raw OCR text before the field-extraction LLM ever sees
    # it (ADR-0011, ADR-0019) — bare PHIDetectionValidator, since fields
    # don't exist yet at this point.
    phi_precheck = phi_validator.validate(ExtractionOutput(raw_text=extraction_output.raw_text))
    if not phi_precheck.is_valid:
        redacted_raw_text = (
            f"[REDACTED: PHI detected in {len(extraction_output.raw_text)} "
            "characters of extracted text; not persisted]"
        )
        await _persist_failure(
            db,
            document,
            job,
            raw_text=redacted_raw_text,
            issues=phi_precheck.issues,
            confidence=extraction_output.confidence,
        )
        # Not a failure per ADR-0023: the PHI gate did exactly its job.
        return ProcessingResult(
            job_id=job.id,
            document_id=document.id,
            raw_text=redacted_raw_text,
            fields={},
            confidence=extraction_output.confidence,
            is_valid=False,
            issues=phi_precheck.issues,
            metadata={"outcome": "phi_detected"},
        )

    try:
        field_output = await run_in_threadpool(
            field_extraction_pipeline.extract_fields, raw_text=extraction_output.raw_text
        )
    except FieldExtractionError as exc:
        if _is_transient_field_extraction_error(exc):
            # Document stays `processing` (ADR-0020) and nothing is
            # persisted — a retry re-runs this whole function from
            # scratch (ADR-0023 section 2).
            raise TransientProcessingError(str(exc)) from exc

        await _persist_failure(
            db,
            document,
            job,
            raw_text=extraction_output.raw_text,
            issues=[f"field extraction failed: {exc}"],
        )
        raise TerminalProcessingError(str(exc)) from exc

    combined_output = ExtractionOutput(
        raw_text=extraction_output.raw_text,
        fields=field_output.fields,
        confidence=(extraction_output.confidence + field_output.confidence) / 2,
    )
    validation_output = validation_pipeline.validate(combined_output)

    extraction = ExtractionResult(
        document_id=document.id,
        job_id=job.id,
        raw_text=combined_output.raw_text,
        fields=combined_output.fields,
        confidence=combined_output.confidence,
    )
    db.add(extraction)
    await ingestion_service.update_status(db, document, DocumentStatus.EXTRACTED)

    validation = ValidationResult(
        document_id=document.id,
        job_id=job.id,
        is_valid=validation_output.is_valid,
        issues=validation_output.issues,
    )
    db.add(validation)
    await db.commit()

    # Not a failure per ADR-0023 even when is_valid is False: the model
    # genuinely didn't find a required field, and the job still completed.
    final_status = DocumentStatus.VALIDATED if validation_output.is_valid else DocumentStatus.FAILED
    await ingestion_service.update_status(db, document, final_status)

    return ProcessingResult(
        job_id=job.id,
        document_id=document.id,
        raw_text=combined_output.raw_text,
        fields=combined_output.fields,
        confidence=combined_output.confidence,
        is_valid=validation_output.is_valid,
        issues=validation_output.issues,
        metadata={
            "ocr_backend": type(extraction_pipeline).__name__,
            "field_extraction_backend": type(field_extraction_pipeline).__name__,
        },
    )
