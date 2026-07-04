import pytest

from modules.processing.metrics import WorkerMetrics


@pytest.fixture
def worker_metrics() -> WorkerMetrics:
    return WorkerMetrics()


def test_counters_start_at_zero(worker_metrics: WorkerMetrics) -> None:
    snapshot = worker_metrics.snapshot()
    assert snapshot["jobs_claimed"] == 0
    assert snapshot["completions"] == 0
    assert snapshot["retries"] == 0
    assert snapshot["terminal_failures"] == 0
    assert snapshot["stale_reclaims"] == 0


def test_record_claim_increments_jobs_claimed(worker_metrics: WorkerMetrics) -> None:
    worker_metrics.record_claim()
    worker_metrics.record_claim()
    assert worker_metrics.jobs_claimed == 2


def test_record_completion_increments_completions(worker_metrics: WorkerMetrics) -> None:
    worker_metrics.record_completion()
    assert worker_metrics.completions == 1


def test_record_retry_increments_retries(worker_metrics: WorkerMetrics) -> None:
    worker_metrics.record_retry()
    assert worker_metrics.retries == 1


def test_record_terminal_failure_increments_terminal_failures(
    worker_metrics: WorkerMetrics,
) -> None:
    worker_metrics.record_terminal_failure()
    assert worker_metrics.terminal_failures == 1


def test_record_stale_reclaim_increments_stale_reclaims(worker_metrics: WorkerMetrics) -> None:
    # Reserved hook (ADR-0024): nothing in this codebase calls this yet,
    # since stale-job detection itself is unimplemented — this proves the
    # counter itself works, ready for that future wiring.
    worker_metrics.record_stale_reclaim()
    assert worker_metrics.stale_reclaims == 1


def test_stage_summary_is_none_for_an_unrecorded_stage(worker_metrics: WorkerMetrics) -> None:
    assert worker_metrics.stage_summary("ocr") is None


def test_stage_summary_computes_min_avg_max(worker_metrics: WorkerMetrics) -> None:
    for seconds in (0.1, 0.3, 0.2):
        worker_metrics.record_stage_duration("ocr", seconds)

    summary = worker_metrics.stage_summary("ocr")

    assert summary is not None
    assert summary.count == 3
    assert summary.min_seconds == pytest.approx(0.1)
    assert summary.max_seconds == pytest.approx(0.3)
    assert summary.avg_seconds == pytest.approx(0.2)


def test_stage_durations_are_tracked_independently_per_stage(
    worker_metrics: WorkerMetrics,
) -> None:
    worker_metrics.record_stage_duration("ocr", 1.0)
    worker_metrics.record_stage_duration("validation", 0.01)

    assert worker_metrics.stage_summary("ocr").count == 1  # type: ignore[union-attr]
    assert worker_metrics.stage_summary("validation").count == 1  # type: ignore[union-attr]
    assert worker_metrics.stage_summary("field_extraction") is None


def test_snapshot_includes_stage_summaries(worker_metrics: WorkerMetrics) -> None:
    worker_metrics.record_stage_duration("ocr", 0.5)

    snapshot = worker_metrics.snapshot()

    assert "ocr" in snapshot["stages"]  # type: ignore[operator]


def test_reset_clears_everything(worker_metrics: WorkerMetrics) -> None:
    worker_metrics.record_claim()
    worker_metrics.record_completion()
    worker_metrics.record_retry()
    worker_metrics.record_terminal_failure()
    worker_metrics.record_stale_reclaim()
    worker_metrics.record_stage_duration("ocr", 0.5)

    worker_metrics.reset()

    assert worker_metrics.snapshot() == {
        "jobs_claimed": 0,
        "completions": 0,
        "retries": 0,
        "terminal_failures": 0,
        "stale_reclaims": 0,
        "stages": {},
    }
