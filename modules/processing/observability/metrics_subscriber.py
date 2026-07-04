"""Metrics observability subscriber (event-backbone structural refactor).

Turns Event objects into modules.processing.metrics.WorkerMetrics
counter/duration updates. Depends only on WorkerMetrics and the Event
model — no event-dispatch logic lives here (that's modules.processing.
events), and this module never calls subscribe() itself (that's
modules.processing.observability.registry, the only wiring point).
"""

from modules.processing.events import Event, EventType
from modules.processing.metrics import metrics


def _job_total_duration_seconds(event: Event) -> float | None:
    duration_ms = event.metadata.get("duration_ms")
    return duration_ms / 1000 if isinstance(duration_ms, (int, float)) else None


def _metrics_subscriber(event: Event) -> None:
    """Turns a lifecycle Event into the matching WorkerMetrics update —
    the same counters worker.py/pipeline.py used to mutate directly,
    before the event backbone existed.
    """
    if event.event_type == EventType.JOB_CLAIMED:
        metrics.record_claim()
    elif event.event_type == EventType.JOB_COMPLETED:
        metrics.record_completion()
        duration = _job_total_duration_seconds(event)
        if duration is not None:
            metrics.record_stage_duration("job_total", duration)
    elif event.event_type == EventType.JOB_RETRYING:
        metrics.record_retry()
        duration = _job_total_duration_seconds(event)
        if duration is not None:
            metrics.record_stage_duration("job_total", duration)
    elif event.event_type == EventType.JOB_FAILED:
        metrics.record_terminal_failure()
        duration = _job_total_duration_seconds(event)
        if duration is not None:
            metrics.record_stage_duration("job_total", duration)
    elif event.event_type == EventType.JOB_STALE_SKIPPED:
        metrics.record_stale_reclaim()
    elif event.event_type == EventType.PIPELINE_STAGE_COMPLETED:
        stage = event.metadata.get("stage")
        duration_ms = event.metadata.get("duration_ms")
        if isinstance(stage, str) and isinstance(duration_ms, (int, float)):
            metrics.record_stage_duration(stage, duration_ms / 1000)
