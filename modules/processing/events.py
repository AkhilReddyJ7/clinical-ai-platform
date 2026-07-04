"""In-process event dispatch core (Increment 8; made pure in Increment 8's
structural refactor; schema-stabilized in Increment 10).

Pure dispatch only: EventType, Event, subscribe/unsubscribe/
clear_subscribers, emit_event. This module has no knowledge of what a
subscriber does — no metrics, no application logging, no import of
anything under modules.processing.observability. Wiring the default
observability subscribers happens exactly once, in
modules.processing.observability.registry.register_default_subscribers,
never here and never inside emit_event itself — see that module for the
metrics/logging consumers execution code actually gets.

Schema contract (Increment 10): the *only* fields any consumer may rely
on are event_type, job_id, document_id, metadata, and timestamp — exactly
the dataclass fields below, nothing more. `job_id`/`document_id` are
plain `str`/`str | None`, not `uuid.UUID`: this module has no opinion on
what identifier scheme a producer uses, only that it's been rendered to
a string by the time it reaches an Event. `metadata` is guaranteed to
always be a `dict` (never `None`), but its *keys* are explicitly NOT
part of this contract — different EventTypes carry different metadata
shapes, the shape can change over time, and no subscriber may assume any
particular key is present without checking (see
modules.processing.observability for the required defensive-access
pattern: `.get(...)`, never `metadata["..."]`).

Not external infrastructure: no Kafka, no Redis, no OpenTelemetry. A
plain in-process list of synchronous callables, dispatched synchronously
in registration order.
"""

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone


class EventType(str, enum.Enum):
    JOB_CLAIMED = "job_claimed"
    JOB_STARTED = "job_started"
    PIPELINE_STAGE_STARTED = "pipeline_stage_started"
    PIPELINE_STAGE_COMPLETED = "pipeline_stage_completed"
    JOB_COMPLETED = "job_completed"
    JOB_RETRYING = "job_retrying"
    JOB_FAILED = "job_failed"
    # Emitted by worker.py's stale-job detection scan (ADR-0024) when it
    # finds and recovers a `running` job nothing has touched recently —
    # deliberately distinct from JOB_RETRYING/JOB_FAILED (the ordinary
    # in-band-exception path), even though the underlying DB transition
    # is the same running -> retrying/failed edge: the *reason* differs
    # (crash recovery vs. an outcome this worker's own attempt reached),
    # and ADR-0024 names that distinction as worth keeping observable.
    JOB_STALE_SKIPPED = "job_stale_skipped"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Event:
    event_type: EventType
    job_id: str
    document_id: str | None
    metadata: dict[str, object] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)


_subscribers: list[Callable[[Event], None]] = []

# A plain stdlib logger, deliberately not shared.logging.logger: this
# module must stay free of any observability-layer dependency (that's
# the whole point of the refactor), and emit_event's own diagnostic
# logging for a misbehaving subscriber is the one exception that needs
# *some* logging capability regardless.
_log = logging.getLogger(__name__)


def subscribe(handler: Callable[[Event], None]) -> None:
    _subscribers.append(handler)


def unsubscribe(handler: Callable[[Event], None]) -> None:
    if handler in _subscribers:
        _subscribers.remove(handler)


def clear_subscribers() -> None:
    """Removes every subscriber, defaults included.

    A pure core operation — this module has no notion of which
    subscribers are "default"; restoring them is the observability
    registry's job (modules.processing.observability.registry).
    """
    _subscribers.clear()


def emit_event(event: Event) -> None:
    """Dispatch `event` to every subscribed handler, synchronously, in
    registration order.

    A handler that raises is caught and logged, never propagated to the
    caller — observability must never be able to break execution.
    Iterates over a snapshot of the subscriber list so a handler that
    subscribes/unsubscribes during dispatch can't corrupt this call's
    iteration.
    """
    for handler in list(_subscribers):
        try:
            handler(event)
        except Exception:
            _log.exception(
                "subscriber %r raised while handling event_type=%s job_id=%s",
                handler,
                event.event_type.value,
                event.job_id,
            )
