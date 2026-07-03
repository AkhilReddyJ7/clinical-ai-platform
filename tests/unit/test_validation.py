from modules.ocr.base import ExtractionOutput
from modules.validation.rules import RequiredFieldsValidator


def test_validation_passes_when_required_fields_present() -> None:
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

    assert result.is_valid
    assert result.issues == []


def test_validation_fails_when_fields_missing() -> None:
    validator = RequiredFieldsValidator()
    extraction = ExtractionOutput(raw_text="", fields={}, confidence=0.0)

    result = validator.validate(extraction)

    assert not result.is_valid
    assert len(result.issues) == 3
