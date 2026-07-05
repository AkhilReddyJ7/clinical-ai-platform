import uuid
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.base import FieldExtractionOutput, FieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.mock import MockExtractionPipeline
from modules.ocr.models import ExtractionResult
from modules.processing.errors import TerminalProcessingError, TransientProcessingError
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import ProcessingResult, run_processing_pipeline
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.models import ValidationResult
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _FakeOCR(ExtractionPipeline):
    def __init__(self, raw_text: str, confidence: float = 1.0) -> None:
        self._raw_text = raw_text
        self._confidence = confidence

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        return ExtractionOutput(raw_text=self._raw_text, confidence=self._confidence)


class _FailingOCR(ExtractionPipeline):
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        raise ExtractionError("corrupted input bytes")


class _NoFieldsExtraction(FieldExtractionPipeline):
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        return FieldExtractionOutput(fields={}, confidence=0.0)


async def _make_document_and_job(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    *,
    data: bytes = b"some clinical note text",
    content_type: str = "text/plain",
) -> tuple[uuid.UUID, uuid.UUID]:
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, data)

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type=content_type,
            size_bytes=len(data),
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()

        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()
        return document.id, job.id


async def _run_pipeline(
    job: Job,
    db: AsyncSession,
    storage: LocalFileStorage,
    *,
    extraction_pipeline: ExtractionPipeline,
    field_extraction_pipeline: FieldExtractionPipeline,
) -> ProcessingResult:
    return await run_processing_pipeline(
        job,
        db=db,
        storage=storage,
        extraction_pipeline=extraction_pipeline,
        field_extraction_pipeline=field_extraction_pipeline,
        phi_validator=PHIDetectionValidator(),
        validation_pipeline=CompositeValidationPipeline(
            [RequiredFieldsValidator(), PHIDetectionValidator()]
        ),
    )


@pytest.mark.asyncio
async def test_successful_pipeline_returns_result_and_persists_extraction_and_validation(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run_pipeline(
            job,
            db,
            storage,
            extraction_pipeline=MockExtractionPipeline(),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
        )

    assert isinstance(result, ProcessingResult)
    assert result.job_id == job_id
    assert result.document_id == document_id
    assert result.fields
    assert result.is_valid is True
    assert result.confidence > 0.0

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.VALIDATED

        extraction = await db.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        assert extraction is not None
        assert extraction.job_id == job_id
        assert extraction.fields
        assert extraction.pipeline_version == "mock"

        validation = await db.scalar(
            select(ValidationResult).where(ValidationResult.document_id == document_id)
        )
        assert validation is not None
        assert validation.job_id == job_id
        assert validation.is_valid is True


@pytest.mark.asyncio
async def test_ocr_extraction_error_persists_failure_and_raises_terminal(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        with pytest.raises(TerminalProcessingError):
            await _run_pipeline(
                job,
                db,
                storage,
                extraction_pipeline=_FailingOCR(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
            )

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.FAILED

        extraction = await db.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        assert extraction is not None
        assert extraction.raw_text.startswith("[EXTRACTION FAILED:")
        assert extraction.pipeline_version == "mock"


@pytest.mark.asyncio
async def test_phi_detected_completes_job_but_fails_document(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run_pipeline(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("patient ssn 123-45-6789 needs follow-up"),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
        )

    # Per ADR-0023: the PHI gate did exactly its job — this is a
    # completed job (no exception), even though the document fails.
    assert result.is_valid is False
    assert result.metadata.get("outcome") == "phi_detected"
    assert "123-45-6789" not in result.raw_text

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.FAILED

        extraction = await db.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        assert extraction is not None
        assert "123-45-6789" not in extraction.raw_text
        assert extraction.raw_text.startswith("[REDACTED:")


@pytest.mark.asyncio
async def test_missing_required_field_completes_job_but_fails_document(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run_pipeline(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("a clinical note with no structured fields at all"),
            field_extraction_pipeline=_NoFieldsExtraction(),
        )

    assert result.is_valid is False
    assert any("missing required field" in issue for issue in result.issues)

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_terminal_field_extraction_error_persists_failure_and_raises_terminal(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    # No API key configured -> FieldExtractionError raised directly, with
    # no __cause__ -> terminal by ADR-0023's default classification.
    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        with pytest.raises(TerminalProcessingError):
            await _run_pipeline(
                job,
                db,
                storage,
                extraction_pipeline=_FakeOCR("a note with some content"),
                field_extraction_pipeline=field_extraction_pipeline,
            )

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.FAILED

        validation = await db.scalar(
            select(ValidationResult).where(ValidationResult.document_id == document_id)
        )
        assert validation is not None
        assert any("field extraction failed" in issue for issue in validation.issues)


@pytest.mark.asyncio
async def test_transient_field_extraction_error_persists_nothing_and_raises_transient(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="test-key", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )
    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
    )

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        with patch.object(
            field_extraction_pipeline._client.messages, "create", side_effect=rate_limit_error
        ):
            with pytest.raises(TransientProcessingError):
                await _run_pipeline(
                    job,
                    db,
                    storage,
                    extraction_pipeline=_FakeOCR("a note with some content"),
                    field_extraction_pipeline=field_extraction_pipeline,
                )

    async with session_factory() as db:
        # Document stays `processing` (ADR-0020) — a transient failure
        # does not end the document's non-terminal cycle.
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.PROCESSING

        extraction = await db.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        assert extraction is None
        validation = await db.scalar(
            select(ValidationResult).where(ValidationResult.document_id == document_id)
        )
        assert validation is None
