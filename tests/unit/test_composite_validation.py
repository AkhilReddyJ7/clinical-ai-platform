from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline
from modules.validation.composite import CompositeValidationPipeline


class _AlwaysValid(ValidationPipeline):
    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        return ValidationOutput(is_valid=True, issues=[])


class _AlwaysInvalid(ValidationPipeline):
    def __init__(self, issue: str) -> None:
        self._issue = issue

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        return ValidationOutput(is_valid=False, issues=[self._issue])


def _extraction() -> ExtractionOutput:
    return ExtractionOutput(raw_text="", fields={}, confidence=0.0)


def test_valid_when_all_validators_pass() -> None:
    composite = CompositeValidationPipeline([_AlwaysValid(), _AlwaysValid()])
    result = composite.validate(_extraction())
    assert result.is_valid
    assert result.issues == []


def test_invalid_and_merges_issues_when_any_validator_fails() -> None:
    composite = CompositeValidationPipeline(
        [_AlwaysValid(), _AlwaysInvalid("issue a"), _AlwaysInvalid("issue b")]
    )
    result = composite.validate(_extraction())
    assert not result.is_valid
    assert result.issues == ["issue a", "issue b"]


def test_empty_validator_list_is_valid() -> None:
    composite = CompositeValidationPipeline([])
    result = composite.validate(_extraction())
    assert result.is_valid
    assert result.issues == []
