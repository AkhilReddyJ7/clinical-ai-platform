import pytest

from modules.evaluation.schemas import RetrievalQueryResult
from modules.evaluation.scoring import (
    aggregate_retrieval_metrics,
    dedupe_ranked,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)


def _result(*, rr: float, r1: float, r5: float, h1: bool, h5: bool) -> RetrievalQueryResult:
    return RetrievalQueryResult(
        query_id="q",
        ranked_doc_ids=[],
        relevant_doc_ids=["d"],
        reciprocal_rank=rr,
        recall_at_1=r1,
        recall_at_5=r5,
        hit_at_1=h1,
        hit_at_5=h5,
        top_score=None,
    )


def test_dedupe_ranked_keeps_first_occurrence_order() -> None:
    assert dedupe_ranked(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_dedupe_ranked_empty() -> None:
    assert dedupe_ranked([]) == []


def test_recall_at_k_full_and_partial() -> None:
    assert recall_at_k(["a", "b"], ["a"], 1) == 1.0
    # one of two relevant docs in top 1 -> capped at 0.5
    assert recall_at_k(["a", "x", "y"], ["a", "b"], 1) == 0.5
    assert recall_at_k(["a", "x", "b"], ["a", "b"], 5) == 1.0
    assert recall_at_k(["x", "y", "z"], ["a"], 5) == 0.0


def test_recall_at_k_rejects_empty_relevant() -> None:
    with pytest.raises(ValueError):
        recall_at_k(["a"], [], 5)


def test_hit_at_k_diverges_from_recall_with_multiple_relevant() -> None:
    ranked = ["a", "x", "y"]
    relevant = ["a", "b"]
    assert recall_at_k(ranked, relevant, 1) == 0.5
    assert hit_at_k(ranked, relevant, 1) is True


def test_hit_at_k_rejects_empty_relevant() -> None:
    with pytest.raises(ValueError):
        hit_at_k(["a"], [], 1)


def test_reciprocal_rank_positions() -> None:
    assert reciprocal_rank(["a", "b"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "a"], ["a"]) == 0.5
    assert reciprocal_rank(["x", "y"], ["a"]) == 0.0


def test_reciprocal_rank_uses_first_relevant() -> None:
    assert reciprocal_rank(["x", "b", "a"], ["a", "b"]) == 0.5


def test_reciprocal_rank_rejects_empty_relevant() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank(["a"], [])


def test_aggregate_retrieval_metrics_means() -> None:
    results = [
        _result(rr=1.0, r1=1.0, r5=1.0, h1=True, h5=True),
        _result(rr=0.5, r1=0.0, r5=1.0, h1=False, h5=True),
        _result(rr=0.0, r1=0.0, r5=0.0, h1=False, h5=False),
    ]
    metrics = aggregate_retrieval_metrics(results)
    assert metrics.scored_query_count == 3
    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.recall_at_1 == pytest.approx(1 / 3)
    assert metrics.recall_at_5 == pytest.approx(2 / 3)
    assert metrics.hit_rate_at_1 == pytest.approx(1 / 3)
    assert metrics.hit_rate_at_5 == pytest.approx(2 / 3)


def test_aggregate_retrieval_metrics_empty_input_is_all_zeros() -> None:
    metrics = aggregate_retrieval_metrics([])
    assert metrics.scored_query_count == 0
    assert metrics.mrr == 0.0
    assert metrics.recall_at_5 == 0.0
    assert metrics.hit_rate_at_5 == 0.0
