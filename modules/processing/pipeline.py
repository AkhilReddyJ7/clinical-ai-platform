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
- An OCR ExtractionError (or a ValueError from an unrecognized content
  type — the same "property of the input, not of network conditions"
  case, just a different exception type) is always terminal.
- The PHI gate correctly halting the call, and RequiredFieldsValidator
  correctly reporting a missing field, are both *not failures* — the job
  completes; only the document's outcome is `failed`.
- A FieldExtractionError is classified by inspecting the chained
  `__cause__` the Anthropic SDK call raised it from (RateLimitError /
  APIConnectionError [including its APITimeoutError subclass] / a 5xx
  APIStatusError -> transient; anything else, including a missing API key
  or a malformed response -> terminal) — without touching
  modules/extraction/anthropic_extractor.py itself.

Increment 6 adds quality signals on top of this, all read-only summaries
that never influence the classification above or the document/job state
transitions: a geometric-mean document confidence (penalizes a single
weak stage instead of averaging it away), a per-field plausibility-
weighted confidence, and an issue-category summary
(missing_required_fields / invalid_data / uncertain_extraction) derived
from the *existing* validator issue text — no new validators, no new
issue strings, no change to `is_valid`.
"""

import math
import re
import time
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
from modules.processing.events import Event, EventType, emit_event
from modules.processing.models import Job
from modules.processing.observability.registry import register_default_subscribers
from modules.validation.base import ValidationPipeline
from modules.validation.models import ValidationResult
from shared.config.settings import get_settings
from sqlalchemy.ext.asyncio import AsyncSession

# Startup-context wiring: registers the metrics/logging subscribers
# exactly once (idempotent — worker.py makes the same call
# independently). Not inside emit_event, not inside modules.processing.events.
register_default_subscribers()


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
    # Per-field confidence (Increment 6) — a plausibility-weighted view of
    # the same scalar field-extraction confidence, not a second score the
    # LLM itself produced (neither pipeline stage returns real per-field
    # scores today). Empty whenever `fields` is empty.
    field_confidence: dict[str, float] = field(default_factory=dict)


def _is_transient_field_extraction_error(exc: FieldExtractionError) -> bool:
    """ADR-0023 section 1's classification, read off the chained cause."""
    cause = exc.__cause__
    if isinstance(cause, anthropic.RateLimitError):
        return True
    if isinstance(cause, anthropic.APIConnectionError):
        # Covers anthropic.APITimeoutError too (a subclass), so a timed-out
        # call is treated the same as any other connection failure.
        return True
    if isinstance(cause, anthropic.APIStatusError):
        return cause.status_code >= 500
    return False


def _aggregate_confidence(ocr_confidence: float, field_confidence: float) -> float:
    """Document-level confidence: geometric mean, not arithmetic mean.

    An arithmetic mean of (0.95, 0.05) is 0.5 — reads as "medium
    confidence" when what actually happened is one stage worked and the
    other essentially failed. The geometric mean of the same pair is
    ~0.22: it correctly lets a single very-weak stage drag the whole
    result down instead of being masked by a strong one. Clamped to
    non-negative inputs defensively; neither stage's confidence is
    expected to be negative, but a geometric mean of a negative number
    isn't a meaningful confidence value.
    """
    return math.sqrt(max(ocr_confidence, 0.0) * max(field_confidence, 0.0))


# Loose, deliberately permissive shape checks — these flag a field as
# *plausible-looking*, not as validated data (that's what a real schema/
# format validator would do, which is out of scope here). A poor match
# lowers that field's confidence; it never removes the field or affects
# is_valid.
_DATE_LIKE = re.compile(
    r"\d{1,4}[-/.\s]\d{1,2}[-/.\s]\d{1,4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4}"
)
_NAME_LIKE = re.compile(r"^[A-Za-z][A-Za-z'-]*(\s+[A-Za-z][A-Za-z'-]*)+$")
_MIN_PLAUSIBLE_MRN_LENGTH = 3


def _field_plausibility(field_name: str, value: str) -> float:
    stripped = value.strip()
    if not stripped:
        return 0.0
    if field_name == "date_of_birth":
        return 1.0 if _DATE_LIKE.search(stripped) else 0.5
    if field_name == "patient_name":
        return 1.0 if _NAME_LIKE.match(stripped) else 0.5
    if field_name == "mrn":
        return 1.0 if len(stripped) >= _MIN_PLAUSIBLE_MRN_LENGTH else 0.5
    return 1.0


def _compute_field_confidence(fields: dict[str, str], base_confidence: float) -> dict[str, float]:
    return {
        name: round(base_confidence * _field_plausibility(name, value), 4)
        for name, value in fields.items()
    }


_MISSING_FIELD_PREFIX = "missing required field"
_PHI_ISSUE_PREFIX = "phi:"


def _categorize_issues(issues: list[str]) -> set[str]:
    """Maps the *existing* validator issue strings to one of three
    reporting categories — purely additive labeling for
    ProcessingResult.metadata. Never changes `issues` itself, never
    changes `is_valid`, and never introduces a new ADR-0023 failure
    category (this is validation-issue labeling, a document-level
    concept ADR-0023 doesn't govern).
    """
    categories: set[str] = set()
    for issue in issues:
        if issue.startswith(_MISSING_FIELD_PREFIX):
            categories.add("missing_required_fields")
        else:
            # Both a PHI hit and an extraction/field-extraction failure
            # record mean the data isn't safe/trustworthy to use as-is.
            categories.add("invalid_data")
    return categories


def _build_metadata(
    *,
    confidence: float,
    field_confidence: dict[str, float],
    issues: list[str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    threshold = get_settings().low_confidence_threshold
    categories = _categorize_issues(issues)
    is_low_confidence = confidence < threshold
    if is_low_confidence:
        categories.add("uncertain_extraction")
    low_confidence_fields = sorted(
        name for name, score in field_confidence.items() if score < threshold
    )

    metadata: dict[str, str] = {
        "low_confidence": str(is_low_confidence).lower(),
        "issue_categories": ",".join(sorted(categories)),
        "low_confidence_fields": ",".join(low_confidence_fields),
    }
    if extra:
        metadata.update(extra)
    return metadata


def _emit_stage_started(job: Job, document_id: uuid.UUID, stage: str) -> None:
    """Increment 8: PIPELINE_STAGE_STARTED — purely observational, emitted
    right before a stage's work begins."""
    emit_event(
        Event(
            event_type=EventType.PIPELINE_STAGE_STARTED,
            job_id=str(job.id),
            document_id=str(document_id),
            metadata={"stage": stage},
        )
    )


def _emit_stage_completed(
    job: Job,
    document_id: uuid.UUID,
    stage: str,
    duration_seconds: float,
    *,
    error_type: str | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    """Increment 8: PIPELINE_STAGE_COMPLETED, replacing Increment 7's
    _log_stage (which called metrics/logger directly). Emitted after a
    stage already succeeded or failed — purely observational, never
    influences what happens next.
    """
    metadata: dict[str, object] = {"stage": stage, "duration_ms": duration_seconds * 1000}
    if error_type is not None:
        metadata["error_type"] = error_type
    if extra_metadata:
        metadata.update(extra_metadata)
    emit_event(
        Event(
            event_type=EventType.PIPELINE_STAGE_COMPLETED,
            job_id=str(job.id),
            document_id=str(document_id),
            metadata=metadata,
        )
    )


def _confidence_snapshot(result: "ProcessingResult") -> dict[str, object]:
    """ADR-0025 confidence signals, packaged as event metadata for the
    `pipeline_total` stage-completed event (Increment 8) — the same data
    Increment 7's _log_confidence_summary logged directly, now carried on
    an event instead.
    """
    field_scores = list(result.field_confidence.values())
    threshold = get_settings().low_confidence_threshold
    snapshot: dict[str, object] = {
        "document_confidence": result.confidence,
        "low_confidence_field_count": sum(1 for score in field_scores if score < threshold),
    }
    if field_scores:
        snapshot["min_field_confidence"] = min(field_scores)
        snapshot["avg_field_confidence"] = sum(field_scores) / len(field_scores)
        snapshot["max_field_confidence"] = max(field_scores)
    return snapshot


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
    pipeline_started_at = time.monotonic()

    document = await ingestion_service.get_document(db, job.document_id)
    if document is None:
        # Not reachable in normal operation (documents are never deleted);
        # nothing to retry against, and nothing to attach a failure record
        # to, so this is unconditionally terminal.
        raise TerminalProcessingError(f"document {job.document_id} not found")

    emit_event(
        Event(
            event_type=EventType.JOB_STARTED,
            job_id=str(job.id),
            document_id=str(document.id),
            metadata={"status": job.status.value},
        )
    )
    _emit_stage_started(job, document.id, "pipeline_total")

    await ingestion_service.update_status(db, document, DocumentStatus.PROCESSING)

    data = await run_in_threadpool(storage.read, document.storage_key)

    _emit_stage_started(job, document.id, "ocr")
    ocr_started_at = time.monotonic()
    try:
        extraction_output = await run_in_threadpool(
            extraction_pipeline.extract, data=data, content_type=document.content_type
        )
    except (ExtractionError, ValueError) as exc:
        _emit_stage_completed(
            job,
            document.id,
            "ocr",
            time.monotonic() - ocr_started_at,
            error_type=type(exc).__name__,
        )
        # ValueError alongside ExtractionError: TesseractExtractionPipeline
        # raises a bare ValueError (not ExtractionError) for a content type
        # it doesn't recognize — previously uncaught here, which meant the
        # document was silently left stuck in `processing` while the job
        # still failed via the worker's generic catch-all. Same terminal
        # classification either way: both are a property of the input, not
        # of network conditions.
        await _persist_failure(
            db,
            document,
            job,
            raw_text=f"[EXTRACTION FAILED: {exc}]",
            issues=[f"extraction failed: {exc}"],
        )
        raise TerminalProcessingError(str(exc)) from exc
    else:
        _emit_stage_completed(job, document.id, "ocr", time.monotonic() - ocr_started_at)

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
        result = ProcessingResult(
            job_id=job.id,
            document_id=document.id,
            raw_text=redacted_raw_text,
            fields={},
            confidence=extraction_output.confidence,
            is_valid=False,
            issues=phi_precheck.issues,
            metadata=_build_metadata(
                confidence=extraction_output.confidence,
                field_confidence={},
                issues=phi_precheck.issues,
                extra={"outcome": "phi_detected"},
            ),
        )
        _emit_stage_completed(
            job,
            document.id,
            "pipeline_total",
            time.monotonic() - pipeline_started_at,
            extra_metadata=_confidence_snapshot(result),
        )
        return result

    _emit_stage_started(job, document.id, "field_extraction")
    field_extraction_started_at = time.monotonic()
    try:
        field_output = await run_in_threadpool(
            field_extraction_pipeline.extract_fields, raw_text=extraction_output.raw_text
        )
    except FieldExtractionError as exc:
        _emit_stage_completed(
            job,
            document.id,
            "field_extraction",
            time.monotonic() - field_extraction_started_at,
            error_type=type(exc).__name__,
        )
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
    else:
        _emit_stage_completed(
            job, document.id, "field_extraction", time.monotonic() - field_extraction_started_at
        )

    combined_output = ExtractionOutput(
        raw_text=extraction_output.raw_text,
        fields=field_output.fields,
        confidence=_aggregate_confidence(extraction_output.confidence, field_output.confidence),
    )
    _emit_stage_started(job, document.id, "validation")
    validation_started_at = time.monotonic()
    validation_output = validation_pipeline.validate(combined_output)
    _emit_stage_completed(job, document.id, "validation", time.monotonic() - validation_started_at)
    field_confidence = _compute_field_confidence(combined_output.fields, combined_output.confidence)

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

    result = ProcessingResult(
        job_id=job.id,
        document_id=document.id,
        raw_text=combined_output.raw_text,
        fields=combined_output.fields,
        confidence=combined_output.confidence,
        is_valid=validation_output.is_valid,
        issues=validation_output.issues,
        field_confidence=field_confidence,
        metadata=_build_metadata(
            confidence=combined_output.confidence,
            field_confidence=field_confidence,
            issues=validation_output.issues,
            extra={
                "ocr_backend": type(extraction_pipeline).__name__,
                "field_extraction_backend": type(field_extraction_pipeline).__name__,
            },
        ),
    )
    _emit_stage_completed(
        job,
        document.id,
        "pipeline_total",
        time.monotonic() - pipeline_started_at,
        extra_metadata=_confidence_snapshot(result),
    )
    return result
