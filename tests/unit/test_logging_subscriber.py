import logging
import uuid

import pytest

from modules.processing.events import Event, EventType
from modules.processing.observability.logging_subscriber import _logging_subscriber


def _event(event_type: EventType, **metadata: object) -> Event:
    return Event(
        event_type=event_type,
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        metadata=metadata,
    )


def test_logs_event_type_job_id_document_id_and_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = _event(EventType.PIPELINE_STAGE_STARTED, stage="ocr")

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        _logging_subscriber(event)

    matching = [r.message for r in caplog.records if "event:" in r.message]
    assert len(matching) == 1
    message = matching[0]
    assert "pipeline_stage_started" in message
    assert f"job_id={event.job_id}" in message
    assert f"document_id={event.document_id}" in message
    assert "stage" in message


def test_logs_at_info_level(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        _logging_subscriber(_event(EventType.JOB_COMPLETED, duration_ms=12.5))

    assert caplog.records[0].levelno == logging.INFO


def test_handles_a_missing_document_id(caplog: pytest.LogCaptureFixture) -> None:
    event = Event(event_type=EventType.JOB_FAILED, job_id=uuid.uuid4(), document_id=None)

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        _logging_subscriber(event)  # must not raise

    assert "document_id=None" in caplog.records[0].message
