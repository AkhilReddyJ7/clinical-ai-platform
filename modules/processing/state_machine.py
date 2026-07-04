"""Legal state-transition graphs for the document and job lifecycles.

Encodes the transition tables from ADR-0020 exactly. Pure and
side-effect-free by design: no database access, no calls to the worker or
API layers. Callers (the future worker epic) are expected to validate a
transition with these functions before persisting it.
"""

from modules.ingestion.models import DocumentStatus
from modules.processing.models import JobStatus


class IllegalTransitionError(ValueError):
    """Raised when a requested state transition is not in the legal graph."""

    def __init__(
        self, current: DocumentStatus | JobStatus, new: DocumentStatus | JobStatus, *, kind: str
    ):
        super().__init__(f"Illegal {kind} transition: {current.value} -> {new.value}")
        self.current = current
        self.new = new
        self.kind = kind


# ADR-0020, "Document lifecycle" table. Anything not listed here (including
# validated -> processing and any self-transition) is illegal.
DOCUMENT_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.UPLOADED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.PROCESSING: frozenset({DocumentStatus.EXTRACTED, DocumentStatus.FAILED}),
    DocumentStatus.EXTRACTED: frozenset({DocumentStatus.VALIDATED, DocumentStatus.FAILED}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.VALIDATED: frozenset(),
}

# ADR-0020, "Job lifecycle" table. `running -> cancelled` is deliberately
# absent ("disallowed this sprint"); completed/failed/cancelled are terminal.
JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.RETRYING, JobStatus.FAILED}),
    JobStatus.RETRYING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def validate_document_transition(current: DocumentStatus, new: DocumentStatus) -> None:
    """Raise IllegalTransitionError unless current -> new is a legal document transition."""
    if new not in DOCUMENT_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(current, new, kind="document")


def validate_job_transition(current: JobStatus, new: JobStatus) -> None:
    """Raise IllegalTransitionError unless current -> new is a legal job transition."""
    if new not in JOB_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(current, new, kind="job")
