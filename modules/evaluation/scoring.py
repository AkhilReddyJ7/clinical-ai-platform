import difflib

from modules.evaluation.schemas import (
    FieldMetrics,
    PHIMetrics,
    RetrievalMetrics,
    RetrievalQueryResult,
)

FUZZY_MATCH_THRESHOLD = 0.85


def fuzzy_ratio(expected: str, predicted: str) -> float:
    """Diagnostic only (ADR-0030) -- never substitutes for exact match in
    the headline field metrics, only helps distinguish formatting noise
    from a genuine miss when reading a report by hand.
    """
    return difflib.SequenceMatcher(None, expected.strip(), predicted.strip()).ratio()


def score_fields(
    expected: dict[str, str], predicted: dict[str, str]
) -> dict[str, tuple[int, int, int]]:
    """Per-field (tp, fp, fn) for one case, over the union of field names
    either side mentions. A wrong value that was actually substituted
    counts as both a false negative (the expected value wasn't produced)
    and a false positive (a wrong value was confidently asserted) -- the
    standard slot-filling treatment (ADR-0030).
    """
    outcomes: dict[str, tuple[int, int, int]] = {}
    for name in set(expected) | set(predicted):
        expected_value = expected.get(name)
        predicted_value = predicted.get(name)
        if expected_value is not None:
            if predicted_value is not None and predicted_value.strip() == expected_value.strip():
                outcomes[name] = (1, 0, 0)
            else:
                outcomes[name] = (0, 1 if predicted_value is not None else 0, 1)
        else:
            outcomes[name] = (0, 1 if predicted_value is not None else 0, 0)
    return outcomes


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def aggregate_field_metrics(
    per_case: list[dict[str, tuple[int, int, int]]],
) -> list[FieldMetrics]:
    """One FieldMetrics row per field name seen across all cases, plus a
    final "overall" row summing across every field name.
    """
    totals: dict[str, list[int]] = {}
    for case in per_case:
        for name, (tp, fp, fn) in case.items():
            bucket = totals.setdefault(name, [0, 0, 0])
            bucket[0] += tp
            bucket[1] += fp
            bucket[2] += fn

    metrics = []
    for name in sorted(totals):
        tp, fp, fn = totals[name]
        precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
        metrics.append(
            FieldMetrics(
                field_name=name,
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                precision=precision,
                recall=recall,
                f1=f1,
            )
        )

    overall_tp = sum(bucket[0] for bucket in totals.values())
    overall_fp = sum(bucket[1] for bucket in totals.values())
    overall_fn = sum(bucket[2] for bucket in totals.values())
    precision, recall, f1 = _precision_recall_f1(overall_tp, overall_fp, overall_fn)
    metrics.append(
        FieldMetrics(
            field_name="overall",
            true_positives=overall_tp,
            false_positives=overall_fp,
            false_negatives=overall_fn,
            precision=precision,
            recall=recall,
            f1=f1,
        )
    )
    return metrics


def score_phi(*, expected_flagged: bool, predicted_flagged: bool) -> tuple[int, int, int, int]:
    """Case-level (tp, fp, fn, tn) -- matches how PHIDetectionValidator
    actually gates a document today (ADR-0011: is_valid for the whole
    text, not per-pattern).
    """
    if expected_flagged and predicted_flagged:
        return (1, 0, 0, 0)
    if predicted_flagged:
        return (0, 1, 0, 0)
    if expected_flagged:
        return (0, 0, 1, 0)
    return (0, 0, 0, 1)


def dedupe_ranked(ids: list[str]) -> list[str]:
    """Order-preserving first-occurrence dedup. Retrieval returns ranked
    *chunks*; relevance labels live at the *document* level (ADR-0037), so
    a multi-chunk document collapses to its best-ranked chunk's position.
    """
    seen: set[str] = set()
    deduped = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def recall_at_k(ranked: list[str], relevant: list[str], k: int) -> float:
    """|relevant found in top k| / |relevant|. Callers must not pass an
    empty relevant set -- no-answer queries are informational only and
    never scored (ADR-0037).
    """
    if not relevant:
        raise ValueError("recall_at_k requires a non-empty relevant set")
    relevant_set = set(relevant)
    return len(relevant_set & set(ranked[:k])) / len(relevant_set)


def hit_at_k(ranked: list[str], relevant: list[str], k: int) -> bool:
    """Did *any* relevant document appear in the top k. With multiple
    relevant docs recall@1 is capped at 1/|relevant|, so this answers the
    separate question "was anything useful surfaced at all".
    """
    if not relevant:
        raise ValueError("hit_at_k requires a non-empty relevant set")
    return bool(set(relevant) & set(ranked[:k]))


def reciprocal_rank(ranked: list[str], relevant: list[str]) -> float:
    """1 / (1-based rank of the first relevant doc); 0.0 when no relevant
    doc was retrieved within the ranking at all. Tie-breaking between
    equal scores is owned by the vector store, not scored here.
    """
    if not relevant:
        raise ValueError("reciprocal_rank requires a non-empty relevant set")
    relevant_set = set(relevant)
    for position, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant_set:
            return 1.0 / position
    return 0.0


def aggregate_retrieval_metrics(results: list[RetrievalQueryResult]) -> RetrievalMetrics:
    """Arithmetic means over already-scored queries. Callers pass only
    queries with a non-empty relevant set; no-answer queries never reach
    any denominator (ADR-0037).
    """
    if not results:
        return RetrievalMetrics(
            scored_query_count=0,
            mrr=0.0,
            recall_at_1=0.0,
            recall_at_5=0.0,
            hit_rate_at_1=0.0,
            hit_rate_at_5=0.0,
        )
    count = len(results)
    return RetrievalMetrics(
        scored_query_count=count,
        mrr=sum(r.reciprocal_rank for r in results) / count,
        recall_at_1=sum(r.recall_at_1 for r in results) / count,
        recall_at_5=sum(r.recall_at_5 for r in results) / count,
        hit_rate_at_1=sum(1 for r in results if r.hit_at_1) / count,
        hit_rate_at_5=sum(1 for r in results if r.hit_at_5) / count,
    )


def aggregate_phi_metrics(outcomes: list[tuple[int, int, int, int]]) -> PHIMetrics:
    tp = sum(o[0] for o in outcomes)
    fp = sum(o[1] for o in outcomes)
    fn = sum(o[2] for o in outcomes)
    tn = sum(o[3] for o in outcomes)
    precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
    return PHIMetrics(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=f1,
    )
