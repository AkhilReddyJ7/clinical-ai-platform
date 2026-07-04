"""Tests for the pure event-dispatch core (modules.processing.events).

Deliberately uses only plain, hand-written handlers — never the built-in
metrics/logging subscribers (those live under modules.processing.
observability and have their own test files) — so these tests prove the
core works independently of any observability consumer.
"""

import ast
import inspect
import logging
import uuid
from collections.abc import Iterator

import pytest

import modules.processing.events as events_module
from modules.processing.events import (
    Event,
    EventType,
    clear_subscribers,
    emit_event,
    subscribe,
    unsubscribe,
)


@pytest.fixture(autouse=True)
def _clear_subscribers_around_test() -> Iterator[None]:
    # Saves and restores whatever subscribers were already registered
    # (in practice, the registry's defaults, wired up when worker.py/
    # pipeline.py were imported elsewhere in this test session) rather
    # than just clearing — leaving the global list empty afterward would
    # silently break every other test file that depends on the default
    # metrics/logging subscribers actually being active.
    original = list(events_module._subscribers)
    clear_subscribers()
    yield
    clear_subscribers()
    events_module._subscribers.extend(original)


def _event(event_type: EventType = EventType.JOB_CLAIMED, **metadata: object) -> Event:
    return Event(
        event_type=event_type,
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        metadata=metadata,
    )


def test_event_core_has_no_observability_imports() -> None:
    """Structural guarantee (object-graph check): modules.processing.events
    must not import WorkerMetrics, the app logger, or any subscriber —
    that coupling belongs entirely to modules.processing.observability.
    """
    assert not hasattr(events_module, "metrics")
    assert not hasattr(events_module, "logger")
    assert not hasattr(events_module, "_metrics_subscriber")
    assert not hasattr(events_module, "_logging_subscriber")
    assert not hasattr(events_module, "register_default_subscribers")


def test_event_core_has_no_forbidden_imports_at_the_ast_level() -> None:
    """Structural guarantee (AST-level check): parses events.py's actual
    import statements rather than grepping raw text, so an explanatory
    comment/docstring that merely *mentions* a forbidden module (e.g.
    "deliberately not shared.logging.logger") can never produce a false
    positive the way a plain substring search would. Also immune to
    whatever else happens to already be imported elsewhere in the test
    session (unlike checking sys.modules, which every other test file
    importing worker.py/pipeline.py would have already populated
    regardless of what events.py itself does).
    """
    tree = ast.parse(inspect.getsource(events_module))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_prefixes = (
        "modules.processing.metrics",
        "shared.logging",
        "modules.processing.observability",
    )
    for module_name in imported_modules:
        assert not module_name.startswith(
            forbidden_prefixes
        ), f"events.py must not import {module_name!r}"


def test_events_module_does_not_self_register_any_subscribers() -> None:
    # clear_subscribers() (in the autouse fixture) already emptied the
    # list; nothing in this module repopulates it on its own — merely
    # having events.py loaded never registers anything.
    assert events_module._subscribers == []


def test_emit_event_works_with_zero_subscribers() -> None:
    emit_event(_event())  # must not raise


def test_emit_event_dispatches_to_all_subscribers_in_registration_order() -> None:
    calls: list[str] = []
    subscribe(lambda e: calls.append("first"))
    subscribe(lambda e: calls.append("second"))

    emit_event(_event())

    assert calls == ["first", "second"]


def test_a_raising_subscriber_does_not_stop_later_subscribers() -> None:
    calls: list[str] = []

    def bad_subscriber(event: Event) -> None:
        raise RuntimeError("boom")

    subscribe(bad_subscriber)
    subscribe(lambda e: calls.append("still ran"))

    emit_event(_event())

    assert calls == ["still ran"]


def test_a_raising_subscriber_does_not_propagate_to_the_caller() -> None:
    def bad_subscriber(event: Event) -> None:
        raise RuntimeError("boom")

    subscribe(bad_subscriber)

    emit_event(_event())  # must not raise


def test_a_raising_subscriber_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    def bad_subscriber(event: Event) -> None:
        raise RuntimeError("boom")

    subscribe(bad_subscriber)

    with caplog.at_level(logging.ERROR):
        emit_event(_event(EventType.JOB_FAILED))

    assert any("subscriber" in r.message and "raised" in r.message for r in caplog.records)


def test_unsubscribe_stops_a_handler_from_receiving_further_events() -> None:
    calls: list[str] = []

    def handler(event: Event) -> None:
        calls.append("called")

    subscribe(handler)
    emit_event(_event())
    unsubscribe(handler)
    emit_event(_event())

    assert calls == ["called"]


def test_unsubscribe_of_an_unregistered_handler_is_a_no_op() -> None:
    def handler(event: Event) -> None:
        pass

    unsubscribe(handler)  # must not raise


def test_clear_subscribers_removes_everything() -> None:
    subscribe(lambda e: None)
    subscribe(lambda e: None)

    clear_subscribers()

    assert events_module._subscribers == []
