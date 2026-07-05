"""ADR-0030: scoring is pure math, no pipeline/DB/network -- every
TP/FP/FN/TN combination for both field-level and PHI-level scoring is
exercised directly.
"""

import pytest

from modules.evaluation.scoring import (
    aggregate_field_metrics,
    aggregate_phi_metrics,
    fuzzy_ratio,
    score_fields,
    score_phi,
)


def test_score_fields_exact_match_is_a_true_positive() -> None:
    outcomes = score_fields({"patient_name": "Jordan Rivera"}, {"patient_name": "Jordan Rivera"})
    assert outcomes == {"patient_name": (1, 0, 0)}


def test_score_fields_missing_prediction_is_a_false_negative_only() -> None:
    outcomes = score_fields({"mrn": "MRN-118223"}, {})
    assert outcomes == {"mrn": (0, 0, 1)}


def test_score_fields_wrong_value_is_false_negative_and_false_positive() -> None:
    outcomes = score_fields({"mrn": "MRN-118223"}, {"mrn": "MRN-999999"})
    assert outcomes == {"mrn": (0, 1, 1)}


def test_score_fields_hallucinated_field_is_a_false_positive_only() -> None:
    outcomes = score_fields({}, {"mrn": "MRN-000000"})
    assert outcomes == {"mrn": (0, 1, 0)}


def test_score_fields_correctly_absent_field_contributes_nothing() -> None:
    # Neither side mentions "date_of_birth" -- it shouldn't appear at all.
    outcomes = score_fields({"mrn": "MRN-1"}, {"mrn": "MRN-1"})
    assert "date_of_birth" not in outcomes


def test_score_fields_whitespace_only_difference_still_matches() -> None:
    outcomes = score_fields({"mrn": "MRN-1"}, {"mrn": "  MRN-1  "})
    assert outcomes == {"mrn": (1, 0, 0)}


def test_aggregate_field_metrics_sums_across_cases_and_adds_overall_row() -> None:
    per_case = [
        {"patient_name": (1, 0, 0), "mrn": (0, 1, 1)},
        {"patient_name": (0, 0, 1), "mrn": (1, 0, 0)},
    ]
    metrics = aggregate_field_metrics(per_case)
    by_name = {m.field_name: m for m in metrics}

    assert by_name["patient_name"].true_positives == 1
    assert by_name["patient_name"].false_negatives == 1
    assert by_name["mrn"].true_positives == 1
    assert by_name["mrn"].false_positives == 1
    assert by_name["mrn"].false_negatives == 1

    overall = by_name["overall"]
    assert overall.true_positives == 2
    assert overall.false_positives == 1
    assert overall.false_negatives == 2
    assert overall.precision == pytest.approx(2 / 3)
    assert overall.recall == pytest.approx(2 / 4)


def test_aggregate_field_metrics_precision_recall_are_zero_with_no_data() -> None:
    metrics = aggregate_field_metrics([{}])
    overall = next(m for m in metrics if m.field_name == "overall")
    assert overall.precision == 0.0
    assert overall.recall == 0.0
    assert overall.f1 == 0.0


def test_score_phi_all_four_outcomes() -> None:
    assert score_phi(expected_flagged=True, predicted_flagged=True) == (1, 0, 0, 0)
    assert score_phi(expected_flagged=False, predicted_flagged=True) == (0, 1, 0, 0)
    assert score_phi(expected_flagged=True, predicted_flagged=False) == (0, 0, 1, 0)
    assert score_phi(expected_flagged=False, predicted_flagged=False) == (0, 0, 0, 1)


def test_aggregate_phi_metrics_computes_precision_recall_f1() -> None:
    outcomes = [
        (1, 0, 0, 0),
        (0, 1, 0, 0),
        (1, 0, 0, 0),
        (0, 0, 0, 1),
    ]
    metrics = aggregate_phi_metrics(outcomes)
    assert metrics.true_positives == 2
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 0
    assert metrics.true_negatives == 1
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == 1.0


def test_fuzzy_ratio_identical_strings_is_one() -> None:
    assert fuzzy_ratio("Jordan Rivera", "Jordan Rivera") == 1.0


def test_fuzzy_ratio_ignores_surrounding_whitespace() -> None:
    assert fuzzy_ratio("Jordan Rivera", "  Jordan Rivera  ") == 1.0


def test_fuzzy_ratio_completely_different_strings_is_low() -> None:
    assert fuzzy_ratio("Jordan Rivera", "MRN-118223") < 0.3
