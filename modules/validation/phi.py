import re
from dataclasses import dataclass

from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline


@dataclass(frozen=True)
class _PHIPattern:
    label: str
    pattern: re.Pattern[str]


_PHI_PATTERNS = (
    _PHIPattern("SSN-like number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    _PHIPattern("email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b")),
    _PHIPattern(
        "phone number",
        # \b can't anchor before "(" (neither side is a word char), so it's
        # only applied to the bare-digits alternative.
        re.compile(r"(?:\(\d{3}\)\s?|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"),
    ),
)


class PHIDetectionValidator(ValidationPipeline):
    """Flags text patterns that resemble real PHI (SSNs, emails, phone
    numbers) as a safety guardrail against accidentally ingesting real
    patient data through a future real OCR backend.

    Deliberately a lightweight pattern-matching heuristic, not a
    comprehensive PHI detector — no NER, no name/address recognition. A
    production system would likely pair this with something like Microsoft
    Presidio; that's a real dependency (spaCy + a language model) not
    justified for this first pass. See docs/adr for the scope tradeoff.
    """

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        issues = [
            f"phi: possible {phi_pattern.label} detected in extracted text"
            for phi_pattern in _PHI_PATTERNS
            if phi_pattern.pattern.search(extraction.raw_text)
        ]
        return ValidationOutput(is_valid=not issues, issues=issues)
