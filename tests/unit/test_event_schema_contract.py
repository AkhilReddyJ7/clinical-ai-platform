"""Increment 10: enforces the Event schema contract itself, distinct from
Increment 8/9's dispatch-mechanics and failure-isolation coverage.

Three concerns, each with its own section below:
- The *shape* of Event is exactly event_type/job_id/document_id/metadata/
  timestamp, with job_id/document_id as plain strings — nothing more,
  and metadata is never allowed to be None.
- metadata's *keys* are explicitly not part of the contract: emit_event
  and both built-in subscribers must tolerate missing, extra, or
  unexpected-shaped metadata without raising.
- No subscriber may access `event.metadata["key"]` directly (a KeyError
  waiting to happen the moment a producer stops sending that key) —
  verified structurally via AST, not just by example.
"""

import ast
import dataclasses
import inspect
import typing
import uuid
from collections.abc import Iterator
from types import ModuleType

import pytest

import modules.processing.observability.logging_subscriber as logging_subscriber_module
import modules.processing.observability.metrics_subscriber as metrics_subscriber_module
from modules.processing.events import Event, EventType, emit_event
from modules.processing.metrics import metrics
from modules.processing.observability.logging_subscriber import _logging_subscriber
from modules.processing.observability.metrics_subscriber import _metrics_subscriber
from modules.processing.observability.registry import (
    _reset_for_testing,
    register_default_subscribers,
)


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    metrics.reset()
    _reset_for_testing()
    yield
    metrics.reset()
    _reset_for_testing()


def _event(event_type: EventType = EventType.JOB_CLAIMED, **metadata: object) -> Event:
    return Event(
        event_type=event_type,
        job_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        metadata=metadata,
    )


# --- 1. Event schema contract shape --------------------------------------


def test_event_has_exactly_the_contracted_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(Event)}
    assert field_names == {"event_type", "job_id", "document_id", "metadata", "timestamp"}


def test_event_field_types_match_the_contract() -> None:
    hints = typing.get_type_hints(Event)
    assert hints["event_type"] is EventType
    assert hints["job_id"] is str
    assert hints["document_id"] == (str | None)
    assert hints["metadata"] == dict[str, object]


def test_metadata_defaults_to_an_empty_dict_never_none() -> None:
    event = Event(event_type=EventType.JOB_CLAIMED, job_id=str(uuid.uuid4()), document_id=None)
    assert event.metadata == {}
    assert event.metadata is not None


def test_document_id_may_be_none_but_job_id_may_not() -> None:
    # document_id=None is explicitly part of the contract (not every event
    # has a known document); job_id is always required.
    event = Event(event_type=EventType.JOB_FAILED, job_id=str(uuid.uuid4()), document_id=None)
    assert event.document_id is None
    assert isinstance(event.job_id, str)


# --- 2. No required metadata keys ----------------------------------------


def test_emit_event_works_with_empty_metadata() -> None:
    emit_event(_event())  # metadata={} via the helper's **metadata default


def test_emit_event_works_with_missing_optional_fields() -> None:
    # A PIPELINE_STAGE_COMPLETED event that omits "stage" and "duration_ms"
    # entirely — both are optional as far as the schema is concerned.
    emit_event(_event(EventType.PIPELINE_STAGE_COMPLETED))


def test_emit_event_works_with_extra_unknown_fields() -> None:
    emit_event(
        _event(
            EventType.JOB_COMPLETED,
            duration_ms=42.0,
            totally_unexpected_field="some value",
            another_future_field={"nested": "shape"},
        )
    )


# --- 3. Subscriber resilience to schema drift ----------------------------


class TestMetricsSubscriberSchemaDrift:
    def test_survives_missing_duration_ms(self) -> None:
        _metrics_subscriber(_event(EventType.JOB_COMPLETED))
        assert metrics.completions == 1
        assert metrics.stage_summary("job_total") is None

    def test_survives_missing_stage(self) -> None:
        _metrics_subscriber(_event(EventType.PIPELINE_STAGE_COMPLETED, duration_ms=100.0))
        # No stage name to record against — nothing recorded, nothing raised.
        assert metrics.snapshot()["stages"] == {}

    def test_survives_unexpected_metadata_value_types(self) -> None:
        _metrics_subscriber(
            _event(
                EventType.PIPELINE_STAGE_COMPLETED,
                stage=["not", "a", "string"],
                duration_ms="not-a-number-either",
            )
        )
        assert metrics.snapshot()["stages"] == {}

    def test_survives_completely_empty_metadata_for_every_event_type(self) -> None:
        for event_type in EventType:
            _metrics_subscriber(
                Event(event_type=event_type, job_id=str(uuid.uuid4()), document_id=None)
            )
        # No exception for any of them; the counters that don't need
        # metadata still fired once each.
        assert metrics.jobs_claimed == 1
        assert metrics.completions == 1
        assert metrics.retries == 1
        assert metrics.terminal_failures == 1
        assert metrics.stale_reclaims == 1


class TestLoggingSubscriberSchemaDrift:
    def test_never_depends_on_any_particular_metadata_shape(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        shapes: list[dict[str, object]] = [
            {},
            {"stage": "ocr"},
            {"nested": {"a": [1, 2, {"b": None}]}},
            {"weird_key": object()},
            {str(i): i for i in range(20)},
        ]
        with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
            for shape in shapes:
                _logging_subscriber(
                    Event(
                        event_type=EventType.JOB_COMPLETED,
                        job_id=str(uuid.uuid4()),
                        document_id=None,
                        metadata=shape,
                    )
                )
        assert len([r for r in caplog.records if "event:" in r.message]) == len(shapes)


# --- 4. Forbid hard coupling in subscribers (structural, AST-level) -----


def _subscript_targets_metadata(node: ast.Subscript) -> bool:
    return isinstance(node.value, ast.Attribute) and node.value.attr == "metadata"


def _find_direct_metadata_indexing(module: ModuleType) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _subscript_targets_metadata(node):
            violations.append(ast.dump(node))
    return violations


def test_metrics_subscriber_never_indexes_metadata_directly() -> None:
    violations = _find_direct_metadata_indexing(metrics_subscriber_module)
    assert violations == [], (
        "metrics_subscriber.py must only read event.metadata via .get(...), "
        f"found direct subscript access: {violations}"
    )


def test_logging_subscriber_never_indexes_metadata_directly() -> None:
    violations = _find_direct_metadata_indexing(logging_subscriber_module)
    assert violations == [], (
        "logging_subscriber.py must only read event.metadata via .get(...), "
        f"found direct subscript access: {violations}"
    )


def test_metrics_subscriber_only_calls_get_on_metadata() -> None:
    """Positive-direction complement to the AST-based negative check
    above: confirms metadata access actually goes through `.get(...)`
    somewhere (i.e. the subscriber isn't simply ignoring metadata
    entirely), not just that bracket-indexing is absent.
    """
    source = inspect.getsource(metrics_subscriber_module)
    assert "metadata.get(" in source
    assert "metadata[" not in source


# --- 5 & 6. Event evolution / backward-forward compatibility ------------


def test_unknown_metadata_keys_do_not_break_the_real_subscribers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    register_default_subscribers()
    event = _event(
        EventType.JOB_COMPLETED,
        duration_ms=10.0,
        # A hypothetical future field no subscriber has ever heard of:
        trace_id="future-tracing-system-field",
        schema_version=2,
    )

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        emit_event(event)  # must not raise despite the unknown fields

    assert metrics.completions == 1
    assert metrics.stage_summary("job_total") is not None
    assert any("event: job_completed" in r.message for r in caplog.records)


def test_an_event_can_be_extended_with_new_fields_without_touching_subscribers() -> None:
    """Simulates a future producer adding a brand-new metadata field to an
    existing EventType — proves no subscriber code needs to change (or
    even know about it) for the system to keep working.
    """
    register_default_subscribers()

    baseline = _event(EventType.PIPELINE_STAGE_COMPLETED, stage="ocr", duration_ms=50.0)
    extended = _event(
        EventType.PIPELINE_STAGE_COMPLETED,
        stage="ocr",
        duration_ms=75.0,
        confidence_snapshot={"min": 0.1, "max": 0.9},  # a field added "later"
    )

    emit_event(baseline)
    emit_event(extended)

    summary = metrics.stage_summary("ocr")
    assert summary is not None
    assert summary.count == 2


def test_old_events_missing_metadata_and_new_events_with_extra_metadata_both_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Backward/forward/mixed compatibility in one dispatch sequence: an
    "old" event (no metadata at all), a "new" event (extra fields), and
    an "in-between" event (partial fields) must all be handled correctly,
    back to back, without one poisoning the next.
    """
    import logging

    register_default_subscribers()

    old_event = Event(event_type=EventType.JOB_RETRYING, job_id=str(uuid.uuid4()), document_id=None)
    in_between_event = _event(EventType.JOB_RETRYING, duration_ms=20.0)
    new_event = _event(
        EventType.JOB_RETRYING,
        duration_ms=30.0,
        retry_reason_code="RATE_LIMITED",
        attempt_number=2,
    )

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        emit_event(old_event)
        emit_event(in_between_event)
        emit_event(new_event)

    assert metrics.retries == 3
    # Only the two events with duration_ms contribute a job_total sample.
    summary = metrics.stage_summary("job_total")
    assert summary is not None
    assert summary.count == 2
    assert len([r for r in caplog.records if "event: job_retrying" in r.message]) == 3


# --- 7. Core dispatch has no metadata-shape dependency -------------------


def test_event_core_never_reads_metadata_contents() -> None:
    """modules.processing.events must not know or care what's inside
    metadata — it only ever passes the dict through, never indexes it.

    AST-based, not a substring search: events.py's own docstring
    *describes* the "never metadata['...']" rule in prose, which would
    false-positive a naive `"metadata[" not in source` check (this was
    caught while writing this test — see the identical false positive
    this project's Increment 8 purity test hit for the same reason).
    """
    import modules.processing.events as events_module

    violations = _find_direct_metadata_indexing(events_module)
    assert violations == []
    # It doesn't even need .get(...) — it never reads metadata at all.
    assert "metadata.get(" not in inspect.getsource(events_module)
