import logging
import uuid
from collections.abc import Iterator

import pytest

import modules.processing.events as events_module
import modules.processing.observability.registry as registry_module
from modules.processing.events import Event, EventType, clear_subscribers, emit_event, subscribe
from modules.processing.metrics import metrics
from modules.processing.observability.logging_subscriber import _logging_subscriber
from modules.processing.observability.metrics_subscriber import _metrics_subscriber
from modules.processing.observability.registry import (
    _reset_for_testing,
    register_default_subscribers,
)


@pytest.fixture(autouse=True)
def _isolate_registry_state() -> Iterator[None]:
    # Resetting _registered (not just clearing subscribers) matters:
    # worker.py/pipeline.py already called register_default_subscribers()
    # at import time, for every other test module in this session — by
    # the time these tests run, _registered is already True process-wide.
    # Without resetting it here, register_default_subscribers() below
    # would be a silent no-op, and these tests would find zero
    # subscribers despite "registering" them.
    metrics.reset()
    clear_subscribers()
    registry_module._registered = False
    yield
    _reset_for_testing()


def _event(event_type: EventType = EventType.JOB_CLAIMED) -> Event:
    return Event(event_type=event_type, job_id=uuid.uuid4(), document_id=uuid.uuid4())


def test_register_default_subscribers_wires_up_metrics() -> None:
    register_default_subscribers()

    emit_event(_event(EventType.JOB_CLAIMED))

    assert metrics.jobs_claimed == 1


def test_register_default_subscribers_wires_up_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    register_default_subscribers()

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        emit_event(_event(EventType.JOB_CLAIMED))

    assert any("event: job_claimed" in r.message for r in caplog.records)


def test_calling_register_default_subscribers_twice_does_not_double_subscribe() -> None:
    # This is the crucial guarantee: worker.py and pipeline.py both call
    # register_default_subscribers() independently at import time. If it
    # weren't idempotent, every event would be double-counted/double-logged.
    register_default_subscribers()
    register_default_subscribers()

    emit_event(_event(EventType.JOB_CLAIMED))

    assert metrics.jobs_claimed == 1


def test_calling_register_default_subscribers_many_times_yields_exactly_one_of_each() -> None:
    """Direct count, not just an observed-effect proxy: the requirement is
    literally "exactly 1 metrics subscriber, exactly 1 logging subscriber,
    no duplicates allowed" — checked against the subscriber list itself.
    """
    register_default_subscribers()
    register_default_subscribers()
    register_default_subscribers()

    metrics_count = sum(1 for h in events_module._subscribers if h is _metrics_subscriber)
    logging_count = sum(1 for h in events_module._subscribers if h is _logging_subscriber)

    assert metrics_count == 1
    assert logging_count == 1
    assert len(events_module._subscribers) == 2


def test_subscribers_are_registered_only_via_the_registry() -> None:
    # Before calling register_default_subscribers(), the (already-cleared)
    # subscriber list stays empty — nothing else populates it.
    emit_event(_event(EventType.JOB_CLAIMED))
    assert metrics.jobs_claimed == 0

    register_default_subscribers()
    emit_event(_event(EventType.JOB_CLAIMED))
    assert metrics.jobs_claimed == 1


def test_reset_for_testing_restores_exactly_the_two_defaults() -> None:
    def extra_handler(event: Event) -> None:
        pass

    register_default_subscribers()
    subscribe(extra_handler)

    _reset_for_testing()
    emit_event(_event(EventType.JOB_CLAIMED))

    # Exactly one increment — the extra handler is gone, the defaults work.
    assert metrics.jobs_claimed == 1
