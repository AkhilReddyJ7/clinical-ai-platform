import uuid

import pytest

from modules.processing.events import Event, EventType
from modules.processing.metrics import metrics
from modules.processing.observability.metrics_subscriber import _metrics_subscriber


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    metrics.reset()


def _event(event_type: EventType, **metadata: object) -> Event:
    return Event(
        event_type=event_type,
        job_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        metadata=metadata,
    )


@pytest.mark.parametrize(
    "event_type,counter_name",
    [
        (EventType.JOB_CLAIMED, "jobs_claimed"),
        (EventType.JOB_COMPLETED, "completions"),
        (EventType.JOB_RETRYING, "retries"),
        (EventType.JOB_FAILED, "terminal_failures"),
        (EventType.JOB_STALE_SKIPPED, "stale_reclaims"),
    ],
)
def test_increments_the_matching_counter(event_type: EventType, counter_name: str) -> None:
    _metrics_subscriber(_event(event_type))
    assert getattr(metrics, counter_name) == 1


@pytest.mark.parametrize(
    "event_type", [EventType.JOB_COMPLETED, EventType.JOB_RETRYING, EventType.JOB_FAILED]
)
def test_records_job_total_duration_from_duration_ms(event_type: EventType) -> None:
    _metrics_subscriber(_event(event_type, duration_ms=250.0))

    summary = metrics.stage_summary("job_total")
    assert summary is not None
    assert summary.avg_seconds == pytest.approx(0.25)


def test_records_stage_duration_for_pipeline_stage_completed() -> None:
    _metrics_subscriber(_event(EventType.PIPELINE_STAGE_COMPLETED, stage="ocr", duration_ms=500.0))

    summary = metrics.stage_summary("ocr")
    assert summary is not None
    assert summary.avg_seconds == pytest.approx(0.5)


def test_pipeline_stage_started_is_ignored() -> None:
    _metrics_subscriber(_event(EventType.PIPELINE_STAGE_STARTED, stage="ocr"))
    assert metrics.stage_summary("ocr") is None


def test_ignores_malformed_metadata_without_raising() -> None:
    _metrics_subscriber(_event(EventType.JOB_COMPLETED, duration_ms="not-a-number"))
    assert metrics.completions == 1
    assert metrics.stage_summary("job_total") is None


def test_ignores_malformed_stage_metadata_without_raising() -> None:
    _metrics_subscriber(_event(EventType.PIPELINE_STAGE_COMPLETED, stage=123, duration_ms=500.0))
    assert metrics.stage_summary("ocr") is None
