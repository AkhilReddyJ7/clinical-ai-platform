import hashlib

from modules.ocr.base import ExtractionOutput, ExtractionPipeline

# Deterministic, clearly-synthetic stand-ins for OCR output — not real PHI.
# Exists only to exercise the pipeline until a real OCR/extraction backend
# is wired in behind the ExtractionPipeline interface.
_SYNTHETIC_NAMES = ["Jordan Rivera", "Casey Morgan", "Alex Chen", "Sam Patel"]
_SYNTHETIC_DOB = ["1975-03-14", "1988-11-02", "1990-07-22", "1966-05-09"]


class MockExtractionPipeline(ExtractionPipeline):
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        if not data:
            return ExtractionOutput(raw_text="", fields={}, confidence=0.0)

        digest = hashlib.sha256(data).hexdigest()
        index = int(digest[:8], 16)

        fields = {
            "patient_name": _SYNTHETIC_NAMES[index % len(_SYNTHETIC_NAMES)],
            "date_of_birth": _SYNTHETIC_DOB[index % len(_SYNTHETIC_DOB)],
            "mrn": f"MOCK-{index % 1_000_000:06d}",
            "document_type": content_type,
        }
        raw_text = " ".join(f"{key}: {value}" for key, value in fields.items())

        return ExtractionOutput(raw_text=raw_text, fields=fields, confidence=0.87)
