from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline

REQUIRED_FIELDS = ("patient_name", "mrn", "date_of_birth")


class RequiredFieldsValidator(ValidationPipeline):
    """Baseline validator: confirms extraction produced the required fields.

    Placeholder for future rule sets (schema validation, PHI redaction
    checks, clinical plausibility checks).
    """

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        issues = [
            f"missing required field: {field_name}"
            for field_name in REQUIRED_FIELDS
            if not extraction.fields.get(field_name)
        ]
        return ValidationOutput(is_valid=not issues, issues=issues)
