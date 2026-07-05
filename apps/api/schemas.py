import uuid
from datetime import datetime

from pydantic import BaseModel

from modules.analytics.schemas import LowConfidenceDocumentOut
from modules.audit.schemas import AuditLogEntryOut
from modules.ingestion.schemas import DocumentOut
from modules.ocr.schemas import ExtractionResultOut
from modules.processing.models import JobStatus, JobTrigger
from modules.validation.schemas import ValidationResultOut


class ProcessEnqueuedOut(BaseModel):
    """Shared 202 body for both POST /documents/{id}/process (ADR-0022)
    and POST /documents/{id}/reprocess (ADR-0032) -- confirms a job was
    created and its lineage (attempt_number/trigger/trigger_note,
    ADR-0031), not extraction/validation content, since none exists yet.
    """

    document_id: uuid.UUID
    job_id: uuid.UUID
    job_status: JobStatus
    attempt_number: int
    trigger: JobTrigger
    trigger_note: str | None = None


class ReprocessIn(BaseModel):
    """POST /documents/{id}/reprocess's request body (ADR-0032)."""

    trigger_note: str | None = None


class ProcessingStatusOut(BaseModel):
    """GET /documents/{id}/result's body (ADR-0022) -- the one place a
    caller looks to answer both "what's the status" and "what's the
    result". Exactly one of the fields below is populated per case:
    - never processed: only `document` (status `uploaded`).
    - in progress: `document` (status `processing`) + `job_status`
      (the active job's own queued/running/retrying status).
    - completed/failed: `document` (status `validated`/`failed`) +
      `extraction` + `validation`, when a result exists to report.
    """

    document: DocumentOut
    job_status: JobStatus | None = None
    extraction: ExtractionResultOut | None = None
    validation: ValidationResultOut | None = None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class AuditLogListOut(BaseModel):
    items: list[AuditLogEntryOut]
    total: int
    limit: int
    offset: int


class DocumentHistoryEntryOut(BaseModel):
    """One Job attempt's lineage summary (ADR-0031/0032) -- flattened
    rather than nesting full ExtractionResultOut/ValidationResultOut,
    since only summary fields are needed for this view. `pipeline_version`/
    `confidence`/`is_valid` are None when the job never produced a result
    row (e.g. a worker-level failure that never reached pipeline.py's
    _persist_failure) -- not every Job has a corresponding result.
    """

    job_id: uuid.UUID
    attempt_number: int
    trigger: JobTrigger
    trigger_note: str | None
    job_status: JobStatus
    created_at: datetime
    pipeline_version: str | None = None
    confidence: float | None = None
    is_valid: bool | None = None


class DocumentHistoryOut(BaseModel):
    document_id: uuid.UUID
    items: list[DocumentHistoryEntryOut]


class LowConfidenceDocumentListOut(BaseModel):
    items: list[LowConfidenceDocumentOut]
    total: int
    limit: int
    offset: int
