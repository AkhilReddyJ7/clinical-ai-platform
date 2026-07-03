from functools import lru_cache

from modules.ingestion.storage import LocalFileStorage, StorageBackend
from modules.ocr.base import ExtractionPipeline
from modules.ocr.mock import MockExtractionPipeline
from modules.validation.base import ValidationPipeline
from modules.validation.rules import RequiredFieldsValidator
from shared.config.settings import get_settings


@lru_cache
def get_storage() -> StorageBackend:
    settings = get_settings()
    return LocalFileStorage(settings.storage_root)


@lru_cache
def get_extraction_pipeline() -> ExtractionPipeline:
    return MockExtractionPipeline()


@lru_cache
def get_validation_pipeline() -> ValidationPipeline:
    return RequiredFieldsValidator()
