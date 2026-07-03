from functools import lru_cache

from modules.ingestion.storage import LocalFileStorage, StorageBackend
from modules.ocr.base import ExtractionPipeline
from modules.ocr.tesseract import TesseractExtractionPipeline
from modules.validation.base import ValidationPipeline
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator
from shared.config.settings import get_settings


@lru_cache
def get_storage() -> StorageBackend:
    settings = get_settings()
    return LocalFileStorage(settings.storage_root)


@lru_cache
def get_extraction_pipeline() -> ExtractionPipeline:
    settings = get_settings()
    return TesseractExtractionPipeline(max_pdf_pages=settings.max_pdf_pages)


@lru_cache
def get_validation_pipeline() -> ValidationPipeline:
    return CompositeValidationPipeline([RequiredFieldsValidator(), PHIDetectionValidator()])
