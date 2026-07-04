"""Job-attempt failure classification signal (ADR-0023).

ADR-0023 already defines the classification a job-attempt failure falls
into: transient (eligible for retry within budget) or terminal (fails the
job immediately). This module does not add a new classification scheme —
it gives process_job implementations a concrete way to signal which of
those two already-defined buckets a given failure belongs to, so the
worker loop's dispatch (Increment 4) has something to act on.

Any exception other than TransientProcessingError is treated as
terminal, matching ADR-0023's conservative stance on unclassified
failures (e.g. a malformed model response: "classified terminal by
default... avoids spending retries on a systemic problem").
"""


class TransientProcessingError(Exception):
    """A job-attempt failure ADR-0023 classifies as transient (retryable)."""


class TerminalProcessingError(Exception):
    """A job-attempt failure ADR-0023 classifies as terminal (non-retryable)."""


def is_retryable(error: BaseException) -> bool:
    """True only for failures explicitly signaled as transient."""
    return isinstance(error, TransientProcessingError)
