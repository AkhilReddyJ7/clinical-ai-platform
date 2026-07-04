"""The one place default observability subscribers are wired up.

worker.py and pipeline.py each call register_default_subscribers() once,
at module-import time (their "startup context") — the event core
(modules.processing.events) never auto-registers anything itself, and
emit_event never registers anything either. register_default_subscribers
is idempotent specifically because both worker.py and pipeline.py call
it independently: without that, importing both (as every real run does)
would double-subscribe both handlers and double-count every event.
"""

from modules.processing.events import clear_subscribers, subscribe
from modules.processing.observability.logging_subscriber import _logging_subscriber
from modules.processing.observability.metrics_subscriber import _metrics_subscriber

_registered = False


def register_default_subscribers() -> None:
    """Subscribes the metrics and logging consumers, exactly once.

    Safe to call from multiple modules (worker.py and pipeline.py both
    do, independently) — a second call is a no-op, not a second
    subscription.
    """
    global _registered
    if _registered:
        return
    subscribe(_metrics_subscriber)
    subscribe(_logging_subscriber)
    _registered = True


def _reset_for_testing() -> None:
    """Test-only: clears every subscriber (defaults included) and
    re-registers exactly the two defaults, undoing anything a test
    added, removed, or left mid-registration.
    """
    global _registered
    clear_subscribers()
    _registered = False
    register_default_subscribers()
