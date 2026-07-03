from pydantic import BaseModel

from modules.ingestion.schemas import DocumentOut
from modules.ocr.schemas import ExtractionResultOut
from modules.validation.schemas import ValidationResultOut


class ProcessingResultOut(BaseModel):
    document: DocumentOut
    extraction: ExtractionResultOut
    validation: ValidationResultOut


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int
