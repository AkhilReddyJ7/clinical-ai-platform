from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from modules.ocr.base import ExtractionOutput


@dataclass
class ValidationOutput:
    is_valid: bool
    issues: list[str] = field(default_factory=list)


class ValidationPipeline(ABC):
    """Interface for validating extracted document fields.

    A future PHI-detection pass or clinical rules engine implements this
    same signature and can replace RequiredFieldsValidator without changing
    callers.
    """

    @abstractmethod
    def validate(self, extraction: ExtractionOutput) -> ValidationOutput: ...
