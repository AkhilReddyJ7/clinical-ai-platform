from modules.ocr.base import ExtractionOutput
from modules.validation.phi import PHIDetectionValidator


def _extraction(raw_text: str) -> ExtractionOutput:
    return ExtractionOutput(raw_text=raw_text, fields={}, confidence=0.9)


def test_flags_ssn_like_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("patient ssn: 123-45-6789"))
    assert not result.is_valid
    assert any("SSN" in issue for issue in result.issues)


def test_flags_space_separated_ssn_like_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("patient ssn 123 45 6789 on file"))
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


def test_flags_ip_address_pattern() -> None:
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("accessed from 192.168.1.104 during visit"))
    assert not result.is_valid
    assert any("IP address" in issue for issue in result.issues)


def test_does_not_flag_invalid_ip_octets() -> None:
    # 999 isn't a valid octet — shouldn't false-positive on any dotted
    # number sequence, only ones that are actually IP-shaped.
    validator = PHIDetectionValidator()
    result = validator.validate(_extraction("ratio was 999.999.999.999 in the report"))
    assert result.is_valid


def test_flags_credit_card_number_regardless_of_separator_style() -> None:
    validator = PHIDetectionValidator()
    for text in (
        "card 4111-1111-1111-1111 on file",
        "card 4111 1111 1111 1111 on file",
        "card 4111111111111111 on file",
    ):
        result = validator.validate(_extraction(text))
        assert not result.is_valid, text
        assert any("credit card" in issue for issue in result.issues)


def test_does_not_flag_person_names_or_addresses() -> None:
    # Documents an intentional scope boundary, not an oversight: no NER,
    # so names/addresses have no reliable regex shape to match on. See
    # docs/adr/0015.
    validator = PHIDetectionValidator()
    result = validator.validate(
        _extraction(
            "Patient: Jonathan Michael Whitfield, resides at "
            "4821 Maple Ridge Lane, Springfield, IL 62704"
        )
    )
    assert result.is_valid


def test_does_not_flag_dates_or_unformatted_digit_runs() -> None:
    # Also intentional: every clinical note has non-DOB dates (visit date,
    # admission date, ...), and a bare 9/10-digit run is indistinguishable
    # from countless benign IDs without context a regex can't see. Adding
    # either would trade signal for noise. See docs/adr/0015.
    validator = PHIDetectionValidator()
    result = validator.validate(
        _extraction("seen on 03/14/1975, ssn on file 123456789, call 5551234567")
    )
    assert result.is_valid


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
