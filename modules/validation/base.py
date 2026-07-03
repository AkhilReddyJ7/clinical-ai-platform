from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from modules.ocr.base import ExtractionOutput


@dataclass
class ValidationOutput:
    is_valid: bool
    issues: list[str] = field(default_factory=list)


class ValidationPipeline(ABC):
    """Interface for validating extracted document fields.

    A future clinical rules engine implements this same signature and can
    replace or compose with the existing validators (RequiredFieldsValidator,
    PHIDetectionValidator) without changing callers — see
    CompositeValidationPipeline for running several together.
    """

    @abstractmethod
    def validate(self, extraction: ExtractionOutput) -> ValidationOutput: ...
