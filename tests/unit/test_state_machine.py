import pytest

from modules.ingestion.models import DocumentStatus
from modules.processing.models import JobStatus
from modules.processing.state_machine import (
    IllegalTransitionError,
    validate_document_transition,
    validate_job_transition,
)

# ADR-0020 "Document lifecycle" table.
LEGAL_DOCUMENT_TRANSITIONS = [
    (DocumentStatus.UPLOADED, DocumentStatus.PROCESSING),
    (DocumentStatus.PROCESSING, DocumentStatus.EXTRACTED),
    (DocumentStatus.PROCESSING, DocumentStatus.FAILED),
    (DocumentStatus.EXTRACTED, DocumentStatus.VALIDATED),
    (DocumentStatus.EXTRACTED, DocumentStatus.FAILED),
    (DocumentStatus.FAILED, DocumentStatus.PROCESSING),
]

# ADR-0020 "Job lifecycle" table.
LEGAL_JOB_TRANSITIONS = [
    (JobStatus.QUEUED, JobStatus.RUNNING),
    (JobStatus.RUNNING, JobStatus.COMPLETED),
    (JobStatus.RUNNING, JobStatus.RETRYING),
    (JobStatus.RETRYING, JobStatus.RUNNING),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.QUEUED, JobStatus.CANCELLED),
    (JobStatus.RETRYING, JobStatus.CANCELLED),
]


ALL_DOCUMENT_PAIRS = [(a, b) for a in DocumentStatus for b in DocumentStatus]
ALL_JOB_PAIRS = [(a, b) for a in JobStatus for b in JobStatus]

ILLEGAL_DOCUMENT_TRANSITIONS = [
    pair for pair in ALL_DOCUMENT_PAIRS if pair not in LEGAL_DOCUMENT_TRANSITIONS
]
ILLEGAL_JOB_TRANSITIONS = [pair for pair in ALL_JOB_PAIRS if pair not in LEGAL_JOB_TRANSITIONS]


@pytest.mark.parametrize("current,new", LEGAL_DOCUMENT_TRANSITIONS)
def test_legal_document_transitions_are_accepted(
    current: DocumentStatus, new: DocumentStatus
) -> None:
    validate_document_transition(current, new)  # must not raise


@pytest.mark.parametrize("current,new", ILLEGAL_DOCUMENT_TRANSITIONS)
def test_illegal_document_transitions_are_rejected(
    current: DocumentStatus, new: DocumentStatus
) -> None:
    with pytest.raises(IllegalTransitionError):
        validate_document_transition(current, new)


def test_validated_document_cannot_be_reprocessed() -> None:
    """ADR-0020: validated -> processing is disallowed by default."""
    with pytest.raises(IllegalTransitionError):
        validate_document_transition(DocumentStatus.VALIDATED, DocumentStatus.PROCESSING)


@pytest.mark.parametrize("current,new", LEGAL_JOB_TRANSITIONS)
def test_legal_job_transitions_are_accepted(current: JobStatus, new: JobStatus) -> None:
    validate_job_transition(current, new)  # must not raise


@pytest.mark.parametrize("current,new", ILLEGAL_JOB_TRANSITIONS)
def test_illegal_job_transitions_are_rejected(current: JobStatus, new: JobStatus) -> None:
    with pytest.raises(IllegalTransitionError):
        validate_job_transition(current, new)


def test_running_job_cannot_be_cancelled() -> None:
    """ADR-0020: running -> cancelled is disallowed this sprint."""
    with pytest.raises(IllegalTransitionError):
        validate_job_transition(JobStatus.RUNNING, JobStatus.CANCELLED)


@pytest.mark.parametrize(
    "terminal_status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_terminal_job_states_accept_no_further_transitions(terminal_status: JobStatus) -> None:
    for candidate in JobStatus:
        with pytest.raises(IllegalTransitionError):
            validate_job_transition(terminal_status, candidate)
