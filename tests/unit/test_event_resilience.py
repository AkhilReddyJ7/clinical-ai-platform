"""Increment 9: proves the event backbone's failure-isolation guarantee
holds under stress, not just in the single-failing-subscriber case
already covered by test_events.py.

No production code is expected to change for this increment — these
tests exercise modules.processing.events.emit_event (and, for the
regression-safety section, the real built-in subscribers) exactly as
they already exist, under harsher conditions: multiple/repeated/
intermittent failures, high volume, and cross-subscriber isolation.
"""

import logging
import uuid
from collections.abc import Iterator

import pytest

import modules.processing.events as events_module
import modules.processing.observability.registry as registry_module
from modules.processing.events import (
    Event,
    EventType,
    clear_subscribers,
    emit_event,
    subscribe,
)
from modules.processing.metrics import metrics
from modules.processing.observability.registry import (
    _reset_for_testing,
    register_default_subscribers,
)
from shared.logging.logger import logger as app_logger


@pytest.fixture(autouse=True)
def _isolate_event_state() -> Iterator[None]:
    # Establishes a *deterministic* baseline (exactly the two defaults
    # registered, _registered=True) rather than capturing-and-restoring
    # whatever state happened to exist — capture/restore is exactly the
    # bug this increment's own "no order dependence" requirement is
    # meant to catch: registry_module._registered is a process-wide flag
    # that worker.py/pipeline.py already set True during collection of
    # *other* test files, so a plain "preserve whatever it was" fixture
    # makes register_default_subscribers() calls in this file silently
    # into no-ops when the full suite runs, while passing when this file
    # runs alone (verified: this exact bug was caught by running both
    # ways before committing). Tests that need a *different* starting
    # point (empty, or "only custom") establish that explicitly for
    # themselves, rather than relying on the fixture's incidental state.
    metrics.reset()
    _reset_for_testing()
    yield
    metrics.reset()
    _reset_for_testing()


def _event(event_type: EventType = EventType.JOB_CLAIMED) -> Event:
    return Event(event_type=event_type, job_id=uuid.uuid4(), document_id=uuid.uuid4())


def _always_fails(event: Event) -> None:
    raise RuntimeError("boom")


# --- 1. Multiple failing subscribers in sequence -----------------------


def test_multiple_failing_subscribers_are_all_invoked_and_each_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fails_a(event: Event) -> None:
        raise ValueError("a failed")

    def fails_b(event: Event) -> None:
        raise TypeError("b failed")

    def fails_c(event: Event) -> None:
        raise RuntimeError("c failed")

    subscribe(fails_a)
    subscribe(fails_b)
    subscribe(fails_c)

    with caplog.at_level(logging.ERROR):
        emit_event(_event())  # must not raise despite three failures

    raised_logs = [r for r in caplog.records if "raised" in r.message]
    assert len(raised_logs) == 3


def test_all_subscribers_failing_still_completes_emit_event() -> None:
    subscribe(_always_fails)
    subscribe(_always_fails)
    subscribe(_always_fails)

    emit_event(_event())  # must return normally, not raise


# --- 2. Mixed success/failure subscriber chains -------------------------


def test_mixed_success_and_failure_chain_runs_every_successful_handler() -> None:
    calls: list[str] = []

    def fails_1(event: Event) -> None:
        raise RuntimeError("first failure")

    def succeeds_1(event: Event) -> None:
        calls.append("s1")

    def fails_2(event: Event) -> None:
        raise RuntimeError("second failure")

    def succeeds_2(event: Event) -> None:
        calls.append("s2")

    def fails_3(event: Event) -> None:
        raise RuntimeError("third failure")

    for handler in (fails_1, succeeds_1, fails_2, succeeds_2, fails_3):
        subscribe(handler)

    emit_event(_event())

    assert calls == ["s1", "s2"]


# --- 3. Repeated failures across multiple events ------------------------


def test_an_always_failing_subscriber_never_breaks_subsequent_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    successful_calls = 0

    def counter(event: Event) -> None:
        nonlocal successful_calls
        successful_calls += 1

    subscribe(_always_fails)
    subscribe(counter)

    with caplog.at_level(logging.ERROR):
        for _ in range(50):
            emit_event(_event())

    assert successful_calls == 50
    assert len([r for r in caplog.records if "raised" in r.message]) == 50


# --- 4A. High-volume dispatch --------------------------------------------


def test_high_volume_dispatch_yields_deterministic_subscriber_counts() -> None:
    call_count = 0

    def counter(event: Event) -> None:
        nonlocal call_count
        call_count += 1

    clear_subscribers()  # isolate from the fixture's default baseline
    subscribe(counter)

    for _ in range(1000):
        emit_event(_event())

    assert call_count == 1000
    # No growth, no corruption: still exactly the one subscriber we added.
    assert events_module._subscribers == [counter]


# --- 4B. Exception storms ------------------------------------------------


def test_exception_storm_does_not_degrade_a_working_sibling_subscriber() -> None:
    successful_calls = 0

    def counter(event: Event) -> None:
        nonlocal successful_calls
        successful_calls += 1

    clear_subscribers()  # isolate from the fixture's default baseline
    subscribe(_always_fails)
    subscribe(counter)

    for _ in range(1000):
        emit_event(_event())

    assert successful_calls == 1000
    assert events_module._subscribers == [_always_fails, counter]


# --- 4C. Mixed intermittent behavior -------------------------------------


def test_intermittent_failures_still_yield_a_consistent_final_state() -> None:
    intermittent_successes = 0
    always_succeeds_calls = 0
    call_number = 0

    def intermittent(event: Event) -> None:
        nonlocal intermittent_successes, call_number
        call_number += 1
        if call_number % 3 == 0:
            raise RuntimeError("intermittent failure")
        intermittent_successes += 1

    def always_succeeds(event: Event) -> None:
        nonlocal always_succeeds_calls
        always_succeeds_calls += 1

    subscribe(intermittent)
    subscribe(always_succeeds)

    total_events = 30
    for _ in range(total_events):
        emit_event(_event())

    # Deterministic: every 3rd call fails (10 of 30), so exactly 20 succeed.
    assert intermittent_successes == 20
    # The sibling subscriber is entirely unaffected by intermittent's
    # failures — it ran on every single event, no exceptions, no drops.
    assert always_succeeds_calls == total_events


# --- 5. State consistency guarantee --------------------------------------


def test_subscriber_list_is_unchanged_after_dispatch_with_failures() -> None:
    def succeeds(event: Event) -> None:
        pass

    subscribe(succeeds)
    subscribe(_always_fails)
    snapshot_before = list(events_module._subscribers)

    emit_event(_event())
    emit_event(_event())

    assert events_module._subscribers == snapshot_before


def test_a_subscriber_that_resubscribes_during_dispatch_does_not_affect_the_current_dispatch() -> (
    None
):
    extra_calls = 0

    def extra(event: Event) -> None:
        nonlocal extra_calls
        extra_calls += 1

    def resubscribes(event: Event) -> None:
        subscribe(extra)  # a subscriber misbehaving by mutating state mid-dispatch

    clear_subscribers()  # isolate from the fixture's default baseline
    subscribe(resubscribes)

    emit_event(_event())
    # emit_event iterates a snapshot taken before any handler ran, so
    # `extra` — added *during* this dispatch — could not have been called
    # in it.
    assert extra_calls == 0
    assert events_module._subscribers == [resubscribes, extra]

    emit_event(_event())
    # On the next call, `extra` is a normal subscriber and runs once. It
    # also re-subscribes nothing itself, so no runaway growth.
    assert extra_calls == 1
    assert len(events_module._subscribers) == 3  # resubscribes, extra, extra (2nd add)


def test_registry_registration_state_is_unaffected_by_subscriber_failures() -> None:
    register_default_subscribers()
    assert registry_module._registered is True
    subscriber_count_before = len(events_module._subscribers)

    subscribe(_always_fails)
    for _ in range(10):
        emit_event(_event())

    # No silent re-registration, no duplicate injection triggered by the
    # failures above.
    assert registry_module._registered is True
    assert len(events_module._subscribers) == subscriber_count_before + 1


# --- Regression: metrics/logging subscriber isolation from each other ---


def test_metrics_subscriber_failure_does_not_prevent_logging_subscriber(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _reset_for_testing()
    monkeypatch.setattr(
        metrics, "record_claim", lambda: (_ for _ in ()).throw(RuntimeError("metrics broke"))
    )

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        emit_event(_event(EventType.JOB_CLAIMED))

    assert any("event: job_claimed" in r.message for r in caplog.records)


def test_logging_subscriber_failure_does_not_prevent_metrics_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_for_testing()
    monkeypatch.setattr(
        app_logger, "info", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("logging broke"))
    )

    emit_event(_event(EventType.JOB_CLAIMED))

    assert metrics.jobs_claimed == 1


# --- 6. Observability remains optional -----------------------------------


def test_system_functions_with_only_custom_subscribers() -> None:
    calls: list[str] = []
    clear_subscribers()  # genuinely "only" custom, not defaults-plus-custom
    subscribe(lambda e: calls.append("custom"))

    emit_event(_event())

    assert calls == ["custom"]
    assert len(events_module._subscribers) == 1


def test_system_functions_with_only_default_subscribers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The fixture already establishes exactly the two defaults; this
    # extra call confirms register_default_subscribers() is a genuine
    # no-op here, not a second subscription.
    register_default_subscribers()
    assert len(events_module._subscribers) == 2

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        emit_event(_event(EventType.JOB_CLAIMED))

    assert metrics.jobs_claimed == 1
    assert any("event: job_claimed" in r.message for r in caplog.records)


def test_system_functions_with_zero_subscribers() -> None:
    clear_subscribers()  # genuinely zero, not the fixture's default baseline
    assert events_module._subscribers == []
    emit_event(_event())  # must not raise
