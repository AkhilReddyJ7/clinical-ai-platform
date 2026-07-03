import re
from dataclasses import dataclass

from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationOutput, ValidationPipeline


@dataclass(frozen=True)
class _PHIPattern:
    label: str
    pattern: re.Pattern[str]


_PHI_PATTERNS = (
    _PHIPattern("SSN-like number", re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")),
    _PHIPattern("email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b")),
    _PHIPattern(
        "phone number",
        # \b can't anchor before "(" (neither side is a word char), so it's
        # only applied to the bare-digits alternative.
        re.compile(r"(?:\(\d{3}\)\s?|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"),
    ),
    _PHIPattern(
        "IP address",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    ),
    _PHIPattern(
        "credit card number",
        re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
    ),
)


class PHIDetectionValidator(ValidationPipeline):
    """Flags text patterns that resemble real PHI/PII (SSNs, emails, phone
    numbers, IP addresses, credit card numbers) as a safety guardrail
    against accidentally ingesting real patient data through the real OCR
    backend.

    Deliberately a lightweight pattern-matching heuristic, not a
    comprehensive PHI detector. Two categories are excluded on purpose,
    for different reasons — see docs/adr/0015 for the full evaluation:

    - Person names and street addresses require NER (no reliable regex
      shape exists for either); a production system would likely pair this
      with something like Microsoft Presidio for those. Deferred pending a
      decision — spaCy + a language model is a real dependency (build
      time, image size, per-call latency), not something to add silently.
    - Generic dates, age+zip-code combinations, and unformatted bare digit
      runs (SSN/phone with no separators) were deliberately left out even
      though they're theoretically regex-matchable: every clinical note
      has multiple non-DOB dates, and a bare 9- or 10-digit number is
      indistinguishable from countless benign IDs without context a plain
      regex can't see. Adding them would trade signal for noise.
    """

    def validate(self, extraction: ExtractionOutput) -> ValidationOutput:
        issues = [
            f"phi: possible {phi_pattern.label} detected in extracted text"
            for phi_pattern in _PHI_PATTERNS
            if phi_pattern.pattern.search(extraction.raw_text)
        ]
        return ValidationOutput(is_valid=not issues, issues=issues)
