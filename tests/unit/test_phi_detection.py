from modules.ocr.base import ExtractionOutput
from modules.validation.phi import PHIDetectionValidator


def _extraction(raw_text: str) -> ExtractionOutput:
    return ExtractionOutput(raw_text=raw_text, fields={}, confidence=0.9)


def test_flags_ssn_like_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("patient ssn: 123-45-6789"))
    assert not result.is_valid
    assert any("SSN" in issue for issue in result.issues)


def test_flags_email_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("contact: jane.doe@example.com"))
    assert not result.is_valid
    assert any("email" in issue for issue in result.issues)


def test_flags_phone_number_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("call (555) 123-4567 for details"))
    assert not result.is_valid
    assert any("phone" in issue for issue in result.issues)


def test_passes_clean_synthetic_text() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(
        _extraction("patient_name: Jordan Rivera date_of_birth: 1990-07-22 mrn: MOCK-522002")
    )
    assert result.is_valid
    assert result.issues == []


def test_flags_multiple_patterns_in_one_pass() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("ssn 123-45-6789, email test@example.com"))
    assert not result.is_valid
    assert len(result.issues) == 2
