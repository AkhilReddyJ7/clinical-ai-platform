import hashlib

from modules.extraction.base import FieldExtractionOutput, FieldExtractionPipeline

# Deterministic, clearly-synthetic stand-ins for structured fields — not real
# PHI. Independent of modules.ocr.mock's synthetic values: that module
# synthesizes from raw upload bytes (bytes -> fields, pre-OCR mock), this one
# synthesizes from OCR'd text (text -> fields, mirroring the real
# AnthropicFieldExtractionPipeline's input). Kept as separate small constants
# rather than importing across mock modules, since the two mocks stand in
# for different pipeline stages.
_SYNTHETIC_NAMES = ["Jordan Rivera", "Casey Morgan", "Alex Chen", "Sam Patel"]
_SYNTHETIC_DOB = ["1975-03-14", "1988-11-02", "1990-07-22", "1966-05-09"]


def synthesize_fields_from_text(raw_text: str) -> dict[str, str]:
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16)

    return {
        "patient_name": _SYNTHETIC_NAMES[index % len(_SYNTHETIC_NAMES)],
        "date_of_birth": _SYNTHETIC_DOB[index % len(_SYNTHETIC_DOB)],
        "mrn": f"MOCK-{index % 1_000_000:06d}",
    }


class MockFieldExtractionPipeline(FieldExtractionPipeline):
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        if not raw_text.strip():
            return FieldExtractionOutput(fields={}, confidence=0.0)

        return FieldExtractionOutput(fields=synthesize_fields_from_text(raw_text), confidence=0.9)
