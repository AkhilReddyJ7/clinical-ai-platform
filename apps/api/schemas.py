import uuid

from pydantic import BaseModel

from modules.audit.schemas import AuditLogEntryOut
from modules.ingestion.schemas import DocumentOut
from modules.ocr.schemas import ExtractionResultOut
from modules.processing.models import JobStatus
from modules.validation.schemas import ValidationResultOut


class ProcessEnqueuedOut(BaseModel):
    """POST /documents/{id}/process's 202 body (ADR-0022): confirms a job
    was created and its initial status -- no extraction/validation
    content, since none exists yet.
    """

    document_id: uuid.UUID
    job_id: uuid.UUID
    job_status: JobStatus


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
