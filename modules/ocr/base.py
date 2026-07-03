from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExtractionOutput:
    raw_text: str
    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


class ExtractionPipeline(ABC):
    """Interface for OCR / document-extraction backends.

    A real backend (Textract, a layout-aware model, an LLM-based extractor)
    implements this same signature so it can replace MockExtractionPipeline
    without changing callers.
    """

    @abstractmethod
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput: ...
