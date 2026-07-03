from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline


class CompositeValidationPipeline(ValidationPipeline):
    """Runs multiple validators against the same extraction and merges
    their findings into a single ValidationOutput — invalid if any
    validator is invalid, issues concatenated in validator order.
    """

    def __init__(self, validators: list[ValidationPipeline]) -> None:
        self._validators = validators

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        issues: list[str] = []
        is_valid = True
        for validator in self._validators:
            result = validator.validate(extraction)
            issues.extend(result.issues)
            is_valid = is_valid and result.is_valid
        return ValidationOutput(is_valid=is_valid, issues=issues)
