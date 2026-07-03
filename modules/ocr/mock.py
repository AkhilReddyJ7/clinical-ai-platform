import hashlib

from modules.ocr.base import ExtractionOutput, ExtractionPipeline

# Deterministic, clearly-synthetic stand-ins for structured fields — not
# real PHI. Used by MockExtractionPipeline (fully synthetic, incl. raw_text)
# and imported directly by TesseractExtractionPipeline (real raw_text, but
# fields stay synthetic pending a real field-extraction backend) — exists
# only to exercise the pipeline/validators until that backend is wired in.
_SYNTHETIC_NAMES = ["Jordan Rivera", "Casey Morgan", "Alex Chen", "Sam Patel"]
_SYNTHETIC_DOB = ["1975-03-14", "1988-11-02", "1990-07-22", "1966-05-09"]


def synthesize_fields(data: bytes, content_type: str) -> dict[str, str]:
    digest = hashlib.sha256(data).hexdigest()
    index = int(digest[:8], 16)

    return {
        "patient_name": _SYNTHETIC_NAMES[index % len(_SYNTHETIC_NAMES)],
        "date_of_birth": _SYNTHETIC_DOB[index % len(_SYNTHETIC_DOB)],
        "mrn": f"MOCK-{index % 1_000_000:06d}",
        "document_type": content_type,
    }


class MockExtractionPipeline(ExtractionPipeline):
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        if not data:
            return ExtractionOutput(raw_text="", fields={}, confidence=0.0)

        fields = synthesize_fields(data, content_type)
        raw_text = " ".join(f"{key}: {value}" for key, value in fields.items())

        return ExtractionOutput(raw_text=raw_text, fields=fields, confidence=0.87)
