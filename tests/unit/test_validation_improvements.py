from modules.ocr.base import ExtractionOutput
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator


def test_whitespace_only_field_value_is_treated_as_missing() -> None:
    # Previously a false negative for "missing": a whitespace-only value
    # is truthy in Python, so `not extraction.fields.get(field_name)` let
    # it through as if the field were meaningfully present.
    validator = RequiredFieldsValidator()
    extraction = ExtractionOutput(
        raw_text="...",
        fields={"patient_name": "   ", "mrn": "MOCK-000123", "date_of_birth": "1990-01-01"},
        confidence=0.9,
    )

    result = validator.validate(extraction)

    assert result.is_valid is False
    assert "missing required field: patient_name" in result.issues


def test_non_whitespace_field_values_still_pass() -> None:
    # Regression: the strip-based check must not start rejecting
    # legitimately present values that merely have surrounding whitespace
    # trimmed away as part of the check (the stored value is untouched).
    validator = RequiredFieldsValidator()
    extraction = ExtractionOutput(
        raw_text="...",
        fields={
            "patient_name": "Jordan Rivera",
            "mrn": "MOCK-000123",
            "date_of_birth": "1990-01-01",
        },
        confidence=0.9,
    )

    result = validator.validate(extraction)

    assert result.is_valid is True
    assert result.issues == []


def test_partial_document_reports_only_the_fields_actually_missing() -> None:
    validator = RequiredFieldsValidator()
    extraction = ExtractionOutput(
        raw_text="...", fields={"patient_name": "Jordan Rivera"}, confidence=0.4
    )

    result = validator.validate(extraction)

    assert result.is_valid is False
    assert len(result.issues) == 2
    assert "missing required field: mrn" in result.issues
    assert "missing required field: date_of_birth" in result.issues
    assert "missing required field: patient_name" not in result.issues


def test_phi_precheck_and_composite_validator_agree_on_the_same_text() -> None:
    """ADR-0011/0019's precheck (bare PHIDetectionValidator, run before the
    field-extraction LLM call) and the full composite (run after) must
    reach the same PHI verdict for the same raw_text — a real consistency
    property, not just an assumption, since a drift between the two would
    mean the precheck's gate and the final validation could disagree.
    """
    raw_text = "patient contact: jane.doe@example.com, ssn 123-45-6789"
    precheck = PHIDetectionValidator()
    composite = CompositeValidationPipeline([RequiredFieldsValidator(), PHIDetectionValidator()])

    precheck_result = precheck.validate(ExtractionOutput(raw_text=raw_text))
    composite_result = composite.validate(
        ExtractionOutput(raw_text=raw_text, fields={}, confidence=0.0)
    )

    assert precheck_result.is_valid is False
    assert composite_result.is_valid is False
    phi_issues_in_composite = [
        issue for issue in composite_result.issues if issue.startswith("phi:")
    ]
    assert phi_issues_in_composite == precheck_result.issues


def test_phi_precheck_and_composite_validator_agree_on_clean_text() -> None:
    raw_text = "patient seen for routine follow-up visit"
    precheck = PHIDetectionValidator()
    composite = CompositeValidationPipeline([RequiredFieldsValidator(), PHIDetectionValidator()])

    precheck_result = precheck.validate(ExtractionOutput(raw_text=raw_text))
    composite_result = composite.validate(
        ExtractionOutput(
            raw_text=raw_text,
            fields={"patient_name": "A", "mrn": "B", "date_of_birth": "C"},
            confidence=1.0,
        )
    )

    assert precheck_result.is_valid is True
    assert not any(issue.startswith("phi:") for issue in composite_result.issues)
