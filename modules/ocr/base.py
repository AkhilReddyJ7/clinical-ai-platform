from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExtractionOutput:
    raw_text: str
    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


class ExtractionError(Exception):
    """Raised when a pipeline cannot process the given bytes at all —
    corrupted data, or content that doesn't match the declared
    content_type. Distinct from successfully extracting little/no text
    (a normal ExtractionOutput with an empty raw_text): this signals the
    input itself couldn't be read, so callers should treat it as a
    processing failure, not a valid (if empty) result.
    """


class ExtractionPipeline(ABC):
    """Interface for OCR / document-extraction backends.

    A real backend (Textract, a layout-aware model, an LLM-based extractor)
    implements this same signature so it can replace MockExtractionPipeline
    without changing callers.
    """

    @abstractmethod
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput: ...
