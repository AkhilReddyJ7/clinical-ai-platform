import difflib

from modules.evaluation.schemas import FieldMetrics, PHIMetrics

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
