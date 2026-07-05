from functools import lru_cache

from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.base import FieldExtractionPipeline
from modules.ingestion.storage import LocalFileStorage, StorageBackend
from modules.ocr.base import ExtractionPipeline
from modules.ocr.tesseract import TesseractExtractionPipeline
from modules.retrieval.answer_base import AnswerGenerator
from modules.retrieval.anthropic_answer import AnthropicAnswerGenerator
from modules.retrieval.base import EmbeddingPipeline, VectorStore
from modules.retrieval.chroma_store import ChromaVectorStore
from modules.retrieval.fastembed_embeddings import FastEmbedEmbeddingPipeline
from modules.retrieval.service import RetrievalService
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
    return TesseractExtractionPipeline(
        max_pdf_pages=settings.max_pdf_pages,
        preprocessing_enabled=settings.ocr_preprocessing_enabled,
        psm=settings.ocr_psm,
    )


@lru_cache
def get_field_extraction_pipeline() -> FieldExtractionPipeline:
    settings = get_settings()
    return AnthropicFieldExtractionPipeline(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_input_chars=settings.anthropic_max_input_chars,
    )


@lru_cache
def get_validation_pipeline() -> ValidationPipeline:
    return CompositeValidationPipeline([RequiredFieldsValidator(), PHIDetectionValidator()])


@lru_cache
def get_phi_validator() -> ValidationPipeline:
    # Used to gate the LLM call on raw_text alone, before fields exist —
    # see process_document. Deliberately a bare PHIDetectionValidator, not
    # the full composite: RequiredFieldsValidator would always fail against
    # an empty fields dict at this point in the flow, which isn't the
    # question being asked here.
    return PHIDetectionValidator()


@lru_cache
def get_embedding_pipeline() -> EmbeddingPipeline:
    settings = get_settings()
    return FastEmbedEmbeddingPipeline(
        model_name=settings.embedding_model_name,
        cache_dir=settings.embedding_model_cache_dir,
    )


@lru_cache
def get_vector_store() -> VectorStore:
    settings = get_settings()
    return ChromaVectorStore(
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=settings.chroma_collection_name,
    )


@lru_cache
def get_answer_generator() -> AnswerGenerator:
    settings = get_settings()
    return AnthropicAnswerGenerator(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_context_chars=settings.answer_max_context_chars,
    )


@lru_cache
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    return RetrievalService(
        embedding_pipeline=get_embedding_pipeline(),
        vector_store=get_vector_store(),
        chunk_size_chars=settings.retrieval_chunk_size_chars,
        overlap_chars=settings.retrieval_chunk_overlap_chars,
    )
