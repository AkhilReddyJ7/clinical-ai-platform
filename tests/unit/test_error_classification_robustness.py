import uuid
from pathlib import Path

import anthropic
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.extraction.base import FieldExtractionError
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.base import ExtractionOutput, ExtractionPipeline
from modules.ocr.models import ExtractionResult
from modules.processing.errors import TerminalProcessingError
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import (
    _is_transient_field_extraction_error,
    run_processing_pipeline,
)
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _wrap(cause: BaseException) -> FieldExtractionError:
    try:
        raise cause
    except type(cause) as exc:
        wrapped = FieldExtractionError(str(exc))
        wrapped.__cause__ = exc
        return wrapped


def test_rate_limit_is_transient() -> None:
    exc = _wrap(
        anthropic.RateLimitError(
            "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
        )
    )
    assert _is_transient_field_extraction_error(exc) is True


def test_connection_error_is_transient() -> None:
    exc = _wrap(anthropic.APIConnectionError(request=_REQUEST))
    assert _is_transient_field_extraction_error(exc) is True


def test_timeout_error_is_transient_via_connection_error_subclassing() -> None:
    # anthropic.APITimeoutError subclasses APIConnectionError — this locks
    # in that the classification catches it without needing its own
    # explicit branch, so a future SDK refactor that breaks this
    # inheritance would be caught by this test, not discovered in
    # production as silently-never-retried timeouts.
    exc = _wrap(anthropic.APITimeoutError(request=_REQUEST))
    assert _is_transient_field_extraction_error(exc) is True


def test_internal_server_error_5xx_is_transient() -> None:
    exc = _wrap(
        anthropic.InternalServerError(
            "server error", response=httpx.Response(500, request=_REQUEST), body=None
        )
    )
    assert _is_transient_field_extraction_error(exc) is True


@pytest.mark.parametrize(
    "status_code,error_cls",
    [
        (400, anthropic.BadRequestError),
        (401, anthropic.AuthenticationError),
        (403, anthropic.PermissionDeniedError),
        (404, anthropic.NotFoundError),
        (429, None),  # handled separately (RateLimitError), not via status code
    ],
)
def test_4xx_status_errors_are_terminal(status_code: int, error_cls: type | None) -> None:
    if error_cls is None:
        return
    exc = _wrap(
        error_cls("client error", response=httpx.Response(status_code, request=_REQUEST), body=None)
    )
    assert _is_transient_field_extraction_error(exc) is False


def test_missing_api_key_error_with_no_cause_is_terminal() -> None:
    exc = FieldExtractionError("Anthropic API key is not configured")
    assert exc.__cause__ is None
    assert _is_transient_field_extraction_error(exc) is False


def test_malformed_response_error_with_no_cause_is_terminal() -> None:
    exc = FieldExtractionError("Anthropic API did not return a tool call (stop_reason=refusal)")
    assert _is_transient_field_extraction_error(exc) is False


class _UnsupportedContentTypeOCR(ExtractionPipeline):
    """Mirrors TesseractExtractionPipeline's real behavior for a content
    type it doesn't recognize: a bare ValueError, not an ExtractionError.
    """

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        raise ValueError(f"unsupported content type for OCR: {content_type}")


@pytest.mark.asyncio
async def test_unrecognized_content_type_value_error_is_normalized_to_terminal(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Regression for a real gap this increment closes: previously only
    ExtractionError was caught here, so a bare ValueError (exactly what
    TesseractExtractionPipeline raises for an unrecognized content type)
    would propagate uncaught — the job would still fail via the worker's
    generic catch-all, but the document would be silently stuck in
    `processing` forever, with no persisted failure record.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    storage_key = f"{uuid.uuid4()}/file.bin"
    storage.save(storage_key, b"some bytes")

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="file.bin",
            content_type="application/octet-stream",
            size_bytes=10,
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()

        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()
        document_id, job_id = document.id, job.id

    async with session_factory() as db:
        claimed_job = await db.get(Job, job_id)
        assert claimed_job is not None
        with pytest.raises(TerminalProcessingError):
            await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=_UnsupportedContentTypeOCR(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),  # unused before raise
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    async with session_factory() as db:
        stored_document = await db.get(Document, document_id)
        assert stored_document is not None
        # The document reaches a terminal status, not stuck in `processing`.
        assert stored_document.status == DocumentStatus.FAILED

        extraction = await db.scalar(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        assert extraction is not None
        assert extraction.raw_text.startswith("[EXTRACTION FAILED:")
