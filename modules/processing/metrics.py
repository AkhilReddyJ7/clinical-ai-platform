"""In-memory worker/pipeline observability counters (Increment 7).

Lightweight, single-process counters and stage-duration samples — no
Prometheus, no OpenTelemetry, no external system, per this increment's
own constraint. A module-level singleton (the same pattern
shared/logging/logger.py uses for `logger`) so worker.py and pipeline.py
can record into the same counters without threading a metrics object
through every function signature — this is instrumentation, not a new
architectural dependency between them.

Process-local only: each worker instance (process, container, or asyncio
task per Increment 3) has its own independent WorkerMetrics. Aggregating
counts across multiple worker instances would need an external system —
explicitly out of scope here, the same reasoning ADR-0021 already
applied to the queue itself (no new service until a concrete need beats
the boring option).
"""

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class StageSummary:
    count: int
    min_seconds: float
    avg_seconds: float
    max_seconds: float


@dataclass
class WorkerMetrics:
    jobs_claimed: int = 0
    completions: int = 0
    retries: int = 0
    terminal_failures: int = 0
    # Reserved for ADR-0024's stale-job detection/reclaim loop, which
    # remains unimplemented (deferred since Increment 4 — see that ADR's
    # "no implementation yet" consequence). Nothing in this codebase calls
    # record_stale_reclaim yet; it exists now so the future detection
    # loop has a counter to call into rather than inventing one alongside
    # the detection logic itself. Exercised directly in tests, not via
    # any real trigger.
    stale_reclaims: int = 0
    _stage_durations: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def record_claim(self) -> None:
        self.jobs_claimed += 1

    def record_completion(self) -> None:
        self.completions += 1

    def record_retry(self) -> None:
        self.retries += 1

    def record_terminal_failure(self) -> None:
        self.terminal_failures += 1

    def record_stale_reclaim(self) -> None:
        self.stale_reclaims += 1

    def record_stage_duration(self, stage: str, seconds: float) -> None:
        self._stage_durations[stage].append(seconds)

    def stage_summary(self, stage: str) -> StageSummary | None:
        samples = self._stage_durations.get(stage)
        if not samples:
            return None
        return StageSummary(
            count=len(samples),
            min_seconds=min(samples),
            avg_seconds=sum(samples) / len(samples),
            max_seconds=max(samples),
        )

    def snapshot(self) -> dict[str, object]:
        """A plain-dict view of every counter and stage summary — for
        logging a periodic summary or for test assertions, not a second
        source of truth (the dataclass fields above remain authoritative).
        """
        return {
            "jobs_claimed": self.jobs_claimed,
            "completions": self.completions,
            "retries": self.retries,
            "terminal_failures": self.terminal_failures,
            "stale_reclaims": self.stale_reclaims,
            "stages": {stage: self.stage_summary(stage) for stage in self._stage_durations},
        }

    def reset(self) -> None:
        """Test-only: clears every counter and sample back to zero.

        Needed because `metrics` below is a process-wide singleton, and
        the test suite runs in one process — without this, tests would
        observe counts left over from whichever tests ran earlier.
        """
        self.jobs_claimed = 0
        self.completions = 0
        self.retries = 0
        self.terminal_failures = 0
        self.stale_reclaims = 0
        self._stage_durations.clear()


metrics = WorkerMetrics()
