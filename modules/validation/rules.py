from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline

REQUIRED_FIELDS = ("patient_name", "mrn", "date_of_birth")


class RequiredFieldsValidator(ValidationPipeline):
    """Baseline validator: confirms extraction produced the required fields.

    Composed with PHIDetectionValidator (see CompositeValidationPipeline)
    rather than absorbing that concern itself — data completeness and PHI
    safety are different checks with different failure modes.
    """

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        # .strip() before the truthiness check: a whitespace-only value
        # (e.g. a field extractor emitting " " instead of omitting the key)
        # is not meaningfully present and was previously treated as if it
        # were — a false negative for "missing" that this closes.
        issues = [
            f"missing required field: {field_name}"
            for field_name in REQUIRED_FIELDS
            if not extraction.fields.get(field_name, "").strip()
        ]
        return ValidationOutput(is_valid=not issues, issues=issues)
