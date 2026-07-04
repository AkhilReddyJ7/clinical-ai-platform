import math

import pytest

from modules.processing.pipeline import (
    _aggregate_confidence,
    _build_metadata,
    _categorize_issues,
    _compute_field_confidence,
    _field_plausibility,
)


def test_aggregate_confidence_is_geometric_mean() -> None:
    assert _aggregate_confidence(0.8, 0.5) == pytest.approx(math.sqrt(0.8 * 0.5))


def test_aggregate_confidence_penalizes_a_single_weak_stage_more_than_an_average_would() -> None:
    geometric = _aggregate_confidence(0.95, 0.05)
    arithmetic = (0.95 + 0.05) / 2

    assert geometric < arithmetic
    assert geometric == pytest.approx(math.sqrt(0.95 * 0.05))


def test_aggregate_confidence_is_zero_if_either_stage_is_zero() -> None:
    assert _aggregate_confidence(0.0, 0.9) == 0.0
    assert _aggregate_confidence(0.9, 0.0) == 0.0


def test_aggregate_confidence_clamps_negative_inputs() -> None:
    assert _aggregate_confidence(-1.0, 0.5) == 0.0


@pytest.mark.parametrize(
    "field_name,value,expected",
    [
        ("date_of_birth", "1990-01-01", 1.0),
        ("date_of_birth", "January 5, 1990", 1.0),
        ("date_of_birth", "not a date at all", 0.5),
        ("patient_name", "Jordan Rivera", 1.0),
        ("patient_name", "asdf1234", 0.5),
        ("mrn", "MOCK-000123", 1.0),
        ("mrn", "1", 0.5),
        ("patient_name", "   ", 0.0),
        ("mrn", "", 0.0),
    ],
)
def test_field_plausibility_scores(field_name: str, value: str, expected: float) -> None:
    assert _field_plausibility(field_name, value) == expected


def test_compute_field_confidence_scales_by_plausibility() -> None:
    fields = {"patient_name": "Jordan Rivera", "mrn": "1"}

    result = _compute_field_confidence(fields, base_confidence=0.8)

    assert result["patient_name"] == pytest.approx(0.8)
    assert result["mrn"] == pytest.approx(0.4)


def test_compute_field_confidence_is_empty_for_no_fields() -> None:
    assert _compute_field_confidence({}, base_confidence=0.9) == {}


def test_categorize_issues_maps_missing_field_issues() -> None:
    categories = _categorize_issues(["missing required field: mrn"])
    assert categories == {"missing_required_fields"}


def test_categorize_issues_maps_phi_and_extraction_failures_to_invalid_data() -> None:
    categories = _categorize_issues(["phi: possible SSN-like number detected in extracted text"])
    assert categories == {"invalid_data"}

    categories = _categorize_issues(["extraction failed: corrupted input"])
    assert categories == {"invalid_data"}


def test_categorize_issues_combines_multiple_categories() -> None:
    categories = _categorize_issues(
        ["missing required field: mrn", "phi: possible email address detected in extracted text"]
    )
    assert categories == {"missing_required_fields", "invalid_data"}


def test_categorize_issues_is_empty_for_no_issues() -> None:
    assert _categorize_issues([]) == set()


def test_build_metadata_flags_low_confidence_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "low_confidence_threshold", 0.5)

    metadata = _build_metadata(
        confidence=0.3, field_confidence={"mrn": 0.2, "patient_name": 0.9}, issues=[]
    )

    assert metadata["low_confidence"] == "true"
    assert "uncertain_extraction" in metadata["issue_categories"]
    assert metadata["low_confidence_fields"] == "mrn"


def test_build_metadata_does_not_flag_confidence_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "low_confidence_threshold", 0.5)

    metadata = _build_metadata(confidence=0.9, field_confidence={"mrn": 0.9}, issues=[])

    assert metadata["low_confidence"] == "false"
    assert "uncertain_extraction" not in metadata["issue_categories"]
    assert metadata["low_confidence_fields"] == ""


def test_build_metadata_merges_extra_keys() -> None:
    metadata = _build_metadata(
        confidence=0.9, field_confidence={}, issues=[], extra={"outcome": "phi_detected"}
    )
    assert metadata["outcome"] == "phi_detected"
