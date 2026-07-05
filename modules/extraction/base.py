from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FieldExtractionOutput:
    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


class FieldExtractionError(Exception):
    """Raised when a field-extraction backend cannot process the given text
    at all — an unreachable provider, a rejected request, or a malformed
    response. Distinct from successfully extracting no fields (a normal
    FieldExtractionOutput with an empty fields dict): this signals the call
    itself failed, so callers should treat it as a processing failure, not
    a valid (if empty) result. Mirrors ExtractionError (modules/ocr/base.py)
    for the same reason.
    """


class FieldExtractionPipeline(ABC):
    """Interface for turning OCR'd text into structured clinical fields.

    Deliberately a separate stage from ExtractionPipeline (modules/ocr/base.py):
    that stage turns bytes into raw_text; this one turns raw_text into
    structured fields. A single Anthropic-backed implementation exists today
    (AnthropicFieldExtractionPipeline) — this ABC is the seam for a future
    second implementation, not a provider-agnostic tree built in advance.
    See docs/adr/0019.
    """

    @abstractmethod
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput: ...

    @property
    def pipeline_version(self) -> str:
        """Identifies which backend/model produced a result (ADR-0031),
        recorded on ExtractionResult at persistence time. A concrete
        property with a default, not abstract: an abstract method would
        break every test-double subclass that implements nothing beyond
        extract_fields. Real implementations override this.
        """
        return type(self).__name__
